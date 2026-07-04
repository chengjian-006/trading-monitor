"""业绩预告 + 预约披露 落库/查询 (v1.7.573)。

cfzy_sys_disclosure_calendar — 定期报告预约/实际披露日历(周期性刷新, 供「财报披露日历」提醒)
cfzy_sys_earnings_forecast   — 业绩预告(当日抓取落库+去重, 供「预增榜」推送)
"""
from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone


async def upsert_disclosure(rows: list[dict]) -> None:
    """批量 upsert 预约披露日历。rows: fetch_disclosure_calendar 的返回。"""
    if not rows:
        return
    await _executemany(
        "INSERT INTO cfzy_sys_disclosure_calendar "
        "(code, report_year, report_type, name, appoint_date, actual_date) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), appoint_date=VALUES(appoint_date), "
        "actual_date=VALUES(actual_date)",
        [(r["code"], r["report_year"], r["report_type"], r["name"],
          r["appoint_date"] or None, r["actual_date"] or None) for r in rows])


async def upcoming_disclosures(codes: list[str], start: str, end: str) -> list[dict]:
    """给定股票在 [start, end] 内预约披露、且尚未实际披露的记录(升序)。"""
    if not codes:
        return []
    ph = ",".join(["%s"] * len(codes))
    return await _fetchall(
        f"SELECT code, name, report_year, report_type, appoint_date "
        f"FROM cfzy_sys_disclosure_calendar "
        f"WHERE code IN ({ph}) AND appoint_date BETWEEN %s AND %s AND actual_date IS NULL "
        f"ORDER BY appoint_date, code",
        (*codes, start, end))


async def upsert_forecasts(rows: list[dict]) -> None:
    """批量 upsert 业绩预告(不动 pushed_at, 由推送侧单独标记)。"""
    if not rows:
        return
    await _executemany(
        "INSERT INTO cfzy_sys_earnings_forecast "
        "(code, report_date, name, notice_date, predict_type, forecast_group, amp_lower, amp_upper, content) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), notice_date=VALUES(notice_date), "
        "predict_type=VALUES(predict_type), forecast_group=VALUES(forecast_group), "
        "amp_lower=VALUES(amp_lower), amp_upper=VALUES(amp_upper), content=VALUES(content)",
        [(r["code"], r["report_date"], r["name"], r["notice_date"] or None, r["predict_type"],
          r["group"], r["amp_lower"], r["amp_upper"], (r["content"] or "")[:500]) for r in rows])


async def forecasts_to_push(notice_since: str, groups: tuple[str, ...] = ("利好",)) -> list[dict]:
    """公告日 >= notice_since、指定分组、尚未推送过的预告(按变动幅度上限降序)。供预增榜。

    改回看窗(原来精确当日): 周五盘后/周末发的预告次日能补上(靠 pushed_at 去重不重推)。
    amp_upper 可能为空(EPS口径票), NULL 在 DESC 下排最后, 不影响有幅度票的置顶。"""
    ph = ",".join(["%s"] * len(groups))
    return await _fetchall(
        f"SELECT code, name, report_date, predict_type, forecast_group, amp_lower, amp_upper, content "
        f"FROM cfzy_sys_earnings_forecast "
        f"WHERE notice_date>=%s AND forecast_group IN ({ph}) AND pushed_at IS NULL "
        f"ORDER BY amp_upper DESC",
        (notice_since, *groups))


async def get_positive_forecast_by_code(code: str) -> dict | None:
    """某票最近的正向业绩预告(买卖卡背景标签用)。取 notice_date 最新的一条利好预告;
    近90天内才算有效(过季的老预告不标)。无则 None。"""
    from datetime import date, timedelta
    lo = (date.today() - timedelta(days=90)).isoformat()
    return await _fetchone(
        "SELECT code, name, report_date, predict_type, amp_lower, amp_upper, notice_date "
        "FROM cfzy_sys_earnings_forecast "
        "WHERE code=%s AND forecast_group='利好' AND notice_date >= %s "
        "ORDER BY notice_date DESC LIMIT 1",
        (code, lo))


async def mark_forecasts_pushed(keys: list[tuple[str, str]]) -> None:
    """标记 (code, report_date) 已推送, 避免次日重复推同一条。"""
    if not keys:
        return
    await _executemany(
        "UPDATE cfzy_sys_earnings_forecast SET pushed_at=NOW() WHERE code=%s AND report_date=%s",
        keys)
