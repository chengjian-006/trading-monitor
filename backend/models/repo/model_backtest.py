"""各买入模型 按周全市场回测结果 CRUD - cfzy_biz_model_backtest 表 (v1.7.x)."""
from backend.models.repo._db import _executemany, _fetchall, _fetchone


async def save_model_backtest(run_date: str, window_start: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_biz_model_backtest "
        "(run_date, signal_id, model_name, window_start, n, win_rate, avg_span, avg_eff, "
        " net_mean, net_after_cost, annualized, pf) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE model_name=VALUES(model_name), window_start=VALUES(window_start), "
        "n=VALUES(n), win_rate=VALUES(win_rate), avg_span=VALUES(avg_span), avg_eff=VALUES(avg_eff), "
        "net_mean=VALUES(net_mean), net_after_cost=VALUES(net_after_cost), annualized=VALUES(annualized), "
        "pf=VALUES(pf), created_at=CURRENT_TIMESTAMP",
        [(run_date, r["signal_id"], r["model_name"], window_start, r["n"], r["win_rate"],
          r["avg_span"], r["avg_eff"], r["net_mean"], r["net_after_cost"], r["annualized"], r["pf"])
         for r in rows],
    )


async def get_latest_model_backtest() -> dict:
    """最近一次全市场回测结果(按年化资金效率降序)。"""
    row = await _fetchone("SELECT MAX(run_date) AS d FROM cfzy_biz_model_backtest")
    if not row or not row.get("d"):
        return {"run_date": None, "window_start": None, "models": []}
    d = str(row["d"])
    rows = await _fetchall(
        "SELECT model_name, signal_id, window_start, n, win_rate, avg_span, avg_eff, "
        "net_mean, net_after_cost, annualized, pf "
        "FROM cfzy_biz_model_backtest WHERE run_date = %s ORDER BY annualized DESC", (d,))
    ws = rows[0]["window_start"] if rows else None
    return {"run_date": d, "window_start": ws, "models": rows}
