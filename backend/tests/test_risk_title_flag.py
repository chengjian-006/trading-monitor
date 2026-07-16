# -*- coding: utf-8 -*-
"""大盘风险标题标记 notifier._with_risk_flag 单测 (v1.7.627)。

大盘风险(谨慎/空仓)生效期间, 所有推送标题前挂醒目风险标;
市场风险状态卡/大盘风控卡本身不挂(它们就是在宣布这件事)。
"""
import asyncio

from backend.services import notifier, market_risk_controller


def _run(coro):
    return asyncio.run(coro)


def _set_state(monkeypatch, state: str, since: str = "13:11"):
    async def _fake():
        return state, (since if state != "GREEN" else "")
    monkeypatch.setattr(market_risk_controller, "get_risk_state_info", _fake)


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
    monkeypatch.setattr(market_risk_controller, "get_risk_state_info", _boom)
    assert _run(notifier._with_risk_flag("📈 买入 · [X]")) == "📈 买入 · [X]"


def test_risk_deco_banner(monkeypatch):
    # v1.7.629: 除标题前缀外, 正文顶部再插一条红色加粗大横幅; v1.7.630 带时间锚点
    _set_state(monkeypatch, "RED", since="13:11")
    title, banner = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert title.startswith("🚨大盘空仓中🚨")
    assert "大盘空仓中（13:11起）" in banner and "<font color='red'>" in banner and "**" in banner

    _set_state(monkeypatch, "YELLOW")
    _, banner_y = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert "大盘谨慎中（13:11起）" in banner_y and "orange" in banner_y

    # 锚点缺失(取不到 updated_at): 横幅仍发, 只是没有「几点起」
    _set_state(monkeypatch, "RED", since="")
    _, banner_ns = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert "大盘空仓中 ——" in banner_ns and "起）" not in banner_ns

    _set_state(monkeypatch, "GREEN")
    t, b = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert t == "📈 买入 · [X]" and b == ""

    # 风险卡自身既不加前缀也不加横幅
    _set_state(monkeypatch, "RED")
    t2, b2 = _run(notifier._risk_deco("🔴 市场风险 · 升到「空仓」档"))
    assert t2 == "🔴 市场风险 · 升到「空仓」档" and b2 == ""


def test_get_risk_state_info_label(monkeypatch):
    # 控制器侧: 今日锚点=HH:MM, 往日锚点=M月D日 HH:MM, GREEN 无锚点
    import time as _time
    from datetime import datetime, timedelta

    async def _noop():
        return None
    monkeypatch.setattr(market_risk_controller, "_refresh_active_cache", _noop)

    now = datetime.now()
    market_risk_controller._active_cache = (_time.monotonic(), "RED", now.replace(hour=13, minute=11))
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "RED" and label == "13:11"

    yesterday = now - timedelta(days=1)
    market_risk_controller._active_cache = (_time.monotonic(), "YELLOW", yesterday.replace(hour=16, minute=40))
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "YELLOW" and label == f"{yesterday.month}月{yesterday.day}日 16:40"

    market_risk_controller._active_cache = (_time.monotonic(), "GREEN", now)
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "GREEN" and label == ""
    market_risk_controller._invalidate_cache()
