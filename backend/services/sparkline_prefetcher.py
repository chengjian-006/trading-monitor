"""分时走势预热任务 (v1.7.30)

盘中每 25 秒主动调用 get_batch_intraday_sparkline,
让 _batch_intraday_cache (TTL 30s) 永远是热的。
前端打开页面时 /api/kline/batch-intraday 直接命中缓存秒返。
"""
import asyncio
import logging
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)


from backend.core.trading_calendar import is_trading_time as _is_trading_time, is_workday as _is_workday  # v1.7.x 统一来源


async def prefetch_intraday_sparklines():
    """盘中预拉全自选池的分时走势, 保持缓存常热。

    v1.7.597: 范围从"focused/hold"放宽到【全池·在池即扫】(补齐 v1.7.589 其它扫描器的一致性,
    focused 不再当逻辑闸门, 用户0708确认); 概念指数不排除(有分时, 拉进来无妨)。
    这份缓存也是「二波过前高」实时扫描器的数据源, 故须覆盖全部自选。"""
    if not _is_trading_time():
        return

    all_stocks = await repository.list_all_stocks()
    codes = sorted({s["code"] for s in all_stocks if s.get("code")})
    if not codes:
        return

    # 接口每批最多 50 (跟 router 一致), 分批拉
    BATCH = 50
    pulled = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        try:
            result = await data_fetcher.get_batch_intraday_sparkline(batch)
            good = {c: v for c, v in result.items() if v and v.get("trends")}
            pulled += len(good)
            # 存盘: 非交易时段/盘后/重启后"走势"列回退到上一交易日
            if good:
                await repository.upsert_sparkline_snapshots(good, today)
        except Exception as e:
            logger.warning(f"[sparkline_prefetch] batch {i}-{i+len(batch)} 失败: {e}")
    logger.debug(f"[sparkline_prefetch] 预拉+存盘 {pulled}/{len(codes)} 只票分时数据")
