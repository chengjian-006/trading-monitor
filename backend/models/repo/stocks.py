"""股票池 CRUD - cfzy_biz_stock_pool 表."""
from datetime import datetime, timedelta

from backend.models.database import get_pool
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def add_stock(code: str, name: str, trade_type: str = "short", status: str = "watch",
                    user_id: int = 1, sort_order: int = 0):
    # ON DUPLICATE 时一并把 deleted_at 清空 —— 重新加入一只曾被逻辑删除的票即"复活"出池可见
    await _execute(
        "INSERT INTO cfzy_biz_stock_pool (code, user_id, name, trade_type, status, sort_order) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE name=%s, trade_type=%s, status=%s, sort_order=%s, deleted_at=NULL",
        (code, user_id, name, trade_type, status, sort_order, name, trade_type, status, sort_order),
    )


async def remove_stock(code: str, user_id: int = 1):
    """逻辑删除: 出池不可见 + 停扫描/行情刷新, 但历史信号与回测 universe 仍保留。
    彻底物理删除走 purge_stock。"""
    await _execute(
        "UPDATE cfzy_biz_stock_pool SET deleted_at = NOW() "
        "WHERE code = %s AND user_id = %s AND deleted_at IS NULL",
        (code, user_id),
    )
    # 自定义预警依附于在池个股, 出池即清理(避免重新加入时残留旧预警)
    await _execute("DELETE FROM cfzy_biz_stock_alerts WHERE code = %s AND user_id = %s", (code, user_id))


async def purge_stock(code: str, user_id: int = 1):
    """物理删除: 连历史信号一起清。用于误加的票, 不可恢复。"""
    await _execute("DELETE FROM cfzy_biz_stock_pool WHERE code = %s AND user_id = %s", (code, user_id))
    await _execute("DELETE FROM cfzy_biz_signals WHERE code = %s AND user_id = %s", (code, user_id))
    await _execute("DELETE FROM cfzy_biz_stock_alerts WHERE code = %s AND user_id = %s", (code, user_id))


async def get_stock_popularity_rank(code: str):
    """该股最近一次同花顺人气榜名次(popularity_rank, 按 code 全局更新, 无则 None)。"""
    row = await _fetchone(
        "SELECT popularity_rank FROM cfzy_biz_stock_pool "
        "WHERE code = %s AND popularity_rank IS NOT NULL LIMIT 1", (code,))
    if not row:
        return None
    return row.get("popularity_rank") if isinstance(row, dict) else row[0]


async def get_latest_popularity_rank(code: str) -> int | None:
    """前一交易日的人气排名(从 cfzy_biz_popularity_daily 表)。"""
    row = await _fetchone(
        "SELECT `rank` FROM cfzy_biz_popularity_daily "
        "WHERE code=%s ORDER BY record_date DESC LIMIT 1", (code,))
    if not row:
        return None
    v = row.get("rank") if isinstance(row, dict) else row[0]
    return int(v) if v is not None else None


