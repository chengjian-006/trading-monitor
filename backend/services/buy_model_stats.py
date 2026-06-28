"""买点模型战绩聚合 — 给盘中买点推送显示「胜率 + 单笔平均收益%」(近3月/近6月)。

数据源: 全市场回测(cfzy_biz_model_winrate, 每日收盘由 model_winrate_refresher 重算)。
口径: 各模型自身真实出场 + 扣费0.30%; 胜率=单笔净收益>0占比; 单笔均收益=净收益均值。
仅 5 个进回测的模型有数(竞价高开弱转强/板块预警 无)。带1小时缓存(日级数据, 不拖慢盘中推送)。
"""
import logging
import time as _time

from backend.models import repository

logger = logging.getLogger(__name__)

_CACHE_TTL = 3600
_cache: dict = {"data": None, "_ts": 0.0}


async def get_buy_model_stats() -> dict:
    """{signal_id: {model_name, win_rate_3m, net_3m, n_3m, win_rate_6m, net_6m, n_6m}}。
    全市场回测 近3月/近6月 胜率+单笔均收益。带1小时缓存。"""
    now = _time.time()
    if _cache["data"] is not None and now - _cache["_ts"] < _CACHE_TTL:
        return _cache["data"]
    try:
        data = await repository.get_model_winrate()
    except Exception as e:
        logger.warning(f"[buy_model_stats] 读模型胜率失败: {e}")
        return _cache["data"] or {}
    _cache["data"] = data
    _cache["_ts"] = now
    return data
