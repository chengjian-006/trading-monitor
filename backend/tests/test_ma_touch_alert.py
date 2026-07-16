"""均线到线提醒(一次性订阅) 测试.

覆盖: 贴线判定 / 防误触离带逻辑 / 一次性失效 / 60天过期 / 幂等订阅 / 买卖卡带提醒行·plunge不带 / 提醒卡形态。
"""
import asyncio
from datetime import date, timedelta

from backend.services import ma_touch_alert as mta
from backend.services import push_pref as pp


# ══════════════ 贴线判定 ══════════════

class TestInBand:
    def test_exact_on_ma_is_touch(self):
        assert mta.in_band(10.0, 10.0) is True

    def test_within_band_upper(self):
        # +0.3% 边界(含)
        assert mta.in_band(10.03, 10.0) is True

    def test_within_band_lower(self):
        assert mta.in_band(9.97, 10.0) is True

    def test_outside_band_upper(self):
        assert mta.in_band(10.05, 10.0) is False

    def test_outside_band_lower(self):
        assert mta.in_band(9.95, 10.0) is False

    def test_invalid_inputs_never_touch(self):
        assert mta.in_band(0, 10.0) is False
        assert mta.in_band(10.0, 0) is False
        assert mta.in_band(-1, 10.0) is False


# ══════════════ 防误触: 订阅后首见在带内不触发, 离带后再回触才算 ══════════════

class TestArming:
    def test_fresh_sub_already_in_band_no_trigger(self):
        armed = {}
        assert mta.touch_verdict(1, 10.0, 10.0, armed) is False
        # 一直贴着线也不触发(没离过带)
        assert mta.touch_verdict(1, 10.01, 10.0, armed) is False

    def test_out_of_band_arms(self):
        armed = {}
        assert mta.touch_verdict(1, 11.0, 10.0, armed) is False
        assert armed[1] is True

    def test_armed_then_back_in_band_triggers(self):
        armed = {}
        mta.touch_verdict(1, 11.0, 10.0, armed)          # 离带 → 武装
        assert mta.touch_verdict(1, 10.02, 10.0, armed) is True

    def test_stays_out_of_band_never_triggers(self):
        armed = {}
        assert mta.touch_verdict(1, 11.0, 10.0, armed) is False
        assert mta.touch_verdict(1, 12.0, 10.0, armed) is False

    def test_restart_semantics_fresh_map_requires_leave_band_again(self):
        # 重启丢内存状态: 空 map 视同刚订阅 —— 已在带内不触发
        assert mta.touch_verdict(7, 10.0, 10.0, {}) is False

    def test_independent_per_subscription(self):
        armed = {}
        mta.touch_verdict(1, 11.0, 10.0, armed)          # 订阅1 武装
        # 订阅2(同价不同线)首见在带内 → 不触发, 不受订阅1状态影响
        assert mta.touch_verdict(2, 10.0, 10.0, armed) is False
        assert mta.touch_verdict(1, 10.0, 10.0, armed) is True


# ══════════════ 60天过期 + kind 注册 ══════════════

class TestExpiry:
    def test_ma_alert_kinds_valid(self):
        for k in ("ma_alert_10", "ma_alert_20", "ma_alert_60"):
            assert k in pp.VALID_KINDS
            assert k in pp.MA_ALERT_KINDS

    def test_until_60_days_inclusive(self):
        d = date(2026, 7, 16)
        # 含今日共60天 → 至今日+59
        assert pp.until_for("ma_alert_10", 0, today=d) == d + timedelta(days=59)
        assert pp.until_for("ma_alert_60", 0, today=d) == d + timedelta(days=59)

    def test_kind_labels_registered(self):
        assert pp.KIND_LABEL["ma_alert_10"] == "到线提醒·10日线"
        assert pp.KIND_LABEL["ma_alert_20"] == "到线提醒·20日线"
        assert pp.KIND_LABEL["ma_alert_60"] == "到线提醒·60日线"

    def test_decide_ignores_ma_alert(self):
        # 到线提醒订阅是"要提醒"不是"要静音", 绝不能压该票的正常信号推送
        v = pp.decide([{"kind": "ma_alert_10", "target": "600519"}],
                      code="600519", signal_id="BUY_VOL_BREAKOUT")
        assert v["suppress_all"] is False and v["mute_lark"] is False


# ══════════════ 幂等订阅(execute_quick_action) ══════════════

