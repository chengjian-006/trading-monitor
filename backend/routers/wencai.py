"""问财候选榜 API — 同花顺问财自然语言选股 (v1.7.540, v1.7.546 支持用户自定义语句).

  GET    /api/wencai              预置榜(全局) + 当前用户自定义榜的最新候选快照
  POST   /api/wencai/search       即时搜索: 输入条件立刻跑一次 pywencai 返回结果(不保存)
  POST   /api/wencai/add-to-pool  把选中的候选股一键加入自选池
  GET    /api/wencai/queries      当前用户保存的自定义选股语句
  POST   /api/wencai/queries      新增一条常驻语句(立即跑一次出榜)
  PUT    /api/wencai/queries/{id} 改语句(name/query/enabled), 启用则即刻重跑、禁用则清掉榜
  DELETE /api/wencai/queries/{id} 删除语句及其候选榜
"""
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.core.config import load_config
from backend.models import repository
from backend.fetcher.wencai_screener import fetch_wencai, WencaiFetchError
from backend.services.quote_refresher import refresh_quotes_for_codes

router = APIRouter(prefix="/api/wencai", tags=["wencai"])

# 即时搜索 / 手工全榜刷新 per-user 节流: 逆向接口高频会招同花顺风控, 限每用户最短间隔
_SEARCH_MIN_INTERVAL = 3.0
_last_search: dict[int, float] = {}
_SCAN_MIN_INTERVAL = 8.0
_last_scan: dict[int, float] = {}


def _result_limit() -> int:
    return int(load_config().get("wencai_screening", {}).get("result_limit", 50))


def _query_id_of(strategy_id: str, user_id: int) -> int | None:
    """从自定义榜 strategy_id (u{uid}_q{qid}) 反解 query_id; 预置榜返回 None。"""
    prefix = f"u{user_id}_q"
    if strategy_id.startswith(prefix):
        try:
            return int(strategy_id[len(prefix):])
        except ValueError:
            return None
    return None


@router.get("")
async def get_wencai(user: Annotated[dict, Depends(get_current_user)]):
    """问财候选榜: 预置榜 + 当前用户自定义榜, 每条含候选清单与刷新状态。"""
    rows = await repository.list_wencai_pool(user["id"])
    strategies = []
    for r in rows:
        qid = _query_id_of(r.get("strategy_id", ""), user["id"])
        strategies.append({
            "strategy_id": r.get("strategy_id"),
            "strategy_name": r.get("strategy_name"),
            "query_text": r.get("query_text"),
            "trade_date": r.get("trade_date"),
            "computed_at": r.get("computed_at"),
            "stock_count": r.get("stock_count", 0),
            "last_error": r.get("last_error") or "",
            "is_custom": r.get("user_id", 0) != 0,
            "query_id": qid,
            "items": r.get("items") or [],
        })
    return {"strategies": strategies}


class SearchRequest(BaseModel):
    query: str


@router.post("/search")
async def search_wencai(req: SearchRequest, user: Annotated[dict, Depends(get_current_user)]):
    """即时搜索: 立刻跑一次问财选股返回结果(不保存)。per-user 节流防风控。"""
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="请输入选股条件")
    now = time.monotonic()
    last = _last_search.get(user["id"], 0.0)
    if now - last < _SEARCH_MIN_INTERVAL:
        raise HTTPException(status_code=429, detail="搜索太频繁, 请稍候再试")
    _last_search[user["id"]] = now
    try:
        items = await fetch_wencai(q, limit=_result_limit())
    except WencaiFetchError as e:
        raise HTTPException(status_code=502, detail=f"问财查询失败: {e}")
    return {"query": q, "stock_count": len(items), "items": items}


@router.post("/scan")
async def manual_scan(user: Annotated[dict, Depends(get_current_user)]):
    """手工触发: 立刻跑「预置榜 + 当前用户启用的自定义榜」全部语句, 刷新候选(串行防并发撞反爬)。

    问财候选榜已改为手工触发(原定时任务下线): 同花顺问财逆向接口易被反爬, 按需点才跑。
    """
    now = time.monotonic()
    if now - _last_scan.get(user["id"], 0.0) < _SCAN_MIN_INTERVAL:
        raise HTTPException(status_code=429, detail="刚刷新过, 请稍候再点")
    _last_scan[user["id"]] = now

    cfg = load_config().get("wencai_screening", {})
    work: list[tuple] = []   # (strategy_id, user_id, name, query)
    for q in (cfg.get("queries") or []):
        if q.get("enabled", True) and q.get("query"):
            sid = q.get("id") or q.get("name") or ""
            work.append((sid, 0, q.get("name") or sid, q.get("query")))
    for uq in await repository.list_user_queries(user["id"]):
        if uq.get("enabled", 1) and uq.get("query_text"):
            sid = repository.pool_strategy_id(user["id"], uq["id"])
            work.append((sid, user["id"], uq.get("name") or sid, uq["query_text"]))

    import asyncio
    succeeded, failed = 0, []
    for i, (sid, uid, name, query) in enumerate(work):
        if i > 0:
            await asyncio.sleep(2.5)   # 语句间隔: 连打易触发同花顺 IP 级风控, 拉开间隔降触发
        r = await _run_into_pool(sid, uid, name, query)
        if r["ok"]:
            succeeded += 1
        else:
            failed.append(name)
    return {"ok": True, "total": len(work), "succeeded": succeeded, "failed": failed}


