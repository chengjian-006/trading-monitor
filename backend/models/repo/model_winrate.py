"""各买入模型 近3月/近6月 全市场回测胜率+单笔均收益 CRUD - cfzy_biz_model_winrate 表 (v1.7.x).
每日收盘由 model_winrate_refresher 重算并 upsert (单行/模型)。买入提醒读此表带战绩。
v1.7.x: 加 monthly_json(逐月胜率序列)+ max_drawdown(逐笔权益曲线最大回撤), 供图鉴果仁式策略卡。"""
import json

from backend.models.repo._db import _executemany, _fetchall


async def save_model_winrate(run_date: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_biz_model_winrate "
        "(signal_id, model_name, win_rate_3m, net_3m, n_3m, win_rate_6m, net_6m, n_6m, "
        " rank_3m, rank_n, run_date, monthly_json, max_drawdown) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE model_name=VALUES(model_name), "
        "win_rate_3m=VALUES(win_rate_3m), net_3m=VALUES(net_3m), n_3m=VALUES(n_3m), "
        "win_rate_6m=VALUES(win_rate_6m), net_6m=VALUES(net_6m), n_6m=VALUES(n_6m), "
        "rank_3m=VALUES(rank_3m), rank_n=VALUES(rank_n), "
        "run_date=VALUES(run_date), monthly_json=VALUES(monthly_json), "
        "max_drawdown=VALUES(max_drawdown), updated_at=CURRENT_TIMESTAMP",
        [(r["signal_id"], r.get("model_name", ""), r.get("win_rate_3m"), r.get("net_3m"),
          r.get("n_3m", 0), r.get("win_rate_6m"), r.get("net_6m"), r.get("n_6m", 0),
          r.get("rank_3m"), r.get("rank_n", 0), run_date,
          json.dumps(r.get("monthly") or [], ensure_ascii=False), r.get("max_drawdown"))
         for r in rows],
    )


async def get_model_winrate() -> dict:
    """{signal_id: {..., monthly(解析后数组), max_drawdown}}。monthly_json → monthly。"""
    rows = await _fetchall("SELECT * FROM cfzy_biz_model_winrate")
    out = {}
    for r in rows:
        d = dict(r)
        raw = d.pop("monthly_json", None)
        try:
            d["monthly"] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            d["monthly"] = []
        out[r["signal_id"]] = d
    return out
