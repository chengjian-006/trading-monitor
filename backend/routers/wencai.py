"""问财候选榜 API — 同花顺问财自然语言选股 (v1.7.540, v1.7.546 支持用户自定义语句).

  GET    /api/wencai              预置榜(全局) + 当前用户自定义榜的最新候选快照
  POST   /api/wencai/search       即时搜索: 输入条件立刻跑一次 pywencai 返回结果(不保存)
  POST   /api/wencai/add-to-pool  把选中的候选股一键加入自选池
  GET    /api/wencai/queries      当前用户保存的自定义选股语句
  POST   /api/wencai/queries      新增一条常驻语句(立即跑一次出榜)
  PUT    /api/wencai/queries/{id} 改语句(name/query/enabled), 启用则即刻重跑、禁用则清掉榜
  DELETE /api/wencai/queries/{id} 删除语句及其候选榜
  GET    /api/wencai/ask-presets  个股「问财提问」预设问题(每用户一套, 空则播种默认)
  POST   /api/wencai/ask-presets  新增一条预设问题模版
  PUT    /api/wencai/ask-presets/{id}       改模版(label/template/enabled)
  DELETE /api/wencai/ask-presets/{id}       删模版
  PUT    /api/wencai/ask-presets/order/all  整体重排(上移/下移)
  POST   /api/wencai/ask-presets/reset      恢复系统默认模版
  POST   /api/wencai/ingest       本地油猴代跑上报: 浏览器登录态查问财→归一化→POST结果落库(共享密钥鉴权, 免JWT)
"""
import asyncio
import hmac
import io
import json
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
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


# ── 扩展/油猴 分发与自更新 (v1.7.681) ──
# 「问财观点」浏览器扩展目前只能开发者模式装(无商店、无自更新), 朋友版本会漂移。
# 部署把整个项目目录随包上服务器(deploy.ps1 tar 全量), 故 extension/ 与 docs/ 在生产可直接读:
#   /ext/version   读已部署的 manifest.json + 油猴 @version 作「最新版真源」(零漂移, 部署即同步)
#   /ext/download  现场把 extension/wencai-opinion/ 打包成 zip 供下载(永远是刚部署那版)
#   /userscript.user.js  托管油猴脚本原文, 供 @updateURL/@downloadURL 自动更新
# 均免鉴权: 纯静态自身分发内容, 无用户输入、无路径穿越(读固定路径)。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXT_DIR = _REPO_ROOT / "extension" / "wencai-opinion"
_USERSCRIPT_FILE = _REPO_ROOT / "docs" / "tampermonkey-wencai-opinion.user.js"