async def update_stock(code: str, user_id: int = 1, **kwargs):
    allowed = {"name", "trade_type", "status", "focused", "strategy"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if fields.get("status") == "hold":
        fields["hold_source"] = "manual"
    elif fields.get("status") == "watch":
        fields["hold_source"] = ""
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [code, user_id]
    await _execute(f"UPDATE cfzy_biz_stock_pool SET {set_clause} WHERE code = %s AND user_id = %s", values)


async def list_stocks(user_id: int = 1, include_deleted: bool = False) -> list[dict]:
    """默认只返回未删除的票 (UI/扫描/行情刷新用)。
    include_deleted=True 时连逻辑删除的也返回 (回测 universe 用)。"""
    where = "WHERE user_id = %s" + ("" if include_deleted else " AND deleted_at IS NULL")
    return await _fetchall(
        f"SELECT * FROM cfzy_biz_stock_pool {where} ORDER BY sort_order ASC, added_at DESC",
        (user_id,),
    )


async def list_all_stocks(include_deleted: bool = False) -> list[dict]:
    """默认只返回未删除的票。include_deleted=True 给回测 universe 用。"""
    where = "" if include_deleted else "WHERE deleted_at IS NULL "
    return await _fetchall(f"SELECT * FROM cfzy_biz_stock_pool {where}ORDER BY added_at DESC")


async def list_quotable_codes() -> list[str]:
    """在池且"可报价的真股票"代码 (套 _QUOTABLE: 6位数字 / 非88x板块指数 / 非index / 未删)。
    供只该处理个股的任务用 (如分时归档 freeze_intraday): 滤掉误加的板块/概念指数(88x)、
    期货主连(lc*非6位)等永远无个股分时的代码, 与行情自愈口径(_QUOTABLE)同源, 防误加污染。"""
    rows = await _fetchall(f"SELECT DISTINCT code FROM cfzy_biz_stock_pool WHERE {_QUOTABLE}")
    return [r["code"] for r in rows]


_QUOTABLE = "deleted_at IS NULL AND code REGEXP '^[0-9]{6}$' AND LEFT(code, 2) <> '88' AND trade_type != 'index'"
# 仅"可报价的真股票": 6位数字, 排除 88x 板块指数 / lc 期货等(它们永远无行情, 否则恒判陈旧)
# 注: 此串被 f-string 拼进"带参数"的查询, 故不能含字面量 % (如 LIKE '88%') —— pymysql 会对带参 SQL
#     做 `query % args`, '88%' 里的 %' 会被当成格式符抛 ValueError. 用 LEFT()<>  规避, 无 % 更稳.


async def get_stale_quote_codes(stale_seconds: int = 180, limit: int = 80) -> list[str]:
    """盘中 quote_updated_at 陈旧(或为空)的自选票代码, 供行情自愈补刷。"""
    rows = await _fetchall(
        f"SELECT code FROM cfzy_biz_stock_pool WHERE {_QUOTABLE} "
        "AND (quote_updated_at IS NULL OR quote_updated_at < NOW() - INTERVAL %s SECOND) "
        "GROUP BY code LIMIT %s",
        (stale_seconds, limit),
    )
    return [r["code"] for r in rows]


async def count_quote_health(stale_seconds: int = 360) -> dict:
    """行情健康计数(去重 code): 陈旧数 / 无价数 / 总数。供数据自检告警。"""
    row = await _fetchone(
        "SELECT COUNT(*) AS total, "
        "SUM(quote_updated_at IS NULL OR quote_updated_at < NOW() - INTERVAL %s SECOND) AS stale, "
        "SUM(price IS NULL OR price = 0) AS null_price "
        f"FROM (SELECT code, MAX(quote_updated_at) AS quote_updated_at, MAX(price) AS price "
        f"FROM cfzy_biz_stock_pool WHERE {_QUOTABLE} GROUP BY code) t",
        (stale_seconds,),
    )
    return {"total": int(row["total"] or 0), "stale": int(row["stale"] or 0),
            "null_price": int(row["null_price"] or 0)} if row else {"total": 0, "stale": 0, "null_price": 0}


async def get_pool_row(code: str) -> dict | None:
    """读自选票已落库的速览字段(quote_refresher 存价/涨跌/换手/量比, stock_tag_refresher 存题材)。
    非自选/已删 → None。给个股弹窗 summary 做"读库优先", 离线/东财被封时也有值。"""
    return await _fetchone(
        "SELECT name, price, pct_change, turnover, volume_ratio, concepts, limit_up_days "
        "FROM cfzy_biz_stock_pool WHERE code = %s AND deleted_at IS NULL LIMIT 1",
        (code,),
    )


async def batch_update_quotes(updates: list[dict]):
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET price=%s, pct_change=%s, amount=%s, "
                "speed=COALESCE(%s,speed), "
                "industry=COALESCE(NULLIF(%s,''),industry), "
                "volume_ratio=COALESCE(NULLIF(%s,0),volume_ratio), "
                "free_cap=COALESCE(NULLIF(%s,0),free_cap), "
                "turnover=COALESCE(NULLIF(%s,0),turnover), "
                "popularity_rank=%s, "
                "ma20=%s, ma10=%s, ma60=%s, "
                "quote_updated_at=NOW() WHERE code=%s",
                [(u["price"], u["pct_change"], u["amount"], u["speed"],
                  u.get("industry", ""), u.get("volume_ratio"), u.get("free_cap"),
                  u.get("turnover"), u.get("popularity_rank"),
                  u.get("ma20"), u.get("ma10"), u.get("ma60"), u["code"]) for u in updates],
            )


