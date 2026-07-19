# -*- coding: utf-8 -*-
"""系统体检框架 (v1.7.698) — 纯函数与隔离性, 不连库不联网."""
import asyncio
from datetime import date, datetime

import pytest

from backend.services import health_checks as hc


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ── 时间基准: 阈值必须按交易日, 否则每周一全线误报 ──

def test_expected_data_date_weekend_uses_last_trading_day():
    """周日看核心表都是"最新=周五", 那是健康的。按自然日算会周一全线误报。"""
    sun = datetime(2026, 7, 19, 10, 0)          # 周日
    assert hc.expected_data_date(sun) == date(2026, 7, 17)   # 上周五


def test_expected_data_date_before_close_uses_prev_day():
    """交易日盘中(未收盘)不能要求已有今日数据。"""
    mon_open = datetime(2026, 7, 20, 10, 0)
    assert hc.expected_data_date(mon_open) == date(2026, 7, 17)


def test_expected_data_date_after_close_uses_today():
    mon_close = datetime(2026, 7, 20, 15, 30)
    assert hc.expected_data_date(mon_close) == date(2026, 7, 20)


# ── 日期解析 ──

@pytest.mark.parametrize("raw,want", [
    ("2026-07-17", date(2026, 7, 17)),
    (date(2026, 7, 17), date(2026, 7, 17)),
    (datetime(2026, 7, 17, 15, 0), date(2026, 7, 17)),
    ("20260717", date(2026, 7, 17)),        # limit_up_daily 存的是紧凑格式
    ("2026-07-17 15:00:00", date(2026, 7, 17)),
])
def test_as_date_formats(raw, want):
    assert hc._as_date(raw) == want


def test_as_date_garbage_returns_none():
    assert hc._as_date("not-a-date") is None
    assert hc._as_date(None) is None


# ── 逐项异常隔离: 检查器自己坏了必须能被看见, 且不能拖垮整轮 ──

def test_broken_check_becomes_failure_not_crash(monkeypatch):
    async def boom():
        raise RuntimeError("检查器自己炸了")

    async def fine():
        return True, "ok", "ok"

    monkeypatch.setattr(hc, "CHECKS", [
        hc.Check("boom", "会炸的项", "测试", hc.CRITICAL, boom),
        hc.Check("fine", "正常项", "测试", hc.WARN, fine),
    ])
    rs = _run(hc.run_checks())
    assert len(rs) == 2, "一项炸了不能少执行其它项"
    bad = [r for r in rs if not r.ok]
    assert len(bad) == 1 and bad[0].key == "boom"
    assert "检查项自身异常" in bad[0].actual
    assert "RuntimeError" in bad[0].actual


def test_hanging_check_times_out_and_is_reported(monkeypatch):
    """卡住的检查项要被超时并记为失败, 不能让整轮无限等。"""
    async def hang():
        await asyncio.sleep(999)

    monkeypatch.setattr(hc, "CHECKS", [
        hc.Check("hang", "卡住的项", "测试", hc.WARN, hang)])
    monkeypatch.setattr(hc.asyncio, "wait_for",
                        lambda c, timeout: asyncio.wait_for(c, timeout=0.05))
    rs = _run(hc.run_checks())
    assert len(rs) == 1 and not rs[0].ok
    assert "检查项自身异常" in rs[0].actual


def test_only_trading_day_checks_skipped_off_hours(monkeypatch):
    """非交易日跳过的项要记成"跳过"而非失败, 否则每个周末都满屏红。"""
    async def never():
        raise AssertionError("非交易日不该执行")

    monkeypatch.setattr(hc, "CHECKS", [
        hc.Check("t", "仅交易日", "测试", hc.CRITICAL, never, only_trading_day=True)])
    monkeypatch.setattr("backend.core.trading_calendar.is_workday", lambda *a, **k: False)
    rs = _run(hc.run_checks())
    assert len(rs) == 1 and rs[0].ok and "跳过" in rs[0].actual


# ── 报告 ──

def _r(key, ok, sev=hc.WARN):
    return hc.CheckResult(key, key, "测试", sev, ok, "实际", "期望")


