"""09:26 自选股集合竞价成交额采集 → cfzy_biz_auction_pool (v1.7.272).

时点: 9:25 集合竞价撮合完成、开盘价已定; 连续竞价 9:30 才开始, 故 9:26 时实时行情的
      成交额/成交量 ≈ 集合竞价量。此刻采集即"集合竞价成交额"。
用途: 攒数据验证"竞价成交额能否提升弱转强买点胜率"。历史无此数据(分钟级缺失),
      只能从上线起逐日向前积累。本任务仅落库, 不推送、不参与任何信号门控。
"""
import logging
from datetime import datetime

from backend import data_fetcher
from backend.models import repository

logger = logging.getLogger(__name__)


def _is_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


async def record_auction_pool_snapshot():
    """采集全部自选股集合竞价快照并落库。"""
    if not _is_trading_day():
        logger.info("[auction_pool] 非交易日, 跳过")
        return

    stocks = await repository.list_all_stocks()
    name_map = {s["code"]: s.get("name", "") for s in stocks if s.get("code")}
    codes = sorted(name_map.keys())
    if not codes:
        logger.info("[auction_pool] 自选股为空, 跳过")
        return

    quotes = await data_fetcher.get_realtime_quotes(codes)
    trade_date = datetime.now().strftime("%Y-%m-%d")
    snaps = []
    for code in codes:
        q = quotes.get(code)
        if not q:
            continue
        pre = float(q.get("pre_close") or 0)
        op = float(q.get("open") or 0)
        amt = float(q.get("amount") or 0)
        vol = float(q.get("volume") or 0)
        # 开盘价未生成(竞价数据未到位)则跳过, 避免记错高开
        if op <= 0 or pre <= 0:
            continue
        gap = round((op - pre) / pre * 100, 2)
        snaps.append({
            "code": code, "trade_date": trade_date,
            "name": name_map.get(code) or q.get("name", ""),
            "pre_close": pre, "open_price": op, "gap_pct": gap,
            "auction_amount": amt, "auction_volume": vol,
        })

    n = await repository.save_auction_snapshots(snaps)
    logger.info(f"[auction_pool] {trade_date} 自选股竞价采集 {len(snaps)}/{len(codes)} 落库 rows={n}")
    return {"trade_date": trade_date, "saved": len(snaps), "total": len(codes)}
