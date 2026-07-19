# -*- coding: utf-8 -*-
"""买点推送的风险档警示行 (v1.7.686) — 纯函数, 不连库.

背景: 原文案写死「回测期内信号胜率30%均值-3.6%」, 数字来自带前视偏差的旧回测脚本;
YELLOW 还写着「信号质量未显著下降」。经 bt_risk_baseline_redo.py 独立样本复核,
两处都不实, 已改为按 OOS 实测取数并按模型分流。
"""
from backend.services.market_risk_controller import GREEN, RED, YELLOW, risk_buy_note


def test_green_adds_no_line():
    assert risk_buy_note(GREEN, "BUY_RALLY_MA10") == ""
    assert risk_buy_note("", "") == ""


def test_red_platform_breakout_gets_strongest_warning():
    """平台突破是 RED 档唯一双段同向垫底的模型, 要单独强警示。

    v1.7.711 起具体数字搬进登记表 cfzy_sys_backtest_claims(硬编码结论会过期且没人发现),
    兜底文案只留定性 —— 故这里断言"强警示措辞"而非具体数值; 数值链路由
    test_backtest_claims.py 覆盖(登记表命中时用表里的文案)。
    """
    note = risk_buy_note(RED, "BUY_PLATFORM_BREAKOUT")
    assert "强烈建议不做" in note and "最脆" in note


def test_red_neutral_models_get_softer_wording():
    """回踩MA10/MA60 在 RED 档两段都接近打平, 不该套用"停开新仓"的通用强警示。"""
    for sid in ("BUY_RALLY_MA10", "BUY_RALLY_MA60"):
        note = risk_buy_note(RED, sid)
        assert "接近打平" in note and "轻仓" in note
        assert "停开新仓" not in note


def test_red_generic_gives_stop_advice():
    note = risk_buy_note(RED, "BUY_VOL_BREAKOUT")
    assert "停开新仓" in note and "空仓档" in note


def test_yellow_no_longer_claims_quality_undamaged():
    """旧文案说 YELLOW「信号质量未显著下降」; 实测 -1.8% vs 正常档 -0.5%, 是显著下降。

    这条断言的是"不再说反话", 与数字搬不搬家无关, 必须一直守住。
    """
    note = risk_buy_note(YELLOW, "BUY_RALLY_MA20")
    assert "控制仓位" in note and "弱于正常档" in note
    assert "未显著下降" not in note


def test_no_stale_numbers_anywhere():
    """旧的 30%/-3.6% 不能再出现在任何一档文案里。"""
    for state in (GREEN, YELLOW, RED):
        for sid in ("", "BUY_PLATFORM_BREAKOUT", "BUY_RALLY_MA10", "BUY_VOL_BREAKOUT"):
            note = risk_buy_note(state, sid)
            assert "30%" not in note and "-3.6%" not in note


def test_unknown_signal_id_falls_back_to_generic():
    assert risk_buy_note(RED, "BUY_SOMETHING_NEW") == risk_buy_note(RED, "")


def test_is_risk_active_removed():
    """is_risk_active 从无调用方却长得像抑制开关, 已删除以免再被误读为安全闸门。"""
    from backend.services import market_risk_controller as mrc
    assert not hasattr(mrc, "is_risk_active")
