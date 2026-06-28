"""收盘后冻结股票池当日分时曲线 → cfzy_sys_intraday_snapshot, 供分时图历史回放.

sparkline_snapshot 只留每票最新一天(PK 仅 code), 历史曲线会被覆盖; 这里按 (code, trade_date)
逐日归档全天分时, 让"过去某天的分时图 + 当天买卖点"可回放. 每天 15:10 收盘后跑一次.
"""
import asyncio
import logging
from datetime import datetime

from backend.models import repository
from backend.core.trading_calendar import is_workday
from backend import data_fetcher

logger = logging.getLogger(__name__)


async def freeze_intraday_snapshots():
    if not is_workday():
        return  # 周末/非交易日不归档, 避免把陈旧分时存到非交易日
    # 只归档"可报价的真股票"(套 _QUOTABLE): 滤掉误加的板块/概念指数(88x)、期货主连(lc*)等
    # 永远无个股分时的代码, 否则它们每天必取数失败、虚拉低归档覆盖率。
    codes = await repository.list_quotable_codes()
    if not codes:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    snapshots: dict[str, list] = {}
    sem = asyncio.Semaphore(5)

    async def _one(code: str):
        async with sem:
            try:
                pts, _pre = await data_fetcher.get_intraday_data(code)
                if pts and len(pts) >= 2:
                    snapshots[code] = pts
            except Exception as e:
                logger.debug(f"[freeze_intraday] {code} 取数失败: {e}")
            await asyncio.sleep(0.05)

    await asyncio.gather(*[_one(c) for c in codes])
    if snapshots:
        await repository.upsert_intraday_snapshots(snapshots, today)
    logger.info(f"[freeze_intraday] 归档 {len(snapshots)}/{len(codes)} 只当日分时, date={today}")
