"""市场情绪温度表(题材热度)采集 — refresh_theme_heat。

交易日窗口内每 5 分钟拉一次涨停池(同花顺主源带 reason_type 涨停题材),
按"涨停题材首标签"聚合各题材当日涨停家数 + 样本股, 整日幂等覆盖写 cfzy_sys_theme_heat。
前端 ThemeHeatPanel 以 日期×题材 矩阵展示主线兴起/退潮。

题材口径: reason_type 形如 "电力+业绩减亏+广东国资", 取首段 "电力" 作主题材
(首段通常是当日核心驱动)。东财备源无 reason_type → 该日题材聚合为空(降级)。
"""
import logging
from collections import defaultdict
from datetime import datetime

from backend.core.trading_calendar import is_workday
from backend.fetcher.limit_pool import get_limit_pool_cached
from backend.models import repository

logger = logging.getLogger(__name__)

_MAX_SAMPLES = 8  # 每题材留几只样本股名(前端点格子看)


def _in_window(now: datetime) -> bool:
    """工作日 09:30~15:10 (含收盘后一档, 收盘快照即当日定版)。"""
    if not is_workday(now):
        return False
    return "09:30" <= now.strftime("%H:%M") <= "15:10"


async def refresh_theme_heat() -> None:
    now = datetime.now()
    if not _in_window(now):
        return

    date = now.strftime("%Y%m%d")
    pool = await get_limit_pool_cached(date)
    boards = (pool or {}).get("boards") or []
    if not boards:
        logger.info("[theme_heat] 涨停池为空或取数失败, 跳过")
        return

    agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "names": []})
    for b in boards:
        reason = (b.get("reason") or "").strip()
        if not reason:
            continue
        theme = reason.split("+")[0].strip()
        if not theme:
            continue
        slot = agg[theme]
        slot["count"] += 1
        if len(slot["names"]) < _MAX_SAMPLES:
            slot["names"].append(b.get("name") or b.get("code") or "")

    if not agg:
        logger.info("[theme_heat] 涨停股无题材字段(可能东财备源), 跳过")
        return

    rows = [(theme, v["count"], ",".join(v["names"])) for theme, v in agg.items()]
    await repository.save_theme_heat(date, rows)
    logger.info(f"[theme_heat] {date} 题材 {len(rows)} 个, 涨停股 {sum(v['count'] for v in agg.values())} 只 已写入")
