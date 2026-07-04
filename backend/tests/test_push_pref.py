"""推送偏好/快捷设置 纯逻辑测试: 签名防伪 + 闸门判定.

DB 读写与 HTTP 端点是集成层, 这里只测可纯函数化的签名与 decide.
"""
from datetime import date

from backend.services import push_pref as pp


class TestSignature:
    def test_sign_verify_roundtrip(self):
        payload = "1|mute||0"
        sig = pp.sign(payload)
        assert pp.verify(payload, sig) is True

    def test_tampered_payload_rejected(self):
        sig = pp.sign("1|mute||0")
        assert pp.verify("1|model_off|BUY_X|0", sig) is False

    def test_empty_sig_rejected(self):
        assert pp.verify("1|mute||0", "") is False
        assert pp.verify("1|mute||0", None) is False

    def test_quick_link_carries_signed_params(self):
        url = pp.build_quick_link("http://x.cn/", user_id=1, kind="snooze", target="300166", days=3)
        assert url.startswith("http://x.cn/api/quick/set?")
        assert "k=snooze" in url and "t=300166" in url and "d=3" in url and "sig=" in url


class TestDecide:
    def test_no_prefs_passes(self):
        v = pp.decide([], code="300166", signal_id="BUY_RALLY_MA10")
        assert v["suppress_all"] is False and v["mute_lark"] is False

    def test_mute_only_mutes_lark_not_all(self):
        v = pp.decide([{"kind": "mute", "target": ""}], code="300166", signal_id="BUY_X")
        assert v["suppress_all"] is False and v["mute_lark"] is True

    def test_snooze_matching_code_suppresses_all(self):
        v = pp.decide([{"kind": "snooze", "target": "300166"}], code="300166", signal_id="BUY_X")
        assert v["suppress_all"] is True

    def test_snooze_other_code_does_not_match(self):
        v = pp.decide([{"kind": "snooze", "target": "000001"}], code="300166", signal_id="BUY_X")
        assert v["suppress_all"] is False

    def test_model_off_matching_signal_suppresses_all(self):
        v = pp.decide([{"kind": "model_off", "target": "BUY_RALLY_MA10"}],
                      code="300166", signal_id="BUY_RALLY_MA10")
        assert v["suppress_all"] is True

    def test_ack_matches_code_and_signal(self):
        v = pp.decide([{"kind": "ack", "target": "300166|BUY_RALLY_MA10"}],
                      code="300166", signal_id="BUY_RALLY_MA10")
        assert v["suppress_all"] is True

    def test_ack_different_signal_same_code_passes(self):
        v = pp.decide([{"kind": "ack", "target": "300166|BUY_RALLY_MA10"}],
                      code="300166", signal_id="BUY_VOL_BREAKOUT")
        assert v["suppress_all"] is False


class TestUntilDate:
    def test_today_scoped_kinds_until_today(self):
        d = date(2026, 6, 19)
        assert pp.until_for("mute", 0, today=d) == d
        assert pp.until_for("model_off", 0, today=d) == d

    def test_snooze_n_days_inclusive(self):
        d = date(2026, 6, 19)
        # 「静音3天」= 含今日共3个自然日 → 至 6/21
        assert pp.until_for("snooze", 3, today=d) == date(2026, 6, 21)

    def test_stop_snooze_behaves_like_snooze(self):
        d = date(2026, 6, 19)   # 周五
        # 当日不提醒 = days=1 → 至今日
        assert pp.until_for("stop_snooze", 1, today=d) == d


class TestStopSnooze:
    """止损升级专用静音: 只被升级检查消费, 不影响这只票的其它推送。"""

    def test_stop_snooze_is_valid_kind(self):
        assert "stop_snooze" in pp.VALID_KINDS

    def test_days_until_week_end_monday_is_seven(self):
        assert pp.days_until_week_end(date(2026, 6, 15)) == 7   # 周一 → 含今日整周7天

    def test_days_until_week_end_friday(self):
        assert pp.days_until_week_end(date(2026, 6, 19)) == 3   # 周五 五六日 = 3天

    def test_days_until_week_end_sunday_is_one(self):
        assert pp.days_until_week_end(date(2026, 6, 21)) == 1   # 周日 → 仅今日

    def test_active_for_matching_code(self):
        prefs = [{"kind": "stop_snooze", "target": "300274"}]
        assert pp.stop_snooze_active(prefs, "300274") is True

    def test_inactive_for_other_code(self):
        prefs = [{"kind": "stop_snooze", "target": "000001"}]
        assert pp.stop_snooze_active(prefs, "300274") is False

    def test_inactive_when_no_prefs(self):
        assert pp.stop_snooze_active([], "300274") is False

    def test_decide_ignores_stop_snooze(self):
        # 关键隔离: 点了止损升级静音, 这只票的买卖点/异动 decide 照常不被压
        v = pp.decide([{"kind": "stop_snooze", "target": "300274"}],
                      code="300274", signal_id="SELL_WEAK_STOP")
        assert v["suppress_all"] is False and v["mute_lark"] is False

    def test_actions_md_has_two_signed_stop_snooze_links(self):
        md = pp.build_stop_escalation_actions_md("http://x.cn/", user_id=1, code="300274",
                                                 today=date(2026, 6, 19))
        assert "当日不提醒" in md and "本周不提醒" in md
        assert md.count("k=stop_snooze") == 2 and md.count("t=300274") == 2 and "sig=" in md