_HB0 = {"last_push_at": None, "fail_streak": 0}


def test_report_all_pass():
    card, crit = hc.build_report_card([_r("a", True), _r("b", True)], _HB0)
    assert "全通过" in card.title and crit is False


def test_report_critical_first():
    card, crit = hc.build_report_card(
        [_r("warn1", False, hc.WARN), _r("crit1", False, hc.CRITICAL)], _HB0)
    assert crit is True and "故障" in card.title
    assert card.fallback.index("crit1") < card.fallback.index("warn1"), "严重项必须排前"


def test_report_flags_missing_checks(monkeypatch):
    """执行项数 < 注册项数 = 有检查项自身没跑起来, 必须在报告里点出来。"""
    monkeypatch.setattr(hc, "CHECKS", [_r(f"c{i}", True) for i in range(5)])
    card, _ = hc.build_report_card([_r("c0", True)], _HB0)
    assert "执行项数少于注册项数" in card.fallback


def test_report_shows_push_failure_streak():
    """告警通路自身的心跳: 连续推送失败要在下一次报告里自曝。"""
    card, _ = hc.build_report_card([_r("a", True)],
                                   {"last_push_at": None, "fail_streak": 3})
    assert "连续失败 3 次" in card.fallback


def test_report_shows_hours_since_last_push():
    from datetime import timedelta
    hb = {"last_push_at": datetime.now() - timedelta(hours=26), "fail_streak": 0}
    card, _ = hc.build_report_card([_r("a", True)], hb)
    assert "距上次成功推送 26 小时" in card.fallback


# ── 推送设计基线 v1.1 合规(首版四条全违反, 用测试钉死) ──

def test_card_uses_system_family_grey():
    """体检属"系统"族(与EOD复核/数据源同族)=grey header, 不能用风险族的橙红抢注意力。"""
    card, _ = hc.build_report_card([_r("a", False, hc.CRITICAL)], _HB0)
    assert card.family == "system"
    assert card.template == "grey"


def test_card_title_uses_system_emoji():
    """系统族标题 emoji 词表是 ⚙️。"""
    for results in ([_r("a", True)], [_r("a", False, hc.CRITICAL)], [_r("a", False)]):
        card, _ = hc.build_report_card(results, _HB0)
        assert card.title.startswith("⚙️"), card.title


def test_card_has_advice_section():
    """五区骨架: 行动建议区必有, 👉 开头。"""
    for results in ([_r("a", True)], [_r("a", False, hc.CRITICAL)]):
        card, _ = hc.build_report_card(results, _HB0)
        assert "👉" in card.fallback


def test_card_has_light_string():
    """系统卡家族图形 = 灯串 + 异常项列表。"""
    card, _ = hc.build_report_card([_r("a", True), _r("b", False, hc.CRITICAL)], _HB0)
    assert "🟢" in card.fallback and "🔴" in card.fallback


def test_card_body_capped_to_avoid_long_card():
    """正文(0~3区)≤10 行: 异常项超 6 条时只列前 6, 其余下沉折叠。"""
    many = [_r(f"bad{i}", False, hc.CRITICAL) for i in range(12)]
    card, _ = hc.build_report_card(many, _HB0)
    assert "另有 6 项异常" in card.fallback
    body_lines = [e for e in card.elements if isinstance(e, dict)]
    assert len(body_lines) <= 10, "正文元素过多, 会撑爆手机端卡片"


def test_card_summary_present():
    """区外标配: 自定义摘要(锁屏横幅直接显示它)。"""
    card, _ = hc.build_report_card([_r("a", True)], _HB0)
    assert card.summary and "系统体检" in card.summary


# ── 注册表自身 ──

def test_registry_keys_unique():
    keys = [c.key for c in hc.CHECKS]
    assert len(keys) == len(set(keys)), "check key 必须唯一(落库主键的一部分)"


def test_registry_severity_valid():
    for c in hc.CHECKS:
        assert c.severity in (hc.CRITICAL, hc.WARN, hc.INFO), c.key
