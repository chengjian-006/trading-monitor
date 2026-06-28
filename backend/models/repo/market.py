"""市场数据快照 - cfzy_sys_market_reports / cfzy_sys_market_snapshot /
cfzy_sys_popularity_snapshot / cfzy_sys_market_overview / cfzy_sys_api_cache 表.

集中 AI 时段报告 / 大盘指数趋势 / 人气榜 / 实时概览 / 通用 API 缓存 5 类数据访问.
"""
from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone


# ── Market Reports (AI 时段报告) ──

async def save_market_report(time_slot: str, content: str, market_data: dict | None = None):
    import json
    data_json = json.dumps(market_data, ensure_ascii=False, default=str) if market_data else None
    await _execute(
        "INSERT INTO cfzy_sys_market_reports (time_slot, content, market_data) VALUES (%s, %s, %s)",
        (time_slot, content, data_json),
    )


async def get_today_reports() -> list[dict]:
    return await _fetchall(
        "SELECT id, time_slot, content, created_at FROM cfzy_sys_market_reports "
        "WHERE DATE(created_at) = CURDATE() ORDER BY created_at DESC"
    )


async def get_latest_report() -> dict | None:
    return await _fetchone(
        "SELECT id, time_slot, content, created_at FROM cfzy_sys_market_reports "
        "ORDER BY created_at DESC LIMIT 1"
    )


async def get_report_context(time_slot: str) -> dict | None:
    """取当日某时段报告保存的结构化数据(market_data), 供收盘统一推送复用其报告正文。"""
    import json
    row = await _fetchone(
        "SELECT market_data FROM cfzy_sys_market_reports "
        "WHERE time_slot = %s AND DATE(created_at) = CURDATE() "
        "ORDER BY created_at DESC LIMIT 1",
        (time_slot,),
    )
    if not row or not row.get("market_data"):
        return None
    md = row["market_data"]
    try:
        return json.loads(md) if isinstance(md, str) else md
    except (ValueError, TypeError):
        return None


# ── Market Snapshot (大盘指数分时+涨跌停, 历史快照) ──

async def upsert_market_snapshot(trade_date: str, index_trends: dict, market_stats: dict):
    import json
    trends_json = json.dumps(index_trends, ensure_ascii=False)
    stats_json = json.dumps(market_stats, ensure_ascii=False)
    await _execute(
        "INSERT INTO cfzy_sys_market_snapshot (trade_date, index_trends, market_stats) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE index_trends=%s, market_stats=%s",
        (trade_date, trends_json, stats_json, trends_json, stats_json),
    )


async def get_market_snapshot(trade_date: str | None = None) -> dict | None:
    import json
    # 不传日期 = 取"最近一个有数据的交易日"快照 (非交易日/盘前回退到上一交易日, 避免分时图空白);
    # 传日期则精确匹配该日。
    if trade_date:
        row = await _fetchone(
            "SELECT * FROM cfzy_sys_market_snapshot WHERE trade_date = %s", (trade_date,)
        )
    else:
        row = await _fetchone(
            "SELECT * FROM cfzy_sys_market_snapshot ORDER BY trade_date DESC LIMIT 1"
        )
    if row:
        if isinstance(row.get("index_trends"), str):
            row["index_trends"] = json.loads(row["index_trends"])
        if isinstance(row.get("market_stats"), str):
            row["market_stats"] = json.loads(row["market_stats"])
    return row


# ── Popularity Snapshot (人气榜历史) ──

async def upsert_popularity_snapshot(trade_date: str, data: dict):
    import json
    data_json = json.dumps(data, ensure_ascii=False)
    await _execute(
        "INSERT INTO cfzy_sys_popularity_snapshot (trade_date, data) "
        "VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE data=%s",
        (trade_date, data_json, data_json),
    )


async def get_popularity_snapshot(trade_date: str | None = None) -> dict | None:
    import json
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_popularity_snapshot WHERE trade_date = %s", (trade_date,)
    )
    if row and isinstance(row.get("data"), str):
        row["data"] = json.loads(row["data"])
    return row


async def get_recent_popularity_dates(limit: int = 5) -> list[str]:
    rows = await _fetchall(
        "SELECT trade_date FROM cfzy_sys_popularity_snapshot ORDER BY trade_date DESC LIMIT %s",
        (limit,),
    )
    return [r["trade_date"] for r in rows]


async def get_recent_hot_concepts(limit: int = 5) -> list[dict]:
    import json
    rows = await _fetchall(
        "SELECT trade_date, data FROM cfzy_sys_popularity_snapshot "
        "ORDER BY trade_date DESC LIMIT %s",
        (limit,),
    )
    result = []
    for row in rows:
        data = row.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        concepts = data.get("hot_concepts", []) if data else []
        result.append({"date": row["trade_date"], "concepts": concepts})
    return result


# ── Market Overview (实时刷新快照, v1.7.97) ──
# 单行 UPSERT 设计 (id=1), 由 refresh_market_overview 定时任务每 30s 写入

async def save_market_overview(global_indices: list, a_indices: list, market_stats: dict):
    import json
    g_json = json.dumps(global_indices, ensure_ascii=False)
    a_json = json.dumps(a_indices, ensure_ascii=False)
    s_json = json.dumps(market_stats, ensure_ascii=False)
    await _execute(
        "INSERT INTO cfzy_sys_market_overview (id, global_indices, a_indices, market_stats) "
        "VALUES (1, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE global_indices=%s, a_indices=%s, market_stats=%s, snapshot_at=NOW()",
        (g_json, a_json, s_json, g_json, a_json, s_json),
    )


