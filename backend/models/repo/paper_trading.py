"""模拟账户(纸面交易) CRUD + 成交事务 + 统计聚合。"""
import aiomysql
from datetime import datetime
from backend.models.database import get_pool

# 各账户(account_key)的创建默认值。新增账户类型只需在此加一项。
# default   = 原模拟账户(20%/笔, 有限子弹, 最多10只)
# unlimited = 无限子弹(5%/笔, 现金可透支/不限持仓数/同股可加仓)
ACCOUNT_DEFAULTS: dict[str, dict] = {
    "default":   {"name": "模拟账户", "buy_position_pct": 0.20, "unlimited_bullets": 0, "max_positions": 10},
    "unlimited": {"name": "无限子弹", "buy_position_pct": 0.05, "unlimited_bullets": 1, "max_positions": 9999},
}
# 信号要灌入的全部账户(顺序即处理顺序)
ACCOUNT_KEYS = ("default", "unlimited")


async def get_or_create_account(user_id: int = 1, account_key: str = "default") -> dict:
    cfg = ACCOUNT_DEFAULTS.get(account_key, ACCOUNT_DEFAULTS["default"])
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_account WHERE user_id=%s AND account_key=%s",
                (user_id, account_key))
            row = await cur.fetchone()
            if row:
                return row
            await cur.execute(
                "INSERT INTO cfzy_biz_paper_account "
                "(user_id, account_key, name, cash, initial_capital, max_positions, "
                " buy_position_pct, unlimited_bullets) "
                "VALUES (%s,%s,%s,1000000.00,1000000.00,%s,%s,%s)",
                (user_id, account_key, cfg["name"], cfg["max_positions"],
                 cfg["buy_position_pct"], cfg["unlimited_bullets"]))
            await conn.commit()
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_account WHERE user_id=%s AND account_key=%s",
                (user_id, account_key))
            return await cur.fetchone()


async def update_settings(user_id: int, initial_capital, max_positions,
                          account_key: str = "default") -> None:
    sets, args = [], []
    if initial_capital is not None:
        sets.append("initial_capital=%s"); args.append(initial_capital)
    if max_positions is not None:
        sets.append("max_positions=%s"); args.append(max_positions)
    if not sets:
        return
    args.extend([user_id, account_key])
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE cfzy_biz_paper_account SET {', '.join(sets)} "
                "WHERE user_id=%s AND account_key=%s", args)
            await conn.commit()


async def reset_account(user_id: int, initial_capital: float, max_positions: int,
                        account_key: str = "default") -> None:
    """清空持仓/流水/曲线, 现金=本金, 记 reset_at。"""
    cfg = ACCOUNT_DEFAULTS.get(account_key, ACCOUNT_DEFAULTS["default"])
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id FROM cfzy_biz_paper_account WHERE user_id=%s AND account_key=%s",
                (user_id, account_key))
            acct = await cur.fetchone()
            if not acct:
                await cur.execute(
                    "INSERT INTO cfzy_biz_paper_account "
                    "(user_id, account_key, name, cash, initial_capital, max_positions, "
                    " buy_position_pct, unlimited_bullets) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user_id, account_key, cfg["name"], initial_capital, initial_capital,
                     max_positions, cfg["buy_position_pct"], cfg["unlimited_bullets"]))
                await conn.commit()
                return
            aid = acct["id"]
            # v1.7.571: 真事务包裹三删一改, 防中途崩溃留下"清了持仓没重置现金"的半重置账户。
            try:
                await conn.begin()
                await cur.execute("DELETE FROM cfzy_biz_paper_position WHERE account_id=%s", (aid,))
                await cur.execute("DELETE FROM cfzy_biz_paper_trade WHERE account_id=%s", (aid,))
                await cur.execute("DELETE FROM cfzy_biz_paper_equity WHERE account_id=%s", (aid,))
                await cur.execute(
                    "UPDATE cfzy_biz_paper_account SET cash=%s, initial_capital=%s, max_positions=%s, "
                    "started_at=NOW(), reset_at=NOW() WHERE id=%s",
                    (initial_capital, initial_capital, max_positions, aid))
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