def _read_dist_versions() -> dict:
    """读已部署扩展 manifest 版本 + 油猴脚本 @version 作为「最新可用版」真源。读不到给空串。"""
    ext_ver = ""
    try:
        mf = json.loads((_EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
        ext_ver = str(mf.get("version", "")).strip()
    except Exception:
        pass
    us_ver = ""
    try:
        head = _USERSCRIPT_FILE.read_text(encoding="utf-8")[:2000]
        m = re.search(r"@version\s+([0-9][0-9.]*)", head)
        if m:
            us_ver = m.group(1)
    except Exception:
        pass
    return {"ext_version": ext_ver, "userscript_version": us_ver}


@router.get("/ext/version")
async def ext_version():
    """给扩展/油猴查「最新可用版本」: 扩展读它比对本地 manifest 版本 → 有新版就红点提醒。"""
    return _read_dist_versions()


@router.get("/ext/trading-day")
async def ext_trading_day():
    """给扩展「定时自动问」判断今天要不要跑 —— 免鉴权, 同 /ext/version。

    起因: 扩展自己只会 `getDay()===0||6` 跳周末, **不认法定节假日**, 于是国庆/春节整周照跑,
    白耗问财额度还平添封号风险。而后端早已接 chinese-calendar(v1.7.464 修休市日误推), 有权威
    交易日历 —— 与其让扩展自己猜, 不如问服务器一次。扩展侧按日期缓存结果, 取不到时回退它自己
    的周末判断(宁可多跑也不要因为服务器不可达就整天不跑)。
    """
    from backend.core.trading_calendar import is_workday

    return {"date": datetime.now().date().isoformat(), "is_trading_day": bool(is_workday())}


@router.get("/ext/download")
async def ext_download():
    """现场把 extension/wencai-opinion/ 打包成 zip 下载(顶层保留 wencai-opinion/ 便于解压加载)。"""
    if not _EXT_DIR.is_dir():
        raise HTTPException(status_code=404, detail="扩展目录不存在")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(_EXT_DIR.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(_EXT_DIR.parent))   # 归档名含顶层 wencai-opinion/
    ver = _read_dist_versions()["ext_version"] or "latest"
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="wencai-opinion-{ver}.zip"'},
    )


@router.get("/quote")
async def wencai_quote(code: str):
    """按 6 位股票代码返当前现价(供扩展详情页算「距现价 %」)。免鉴权、只收合法代码。"""
    code = re.sub(r"\D", "", code or "")[:6]
    if len(code) != 6:
        raise HTTPException(status_code=400, detail="code 非法")
    from backend.fetcher.quotes import get_realtime_quotes
    try:
        q = await get_realtime_quotes([code])
    except Exception:
        q = {}
    d = q.get(code) or {}
    return {"code": code, "price": d.get("price"), "pct_change": d.get("pct_change"),
            "name": d.get("name", ""), "pre_close": d.get("pre_close")}


@router.get("/userscript.user.js")
async def userscript():
    """托管油猴脚本原文: 供篡改猴 @updateURL/@downloadURL 拉取自动更新(路径 .user.js 便于点开即装)。"""
    if not _USERSCRIPT_FILE.is_file():
        raise HTTPException(status_code=404, detail="油猴脚本不存在")
    text = _USERSCRIPT_FILE.read_text(encoding="utf-8")
    return Response(content=text, media_type="text/javascript; charset=utf-8")


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


# ── 本地油猴代跑上报 (www.iwencai.com 登录态浏览器代跑) ──
# 云端出口 IP 被同花顺 IP 级风控(pywencai 从生产拉不到), 改为在本机浏览器登录态页面内查问财、
# 油猴脚本归一化后 POST 到这里落库。脚本在同花顺域无本系统 JWT, 故不走 get_current_user, 靠
# 共享密钥 ingest_token 鉴权(仿 blogger renew_token)。语句可自定义: 脚本每轮从 /ingest/queries
# 拉「当前该跑的语句清单」(预置 + 各用户启用的自定义), 不写死。normalize 在油猴 JS 做, 服务器
# 只做防御性清洗(不信客户端): code 6位校验 / 名称·标签截断 / 数值强转 / 白名单 extra / 条数上限。

_INGEST_MAX_ITEMS = 200
_INGEST_EXTRA_ALLOWED = {"tech_pattern", "buy_signal", "concepts", "industry",
                         "turnover", "amount", "free_cap"}


def _uid_of(strategy_id: str) -> int:
    """从 strategy_id 反解 user_id: 自定义榜 u{uid}_q{qid} → uid; 预置榜(breakout 等) → 0。"""
    m = re.match(r"^u(\d+)_q\d+$", strategy_id or "")
    return int(m.group(1)) if m else 0


def _ingest_token_ok(token: str) -> bool:
    expected = load_config().get("wencai_screening", {}).get("ingest_token", "") or ""
    return bool(expected) and hmac.compare_digest(str(token), str(expected))


class IngestQueriesRequest(BaseModel):
    token: str


@router.post("/ingest/queries")
async def ingest_queries(req: IngestQueriesRequest):
    """给本地油猴脚本下发「当前该跑的选股语句清单」= 预置(config) + 各用户启用的自定义语句。

    语句可在系统里自定义(config 预置 + 前端 /api/wencai/queries 增删改), 脚本每轮拉最新清单跑,
    不写死。共享密钥鉴权(同 ingest)。返回 [{strategy_id, name, query}]。
    """
    if not _ingest_token_ok(req.token):
        raise HTTPException(status_code=401, detail="ingest token 无效")
    cfg = load_config().get("wencai_screening", {})
    out: list[dict] = []
    for q in (cfg.get("queries") or []):
        if q.get("enabled", True) and q.get("query"):
            sid = q.get("id") or q.get("name") or ""
            out.append({"strategy_id": sid, "name": q.get("name") or sid, "query": q.get("query")})
    try:
        for uq in await repository.list_all_enabled_queries():
            if uq.get("query_text"):
                sid = repository.pool_strategy_id(uq["user_id"], uq["id"])
                out.append({"strategy_id": sid, "name": uq.get("name") or sid,
                            "query": uq["query_text"]})
    except Exception:
        pass
    return {"queries": out}


class IngestItem(BaseModel):
    code: str
    name: str = ""
    price: float | None = None
    pct_change: float | None = None
    extra: dict = {}


class IngestRequest(BaseModel):
    token: str
    strategy_id: str
    strategy_name: str = ""
    query_text: str = ""
    trade_date: str = ""
    items: list[IngestItem] = []


def _sanitize_ingest_items(items: list[IngestItem]) -> list[dict]:
    """油猴上报的候选行防御性清洗(不信客户端): 只留 6 位代码的行, 字段限长/强转, extra 走白名单。"""
    out: list[dict] = []
    for it in items[:_INGEST_MAX_ITEMS]:
        code = str(it.code or "").strip().zfill(6)
        if not re.match(r"^\d{6}$", code):
            continue
        extra: dict = {}
        for k, v in (it.extra or {}).items():
            if k not in _INGEST_EXTRA_ALLOWED or v is None:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                extra[k] = round(float(v), 4)
            else:
                extra[k] = str(v)[:120]
        num = lambda x: float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None
        out.append({"code": code, "name": str(it.name or "").strip()[:40],
                    "price": num(it.price), "pct_change": num(it.pct_change), "extra": extra})
    return out


@router.post("/ingest")
async def ingest_wencai(req: IngestRequest):
    """本地油猴代跑上报一条选股语句的候选结果 → 整行 UPSERT 进 cfzy_sys_wencai_pool。

    共享密钥鉴权(config.wencai_screening.ingest_token); token 不匹配/为空则拒收, 绝不落库。
    一条语句一次 POST; 归一化在油猴 JS 完成, 这里只清洗+落库。user_id 从 strategy_id 反解。
    """
    if not _ingest_token_ok(req.token):
        raise HTTPException(status_code=401, detail="ingest token 无效")
    sid = (req.strategy_id or "").strip()[:40]
    if not sid:
        raise HTTPException(status_code=400, detail="strategy_id 为空")
    items = _sanitize_ingest_items(req.items)
    trade_date = (req.trade_date or "").strip()[:10] or datetime.now().strftime("%Y-%m-%d")
    name = (req.strategy_name or "").strip()[:40] or sid
    query_text = (req.query_text or "").strip()[:255]
    await repository.upsert_wencai_strategy(sid, _uid_of(sid), name, query_text, trade_date, items)
    return {"ok": True, "strategy_id": sid, "stock_count": len(items)}


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


# ── 问财观点参考 (v1.7.627) ──
# chat「智能调度」投顾式推荐(aime stream-query SSE)的存档: 本地油猴登录态发问、把整段话术上报,
# 服务器拿话术去撞全市场名称字典抽出被提及的股票。共享密钥鉴权(同 ingest)。是 LLM 观点非回测信号。

_OPINION_MAX_ANSWER = 50000   # 话术封顶(防超大 body); MEDIUMTEXT 够存
_OPINION_MAX_STOCKS = 12
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _scan_names(text: str, names: list[dict]) -> list[dict]:
    """纯CPU: 在话术文本里撞全市场名称字典/6位代码。~5000名称的 O(N×len) 扫描,
    必须放线程池跑(见 _extract_stocks), 绝不能直接在单worker事件循环上跑(否则匿名接口可打挂全站)。"""
    code2name = {str(r["code"]): str(r["name"]) for r in names}
    found: dict[str, dict] = {}   # code -> {code, name, count, idx}

    for m in _CODE_RE.finditer(text):
        code = m.group(1)
        if code in code2name:
            f = found.setdefault(code, {"code": code, "name": code2name[code],
                                        "count": 0, "idx": m.start()})
            f["count"] += 1
            f["idx"] = min(f["idx"], m.start())

    for r in names:
        nm = str(r["name"])
        if len(nm) < 3 or nm not in text:
            continue
        code = str(r["code"])
        cnt = text.count(nm)
        idx = text.find(nm)
        f = found.get(code)
        if f:
            f["count"] += cnt
            f["idx"] = min(f["idx"], idx)
        else:
            found[code] = {"code": code, "name": nm, "count": cnt, "idx": idx}

    ranked = sorted(found.values(), key=lambda x: (-x["count"], x["idx"]))
    return [{"code": s["code"], "name": s["name"], "primary": i == 0}
            for i, s in enumerate(ranked[:_OPINION_MAX_STOCKS])]


async def _extract_stocks(text: str) -> list[dict]:
    """从投顾话术里撞出被提及的 A 股: ①正文里的 6 位代码 ②全市场名称字典命中(全名 len>=3 防误伤)。
    按出现次数降序、首现位置升序排, 第一只标 primary。CPU重活卸到线程池, 不阻塞事件循环。"""
    if not text:
        return []
    try:
        names = await repository.all_stock_names()   # [{code, name}]
    except Exception:
        names = []
    return await asyncio.to_thread(_scan_names, text, names)


# ── H4 安全整改 (v1.7.653): /opinion 无鉴权(用户拍板)靠 IP 限流兜底防匿名刷库/DoS ──
# 阈值远超油猴正常量(每天几次), 只挡洪水; 进程内滑窗·单 worker 足够。
_OPINION_RL_WINDOW = 60          # 分钟滑窗(秒)
_OPINION_RL_MAX = 30             # 每 IP 每分钟上限
_OPINION_RL_DAY_MAX = 500        # 每 IP 每日上限(挡持续刷库)
_opinion_hits: dict[str, list[float]] = {}
_opinion_day: dict[str, tuple[str, int]] = {}


def _opinion_client_ip(request: Request) -> str:
    """真实客户端 IP(问财观点按IP限流用)。优先 X-Real-IP(nginx设, 不可伪造), 退而取 XFF 最后一段;
    绝不取 XFF 首段(客户端可控, 换首段即可绕过按IP限流)。"""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _opinion_rate_limit(request: Request) -> None:
    """超每分钟或每日上限 → 429; 进程内惰性清理防字典膨胀。"""
    ip = _opinion_client_ip(request)
    now = time.time()
    hits = [t for t in _opinion_hits.get(ip, []) if t > now - _OPINION_RL_WINDOW]
    if len(hits) >= _OPINION_RL_MAX:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="上报过于频繁，请稍后再试", headers={"Retry-After": "60"})
    hits.append(now)
    _opinion_hits[ip] = hits
    day = datetime.now().strftime("%Y%m%d")
    d, c = _opinion_day.get(ip, (day, 0))
    if d != day:
        d, c = day, 0
    if c >= _OPINION_RL_DAY_MAX:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="今日上报已达上限")
    _opinion_day[ip] = (d, c + 1)
    if len(_opinion_hits) > 5000:      # 惰性清理
        for k in [k for k, v in _opinion_hits.items() if not v or v[-1] <= now - _OPINION_RL_WINDOW]:
            _opinion_hits.pop(k, None)


