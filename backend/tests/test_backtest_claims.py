# -*- coding: utf-8 -*-
"""回测结论登记表 (v1.7.711) — 纯函数与降级行为, 不连库.

要守住的核心性质: **登记表不可用时, 推送不能变哑**。这类"为了正确性反而把功能搞没"
的退化, 比数字过期更严重 —— 0719 的 is_risk_active 就是反例(看着像安全闸, 实际零调用)。
"""
import asyncio

import pytest

from backend.services import backtest_claims as bc
from backend.services.market_risk_controller import (
    GREEN, RED, YELLOW, risk_buy_note, risk_buy_note_async,
)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    bc.invalidate()
    yield
    bc.invalidate()


# ── 降级: 查不到 / 读库炸了, 都要回退到兜底文案而非空串 ──

def test_text_of_falls_back_when_missing(monkeypatch):
    async def _empty():
        return {}
    monkeypatch.setattr(bc, "_load", _empty)
    assert _run(bc.text_of("不存在的key", "兜底文案")) == "兜底文案"


def test_text_of_falls_back_when_db_dies(monkeypatch):
    async def _boom():
        raise RuntimeError("DB down")
    monkeypatch.setattr(bc, "_load", _boom)
    with pytest.raises(RuntimeError):
        _run(bc.text_of("k", "兜底"))     # text_of 本身不吞异常


def test_risk_note_never_goes_silent_on_db_failure(monkeypatch):
    """登记表炸了, 风险警示行仍必须有内容 —— 这是最关键的一条。"""
    async def _boom(*a, **k):
        raise RuntimeError("DB down")
    monkeypatch.setattr(bc, "text_of", _boom)
    for state in (RED, YELLOW):
        note = _run(risk_buy_note_async(state, "BUY_VOL_BREAKOUT"))
        assert note, f"{state} 档警示行不能为空"
        assert "大盘" in note


def test_registry_value_used_when_present(monkeypatch):
    async def _load():
        return {"risk_note_red": {"claim_key": "risk_note_red", "text": "来自登记表的文案"}}
    monkeypatch.setattr(bc, "_load", _load)
    assert _run(risk_buy_note_async(RED, "BUY_VOL_BREAKOUT")) == "来自登记表的文案"


# ── 分流仍然生效(v1.7.686 的模型分流不能被登记表改造搞丢) ──

def test_model_routing_picks_distinct_keys():
    from backend.services.market_risk_controller import _note_slot
    assert _note_slot(RED, "BUY_PLATFORM_BREAKOUT")[0] == "risk_note_red_fragile"
    assert _note_slot(RED, "BUY_RALLY_MA10")[0] == "risk_note_red_neutral"
    assert _note_slot(RED, "BUY_RALLY_MA60")[0] == "risk_note_red_neutral"
    assert _note_slot(RED, "BUY_VOL_BREAKOUT")[0] == "risk_note_red"
    assert _note_slot(YELLOW, "任意")[0] == "risk_note_yellow"
    assert _note_slot(GREEN, "任意") is None


def test_green_adds_no_note():
    assert _run(risk_buy_note_async(GREEN, "BUY_RALLY_MA10")) == ""
    assert risk_buy_note(GREEN, "BUY_RALLY_MA10") == ""


# ── 兜底文案本身不许再写死数字(否则等于换了个地方硬编码) ──

def test_fallback_texts_carry_no_hardcoded_stats():
    from backend.services.market_risk_controller import _NOTE_KEYS
    import re
    bad = re.compile(r"胜率\s*\d|PF\s*[\d.]|均\s*-?\d+\.\d+%")
    for (_state, _slot), (key, fallback) in _NOTE_KEYS.items():
        assert not bad.search(fallback), f"{key} 的兜底文案里写死了数字: {fallback}"
