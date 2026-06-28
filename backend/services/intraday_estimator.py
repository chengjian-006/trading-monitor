"""盘中成交量虚拟预估工具。

A股交易时段 9:30-11:30 + 13:00-15:00 = 4 小时 = 240 分钟。
各时段成交量分布不均：开盘活跃、午前减弱、午后回升、尾盘集合放量。

用时间系数（累计成交量占全天比例）反推全天预估量：
    预估全天量 = 当前累计成交量 ÷ 该时点累计占比
"""

from datetime import datetime, time as dtime
from bisect import bisect_left


# (hh*60+mm, 该时点累计成交占全天比例)
# 标定来源(v1.7.518): 全市场 5 分钟真实成交额(cfzy_sys_kline_5m)最近 30 个交易日
# 实测均值, 由 backend/scripts/diag_turnover_coef.py 反算。
# 旧版为手填经验值, 早盘系数明显偏低(10:00 填 0.24 实测 0.328), 导致开盘累计额
# ÷ 偏小系数 把全天外推放大约 1.4 倍(早盘越猛越离谱); 现整表换实测值。
# 注: 实测显示早盘占比与当天总量无关(高/低量日 10:00 占比都≈0.33), U 型形状稳定,
#     固定系数表成立, 无需按热度分档。
_TIME_COEF_TABLE = [
    (9 * 60 + 30, 0.000),   # 9:30 开盘
    (9 * 60 + 45, 0.220),   # 首15min最重(集合竞价后井喷, 实测占22%)
    (10 * 60 +  0, 0.328),  # 首30min实测~33%, A股开盘高度前置
    (10 * 60 + 15, 0.409),
    (10 * 60 + 30, 0.469),
    (10 * 60 + 45, 0.521),
    (11 * 60 +  0, 0.564),
    (11 * 60 + 15, 0.603),
    (11 * 60 + 30, 0.639),   # 11:30 午休前(上午实测~64%)
    (13 * 60 +  0, 0.639),   # 13:00 午后开盘（午休累计不变）
    (13 * 60 + 15, 0.694),
    (13 * 60 + 30, 0.736),
    (13 * 60 + 45, 0.776),
    (14 * 60 +  0, 0.813),
    (14 * 60 + 15, 0.851),
    (14 * 60 + 30, 0.888),
    (14 * 60 + 45, 0.935),   # 尾盘前
    (14 * 60 + 57, 0.978),   # 收盘集合竞价前
    (15 * 60 +  0, 1.000),   # 收盘(末段集合竞价)
]
_TIME_KEYS = [k for k, _ in _TIME_COEF_TABLE]


def _interp_coef(minutes_in_day: int) -> float:
    """根据"自零点起的分钟数"线性插值取累计成交占比。"""
    if minutes_in_day <= _TIME_KEYS[0]:
        return 0.0
    if minutes_in_day >= _TIME_KEYS[-1]:
        return 1.0
    pos = bisect_left(_TIME_KEYS, minutes_in_day)
    k1, v1 = _TIME_COEF_TABLE[pos - 1]
    k2, v2 = _TIME_COEF_TABLE[pos]
    if k2 == k1:
        return v1
    return v1 + (v2 - v1) * (minutes_in_day - k1) / (k2 - k1)


def project_full_day_amount(intraday_amount: float, now: datetime | None = None) -> float | None:
    """根据当前累计成交额(元)与时点外推全天预估成交额。

    与 project_full_day_volume 用同一时点系数表; 仅参数语义不同。
    非交易时段或开盘前返回 None。
    """
    return project_full_day_volume(intraday_amount, now)


def project_full_day_volume(intraday_volume: float, now: datetime | None = None) -> float | None:
    """根据当前累计成交量与时点，外推全天预估成交量。

    Args:
        intraday_volume: 当前累计成交量（盘中实时）
        now: 当前时间，默认 datetime.now()

    Returns:
        预估全天成交量；非交易时段或开盘前返回 None
    """
    if intraday_volume is None or intraday_volume <= 0:
        return None
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute

    # 非交易时段
    if minutes < 9 * 60 + 30 or minutes > 15 * 60:
        return None
    # 午休时段（11:30-13:00）按 11:30 处理（实测~64% 已成交，剩~36% 未发生）
    if 11 * 60 + 30 < minutes < 13 * 60:
        minutes = 11 * 60 + 30

    coef = _interp_coef(minutes)
    if coef <= 0:
        return None
    if coef >= 1:
        return float(intraday_volume)
    return float(intraday_volume) / coef


def is_intraday(now: datetime | None = None) -> bool:
    """判断当前是否处于 A股交易时段（不含集合竞价前）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))