class OpinionIngestRequest(BaseModel):
    token: str = ""                 # 已不校验(2026-07-16 用户拍板去掉观点上报鉴权), 留字段兼容旧客户端
    question: str
    answer_text: str = ""
    trace_id: str = ""
    agent_mode: str = ""
    uploader: str = ""              # 上报人昵称(共用 token 下区分是谁问的)
    reasoning: str = ""             # 思考过程(问财 deep_research reasoning), 供网页折叠展示
    conclusion: dict = {}           # 结构化结论(客户端从话术抽的 主推/买点/止盈/止损/周期/逻辑/风险)
    only_with_stock: bool = False   # True: 话术里没撞出个股就不落库(客户端「仅识别出个股才上报」)


@router.post("/opinion")
async def ingest_opinion(req: OpinionIngestRequest, request: Request):
    """本地浏览器代跑上报一条问财 chat 观点 → 抽股票 → 落 cfzy_biz_wencai_opinion(全局 user_id=0)。

    不做 token 鉴权(2026-07-16 用户拍板: 个人自用降低配置门槛; 候选榜 ingest 两口仍留密钥)。
    答案话术在客户端从 SSE 拼好整段传来, 这里撞字典抽票 + 落库。
    only_with_stock=True 且没抽出个股时跳过入库(返回 skipped)。
    """
    _opinion_rate_limit(request)   # H4: 无鉴权靠限流兜底
    question = (req.question or "").strip()[:255]
    if not question:
        raise HTTPException(status_code=400, detail="question 为空")
    answer = (req.answer_text or "").strip()[:_OPINION_MAX_ANSWER]
    stocks = await _extract_stocks(answer)
    if req.only_with_stock and not stocks:
        return {"ok": True, "skipped": True, "stock_count": 0, "stocks": []}
    conclusion = req.conclusion if isinstance(req.conclusion, dict) else {}
    oid = await repository.insert_wencai_opinion(
        0, question, answer, stocks, (req.agent_mode or "").strip(),
        (req.trace_id or "").strip(), (req.uploader or "").strip(),
        (req.reasoning or "").strip(), conclusion)
    return {"ok": True, "id": oid, "stock_count": len(stocks),
            "stocks": [s["name"] for s in stocks], "stock_items": stocks}