async def batch_update_core_quotes(updates: list[dict]):
    """只写核心行情(价/涨跌幅/成交额), 不碰 extra/排名等慢字段。
    给 quote_refresher 在取到行情后"先快速落库"用, 避免后续慢的东财 extra 超时导致整轮不写、价/涨跌幅长期不刷。"""
    if not updates:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET price=%s, pct_change=%s, amount=%s, "
                "quote_updated_at=NOW() WHERE code=%s",
                [(u["price"], u["pct_change"], u["amount"], u["code"]) for u in updates],
            )


async def fetch_kline_close_batch(codes: list, n: int = 20) -> dict:
    """批量查最近N日收盘价: 返回 {code: [close,...]}(最新在前)。

    v1.7.571: 加 trade_date 下界 — 原来无下界会把每只票在 kline_cache(全市场库,每票约5年≈1200行)
    的全部历史都拉回来只为取最近 n 根算均线, 每3秒全池调一次=跨云传数万行白耗带宽。
    下界取 max(n, 60) 交易日 × 1.6 的自然日缓冲(覆盖周末/节假日), 保证够 n 根。
    """
    if not codes:
        return {}
    cal_days = int(max(n, 60) * 1.6) + 10   # n=60 → ≈106 自然日 ≈70+ 交易日, 稳过60根
    cutoff = (datetime.now() - timedelta(days=cal_days)).strftime("%Y-%m-%d")
    placeholders = ",".join(["%s"] * len(codes))
    rows = await _fetchall(
        f"SELECT code, trade_date, close FROM cfzy_sys_kline_cache "
        f"WHERE code IN ({placeholders}) AND trade_date >= %s ORDER BY code, trade_date DESC",
        (*codes, cutoff),
    )
    result: dict[str, list[float]] = {c: [] for c in codes}
    for r in rows:
        code = r["code"]
        if len(result.get(code, [])) < n:
            result.setdefault(code, []).append(float(r["close"]))
    return result


async def batch_update_sector_rank(updates: list[tuple]):
    """updates = [(code, rank_or_None), ...]"""
    if not updates:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET sector_rank = %s WHERE code = %s",
                [(rank, code) for code, rank in updates],
            )


async def batch_update_board_strength(updates: list[dict]):
    """写持仓在最热题材板块内的强弱名次。
    updates = [{code, board_name, board_rank, board_total}]
    按 code 更新(板块强弱与 user 无关, 同 code 各用户一致)。"""
    if not updates:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET board_name=%s, board_rank=%s, board_total=%s "
                "WHERE code=%s",
                [(u.get("board_name", ""), u.get("board_rank"), u.get("board_total"), u["code"])
                 for u in updates],
            )


async def batch_update_sort_order(user_id: int, codes: list[str]):
    """按给定 codes 顺序写 sort_order(下标即顺序), 用于股票池手动拖拽排序。"""
    if not codes:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET sort_order = %s WHERE code = %s AND user_id = %s",
                [(i, code, user_id) for i, code in enumerate(codes)],
            )


async def batch_update_stock_tags(updates: list[dict]):
    """v1.7.x: 批量写概念题材 + 连板数标签.
    updates = [{code, concepts(str逗号分隔), limit_up_days(int|None)}]
    """
    if not updates:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE cfzy_biz_stock_pool SET concepts=%s, limit_up_days=%s, "
                "tags_updated_at=NOW() WHERE code=%s",
                [(u.get("concepts", ""), u.get("limit_up_days"), u["code"]) for u in updates],
            )
