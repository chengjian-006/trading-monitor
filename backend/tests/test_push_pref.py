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