@router.get("/opinions")
async def list_opinions(user: Annotated[dict, Depends(get_current_user)]):
    """问财观点参考列表(全局 + 本人), 按时间倒序。"""
    return {"opinions": await repository.list_wencai_opinions(user["id"])}


@router.delete("/opinions/{opinion_id}")
async def delete_opinion(opinion_id: int, user: Annotated[dict, Depends(get_current_user)]):
    await repository.delete_wencai_opinion(opinion_id, user["id"])
    return {"ok": True}


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


# ── 个股「问财提问」预设问题 (v1.7.786) ──
# 自选股行内那个弹窗里的一点即问模版, 原写死前端常量, 改成每用户存库可维护。
# 模版用 {name}/{code} 占位, 渲染在前端做。首次取列表时若一条都没有, 自动播种系统默认 4 条。

_ASK_LABEL_MAX = 40
_ASK_TEMPLATE_MAX = 255
_ASK_PRESET_MAX = 20        # 单用户模版条数上限(弹窗里一屏能点得过来的量)


@router.get("/ask-presets")
async def list_ask_presets(user: Annotated[dict, Depends(get_current_user)]):
    rows = await repository.list_ask_presets(user["id"])
    if not rows:
        rows = await repository.seed_ask_presets(user["id"])   # 首次进来给一套默认的
    return {"presets": rows}


