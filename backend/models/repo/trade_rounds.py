"""交易回合持久化 — cfzy_biz_trade_rounds + cfzy_biz_round_legs.

幂等策略: 按 (user_id, code, source) 整体重建(删后插); 回合腿经外键 ON DELETE CASCADE 自动清理.
"""
import aiomysql

from backend.models.database import get_pool
from backend.models.repo._db import _fetchall

_ROUND_COLS = (
    "user_id, code, name, source, source_ref, status, open_date, open_time, "
    "close_date, close_time, entry_price, exit_price, peak_qty, is_scaled_in, "
    "is_scaled_out, total_buy_amount, total_sell_amount, total_fee, realized_pnl, "
    "realized_pnl_pct, entry_signal_pk, entry_signal_id, entry_model_name, "
    "entry_deviation_pct, exit_reason, "
    # v1.7.685: 建表时就留了这 6 列, 但插入列清单一直没带上 → 线上恒为 NULL,
    # 回合表因此只是流水台账、出不了"买点准不准/卖点早不早"的结论。由 attach_excursions 回填。
    "holding_days, mfe_pct, mfe_date, mae_pct, mae_date, max_drawdown_pct"
)
_ROUND_PH = ",".join(["%s"] * 31)


async def get_trades_for_rounds(user_id: int) -> list[dict]:
    """取用户全量成交, 合并三项费用为 fee_total, 按 code+时间升序(供回合构建器)."""
    return await _fetchall(
        "SELECT id, trade_date, trade_time, code, name, direction, quantity, price, "
        "amount, (COALESCE(fee,0)+COALESCE(stamp_tax,0)+COALESCE(transfer_fee,0)) AS fee_total "
        "FROM cfzy_biz_trades WHERE user_id = %s ORDER BY code, trade_date, trade_time",
        (user_id,),
    )


async def get_buy_signals_by_code(user_id: int, code: str) -> list[dict]:
    """取该票全部买点信号(BUY_ 前缀), 供回合买点归因."""
    return await _fetchall(
        "SELECT id, signal_id, signal_name, price, DATE(triggered_at) AS date "
        "FROM cfzy_biz_signals WHERE user_id = %s AND code = %s "
        "AND signal_id LIKE 'BUY\\_%%' ORDER BY triggered_at ASC",
        (user_id, code),
    )


async def get_buy_signals_for_user(user_id: int) -> dict[str, list[dict]]:
    """一次取用户全部买点信号, 按 code 分组归因。

    替代回合重建里逐 code 调 get_buy_signals_by_code 的 N+1 查询: 用户有 N 个不同
    code 就要 N 次往返, 跨云 DB 每次 ~44ms 会累成秒级。这里一次查询 + Python 分组。
    """
    rows = await _fetchall(
        "SELECT code, id, signal_id, signal_name, price, DATE(triggered_at) AS date "
        "FROM cfzy_biz_signals WHERE user_id = %s "
        "AND signal_id LIKE 'BUY\\_%%' ORDER BY code, triggered_at ASC",
        (user_id,),
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["code"], []).append(r)
    return grouped


def _round_row(user_id: int, r: dict) -> tuple:
    return (
        user_id, r["code"], r["name"], r["source"], r.get("source_ref", ""),
        r["status"], r["open_date"], r["open_time"], r["close_date"], r["close_time"],
        r["entry_price"], r["exit_price"], r["peak_qty"], int(r["is_scaled_in"]),
        int(r["is_scaled_out"]), r["total_buy_amount"], r["total_sell_amount"],
        r["total_fee"], r["realized_pnl"], r["realized_pnl_pct"],
        r.get("entry_signal_pk"), r.get("entry_signal_id"), r.get("entry_model_name"),
        r.get("entry_deviation_pct"), r.get("exit_reason"),
        r.get("holding_days"), r.get("mfe_pct"), r.get("mfe_date"),
        r.get("mae_pct"), r.get("mae_date"), r.get("max_drawdown_pct"),
    )


async def get_daily_bars_for_codes(codes: list[str], start_date: str) -> dict[str, list[dict]]:
    """一次取多票日线(high/low), 按 code 分组升序 — 给回合 MFE/MAE 回填用.

    逐 code 查会 N+1(174 只 × 跨云 44ms ≈ 8 秒), 故一次 IN 查询 + Python 分组。
    """
    if not codes:
        return {}
    ph = ",".join(["%s"] * len(codes))
    rows = await _fetchall(
        f"SELECT code, trade_date, high, low FROM cfzy_sys_kline_cache "
        f"WHERE code IN ({ph}) AND trade_date >= %s ORDER BY code, trade_date",
        tuple(codes) + (start_date,),
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["code"], []).append(r)
    return grouped


async def replace_rounds_for_code(user_id: int, code: str, source: str, rounds: list[dict]):
    """删除该 (user,code,source) 全部回合后重插. rounds 含 legs."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM cfzy_biz_trade_rounds WHERE user_id=%s AND code=%s AND source=%s",
                (user_id, code, source),
            )
            for r in rounds:
                await cur.execute(
                    f"INSERT INTO cfzy_biz_trade_rounds ({_ROUND_COLS}) VALUES ({_ROUND_PH})",
                    _round_row(user_id, r),
                )
                round_id = cur.lastrowid
                legs = r.get("legs") or []
                if legs:
                    await cur.executemany(
                        "INSERT INTO cfzy_biz_round_legs "
                        "(round_id, leg_type, trade_date, trade_time, price, qty, amount, "
                        "fee, is_virtual, trade_id, running_qty) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(round_id, lg["leg_type"], lg["trade_date"], lg["trade_time"],
                          lg["price"], lg["qty"], lg["amount"], lg["fee"],
                          int(lg.get("is_virtual", 0)), lg.get("trade_id"), lg["running_qty"])
                         for lg in legs],
                    )
        await conn.commit()


async def get_rounds(user_id: int, status: str | None = None,
                     limit: int | None = None) -> list[dict]:
    """读回合头列表(供前端/分析), 可按 status 过滤."""
    sql = "SELECT * FROM cfzy_biz_trade_rounds WHERE user_id = %s"
    args: list = [user_id]
    if status:
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY open_date DESC, open_time DESC"
    if limit:
        sql += " LIMIT %s"
        args.append(int(limit))
    return await _fetchall(sql, tuple(args))


async def get_round_legs(round_ids: list[int]) -> dict[int, list[dict]]:
    """批量取回合腿, 按 round_id 分组(给回合详情/K线标注用)."""
    if not round_ids:
        return {}
    ph = ",".join(["%s"] * len(round_ids))
    rows = await _fetchall(
        f"SELECT * FROM cfzy_biz_round_legs WHERE round_id IN ({ph}) "
        f"ORDER BY round_id, trade_date, trade_time",
        tuple(round_ids),
    )
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["round_id"], []).append(r)
    return grouped
