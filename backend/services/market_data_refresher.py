import asyncio
import logging
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend.services.ai_analyst import get_index_trends, get_market_stats

logger = logging.getLogger(__name__)

EXTENDED_HOURS = [
    {"start": "09:15", "end": "11:35"},
    {"start": "12:55", "end": "15:05"},
]

# 港股交易到 16:00 (比 A 股晚收 1 小时), 中午 12:00-13:00 休市。
# A 股时段外但港股仍在交易的窗口: 此时只补刷港股两个指数, A 股分时/涨跌停保持当日收盘值不动。
HK_EXTRA_HOURS = [
    {"start": "11:35", "end": "12:05"},
    {"start": "15:05", "end": "16:05"},
]


def _in_windows(windows: list[dict]) -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return any(p["start"] <= t <= p["end"] for p in windows)


def _is_market_time() -> bool:
    return _in_windows(EXTENDED_HOURS)


def _is_hk_extra_time() -> bool:
    return _in_windows(HK_EXTRA_HOURS)


async def refresh_market_data():
    if _is_market_time():
        trade_date = datetime.now().strftime("%Y-%m-%d")
        # 同步阻塞函数卸线程池, 不冻结 event loop(同 plunge_detector)
        index_trends = await asyncio.to_thread(get_index_trends)
        market_stats = await asyncio.to_thread(get_market_stats)
        if not index_trends and not market_stats:
            logger.warning("Market data refresh: both index_trends and market_stats empty")
            return
        await repository.upsert_market_snapshot(trade_date, index_trends, market_stats)
    elif _is_hk_extra_time():
        await _refresh_hk_only()


async def _refresh_hk_only():
    """A 股收盘后、港股仍在交易(至 16:00)的时段, 只补刷港股两个指数并并回当日快照,
    A 股分时与 market_stats 保持当日收盘值不动, 避免被收盘后的重取覆盖。"""
    from backend.services.ai_analyst import HK_INDEX, _fetch_hk_trend

    snap = await repository.get_market_snapshot()
    if not snap or not isinstance(snap.get("index_trends"), dict):
        return
    it = snap["index_trends"]
    ms = snap.get("market_stats") or {}
    changed = False
    for qcode, name in HK_INDEX:
        ht = _fetch_hk_trend(qcode)
        if ht.get("trends"):
            ht["name"] = name
            it[qcode] = ht
            changed = True
    if changed:
        await repository.upsert_market_snapshot(str(snap["trade_date"]), it, ms)