class AddToPoolRequest(BaseModel):
    stocks: list[dict]   # [{code, name}]


@router.post("/add-to-pool")
async def add_to_pool(req: AddToPoolRequest, user: Annotated[dict, Depends(get_current_user)]):
    """把问财候选股一键加入自选池(逐只 upsert + 复活逻辑删, 最后批量刷行情)。"""
    added = 0
    codes: list[str] = []
    for item in req.stocks:
        code = str(item.get("code", "")).strip().zfill(6)
        name = str(item.get("name", "")).strip()
        if len(code) != 6 or not code.isdigit():
            continue
        await repository.add_stock(code, name, "short", "watch", user["id"])
        codes.append(code)
        added += 1
    if codes:
        await refresh_quotes_for_codes(codes)
    return {"ok": True, "added": added, "total": len(req.stocks)}


# ── 用户自定义选股语句 ──

@router.get("/queries")
async def list_queries(user: Annotated[dict, Depends(get_current_user)]):
    return {"queries": await repository.list_user_queries(user["id"])}


class QueryCreate(BaseModel):
    name: str = ""
    query: str


async def _run_into_pool(strategy_id: str, user_id: int, name: str, query: str) -> dict:
    """立即跑一条语句并写进候选榜; 返回 {ok, stock_count, error}。"""
    from datetime import datetime
    trade_date = datetime.now().strftime("%Y-%m-%d")
    try:
        items = await fetch_wencai(query, limit=_result_limit())
        await repository.upsert_wencai_strategy(strategy_id, user_id, name, query, trade_date, items)
        return {"ok": True, "stock_count": len(items), "error": ""}
    except WencaiFetchError as e:
        # 仍建一行(空结果+错误), 让榜出现、下次定时重试
        await repository.upsert_wencai_strategy(strategy_id, user_id, name, query, trade_date, [], str(e))
        return {"ok": False, "stock_count": 0, "error": str(e)}


@router.post("/queries")
async def create_query(req: QueryCreate, user: Annotated[dict, Depends(get_current_user)]):
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="请输入选股条件")
    name = (req.name or "").strip() or (q[:18] + ("…" if len(q) > 18 else ""))
    qid = await repository.add_wencai_query(user["id"], name, q)
    sid = repository.pool_strategy_id(user["id"], qid)
    run = await _run_into_pool(sid, user["id"], name, q)
    return {"ok": True, "id": qid, "run": run}


class QueryUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    enabled: int | None = None


@router.put("/queries/{query_id}")
async def update_query(query_id: int, req: QueryUpdate,
                       user: Annotated[dict, Depends(get_current_user)]):
    existing = await repository.get_wencai_query(query_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="语句不存在")
    fields = {}
    if req.name is not None:
        fields["name"] = req.name.strip()
    if req.query is not None:
        fields["query_text"] = req.query.strip()
    if req.enabled is not None:
        fields["enabled"] = int(req.enabled)
    await repository.update_wencai_query(query_id, user["id"], **fields)

    sid = repository.pool_strategy_id(user["id"], query_id)
    name = fields.get("name", existing["name"])
    query = fields.get("query_text", existing["query_text"])
    enabled = fields.get("enabled", existing["enabled"])
    run = None
    if enabled:
        run = await _run_into_pool(sid, user["id"], name, query)   # 启用: 即刻重跑刷新榜
    else:
        await repository.delete_wencai_pool_row(sid)               # 禁用: 从榜移除
    return {"ok": True, "run": run}


@router.delete("/queries/{query_id}")
async def delete_query(query_id: int, user: Annotated[dict, Depends(get_current_user)]):
    sid = repository.pool_strategy_id(user["id"], query_id)
    await repository.delete_wencai_query(query_id, user["id"])
    await repository.delete_wencai_pool_row(sid)
    return {"ok": True}