class TestIdempotentSubscribe:
    def _run(self, existing, monkeypatch):
        from backend.models.repo import push_pref as pref_repo
        calls = {"added": []}

        async def fake_active_prefs(user_id):
            return existing

        async def fake_add_pref(user_id, kind, target, until):
            calls["added"].append((user_id, kind, target, until))

        monkeypatch.setattr(pref_repo, "active_prefs", fake_active_prefs)
        monkeypatch.setattr(pref_repo, "add_pref", fake_add_pref)
        ok, label, detail = asyncio.run(pp.execute_quick_action(1, "ma_alert_10", "600519", 0))
        return ok, label, detail, calls

    def test_duplicate_click_is_idempotent(self, monkeypatch):
        existing = [{"id": 5, "kind": "ma_alert_10", "target": "600519"}]
        ok, label, detail, calls = self._run(existing, monkeypatch)
        assert ok is True
        assert "已在监控中" in label + detail
        assert calls["added"] == []          # 不重复落库, 不刷新60天窗口

    def test_other_line_same_code_not_blocked(self, monkeypatch):
        # 同票不同线(已有20日线订阅)不算重复
        existing = [{"id": 5, "kind": "ma_alert_20", "target": "600519"}]
        ok, label, detail, calls = self._run(existing, monkeypatch)
        assert ok is True and len(calls["added"]) == 1
        assert calls["added"][0][1] == "ma_alert_10"

    def test_fresh_subscribe_confirm_copy(self, monkeypatch):
        ok, label, detail, calls = self._run([], monkeypatch)
        assert ok is True and len(calls["added"]) == 1
        assert "600519" in detail and "10日均线" in detail and "一次性" in detail


# ══════════════ 买卖卡带提醒行 / plunge 不带 ══════════════

class TestCardRow:
    def test_md_has_three_signed_links(self):
        md = pp.build_ma_alert_md("http://x.cn/", user_id=1, code="600519", name="贵州茅台")
        assert md.startswith("🔔 到线提醒：")
        assert "[10日线]" in md and "[20日线]" in md and "[60日线]" in md
        for k in ("ma_alert_10", "ma_alert_20", "ma_alert_60"):
            assert f"k={k}" in md
        assert md.count("t=600519") == 3 and md.count("sig=") == 3

    def test_md_empty_without_site_or_code(self):
        assert pp.build_ma_alert_md("", 1, "600519", "贵州茅台") == ""
        assert pp.build_ma_alert_md("http://x.cn/", 1, "", "") == ""

    def test_eligible_buy_sell_reduce_with_code(self):
        assert pp.ma_alert_eligible("buy", "600519") is True
        assert pp.ma_alert_eligible("sell", "600519") is True
        assert pp.ma_alert_eligible("reduce", "600519") is True

    def test_plunge_or_no_code_not_eligible(self):
        assert pp.ma_alert_eligible("plunge", "600519") is False   # 大盘急跌卡不挂
        assert pp.ma_alert_eligible("buy", "") is False            # 无个股不挂


# ══════════════ 提醒卡形态(情报蓝卡 + KPI三栏 + 建议 + 摘要 + 分时图链接) ══════════════

class TestTouchCard:
    def test_card_shape(self):
        card = mta.build_touch_card("贵州茅台", "600519", 10, 1700.5, 1699.2, "http://x.cn")
        assert card.family == "intel" and card.template == "blue"
        assert card.title == "🔔 到线提醒 · 贵州茅台(600519) 触及10日线"
        kpi = card.elements[0]
        assert kpi["tag"] == "column_set" and len(kpi["columns"]) == 3
        texts = "".join(str(c) for c in kpi["columns"])
        assert "¥1700.50" in texts and "¥1699.20" in texts     # 现价 / 10日线值
        assert "+0.08%" in texts                               # 距离%
        assert "现价" in texts and "10日线" in texts and "距离" in texts
        assert any("你订的到线提醒到了" in (e.get("content") or "") for e in card.elements
                   if isinstance(e, dict) and e.get("tag") == "markdown")
        assert "贵州茅台" in card.summary and "10日线" in card.summary
        assert card.link_url == "http://x.cn/intraday?code=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0"
        assert "600519" in card.fallback and "10日线" in card.fallback

    def test_card_without_site_has_no_link(self):
        card = mta.build_touch_card("贵州茅台", "600519", 20, 100.0, 100.1, "")
        assert card.link_url == ""


# ══════════════ 一次性失效(编排: 触发→发卡→撤销订阅行; 发送失败不失效) ══════════════