class AskPresetCreate(BaseModel):
    label: str = ""
    template: str


def _clean_ask_preset(label: str, template: str) -> tuple[str, str]:
    t = (template or "").strip()
    if not t:
        raise HTTPException(status_code=400, detail="请输入问题模版")
    if len(t) > _ASK_TEMPLATE_MAX:
        raise HTTPException(status_code=400, detail=f"问题模版最长 {_ASK_TEMPLATE_MAX} 字")
    lb = (label or "").strip() or (t[:12] + ("…" if len(t) > 12 else ""))
    return lb[:_ASK_LABEL_MAX], t


@router.post("/ask-presets")
async def create_ask_preset(req: AskPresetCreate,
                            user: Annotated[dict, Depends(get_current_user)]):
    existing = await repository.list_ask_presets(user["id"])
    if len(existing) >= _ASK_PRESET_MAX:
        raise HTTPException(status_code=400, detail=f"模版最多 {_ASK_PRESET_MAX} 条, 请先删几条")
    label, template = _clean_ask_preset(req.label, req.template)
    pid = await repository.add_ask_preset(user["id"], label, template)
    return {"ok": True, "id": pid}


class AskPresetUpdate(BaseModel):
    label: str | None = None
    template: str | None = None
    enabled: int | None = None


@router.put("/ask-presets/{preset_id}")
async def update_ask_preset(preset_id: int, req: AskPresetUpdate,
                            user: Annotated[dict, Depends(get_current_user)]):
    existing = await repository.get_ask_preset(preset_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="模版不存在")
    fields: dict = {}
    if req.template is not None or req.label is not None:
        label, template = _clean_ask_preset(
            req.label if req.label is not None else existing["label"],
            req.template if req.template is not None else existing["template"],
        )
        if req.label is not None:
            fields["label"] = label
        if req.template is not None:
            fields["template"] = template
    if req.enabled is not None:
        fields["enabled"] = int(req.enabled)
    await repository.update_ask_preset(preset_id, user["id"], **fields)
    return {"ok": True}


@router.delete("/ask-presets/{preset_id}")
async def delete_ask_preset(preset_id: int, user: Annotated[dict, Depends(get_current_user)]):
    await repository.delete_ask_preset(preset_id, user["id"])
    return {"ok": True}


class AskPresetReorder(BaseModel):
    ids: list[int]


@router.put("/ask-presets/order/all")
async def reorder_ask_presets(req: AskPresetReorder,
                              user: Annotated[dict, Depends(get_current_user)]):
    """按前端给的 id 顺序整体重排(上移/下移用)。"""
    await repository.reorder_ask_presets(user["id"], [int(i) for i in (req.ids or [])])
    return {"ok": True}


@router.post("/ask-presets/reset")
async def reset_ask_presets(user: Annotated[dict, Depends(get_current_user)]):
    """恢复系统默认 4 条(清掉本人现有全部模版, 不可撤销)。"""
    rows = await repository.seed_ask_presets(user["id"], replace=True)
    return {"ok": True, "presets": rows}
