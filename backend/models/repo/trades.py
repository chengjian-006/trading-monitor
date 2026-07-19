"""交割单 CRUD + 持仓同步 - cfzy_biz_trades 表 + cfzy_biz_stock_pool 联动."""
from datetime import date, datetime

from backend.models.database import get_pool
from backend.models.repo._db import _fetchall, _fetchone

# 跨格式/跨日重复导入去重窗口(天): 交割单(真实成交日)与历史成交(用户手选注入日)会给
# 同一笔成交打上不同 trade_date(实测差 1~7 天), 唯一键含 trade_date 拦不住。指纹相同且
# 成交日相距在此窗口内即判为同一笔跳过; 窗口足够宽覆盖日期漂移, 又能放过几十天后偶然
# 撞到同量同价同秒的另一笔真实成交(概率极低但留个保险)。
_DEDUP_WINDOW_DAYS = 30


def _as_date(v):
    """trade_date 归一为 date(支持 date / datetime / 'YYYY-MM-DD' 字符串)。"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _fingerprint(code, direction, quantity, price, trade_time):
    """日期无关的量价时间指纹: 秒级时间 + 量 + 价。成交编号缺失时的兜底判据。"""
    return (str(code), str(direction), int(quantity),
            round(float(price), 3), str(trade_time))


def _deal_no(rec) -> str | None:
    """取成交编号(每笔成交全局唯一号); 空/缺 → None。"""
    v = rec.get("deal_no")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _same_fill(r_fp, r_deal, r_date, e_fp, e_deal, e_date, window_days: int) -> bool:
    """判两笔是否同一笔成交:
      - 双方都有成交编号 → 严格按编号相等(等量拆单编号不同=不同笔, 保留; 重复导入编号相同=同笔, 去重);
      - 任一方无编号(跨格式: 平安"历史成交"可能无编号) → 退回 量价时间指纹 + 成交日相距≤window。
    这样等量拆单靠编号区分、跨格式靠指纹兜底, 两头都不误判。"""
    if r_deal and e_deal:
        return r_deal == e_deal
    if r_fp != e_fp:
        return False
    if r_date is None or e_date is None:
        return True
    return abs((r_date - e_date).days) <= window_days


def filter_new_records(records: list[dict], existing: list[dict],
                       window_days: int = _DEDUP_WINDOW_DAYS) -> list[dict]:
    """从待入库 records 里剔除与 existing(或同批已留)重复的成交, 返回应新增的子集。

    去重按"同一笔"关系(_same_fill)做多重集消费: 每条 incoming 至多消费一条尚未被消费的
    已存在记录; 消费到=重复导入跳过, 消费不到=新成交(含同秒同量同价但成交编号不同的等量拆单)保留。
    纯逻辑, 不连库, 供单测。"""
    pool = []   # 可消费池: 已存在(DB) + 同批已保留, 每项 {fp, deal, date}
    for e in existing:
        pool.append({
            "fp": _fingerprint(e["code"], e["direction"], e["quantity"], e["price"], e["trade_time"]),
            "deal": _deal_no(e),
            "date": _as_date(e["trade_date"]),
        })

    fresh = []
    for r in records:
        rfp = _fingerprint(r["code"], r["direction"], r["quantity"], r["price"], r["trade_time"])
        rdeal = _deal_no(r)
        rdate = _as_date(r["trade_date"])
        hit = None
        for i, e in enumerate(pool):
            if _same_fill(rfp, rdeal, rdate, e["fp"], e["deal"], e["date"], window_days):
                hit = i
                break
        if hit is not None:
            pool.pop(hit)   # 消费掉该已存在记录, 防两条相同 incoming 撞同一条
            continue
        fresh.append(r)
        pool.append({"fp": rfp, "deal": rdeal, "date": rdate})   # 同批后续可与它去重
    return fresh


async def has_import_today(user_id: int) -> bool:
    """用户今天(按 imported_at)是否上传过交割单。"""
    row = await _fetchone(
        "SELECT 1 AS x FROM cfzy_biz_trades WHERE user_id = %s AND DATE(imported_at) = CURDATE() LIMIT 1",
        (user_id,),
    )
    return row is not None


async def get_latest_import_time(user_id: int):
    """用户最近一次上传交割单的时间(无则 None)。"""
    row = await _fetchone(
        "SELECT MAX(imported_at) AS t FROM cfzy_biz_trades WHERE user_id = %s",
        (user_id,),
    )
    return row.get("t") if row else None


async def delete_trades_on_date(user_id: int, trade_date) -> int:
    """删除用户某交易日的全部成交记录, 返回删除行数。

    历史成交「替换该日」导入用: 先清该日再写入这批, 防与交割单同日双重计数。
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM cfzy_biz_trades WHERE user_id = %s AND trade_date = %s",
                (user_id, trade_date),
            )
            return cur.rowcount


