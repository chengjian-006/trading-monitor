# -*- coding: utf-8 -*-
"""短线情绪温度分 + 四阶段 纯函数测试 (不连库不跑子进程)."""
from backend.services import emotion_refresher as E


def test_score_hot_day_high():
    # 高潮日: 溢价+5 封板0.9 最高7板 涨停90 炸板0.1 放量+30 → 应接近满分
    s = E._emotion_score(premium=5.0, seal_rate=0.9, highest=7, limit_up=90, zha_rate=0.1, vol_ratio=30.0)
    assert s >= 90, s


def test_score_cold_day_low():
    # 冰点日: 溢价-4 封板0.4 最高2板 涨停10 炸板0.5 缩量-25 → 应接近 0
    s = E._emotion_score(premium=-4.0, seal_rate=0.4, highest=2, limit_up=10, zha_rate=0.5, vol_ratio=-25.0)
    assert s <= 20, s


def test_score_renormalizes_on_missing():
    # 数据降级: 只有涨停家数一项(其余 None) → 仍出分(在该项上归一)
    s = E._emotion_score(premium=None, seal_rate=None, highest=None, limit_up=80, zha_rate=None, vol_ratio=None)
    assert s == 100, s
    # 全 None → None
    assert E._emotion_score(None, None, None, None, None, None) is None


def test_factor_normalization_bounds():
    f = E._score_factors(premium=99, seal_rate=2.0, highest=99, limit_up=999, zha_rate=-1, vol_ratio=999)
    assert all(v == 100.0 for v in f.values())          # 全部封顶 100
    f2 = E._score_factors(premium=-99, seal_rate=0.0, highest=1, limit_up=0, zha_rate=1.0, vol_ratio=-99)
    assert all(v == 0.0 for v in f2.values())           # 全部封底 0
    # 量能 0%(持平昨日) → 中位 50
    assert E._score_factors(0, None, None, None, None, 0.0)["vol"] == 50.0


def test_derive_cycle_bands_and_ebb_override():
    # 退潮判据优先(即便温度分不低): phase=退潮 → 退潮
    assert E._derive_cycle(70, "退潮") == "退潮"
    # 高潮 / 冰点 / 回暖 按分档
    assert E._derive_cycle(80, "高潮") == "高潮"
    assert E._derive_cycle(65, "中性") == "高潮"      # 边界含
    assert E._derive_cycle(20, "冰点") == "冰点"
    assert E._derive_cycle(30, "中性") == "冰点"      # 边界含
    assert E._derive_cycle(45, "中性") == "回暖"
    assert E._derive_cycle(None, "中性") is None


def test_cycle_alert_spec_transitions():
    # 有动作意义的转场 → 出卡规格
    assert E._cycle_alert_spec("冰点", "回暖")[0].endswith("启动")       # 回暖启动
    assert E._cycle_alert_spec("回暖", "高潮")[4] == ("高潮", "red")     # 进高潮
    assert E._cycle_alert_spec("冰点", "高潮") is not None               # 直接跳高潮也提醒
    assert E._cycle_alert_spec("高潮", "退潮")[4] == ("退潮", "green")   # 退潮减仓
    assert E._cycle_alert_spec("回暖", "退潮") is not None
    # 无动作意义/反向转场 → 不提醒
    assert E._cycle_alert_spec("冰点", "退潮") is None                   # 本就冷, 退潮不再提醒
    assert E._cycle_alert_spec("高潮", "回暖") is None                   # 单纯降温不提醒(留给退潮)
    assert E._cycle_alert_spec("回暖", "冰点") is None
    assert E._cycle_alert_spec("高潮", "高潮") is None
