"""模型回测历史记录持久化 — cfzy_biz_backtest_runs.

每次「模型回测」成功完成后自动存一条(含参数/汇总/月度/逐笔明细)。
列表查询只取轻量字段(不拉 monthly/trades 大字段); 详情单独按 id 取全量。
每用户保留最近 MAX_KEEP 条, 插入时顺手裁旧防无界增长。
"""
import json

from backend.models.database import get_pool
from backend.models.repo._db import _fetchall, _fetchone, _execute

MAX_KEEP = 200   # 每用户保留最近 N 条


async def save_run(user_id: int, run: dict) -> int:
    """落一条回测记录, 返回新 id。run 为 run_model_backtest 的返回 + 范围/口径/参数/窗口。"""
    sql = (
        "INSERT INTO cfzy_biz_backtest_runs "
        "(user_id, model_id, model_name, scope, koujing, lookback_days, "
        " window_start, window_end, params_json, overall_json, monthly_json, "
        " trades_json, scanned, trades_total, trades_truncated) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    args = (
        user_id, run.get("model_id", ""), run.get("model_name", ""),
        run.get("scope", "pool"), run.get("koujing", "daily"),
        int(run.get("lookback_days", 0)),
        run.get("window_start", ""), run.get("window_end", ""),
        json.dumps(run.get("params") or {}, ensure_ascii=False),
        json.dumps(run.get("overall") or {}, ensure_ascii=False),
        json.dumps(run.get("monthly") or {}, ensure_ascii=False),
        json.dumps(run.get("trades") or [], ensure_ascii=False),
        int(run.get("scanned", 0)), int(run.get("trades_total", 0)),
        1 if run.get("trades_truncated") else 0,
    )
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            new_id = cur.lastrowid
    await _prune(user_id)
    return new_id


async def _prune(user_id: int):
    """超出 MAX_KEEP 的旧记录删掉(按 id 留最新)。"""
    row = await _fetchone(
        "SELECT id FROM cfzy_biz_backtest_runs WHERE user_id=%s "
        "ORDER BY id DESC LIMIT 1 OFFSET %s", (user_id, MAX_KEEP))
    if row:
        await _execute(
            "DELETE FROM cfzy_biz_backtest_runs WHERE user_id=%s AND id<=%s",
            (user_id, row["id"]))


async def list_runs(user_id: int, limit: int = 100) -> list[dict]:
    """列表(轻量, 不含 monthly/trades 大字段)。overall/params 解析回对象。"""
    rows = await _fetchall(
        "SELECT id, model_id, model_name, scope, koujing, lookback_days, "
        "window_start, window_end, params_json, overall_json, scanned, "
        "trades_total, trades_truncated, created_at "
        "FROM cfzy_biz_backtest_runs WHERE user_id=%s ORDER BY id DESC LIMIT %s",
        (user_id, int(limit)))
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "model_id": r["model_id"], "model_name": r["model_name"],
            "scope": r["scope"], "koujing": r["koujing"], "lookback_days": r["lookback_days"],
            "window_start": r["window_start"], "window_end": r["window_end"],
            "params": _loads(r["params_json"], {}),
            "overall": _loads(r["overall_json"], {}),
            "scanned": r["scanned"], "trades_total": r["trades_total"],
            "trades_truncated": bool(r["trades_truncated"]),
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
        })
    return out


async def get_run(user_id: int, run_id: int) -> dict | None:
    """单条全量(含 monthly + 逐笔 trades)。"""
    r = await _fetchone(
        "SELECT * FROM cfzy_biz_backtest_runs WHERE id=%s AND user_id=%s",
        (run_id, user_id))
    if not r:
        return None
    return {
        "id": r["id"], "model_id": r["model_id"], "model_name": r["model_name"],
        "scope": r["scope"], "koujing": r["koujing"], "lookback_days": r["lookback_days"],
        "window_start": r["window_start"], "window_end": r["window_end"],
        "params": _loads(r["params_json"], {}),
        "overall": _loads(r["overall_json"], {}),
        "monthly": _loads(r["monthly_json"], {}),
        "trades": _loads(r["trades_json"], []),
        "scanned": r["scanned"], "trades_total": r["trades_total"],
        "trades_truncated": bool(r["trades_truncated"]),
        "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
    }


async def delete_run(user_id: int, run_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            n = await cur.execute(
                "DELETE FROM cfzy_biz_backtest_runs WHERE id=%s AND user_id=%s",
                (run_id, user_id))
    return n > 0


def _loads(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default
