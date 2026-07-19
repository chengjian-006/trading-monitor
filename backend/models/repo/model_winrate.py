"""各买入模型 近3月/近6月 全市场回测胜率+单笔均收益 CRUD - cfzy_biz_model_winrate 表 (v1.7.x).
每日收盘由 model_winrate_refresher 重算并 upsert (单行/模型)。买入提醒读此表带战绩。
v1.7.x: 加 monthly_json(逐月胜率序列)+ max_drawdown(逐笔权益曲线最大回撤), 供图鉴果仁式策略卡。"""
import json

from backend.models.repo._db import _execute, _executemany, _fetchall


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


# ── 断点续算暂存 (cfzy_sys_model_winrate_stage) ──────────────────────────────

async def stage_model_winrate_code(anchor: str, code: str, trades: list) -> None:
    """把一只票算完的窗口内交易 [[模型名,触发日,净收益], ...] 落暂存(空列表也落, 标记已算)。"""
    await _execute(
        "INSERT INTO cfzy_sys_model_winrate_stage (anchor, code, trades_json) "
        "VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE trades_json=VALUES(trades_json), "
        "created_at=CURRENT_TIMESTAMP",
        (anchor, code, json.dumps(trades, ensure_ascii=False)),
    )


async def staged_model_winrate_codes(anchor: str) -> set:
    """该锚点已落暂存(已算)的股票代码集合, 用于断点续算跳过。"""
    rows = await _fetchall(
        "SELECT code FROM cfzy_sys_model_winrate_stage WHERE anchor=%s", (anchor,)
    )
    return {r["code"] for r in rows}


async def staged_model_winrate_count(anchor: str) -> int:
    rows = await _fetchall(
        "SELECT COUNT(*) AS n FROM cfzy_sys_model_winrate_stage WHERE anchor=%s", (anchor,)
    )
    return int(rows[0]["n"]) if rows else 0


async def load_model_winrate_stage(anchor: str) -> list[dict]:
    """载入该锚点全部暂存行(定稿聚合用)。"""
    return await _fetchall(
        "SELECT code, trades_json FROM cfzy_sys_model_winrate_stage WHERE anchor=%s", (anchor,)
    )


async def clear_model_winrate_stage(anchor: str | None = None, exclude_anchor: str | None = None) -> None:
    """清暂存: 给 anchor 清该锚点(定稿后); 给 exclude_anchor 清"除它以外"的旧锚点(换交易日重来)。"""
    if anchor is not None:
        await _execute("DELETE FROM cfzy_sys_model_winrate_stage WHERE anchor=%s", (anchor,))
    elif exclude_anchor is not None:
        await _execute("DELETE FROM cfzy_sys_model_winrate_stage WHERE anchor<>%s", (exclude_anchor,))


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
