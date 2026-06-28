"""Market Overview Refresher (v1.7.97)

定时拉"全球指数 + A股四指数 + 市场温度"快照写入 DB,
前端 MarketOverviewBar 从 DB 读, 多用户共享同一份外部 API 调用结果。

- 调度建议: 交易时段 30s 一次, 非交易时段 5min(可后续在 scheduled_tasks 表配置)
- 单行 UPSERT 设计 (id=1)
- 任一数据源拉失败不阻塞 — 已有数据保留旧值
"""
import asyncio
import logging

from backend.models import repository
from backend.services import ai_analyst

logger = logging.getLogger(__name__)


async def refresh_market_overview():
    loop = asyncio.get_event_loop()
    # 三个数据源是同步阻塞 IO (requests/akshare), 用线程池并发拉
    global_indices, a_indices, market_stats = await asyncio.gather(
        loop.run_in_executor(None, ai_analyst.get_global_indices),
        loop.run_in_executor(None, ai_analyst.get_market_indices),
        loop.run_in_executor(None, ai_analyst.get_market_stats),
        return_exceptions=True,
    )
    # gather with return_exceptions=True: 任一异常变成 Exception 对象, 不整体失败
    if isinstance(global_indices, Exception):
        logger.warning(f"[market_overview] global_indices 拉取失败: {global_indices}")
        global_indices = []
    if isinstance(a_indices, Exception):
        logger.warning(f"[market_overview] a_indices 拉取失败: {a_indices}")
        a_indices = []
    if isinstance(market_stats, Exception):
        logger.warning(f"[market_overview] market_stats 拉取失败: {market_stats}")
        market_stats = {}

    # 全部空时不覆盖旧数据
    if not global_indices and not a_indices and not market_stats:
        logger.warning("[market_overview] 三个数据源都空, 跳过写库保留旧数据")
        return

    # 用情绪快照的精确涨停/跌停(同花顺官方, 已抓)覆盖 market_stats 的新浪近似值,
    # 让 regime 大盘评分用准确数 (新浪全市场含新股/特殊股, 阈值估算偏多)
    if isinstance(market_stats, dict):
        try:
            snap = await repository.get_latest_emotion()
            if snap:
                if snap.get("limit_up_count") is not None:
                    market_stats["limit_up"] = snap["limit_up_count"]
                if snap.get("limit_down_count") is not None:
                    market_stats["limit_down"] = snap["limit_down_count"]
        except Exception as e:
            logger.warning(f"[market_overview] 情绪快照精确涨跌停覆盖失败: {e}")

    await repository.save_market_overview(global_indices, a_indices, market_stats)
    logger.debug(
        f"[market_overview] 已刷新: 全球{len(global_indices)} A股{len(a_indices)} "
        f"涨停{market_stats.get('limit_up', '?')}"
    )
