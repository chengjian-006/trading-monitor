"""持仓态 → 全市场五年 T+1/T+3 前向收益分布 CRUD - cfzy_biz_holding_state_fwd 表 (v1.7.x).
每周由 holding_brief.refresh_holding_state_fwd 重算并 upsert (单行/态)。
持仓研判晚报读此表给每只持仓挂「同类形态历史次日分布」客观概率。"""
from backend.models.repo._db import _executemany, _fetchall


async def save_holding_state_fwd(run_date: str, dist: dict[str, dict]) -> int:
    """dist: {state: {n, up_rate_1, median_1, p10_1, p90_1, up_rate_3, median_3, p10_3, p90_3}}。"""
    if not dist:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_biz_holding_state_fwd "
        "(state, n, up_rate_1, median_1, p10_1, p90_1, up_rate_3, median_3, p10_3, p90_3, run_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE n=VALUES(n), "
        "up_rate_1=VALUES(up_rate_1), median_1=VALUES(median_1), p10_1=VALUES(p10_1), p90_1=VALUES(p90_1), "
        "up_rate_3=VALUES(up_rate_3), median_3=VALUES(median_3), p10_3=VALUES(p10_3), p90_3=VALUES(p90_3), "
        "run_date=VALUES(run_date), updated_at=CURRENT_TIMESTAMP",
        [(st, d["n"], d["up_rate_1"], d["median_1"], d["p10_1"], d["p90_1"],
          d["up_rate_3"], d["median_3"], d["p10_3"], d["p90_3"], run_date)
         for st, d in dist.items()],
    )


async def get_holding_state_fwd() -> dict:
    """{state: {n, up_rate_1, median_1, p10_1, p90_1, up_rate_3, median_3, p10_3, p90_3, run_date}}。"""
    rows = await _fetchall("SELECT * FROM cfzy_biz_holding_state_fwd")
    return {r["state"]: r for r in rows}