async def save_trade_records(user_id: int, records: list[dict]) -> int:
    """增量保存交割记录, 重复数据自动跳过. 返回新增条数.

    去重两层: ① 日期无关指纹(_fingerprint)—— 同一笔成交在交割单/历史成交两种格式里
    trade_date 不一致也能识别为重复(指纹相同且成交日在 _DEDUP_WINDOW_DAYS 内即跳过);
    ② INSERT IGNORE 兜底精确重复(同 uk_trade)。
    """
    if not records:
        return 0
    codes = {r["code"] for r in records}
    placeholders = ",".join(["%s"] * len(codes))
    existing = await _fetchall(
        f"SELECT code, direction, quantity, price, trade_time, trade_date, deal_no "
        f"FROM cfzy_biz_trades WHERE user_id = %s AND code IN ({placeholders})",
        (user_id, *codes),
    )
    fresh = filter_new_records(records, existing)
    if not fresh:
        return 0

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT IGNORE INTO cfzy_biz_trades "
                "(user_id, trade_date, trade_time, code, name, direction, quantity, price, amount, fee, stamp_tax, transfer_fee, net_amount, deal_no) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(user_id, r["trade_date"], r["trade_time"], r["code"], r["name"],
                  r["direction"], r["quantity"], r["price"], r["amount"],
                  r["fee"], r["stamp_tax"], r["transfer_fee"], r["net_amount"], r.get("deal_no"))
                 for r in fresh],
            )
            return cur.rowcount


async def get_all_trade_records(user_id: int) -> list[dict]:
    """获取用户全量交割记录 (用于分析)."""
    return await _fetchall(
        "SELECT trade_date, trade_time, code, name, direction, quantity, price, amount, "
        "fee, stamp_tax, transfer_fee, net_amount FROM cfzy_biz_trades "
        "WHERE user_id = %s ORDER BY trade_date, trade_time",
        (user_id,),
    )


async def sync_positions_from_trades(user_id: int, holdings: dict):
    """根据交割单分析结果同步持仓状态到股票池.
    holdings: {code: {"name": str, "quantity": int}}
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for code, info in holdings.items():
                await cur.execute(
                    "INSERT INTO cfzy_biz_stock_pool (code, user_id, name, status, hold_source) "
                    "VALUES (%s, %s, %s, 'hold', 'trade') "
                    "ON DUPLICATE KEY UPDATE status='hold', hold_source='trade', deleted_at=NULL",
                    (code, user_id, info["name"]),
                )
            if holdings:
                placeholders = ",".join(["%s"] * len(holdings))
                params = [user_id] + list(holdings.keys())
                await cur.execute(
                    f"UPDATE cfzy_biz_stock_pool SET status='watch', hold_source='' "
                    f"WHERE user_id = %s AND status = 'hold' AND hold_source = 'trade' AND code NOT IN ({placeholders})",
                    params,
                )
            else:
                await cur.execute(
                    "UPDATE cfzy_biz_stock_pool SET status='watch', hold_source='' "
                    "WHERE user_id = %s AND status = 'hold' AND hold_source = 'trade'",
                    (user_id,),
                )