async def get_position(account_id: int, code: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_position WHERE account_id=%s AND code=%s", (account_id, code))
            return await cur.fetchone()


async def list_positions(account_id: int) -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_position WHERE account_id=%s ORDER BY open_time DESC", (account_id,))
            return list(await cur.fetchall())


async def position_count(account_id: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM cfzy_biz_paper_position WHERE account_id=%s", (account_id,))
            return int((await cur.fetchone())[0])


async def sum_position_cost(account_id: int) -> float:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(cost_amount),0) FROM cfzy_biz_paper_position WHERE account_id=%s", (account_id,))
            return float((await cur.fetchone())[0])


# 可重试失败原因: 当天资金/仓位释放后可能补成交, 故不锁定(下次扫描可再试)。
# 其余失败(无权限/已持有)是终态: 记一次即锁定, 不再重试/刷屏。
RETRIABLE_FAIL_REASONS = ("资金不足", "仓位已满")


async def signal_processed(account_id: int, code: str, signal_id: str, trade_date) -> bool:
    """该(账户,股,买点,日)是否已成交或已终态失败 → 不再处理。
    可重试失败(资金不足/仓位已满)不算"已处理", 留给后续扫描在资金/仓位释放后补买。"""
    placeholders = ",".join(["%s"] * len(RETRIABLE_FAIL_REASONS))
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM cfzy_biz_paper_trade WHERE account_id=%s AND code=%s AND signal_id=%s "
                f"AND trade_date=%s AND (status='success' OR fail_reason NOT IN ({placeholders})) LIMIT 1",
                (account_id, code, signal_id, trade_date, *RETRIABLE_FAIL_REASONS))
            return (await cur.fetchone()) is not None


async def record_failure(account: dict, code: str, name: str, signal_id: str,
                         signal_name: str, direction: str, price: float,
                         fail_reason: str) -> None:
    """记一笔失败成交(不动现金/持仓)。同(账户,股,买点,日)已有失败行则更新(刷新原因/时间),
    否则插入, 避免可重试失败每次扫描刷一条。"""
    aid = account["id"]
    uid = account["user_id"]
    cash = float(account["cash"])
    today = datetime.now().date()
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM cfzy_biz_paper_trade WHERE account_id=%s AND code=%s AND signal_id=%s "
                "AND trade_date=%s AND status='failed' LIMIT 1", (aid, code, signal_id, today))
            existing = await cur.fetchone()
            if existing:
                await cur.execute(
                    "UPDATE cfzy_biz_paper_trade SET fail_reason=%s, price=%s, name=%s, "
                    "trade_time=NOW() WHERE id=%s", (fail_reason, price, name, existing[0]))
            else:
                await cur.execute(
                    "INSERT INTO cfzy_biz_paper_trade (account_id,user_id,code,name,side,qty,price,amount,fee,"
                    "cash_after,signal_id,signal_name,signal_direction,note,trade_date,status,fail_reason) "
                    "VALUES (%s,%s,%s,%s,'buy',0,%s,0,0,%s,%s,%s,%s,'',%s,'failed',%s)",
                    (aid, uid, code, name, price, cash, signal_id, signal_name, direction, today, fail_reason))
            await conn.commit()


