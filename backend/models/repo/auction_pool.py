"""自选股集合竞价成交额快照 CRUD - cfzy_biz_auction_pool 表 (v1.7.272).

每交易日 9:26 由 auction_pool_refresher.record_auction_pool_snapshot 写入一批,
同 (code, trade_date) 唯一, 重跑用 ON DUPLICATE KEY UPDATE 覆盖。
"""
from backend.models.repo._db import _executemany, _fetchall, _fetchone


async def save_auction_snapshots(snaps: list[dict]) -> int:
    """批量落库自选股集合竞价快照。snaps 每项含
    code/trade_date/name/pre_close/open_price/gap_pct/auction_amount/auction_volume。"""
    if not snaps:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_biz_auction_pool "
        "(code, trade_date, name, pre_close, open_price, gap_pct, auction_amount, auction_volume) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), pre_close=VALUES(pre_close), "
        "open_price=VALUES(open_price), gap_pct=VALUES(gap_pct), "
        "auction_amount=VALUES(auction_amount), auction_volume=VALUES(auction_volume), "
        "captured_at=CURRENT_TIMESTAMP",
        [(s["code"], s["trade_date"], s.get("name", ""),
          s.get("pre_close", 0), s.get("open_price", 0), s.get("gap_pct", 0),
          s.get("auction_amount", 0), s.get("auction_volume", 0)) for s in snaps],
    )


async def get_auction_snapshots(trade_date: str, min_amount: float = 0.0) -> list[dict]:
    """取某交易日自选股竞价快照, 竞价成交额≥min_amount(元), 按竞价成交额降序。"""
    return await _fetchall(
        "SELECT code, name, pre_close, open_price, gap_pct, auction_amount, auction_volume, captured_at "
        "FROM cfzy_biz_auction_pool WHERE trade_date = %s AND auction_amount >= %s "
        "ORDER BY auction_amount DESC",
        (trade_date, min_amount),
    )


async def get_auction_latest_date() -> str | None:
    """最近有竞价数据的交易日。"""
    row = await _fetchone("SELECT MAX(trade_date) AS d FROM cfzy_biz_auction_pool")
    return row.get("d") if row else None
