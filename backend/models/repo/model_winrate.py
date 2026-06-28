"""各买入模型 近3月/近6月 全市场回测胜率+单笔均收益 CRUD - cfzy_biz_model_winrate 表 (v1.7.x).
每日收盘由 model_winrate_refresher 重算并 upsert (单行/模型)。买入提醒读此表带战绩。"""
from backend.models.repo._db import _executemany, _fetchall


async def save_model_winrate(run_date: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_biz_model_winrate "
        "(signal_id, model_name, win_rate_3m, net_3m, n_3m, win_rate_6m, net_6m, n_6m, "
        " rank_3m, rank_n, run_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE model_name=VALUES(model_name), "
        "win_rate_3m=VALUES(win_rate_3m), net_3m=VALUES(net_3m), n_3m=VALUES(n_3m), "
        "win_rate_6m=VALUES(win_rate_6m), net_6m=VALUES(net_6m), n_6m=VALUES(n_6m), "
        "rank_3m=VALUES(rank_3m), rank_n=VALUES(rank_n), "
        "run_date=VALUES(run_date), updated_at=CURRENT_TIMESTAMP",
        [(r["signal_id"], r.get("model_name", ""), r.get("win_rate_3m"), r.get("net_3m"),
          r.get("n_3m", 0), r.get("win_rate_6m"), r.get("net_6m"), r.get("n_6m", 0),
          r.get("rank_3m"), r.get("rank_n", 0), run_date)
         for r in rows],
    )


async def get_model_winrate() -> dict:
    """{signal_id: {model_name, win_rate_3m, net_3m, n_3m, win_rate_6m, net_6m, n_6m, run_date}}。"""
    rows = await _fetchall("SELECT * FROM cfzy_biz_model_winrate")
    return {r["signal_id"]: r for r in rows}
