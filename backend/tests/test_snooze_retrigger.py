"""snooze 到期语义升级 (功能9, v1.7.x): 仅今日/本周/直到再次突破.

- until_for: snooze_until_retrigger 用远期日期(条件型, 不靠日期过期, 靠引擎再触发时撤销)
- retrigger_verdict: 有该票该模型的条件静音时 —— 昨(上一交易日)也触发=连续→压住; 昨没触发=新一轮突破→放行+撤销
- 落地页: 三档各生成正确签名链接
"""
from datetime import date, timedelta

from backend.services import push_pref as ps


class TestUntilForRetrigger:
    def test_retrigger_far_future(self):
        today = date(2026, 7, 12)
        u = ps.until_for("snooze_until_retrigger", 0, today)
        assert u >= today + timedelta(days=3000)   # 远期, SQL 存活判定不误杀

    def test_snooze_still_date_based(self):
        today = date(2026, 7, 12)
        assert ps.until_for("snooze", 1, today) == today          # 今日
        assert ps.until_for("snooze", 3, today) == today + timedelta(days=2)

    def test_valid_kind_registered(self):
        assert "snooze_until_retrigger" in ps.VALID_KINDS


def _pref(kind, target, pid=1):
    return {"id": pid, "kind": kind, "target": target}


class TestRetriggerVerdict:
    def test_no_matching_snooze(self):
        v = ps.retrigger_verdict([_pref("snooze", "600000")], "600519", "BUY_VOL_BREAKOUT",
                                 triggered_prev_trading_day=False)
        assert v["has_snooze"] is False and v["suppress"] is False and v["revoke_id"] is None

    def test_continuous_suppresses(self):
        prefs = [_pref("snooze_until_retrigger", "600519|BUY_VOL_BREAKOUT", pid=7)]
        v = ps.retrigger_verdict(prefs, "600519", "BUY_VOL_BREAKOUT",
                                 triggered_prev_trading_day=True)
        assert v["has_snooze"] is True and v["suppress"] is True and v["revoke_id"] is None

    def test_fresh_breakout_allows_and_revokes(self):
        prefs = [_pref("snooze_until_retrigger", "600519|BUY_VOL_BREAKOUT", pid=7)]
        v = ps.retrigger_verdict(prefs, "600519", "BUY_VOL_BREAKOUT",
                                 triggered_prev_trading_day=False)
        assert v["has_snooze"] is True and v["suppress"] is False and v["revoke_id"] == 7

    def test_other_code_not_matched(self):
        prefs = [_pref("snooze_until_retrigger", "600519|BUY_VOL_BREAKOUT")]
        v = ps.retrigger_verdict(prefs, "000001", "BUY_VOL_BREAKOUT",
                                 triggered_prev_trading_day=True)
        assert v["has_snooze"] is False


class TestPrevTradingDay:
    def test_skips_weekend(self):
        from backend.core.trading_calendar import prev_trading_day
        # 周一 2026-07-13 的上一交易日 = 周五 2026-07-10(跳过周末)
        assert prev_trading_day(date(2026, 7, 13)) == date(2026, 7, 10)

    def test_normal_weekday(self):
        from backend.core.trading_calendar import prev_trading_day
        # 周三→周二(非节假日)
        assert prev_trading_day(date(2026, 7, 15)) == date(2026, 7, 14)


class TestSnoozeOptionsPage:
    def test_page_has_three_signed_links(self):
        html = ps.render_snooze_options_page("https://x.com", 1, "600519", "涨停", "BUY_VOL_BREAKOUT")
        assert "仅今日" in html and "本周" in html and "直到再次突破" in html
        # 三档链接都在, 各带 sig
        assert html.count("/api/quick/set?") == 3
        assert "k=snooze" in html and "k=snooze_until_retrigger" in html

    def test_signal_snooze_link_points_to_options(self):
        link = ps.build_signal_snooze_link("https://x.com", 1, "600519", "BUY_VOL_BREAKOUT")
        assert "/api/quick/snooze-options?" in link and "sig=" in link