async def get_market_overview() -> dict | None:
    import json
    row = await _fetchone("SELECT * FROM cfzy_sys_market_overview WHERE id = 1")
    if not row:
        return None
    for k in ("global_indices", "a_indices", "market_stats"):
        if isinstance(row.get(k), str):
            try:
                row[k] = json.loads(row[k])
            except (ValueError, TypeError):
                row[k] = [] if k != "market_stats" else {}
    return row


# ── 股票池"走势"列迷你分时存盘 (盘中写, 非交易时段回退上一交易日) ──

async def upsert_sparkline_snapshots(snapshots: dict, trade_date: str) -> None:
    """snapshots: {code: {trends, pre_close, ...}} — 只传非空的。逐条 UPSERT。"""
    import json
    if not snapshots:
        return
    await _executemany(
        "INSERT INTO cfzy_sys_sparkline_snapshot (code, trade_date, data) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE trade_date=VALUES(trade_date), data=VALUES(data)",
        [(code, trade_date, json.dumps(d, ensure_ascii=False)) for code, d in snapshots.items()],
    )


async def get_sparkline_snapshot_today(code: str, trade_date: str) -> dict | None:
    """取某票"指定交易日"的存盘走势 → {trends, pre_close, ...}，日期不符(陈旧)或无则 None。

    分时弹窗 DB 优先用: 表内每票仅一行(唯一键 code), trade_date 标识焐热时点;
    带日期过滤可杜绝把上一交易日的走势当今天显示。
    """
    import json
    row = await _fetchone(
        "SELECT data FROM cfzy_sys_sparkline_snapshot WHERE code = %s AND trade_date = %s",
        (code, trade_date),
    )
    if not row:
        return None
    d = row.get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return None
    return d if isinstance(d, dict) else None


async def get_sparkline_snapshots(codes: list[str]) -> dict:
    """取指定 codes 的存盘走势 → {code: {trends, pre_close, ...}}。"""
    import json
    if not codes:
        return {}
    placeholders = ",".join(["%s"] * len(codes))
    rows = await _fetchall(
        f"SELECT code, data FROM cfzy_sys_sparkline_snapshot WHERE code IN ({placeholders})",
        tuple(codes),
    )
    out = {}
    for r in rows:
        d = r.get("data")
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except (ValueError, TypeError):
                d = None
        if d:
            out[r["code"]] = d
    return out


# ── 每日分时曲线归档 (固化, 供历史回放) ──

async def upsert_intraday_snapshots(snapshots: dict, trade_date: str) -> None:
    """snapshots: {code: [ {time,price,avg_price,volume}, ... ]} — 逐条 UPSERT 到当日。"""
    import json
    if not snapshots:
        return
    await _executemany(
        "INSERT INTO cfzy_sys_intraday_snapshot (code, trade_date, data) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE data=VALUES(data)",
        [(code, trade_date, json.dumps(pts, ensure_ascii=False)) for code, pts in snapshots.items()],
    )


async def get_intraday_snapshot(code: str, trade_date: str) -> list | None:
    """取某日某票冻结的分时曲线 → [ {time,price,avg_price,volume}, ... ]，无则 None。"""
    import json
    row = await _fetchone(
        "SELECT data FROM cfzy_sys_intraday_snapshot WHERE code = %s AND trade_date = %s",
        (code, trade_date),
    )
    if not row:
        return None
    d = row.get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return None
    return d if isinstance(d, list) else None


async def list_intraday_snapshot_dates(code: str, limit: int = 30) -> list[str]:
    """某票有归档分时的交易日列表(倒序)。"""
    rows = await _fetchall(
        "SELECT trade_date FROM cfzy_sys_intraday_snapshot WHERE code = %s "
        "ORDER BY trade_date DESC LIMIT %s",
        (code, limit),
    )
    return [r["trade_date"] for r in rows]


async def get_prev_close_before(code: str, trade_date: str) -> float:
    """日K缓存里 trade_date 之前最近一个交易日的收盘价, 无则 0。
    供历史分时回放补昨收(归档分时不含昨收), 让红绿基准与真实涨跌幅一致。"""
    row = await _fetchone(
        "SELECT close FROM cfzy_sys_kline_cache WHERE code = %s AND trade_date < %s "
        "ORDER BY trade_date DESC LIMIT 1",
        (code, trade_date),
    )
    if not row:
        return 0.0
    try:
        return float(row.get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


# ── 通用 DB 缓存 (外部 API stale fallback) ──

async def api_cache_set(key: str, payload) -> None:
    """成功结果落 DB 缓存 (UPSERT). 失败 silent, 不阻塞主链路."""
    import json as _json
    import logging as _logging
    try:
        payload_json = _json.dumps(payload, default=str, ensure_ascii=False)
        await _execute(
            "INSERT INTO cfzy_sys_api_cache (cache_key, payload) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE payload = VALUES(payload), updated_at = CURRENT_TIMESTAMP",
            (key, payload_json),
        )
    except Exception as e:
        _logging.getLogger(__name__).warning(f"[api_cache_set] {key} failed: {e}")


async def api_cache_get(key: str, max_stale_seconds: int = 7200):
    """取上一份成功结果. 超过 max_stale_seconds 视为太旧不可用. 返回 (payload, age_seconds)."""
    import json as _json
    import logging as _logging
    try:
        row = await _fetchone(
            "SELECT payload, UNIX_TIMESTAMP(updated_at) AS ts FROM cfzy_sys_api_cache WHERE cache_key = %s",
            (key,),
        )
        if not row:
            return None, 0
        import time as _time
        age = int(_time.time() - int(row["ts"]))
        if age > max_stale_seconds:
            return None, age
        payload = row["payload"]
        if isinstance(payload, str):
            payload = _json.loads(payload)
        return payload, age
    except Exception as e:
        _logging.getLogger(__name__).warning(f"[api_cache_get] {key} failed: {e}")
        return None, 0