async def apply_fill(account: dict, action: dict, code: str, name: str,
                     signal_id: str, signal_name: str, direction: str,
                     entry_model_name: str = "") -> None:
    """单事务: 写流水 + 改持仓 + 改现金。action 来自 paper_trader.decide()。

    v1.7.571: ①真事务包裹(conn.begin/commit/rollback) — 全局连接池 autocommit=True 时原来的
      conn.commit() 是空操作, 三条写(流水/持仓/现金)非原子, 中途崩溃会写了流水没扣钱=账户不平。
    ②现金改增量写 `cash = cash + delta` — 原来写调用方预读算出的绝对 cash_after, 两笔并发都基于
      同一旧现金算 → 后写覆盖先写(丢失更新, 现金凭空多出)。增量写由 DB 原子累加, 天然正确。
    """
    aid = account["id"]
    uid = account["user_id"]
    today = datetime.now().date()
    # 现金增量: 买 -(成交额+费), 卖 +(成交额-费)
    cash_delta = ((action["amount"] - action["fee"]) if action["side"] == "sell"
                  else -(action["amount"] + action["fee"]))
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO cfzy_biz_paper_trade (account_id,user_id,code,name,side,qty,price,amount,fee,"
                    "cash_after,signal_id,signal_name,signal_direction,realized_pnl,realized_pnl_pct,note,trade_date) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (aid, uid, code, name, action["side"], action["qty"], action["price"], action["amount"],
                     action["fee"], action["cash_after"], signal_id, signal_name, direction,
                     action.get("realized_pnl"), action.get("realized_pnl_pct"), action.get("note", ""), today))
                if action["side"] == "buy":
                    # 无限子弹账户同股可加仓: 已持仓则累加股数/成本(均价随之摊薄), 首仓信息(开仓日/入仓买点)保留。
                    # 普通账户买入前已持仓会在 decide() 被跳过, 走不到 ON DUPLICATE 分支, 行为不变。
                    await cur.execute(
                        "INSERT INTO cfzy_biz_paper_position (account_id,user_id,code,name,qty,cost_amount,"
                        "open_date,entry_signal_id,entry_model_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE qty=qty+VALUES(qty), "
                        "cost_amount=cost_amount+VALUES(cost_amount), name=VALUES(name)",
                        (aid, uid, code, name, action["qty"], action["amount"] + action["fee"], today,
                         signal_id, entry_model_name))
                else:
                    if action.get("close_position"):
                        await cur.execute(
                            "DELETE FROM cfzy_biz_paper_position WHERE account_id=%s AND code=%s", (aid, code))
                    else:
                        await cur.execute(
                            "UPDATE cfzy_biz_paper_position SET qty=qty-%s, cost_amount=cost_amount-%s "
                            "WHERE account_id=%s AND code=%s",
                            (action["qty"], action["cost_basis_sold"], aid, code))
                await cur.execute(
                    "UPDATE cfzy_biz_paper_account SET cash = cash + %s WHERE id=%s", (cash_delta, aid))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


async def list_trades(account_id: int, limit: int = 100, offset: int = 0) -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_trade WHERE account_id=%s ORDER BY trade_time DESC, id DESC "
                "LIMIT %s OFFSET %s", (account_id, limit, offset))
            return list(await cur.fetchall())


async def upsert_equity(account_id: int, user_id: int, snap_date, cash: float,
                        holdings_mv: float, total_equity: float, total_return_pct: float,
                        position_count_: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_paper_equity (account_id,user_id,snap_date,cash,holdings_mv,"
                "total_equity,total_return_pct,position_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE cash=VALUES(cash), holdings_mv=VALUES(holdings_mv), "
                "total_equity=VALUES(total_equity), total_return_pct=VALUES(total_return_pct), "
                "position_count=VALUES(position_count)",
                (account_id, user_id, snap_date, cash, holdings_mv, total_equity, total_return_pct, position_count_))
            await conn.commit()


async def get_equity_curve(account_id: int) -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT snap_date,total_equity,total_return_pct,position_count FROM cfzy_biz_paper_equity "
                "WHERE account_id=%s ORDER BY snap_date ASC", (account_id,))
            return list(await cur.fetchall())


async def realized_stats(account_id: int) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COUNT(*) AS n, SUM(realized_pnl>0) AS win, "
                "COALESCE(SUM(realized_pnl),0) AS pnl, "
                "COALESCE(SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),0) AS gain, "
                "COALESCE(-SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END),0) AS loss "
                "FROM cfzy_biz_paper_trade WHERE account_id=%s AND side='sell'", (account_id,))
            return await cur.fetchone()


async def model_stats(account_id: int) -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT b.signal_id AS model, COUNT(*) AS n, SUM(s.realized_pnl>0) AS win, "
                "COALESCE(SUM(s.realized_pnl),0) AS pnl, COALESCE(AVG(s.realized_pnl_pct),0) AS avg_pct "
                "FROM cfzy_biz_paper_trade s "
                "JOIN cfzy_biz_paper_trade b ON b.account_id=s.account_id AND b.code=s.code "
                "  AND b.side='buy' AND b.status='success' AND b.trade_time<=s.trade_time "
                "WHERE s.account_id=%s AND s.side='sell' "
                "  AND b.id=(SELECT MAX(b2.id) FROM cfzy_biz_paper_trade b2 WHERE b2.account_id=s.account_id "
                "    AND b2.code=s.code AND b2.side='buy' AND b2.status='success' AND b2.trade_time<=s.trade_time) "
                "GROUP BY b.signal_id ORDER BY pnl DESC", (account_id,))
            return list(await cur.fetchall())
