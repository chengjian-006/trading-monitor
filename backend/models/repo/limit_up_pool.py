"""每日涨停复盘存档 CRUD — cfzy_sys_limit_up_pool(明细) + cfzy_sys_limit_up_daily(日汇总).

每交易日收盘后 archive_limit_up 拉同花顺涨停池写入:
  - 明细表: 每只涨停股一行(代码/名称/板数/连板标签/涨停概念/涨幅/炸板次数)
  - 汇总表: 当日 涨停数/曾涨停/跌停/炸板/封板率

供「每日涨停复盘」看板/导出/推送读取, 及"某概念连续几天上榜"类历史分析。
"""
from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone


async def upsert_daily(trade_date: str, meta: dict, boards: list[dict]) -> int:
    """写/覆盖某交易日的涨停明细 + 汇总。trade_date: 'YYYYMMDD'。返回写入明细行数。"""
    await _execute(
        "INSERT INTO cfzy_sys_limit_up_daily "
        "(trade_date, limit_up_count, limit_up_history, limit_down_count, broken_board_count, seal_rate) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "limit_up_count=VALUES(limit_up_count), limit_up_history=VALUES(limit_up_history), "
        "limit_down_count=VALUES(limit_down_count), broken_board_count=VALUES(broken_board_count), "
        "seal_rate=VALUES(seal_rate)",
        (trade_date, meta.get("limit_up_count"), meta.get("limit_up_history"),
         meta.get("limit_down_count"), meta.get("broken_board_count"), meta.get("seal_rate")))

    # 明细: 先清当日再批量插(覆盖式, 防盘中早存的残缺被保留)
    await _execute("DELETE FROM cfzy_sys_limit_up_pool WHERE trade_date = %s", (trade_date,))
    if not boards:
        return 0
    rows = [(trade_date, b.get("code", ""), b.get("name", ""), int(b.get("height") or 1),
             b.get("streak_label", ""), b.get("reason", ""),
             b.get("pct"), int(b.get("open_times") or 0)) for b in boards if b.get("code")]
    if rows:
        await _executemany(
            "INSERT INTO cfzy_sys_limit_up_pool "
            "(trade_date, code, name, height, streak_label, reason, pct, open_times) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    return len(rows)


async def get_daily(trade_date: str) -> dict | None:
    """取某交易日的 {meta, boards}(按板数降序、代码升序)。无数据返回 None。"""
    summary = await _fetchone(
        "SELECT * FROM cfzy_sys_limit_up_daily WHERE trade_date = %s", (trade_date,))
    boards = await _fetchall(
        "SELECT code, name, height, streak_label, reason, pct, open_times "
        "FROM cfzy_sys_limit_up_pool WHERE trade_date = %s "
        "ORDER BY height DESC, code ASC", (trade_date,))
    if not summary and not boards:
        return None
    return {"trade_date": trade_date, "meta": summary or {}, "boards": boards}


async def list_dates(limit: int = 60) -> list[str]:
    """有存档的交易日列表(倒序)。"""
    rows = await _fetchall(
        "SELECT trade_date FROM cfzy_sys_limit_up_daily ORDER BY trade_date DESC LIMIT %s", (limit,))
    return [r["trade_date"] for r in rows]


async def latest_date() -> str | None:
    row = await _fetchone("SELECT MAX(trade_date) AS d FROM cfzy_sys_limit_up_daily")
    return row["d"] if row and row.get("d") else None


async def concept_streak(keyword: str, days: int = 10) -> list[dict]:
    """某概念关键词最近 N 个存档日的上榜只数(供'连续几天上榜'分析)。"""
    rows = await _fetchall(
        "SELECT trade_date, COUNT(*) AS cnt FROM cfzy_sys_limit_up_pool "
        "WHERE reason LIKE %s GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s",
        (f"%{keyword}%", days))
    return rows
