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


def test_risk_deco_banner(monkeypatch):
    # v1.7.629: 除标题前缀外, 正文顶部再插一条红色加粗大横幅
    _set_state(monkeypatch, "RED")
    title, banner = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert title.startswith("🚨大盘空仓中🚨")
    assert "大盘空仓中" in banner and "<font color='red'>" in banner and "**" in banner

    _set_state(monkeypatch, "YELLOW")
    _, banner_y = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert "大盘谨慎中" in banner_y and "orange" in banner_y

    _set_state(monkeypatch, "GREEN")
    t, b = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert t == "📈 买入 · [X]" and b == ""

    # 风险卡自身既不加前缀也不加横幅
    _set_state(monkeypatch, "RED")
    t2, b2 = _run(notifier._risk_deco("🔴 市场风险 · 升到「空仓」档"))
    assert t2 == "🔴 市场风险 · 升到「空仓」档" and b2 == ""
