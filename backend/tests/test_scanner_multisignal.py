"""多强档信号合并推送正文 _build_multi_signal_body 单测(基线 v1.1 五区骨架文本)。

取舍背景: 该卡保留 send_wechat_text 文本通道(mute_lark 静音语义无法迁到 send_card),
只把正文升级为 结论行 → 明细 → 👉建议 的骨架, 排版规范(数字加粗/涨跌带色/正文无时间)同结构卡。
"""
import re
from types import SimpleNamespace


def _sig(name, direction, sid="BUY_X"):
    return SimpleNamespace(signal_name=name, direction=direction, signal_id=sid)


def test_multi_buy_skeleton():
    from backend.services.scanner import _build_multi_signal_body
    items = [(_sig("缩量后放量突破", "buy", "BUY_VOL_BREAKOUT"), "突破昨高×1.02 | 放量2.1×"),
             (_sig("回踩10MA突破", "buy", "BUY_S3"), "回踩企稳")]
    body = _build_multi_signal_body(
        items, name="拓普集团", code="601689", price=58.39, stock_pct=2.7,
        amount_suffix=" | 成交额 3.2亿", strategy="", stats_map={})
    lines = body.splitlines()
    # 结论行: 事件+标的+关键数(现价加粗/涨跌 pct_md 带色)
    assert lines[0].startswith("▲ **多买点叠加 · 拓普集团(601689)**")
    assert "**58.39**" in lines[0] and "+2.7%" in lines[0] and "red" in lines[0]
    # 明细区: 信号名加粗, 触发依据逐条(旧 ┌─│└─ 引用块边框已弃)
    assert "▲ **缩量后放量突破**" in body
    assert "　· 突破昨高×1.02" in body and "　· 放量2.1×" in body
    assert "┌─" not in body and "│" not in body and "└─" not in body
    # 建议区收尾; 正文不写时间(标题栏已带)
    assert body.rstrip().endswith("👉 **多买点共振，按最强模型跟进**")
    assert not re.search(r"\d{1,2}:\d{2}", body)


def test_sell_mix_strategy_and_risk_line():
    from backend.services.scanner import _build_multi_signal_body
    items = [(_sig("破位止损", "sell", "SELL_STOP"), "跌破止损位 9.80"),
             (_sig("缩量后放量突破", "buy", "BUY_VOL_BREAKOUT"), "突破昨高")]
    body = _build_multi_signal_body(
        items, name="甲", code="600000", price=10.0, stock_pct=-1.8,
        amount_suffix="", strategy="按既定计划执行", stats_map={},
        risk_line="⚠️ 市场风险·空仓预警(RED)")
    lines = body.splitlines()
    assert lines[0].startswith("▼ **多信号触发 · 甲(600000)**")
    assert "-1.8%" in lines[0] and "green" in lines[0]     # 跌=绿(pct_md)
    assert lines[1] == "⚠️ 市场风险·空仓预警(RED)"          # 风险标记紧跟结论行
    # 有操作策略时建议区用策略原文
    assert body.rstrip().endswith("👉 **按既定计划执行**")


def test_advice_fallback_by_direction():
    from backend.services.scanner import _build_multi_signal_body
    sell_only = [(_sig("破位止损", "sell", "SELL_STOP"), "a"),
                 (_sig("止盈提醒", "sell", "SELL_TP"), "b")]
    body = _build_multi_signal_body(
        sell_only, name="甲", code="600000", price=10.0, stock_pct=0.0,
        amount_suffix="", strategy="", stats_map={})
    assert body.rstrip().endswith("👉 **多卖点共振，优先核实离场**")
    mixed = [(_sig("破位止损", "sell", "SELL_STOP"), "a"),
             (_sig("突破", "buy", "BUY_X"), "b")]
    body2 = _build_multi_signal_body(
        mixed, name="甲", code="600000", price=10.0, stock_pct=0.0,
        amount_suffix="", strategy="", stats_map={})
    assert body2.rstrip().endswith("👉 **买卖信号并存，先核实卖点再动手**")
