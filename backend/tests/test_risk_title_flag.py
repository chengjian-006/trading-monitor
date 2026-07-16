# -*- coding: utf-8 -*-
"""大盘风险标题标记 notifier._with_risk_flag 单测 (v1.7.627)。

大盘风险(谨慎/空仓)生效期间, 所有推送标题前挂醒目风险标;
市场风险状态卡/大盘风控卡本身不挂(它们就是在宣布这件事)。
"""
import asyncio

from backend.services import notifier, market_risk_controller


def _run(coro):
    return asyncio.run(coro)


def _set_state(monkeypatch, state: str):
    async def _fake():
        return state
    monkeypatch.setattr(market_risk_controller, "get_risk_state", _fake)


def test_red_prefixes_all_titles(monkeypatch):
    _set_state(monkeypatch, "RED")
    assert _run(notifier._with_risk_flag("📈 买入 · [二波过前高]")) == "🚨大盘空仓中🚨 📈 买入 · [二波过前高]"
    assert _run(notifier._with_risk_flag("🔔 自定义预警")).startswith("🚨大盘空仓中🚨")


def test_yellow_prefixes(monkeypatch):
    _set_state(monkeypatch, "YELLOW")
    assert _run(notifier._with_risk_flag("📊 盘面播报")) == "⚠️大盘谨慎中 📊 盘面播报"


def test_green_untouched(monkeypatch):
    _set_state(monkeypatch, "GREEN")
    assert _run(notifier._with_risk_flag("📈 买入 · [X]")) == "📈 买入 · [X]"


def test_risk_cards_themselves_not_prefixed(monkeypatch):
    _set_state(monkeypatch, "RED")
    assert _run(notifier._with_risk_flag("🔴 市场风险 · 升到「空仓」档")) == "🔴 市场风险 · 升到「空仓」档"
    assert _run(notifier._with_risk_flag("📛 大盘风控·退潮提示")) == "📛 大盘风控·退潮提示"


def test_state_fetch_failure_falls_back_to_plain(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(market_risk_controller, "get_risk_state", _boom)
    assert _run(notifier._with_risk_flag("📈 买入 · [X]")) == "📈 买入 · [X]"
