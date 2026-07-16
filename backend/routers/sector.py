"""板块 API - v1.7.x.

v1.7.640: strength-batch(板块强弱, 给决策快查卡的逐票板块实时强度)已移除(用户拍板)——
东财板块接口对生产 IP 封禁, 每次调用都是池内全部行业(~44个)逐个失败的废请求
(2~3s 延迟 + "Industry BK map failed" 日志刷屏), 数据恒空打分层空转, 前后端整链砍掉。
保留 /ranking (行业板块涨幅榜热力图, 有腾讯备源+DB兜底, 工作正常)。
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend import data_fetcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sector", tags=["sector"])


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
