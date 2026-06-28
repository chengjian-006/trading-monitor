"""板块强度 API - v1.7.x.

给前端决策快查卡按需拉指定 codes 的板块实时强度数据 (板块涨幅 / 龙头涨幅 / 自身排名).
不依赖已下线的 sector_leader 调度任务 (cfzy_biz_stock_pool.sector_rank 字段恒空).

设计:
  - 输入 codes 集合
  - 找每只票的 industry (从 stock pool 已有字段读)
  - unique industries 集合并发拉 get_sector_overview
  - 内部 30s 缓存 (复用 fetcher.sectors 内缓存) 减少外部 API 压力
"""
import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sector", tags=["sector"])


@router.get("/strength-batch")
async def sector_strength_batch(
    user: Annotated[dict, Depends(get_current_user)],
    codes: str = Query("", description="逗号分隔 code 列表"),
):
    """返回 { code: {industry, pct_today, leader_name, leader_pct, self_pct, self_rank} }.

    - industry/leader/pct_today 取自 get_sector_overview (30s 缓存)
    - self_rank: 自己在板块 top 5 内的名次, 不在 top 5 → None
    - self_pct: 自己当日涨跌幅 (来自股票池 row 已存的 pct_change)
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {}
    code_list = code_list[:50]   # 上限保护

    # 读股票池 (一次 query)
    all_stocks = await repository.list_all_stocks()
    stocks_by_code: dict[str, dict] = {s["code"]: s for s in all_stocks if s["code"] in code_list}
    if not stocks_by_code:
        return {}

    # 收集涉及的 industries
    industries = sorted({s.get("industry") or "" for s in stocks_by_code.values() if s.get("industry")})
    if not industries:
        return {code: _empty_strength(stocks_by_code[code]) for code in code_list if code in stocks_by_code}

    # 并发拉每个 industry 的 overview (30s 缓存命中)
    sem = asyncio.Semaphore(5)

    async def _fetch_one(industry: str):
        async with sem:
            try:
                ov = await data_fetcher.get_sector_overview(industry, top_n=5)
                return industry, ov
            except Exception as e:
                logger.warning(f"[sector_strength] {industry} 取数失败: {e}")
                return industry, None

    pairs = await asyncio.gather(*[_fetch_one(ind) for ind in industries])
    overview_map: dict[str, dict | None] = {ind: ov for ind, ov in pairs}

    # 拼装输出
    out: dict[str, dict] = {}
    for code in code_list:
        stock = stocks_by_code.get(code)
        if not stock:
            continue
        industry = stock.get("industry") or ""
        self_pct = stock.get("pct_change")
        ov = overview_map.get(industry) if industry else None
        if not ov:
            out[code] = {
                "industry": industry, "pct_today": None,
                "leader_name": "", "leader_pct": None,
                "self_pct": self_pct, "self_rank": None,
            }
            continue
        # 自己在 top 5 内的名次
        self_rank = None
        for i, item in enumerate(ov.get("top_stocks") or [], 1):
            if item.get("code") == code:
                self_rank = i
                break
        out[code] = {
            "industry": industry,
            "pct_today": ov.get("pct_today"),
            "leader_name": ov.get("leader_name") or "",
            "leader_pct": ov.get("leader_pct"),
            "self_pct": self_pct,
            "self_rank": self_rank,
        }
    return out


def _empty_strength(stock: dict) -> dict:
    return {
        "industry": stock.get("industry") or "",
        "pct_today": None, "leader_name": "", "leader_pct": None,
        "self_pct": stock.get("pct_change"), "self_rank": None,
    }


@router.get("/ranking")
async def sector_ranking(
    user: Annotated[dict, Depends(get_current_user)],
    top_n: int = Query(100, ge=1, le=100, description="返回前 N 个行业板块, 默认全量100"),
):
    """全市场行业板块当日涨幅榜 (东财式热力图数据源).

    返回按涨幅降序的行业板块列表: [{rank, industry, bk_code, pct_today}].
    复用 fetcher.sectors.get_sector_ranking 内 60s 缓存 + DB stale fallback.
    前端拿全量后既可铺热力图, 也可切出领涨/领跌两端.
    """
    try:
        ranking = await data_fetcher.get_sector_ranking(top_n=top_n)
    except Exception as e:
        logger.warning(f"[sector_ranking] 取数失败: {e}")
        ranking = []
    up = sum(1 for r in ranking if (r.get("pct_today") or 0) > 0)
    down = sum(1 for r in ranking if (r.get("pct_today") or 0) < 0)
    return {"ranking": ranking, "up_count": up, "down_count": down, "total": len(ranking)}
