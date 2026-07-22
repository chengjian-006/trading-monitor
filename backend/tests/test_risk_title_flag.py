# -*- coding: utf-8 -*-
"""大盘风险标记 notifier 单测 (v1.7.627; v1.7.652 标题前缀改 header 小标签)。

大盘风险(谨慎/空仓)生效期间, 非风险卡做两处标记: header 小标签(_risk_tag) + 正文横幅(_risk_deco);
标题不再前缀风险标(不盖卡片主信息)。市场风险状态卡/大盘风控卡本身不挂(它们就是在宣布这件事)。
"""
import asyncio

from backend.services import notifier, market_risk_controller


def _run(coro):
    return asyncio.run(coro)


def _set_state(monkeypatch, state: str, since: str = "13:11"):
    async def _fake():
        return state, (since if state != "GREEN" else "")
    monkeypatch.setattr(market_risk_controller, "get_risk_state_info", _fake)


def test_red_gives_tag_not_title_prefix(monkeypatch):
    # v1.7.652: header 小标签, 标题保持原样不加前缀; v1.7.752 retier: 档名空仓→危险
    _set_state(monkeypatch, "RED")
    assert _run(notifier._risk_tag("📈 买入 · [二波过前高]")) == ("大盘危险中", "red")
    assert _run(notifier._with_risk_flag("📈 买入 · [二波过前高]")) == "📈 买入 · [二波过前高]"


def test_yellow_gives_tag(monkeypatch):
    _set_state(monkeypatch, "YELLOW")
    assert _run(notifier._risk_tag("📊 盘面播报")) == ("大盘谨慎", "orange")
    assert _run(notifier._with_risk_flag("📊 盘面播报")) == "📊 盘面播报"


def test_green_gives_normal_tag(monkeypatch):
    # v1.7.740 (Deploy 2A): 三档统一戳 —— 正常档也挂「大盘正常/green」小标签(此前 GREEN 不挂)。
    # 正文横幅仍只在谨慎/空仓档出现(_with_risk_flag/_risk_deco 不变)。
    _set_state(monkeypatch, "GREEN")
    assert _run(notifier._risk_tag("📈 买入 · [X]")) == ("大盘正常", "green")
    assert _run(notifier._with_risk_flag("📈 买入 · [X]")) == "📈 买入 · [X]"


def test_risk_cards_themselves_no_tag(monkeypatch):
    _set_state(monkeypatch, "RED")
    assert _run(notifier._risk_tag("🔴 市场风险 · 升到「危险」档")) is None
    assert _run(notifier._risk_tag("📛 大盘风控·XX提示")) is None


def test_state_fetch_failure_no_tag(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(market_risk_controller, "get_risk_state_info", _boom)
    assert _run(notifier._risk_tag("📈 买入 · [X]")) is None
    assert _run(notifier._with_risk_flag("📈 买入 · [X]")) == "📈 买入 · [X]"


def test_risk_deco_banner(monkeypatch):
    # 标题不再前缀(v1.7.652), 但正文顶部横幅照旧(带时间锚点)
    _set_state(monkeypatch, "RED", since="13:11")
    title, banner = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert title == "📈 买入 · [X]"            # 标题原样, 不加前缀
    assert "大盘危险中（13:11起）" in banner and "<font color='red'>" in banner and "**" in banner

    _set_state(monkeypatch, "YELLOW")
    _, banner_y = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert "大盘谨慎中（13:11起）" in banner_y and "orange" in banner_y

    # 锚点缺失(取不到 updated_at): 横幅仍发, 只是没有「几点起」
    _set_state(monkeypatch, "RED", since="")
    _, banner_ns = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert "大盘危险中 ·" in banner_ns and "停开新仓" in banner_ns and "起）" not in banner_ns

    _set_state(monkeypatch, "GREEN")
    t, b = _run(notifier._risk_deco("📈 买入 · [X]"))
    assert t == "📈 买入 · [X]" and b == ""

    # 风险卡自身既不加前缀也不加横幅
    _set_state(monkeypatch, "RED")
    t2, b2 = _run(notifier._risk_deco("🔴 市场风险 · 升到「危险」档"))
    assert t2 == "🔴 市场风险 · 升到「危险」档" and b2 == ""


def test_get_risk_state_info_label(monkeypatch):
    # 控制器侧: 今日锚点=HH:MM, 往日锚点=M月D日 HH:MM, GREEN 无锚点
    import time as _time
    from datetime import datetime, timedelta

    async def _noop():
        return None
    monkeypatch.setattr(market_risk_controller, "_refresh_active_cache", _noop)

    now = datetime.now()
    market_risk_controller._active_cache = (_time.monotonic(), "RED", now.replace(hour=13, minute=11), 1)
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "RED" and label == "13:11"

    yesterday = now - timedelta(days=1)
    market_risk_controller._active_cache = (
        _time.monotonic(), "YELLOW", yesterday.replace(hour=16, minute=40), 3)
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "YELLOW" and label == f"{yesterday.month}月{yesterday.day}日 16:40"
    assert _run(market_risk_controller.get_risk_streak_days()) == 3

    market_risk_controller._active_cache = (_time.monotonic(), "GREEN", now, 5)
    st, label = _run(market_risk_controller.get_risk_state_info())
    assert st == "GREEN" and label == ""
    market_risk_controller._invalidate_cache()
