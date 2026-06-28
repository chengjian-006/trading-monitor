import asyncio
import logging
import time
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)

_sector_top_cache: dict[str, list[dict]] = {}
_sector_cache_ts: float = 0
SECTOR_CACHE_TTL = 45

_bk_map_cache: dict[str, str] = {}
_bk_map_ts: float = 0
BK_MAP_CACHE_TTL = 3600

# v1.7.38: 移除"板块最强"信号推送(SECTOR_LEADER 已永久停用)
# refresh_sector_leaders 仍保留 — 它把板块内排名写入 cfzy_biz_stock_pool.sector_rank,
# 供 SCORE_STRENGTH (I 维度: 板块内排名) 使用。


from backend.core.trading_calendar import is_trading_time as _is_trading_time  # v1.7.x 统一来源


async def _get_bk_map() -> dict[str, str]:
    global _bk_map_cache, _bk_map_ts
    now = time.time()
    if now - _bk_map_ts < BK_MAP_CACHE_TTL and _bk_map_cache:
        return _bk_map_cache
    _bk_map_cache = await data_fetcher.get_industry_bk_map()
    _bk_map_ts = now
    return _bk_map_cache


async def _refresh_sector_cache(industries: set[str]):
    global _sector_top_cache, _sector_cache_ts
    now = time.time()
    if now - _sector_cache_ts < SECTOR_CACHE_TTL and _sector_top_cache:
        return

    bk_map = await _get_bk_map()
    relevant = {ind: bk_map[ind] for ind in industries if ind in bk_map}
    if not relevant:
        return

    sem = asyncio.Semaphore(3)

    async def _fetch_one(industry: str, bk: str):
        async with sem:
            result = await data_fetcher.get_sector_top_stocks(bk, top_n=5)
            await asyncio.sleep(0.15)
            return industry, result

    results = await asyncio.gather(
        *[_fetch_one(ind, bk) for ind, bk in relevant.items()],
        return_exceptions=True,
    )

    for r in results:
        if isinstance(r, tuple):
            industry, top_stocks = r
            if top_stocks:
                _sector_top_cache[industry] = top_stocks

    _sector_cache_ts = now


async def refresh_sector_leaders():
    if not _is_trading_time():
        return

    all_stocks = await repository.list_all_stocks()
    if not all_stocks:
        return

    industries = {s.get("industry", "") for s in all_stocks if s.get("industry")}
    if not industries:
        return

    await _refresh_sector_cache(industries)

    rank_updates: list[tuple] = []
    for s in all_stocks:
        code = s["code"]
        industry = s.get("industry", "")
        if not industry or industry not in _sector_top_cache:
            rank_updates.append((code, None))
            continue

        top_list = _sector_top_cache[industry]
        rank = None
        for i, item in enumerate(top_list, 1):
            if item["code"] == code:
                rank = i
                break
        rank_updates.append((code, rank))

    await repository.batch_update_sector_rank(rank_updates)
    logger.info(f"[SectorLeader] {len(rank_updates)} stocks ranked in sectors")