def _patch_orchestration(monkeypatch, *, subs, price, closes, send_ok=True):
    """打桩 run_ma_touch_alert 的全部外部依赖, 返回捕获容器。"""
    import backend.core.trading_calendar as cal
    import backend.core.config as core_config
    import backend.data_fetcher as data_fetcher
    from backend.models import repository
    from backend.models.repo import push_pref as pref_repo
    from backend.services import notifier

    captured = {"cards": [], "revoked": []}

    monkeypatch.setattr(cal, "is_workday", lambda: True)
    monkeypatch.setattr(mta, "_in_window", lambda t: True)
    monkeypatch.setattr(core_config, "load_config", lambda: {"site_url": "http://x.cn"})

    async def fake_subs(kinds):
        return subs

    async def fake_quotes(codes):
        return {c: {"price": price, "name": f"名{c}", "pct_change": 0.0} for c in codes}

    async def fake_kline_batch(codes, n, before=None):
        return {c: list(closes) for c in codes}

    async def fake_send_card(card):
        captured["cards"].append(card)
        return send_ok

    async def fake_revoke(user_id, pref_id):
        captured["revoked"].append((user_id, pref_id))

    monkeypatch.setattr(pref_repo, "active_prefs_of_kinds", fake_subs)
    monkeypatch.setattr(pref_repo, "revoke", fake_revoke)
    monkeypatch.setattr(data_fetcher, "get_realtime_quotes", fake_quotes)
    monkeypatch.setattr(repository, "fetch_kline_close_batch", fake_kline_batch)
    monkeypatch.setattr(notifier, "send_card", fake_send_card)
    return captured


class TestOneShotOrchestration:
    SUB = {"id": 5, "user_id": 1, "kind": "ma_alert_10", "target": "600519"}
    CLOSES = [10.0] * 9          # 9根历史收盘=10 → 现价10时 MA10=10(贴线)

    def test_first_sight_in_band_no_fire(self, monkeypatch):
        mta._armed.clear()
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.0, closes=self.CLOSES)
        asyncio.run(mta.run_ma_touch_alert())
        assert cap["cards"] == [] and cap["revoked"] == []
        assert mta._armed.get(5) is None     # 在带内首见: 不武装也不触发

    def test_leave_band_then_touch_fires_and_revokes(self, monkeypatch):
        mta._armed.clear()
        # 第一轮: 现价10.5 离带 → 武装
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.5, closes=self.CLOSES)
        asyncio.run(mta.run_ma_touch_alert())
        assert cap["cards"] == [] and mta._armed.get(5) is True
        # 第二轮: 现价回贴 MA10 → 触发 + 发卡 + 该订阅行失效(一次性)
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.0, closes=self.CLOSES)
        asyncio.run(mta.run_ma_touch_alert())
        assert len(cap["cards"]) == 1
        assert "触及10日线" in cap["cards"][0].title
        assert cap["revoked"] == [(1, 5)]
        assert 5 not in mta._armed
        mta._armed.clear()

    def test_send_failure_keeps_subscription(self, monkeypatch):
        mta._armed.clear()
        mta._armed[5] = True                  # 已离带武装
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.0,
                                   closes=self.CLOSES, send_ok=False)
        asyncio.run(mta.run_ma_touch_alert())
        assert len(cap["cards"]) == 1         # 尝试过发送
        assert cap["revoked"] == []           # 发送失败不失效, 下轮重试
        assert mta._armed.get(5) is True
        mta._armed.clear()

    def test_other_line_subscription_untouched(self, monkeypatch):
        # 同票 10日线触发失效, 60日线订阅不受影响(仍在, 状态保留)
        mta._armed.clear()
        mta._armed[5] = True
        mta._armed[6] = True
        subs = [dict(self.SUB), {"id": 6, "user_id": 1, "kind": "ma_alert_60", "target": "600519"}]
        # 60根收盘: 前50根=14, 后9根=10 → MA60 远离现价10; MA10=10 贴线
        closes = [10.0] * 9 + [14.0] * 51
        cap = _patch_orchestration(monkeypatch, subs=subs, price=10.0, closes=closes)
        asyncio.run(mta.run_ma_touch_alert())
        assert len(cap["cards"]) == 1 and "10日线" in cap["cards"][0].title
        assert cap["revoked"] == [(1, 5)]
        assert mta._armed.get(6) is True      # 60日线订阅继续监控
        mta._armed.clear()

    def test_stale_armed_state_cleaned_for_gone_subs(self, monkeypatch):
        mta._armed.clear()
        mta._armed[99] = True                 # 已过期/撤销订阅的残留状态
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.5, closes=self.CLOSES)
        asyncio.run(mta.run_ma_touch_alert())
        assert 99 not in mta._armed
        assert cap["cards"] == []
        mta._armed.clear()

    def test_insufficient_kline_skips(self, monkeypatch):
        mta._armed.clear()
        mta._armed[5] = True
        cap = _patch_orchestration(monkeypatch, subs=[dict(self.SUB)], price=10.0, closes=[10.0] * 3)
        asyncio.run(mta.run_ma_touch_alert())
        assert cap["cards"] == [] and cap["revoked"] == []
        mta._armed.clear()
