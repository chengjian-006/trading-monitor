# -*- coding: utf-8 -*-
"""市场风险状态机 + 历史指标口径修复 (v1.7.570) + 基线 v1.1 构卡(退潮提示卡/解除卡)。
纯函数与 mock 推送, 不连库不联网。"""
from unittest.mock import AsyncMock

from backend.services import market_risk_controller as mrc
from backend.services.market_risk_controller import _run_state_machine, _hist_indicators


# ── RED→YELLOW 降级(补推送分支的前提: 该迁移确实会发生) ──

def test_state_machine_red_to_yellow_on_recovery():
    """RED 态下广度>25%、涨跌比>40%、5日均收益>0 → 降级 YELLOW(触发新的降级卡)。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 50.0}
    assert _run_state_machine("RED", today, breadth=50.0) == "YELLOW"


def test_state_machine_red_stays_red_when_still_weak():
    today = {"advance_ratio": 30.0, "avg_ret_ma5": -1.5, "low52_ratio": 20.0,
             "zha_rate": 40.0, "breadth_ma20": 18.0}
    assert _run_state_machine("RED", today, breadth=18.0) == "RED"


def test_state_machine_red_needs_all_three_to_exit():
    """只满足部分回暖条件(涨跌比够但广度不够)仍留 RED。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 20.0}
    assert _run_state_machine("RED", today, breadth=20.0) == "RED"   # 广度20<25


# ── 历史指标: 脏 low=0 行不再炸整轮 EOD(v1.7.570 crash guard) ──

def test_hist_indicators_no_crash_on_zero_low_rows():
    """某票 low 全为 0(脏数据)时, 原 min([]) 抛 ValueError 会炸掉整轮评估; 修复后安全跳过。"""
    rows = [
        ("000001", "2026-06-10", 10.0, 10.5, 0.0),
        ("000001", "2026-06-11", 10.2, 10.6, 0.0),
        ("000001", "2026-06-12", 10.1, 10.3, 0.0),
    ]
    out = _hist_indicators(rows, need_days=6)   # 不应抛异常
    assert isinstance(out, list)                # 小样本(n<1000)输出为空, 但不炸


# ── 大盘风控·退潮提示卡(基线 v1.1: risk 家族橙/状态红 + heading结论 + ✅维度列举 + 👉建议) ──

async def test_emit_risk_dimension_builds_risk_card(monkeypatch):
    from backend.services import notifier
    mrc._ebb_emit.clear()
    monkeypatch.setattr(mrc, "get_risk_state", AsyncMock(return_value=mrc.YELLOW))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(notifier, "send_card", sent)
    desc = "✅ 涨停家数骤降：今 **30** 家 ← 昨 **80** 家"
    await mrc.emit_risk_dimension("退潮", desc, "整板在抽血，强势股先走一部分")
    assert sent.await_count == 1
    card = sent.await_args[0][0]
    assert card.title == "📛 大盘风控·退潮提示"
    assert card.family == "risk" and card.template == "orange"   # 谨慎档 → 橙(非旧默认蓝)
    assert card.tags == [("谨慎", "orange")]
    assert "大盘风控" in card.summary and "退潮" in card.summary
    assert card.elements[0]["text_size"] == "heading"            # 结论 heading 行
    assert "涨停家数骤降" in card.elements[1]["content"]          # ✅维度列举
    assert "👉 **整板在抽血，强势股先走一部分**" in card.elements[2]["content"]
    assert "涨停家数骤降" in card.fallback and "👉" in card.fallback

    # 同维度重复 → 当日去重不再推
    await mrc.emit_risk_dimension("退潮", desc, "整板在抽血，强势股先走一部分")
    assert sent.await_count == 1

    # 新增维度 → 合并两项再推一张; 状态 RED 时红卡(risk_hot)
    monkeypatch.setattr(mrc, "get_risk_state", AsyncMock(return_value=mrc.RED))
    await mrc.emit_risk_dimension("溢价", "✅ 溢价转负 **-0.77%**", "别追高，手中高位股谨慎")
    assert sent.await_count == 2
    card2 = sent.await_args[0][0]
    assert "（2项）" in card2.title
    assert card2.family == "risk_hot" and card2.template == "red"
    assert "涨停家数骤降" in card2.elements[1]["content"]
    assert "溢价转负" in card2.elements[1]["content"]
    assert "整板在抽血" in card2.elements[2]["content"] and "别追高" in card2.elements[2]["content"]
    mrc._ebb_emit.clear()


# ── EOD 恢复正常 → 解除卡(基线 v1.1 标准型: 灰header + 副标题时间线 + 解除条件 + 👉建议) ──

async def test_eod_green_recovery_sends_dismiss_card(monkeypatch):
    cur = {"date": "2026-07-16", "advance_ratio": 55.0, "avg_ret_ma5": 0.4,
           "low52_ratio": 2.0, "zha_rate": 10.0, "adv": 3000, "dec": 1500}
    monkeypatch.setattr("backend.core.trading_calendar.is_workday", lambda: True)
    monkeypatch.setattr(mrc, "_gather_metrics", AsyncMock(return_value=([], cur, 45.0)))
    monkeypatch.setattr(mrc, "_get_prev_state", AsyncMock(return_value=mrc.YELLOW))
    monkeypatch.setattr(mrc, "_get_row", AsyncMock(return_value=None))
    monkeypatch.setattr(mrc, "_upsert_risk", AsyncMock())
    monkeypatch.setattr(mrc, "_nongreen_streak", AsyncMock(return_value=("7月8日", 6)))
    state_card = AsyncMock()
    monkeypatch.setattr(mrc, "_push_state_card", state_card)
    dismiss = AsyncMock()
    monkeypatch.setattr(mrc, "_push_dismiss", dismiss)

    await mrc.market_risk_eod()   # YELLOW + 55%/45%/+0.4 → GREEN

    assert state_card.await_count == 0 and dismiss.await_count == 1
    card = dismiss.await_args[0][0]
    assert card.title == "✅ 预警解除 · 市场风险预警"
    assert card.template == "grey"                               # 灰 = 中性收尾
    assert card.subtitle == "7月8日 发布 → 今日解除，生效 6 个交易日"   # 时间线
    cond = card.elements[0]["content"]
    assert "解除条件" in cond and "**55%**" in cond and "≥ 42%" in cond   # 写明解除条件+实测值加粗
    assert "👉" in card.elements[-1]["content"]
