"""交易复盘每周日推送: 给交易者本人(user_id=1)生成近一月复盘, 有平仓才推一张卡(单渠道)。"""
import backend.services.ai_advisor.trade_coach as tc


async def test_weekly_sends_card_when_closed_rounds(monkeypatch):
    sent = []

    async def fake_gen(uid, s, e, **k):
        assert uid == tc.OWNER_USER_ID
        return {"facts": {"n_closed": 2,
                          "listen_vs_self": {"listen": {"n": 1, "win_rate": 100.0},
                                             "self": {"n": 1, "win_rate": 0.0}}},
                "narrative": "n" * 120, "as_of": "2026-07-19"}

    async def fake_send(report):
        sent.append(report)

    monkeypatch.setattr(tc, "generate_coach_report", fake_gen)
    monkeypatch.setattr(tc, "_send_coach_card", fake_send)
    await tc.run_trade_coach_weekly()
    assert len(sent) == 1


async def test_weekly_skips_when_no_closed_rounds(monkeypatch):
    sent = []

    async def fake_gen(uid, s, e, **k):
        return {"facts": {"n_closed": 0,
                          "listen_vs_self": {"listen": {"n": 0, "win_rate": 0.0},
                                             "self": {"n": 0, "win_rate": 0.0}}},
                "narrative": None, "as_of": "2026-07-19"}

    async def fake_send(report):
        sent.append(report)

    monkeypatch.setattr(tc, "generate_coach_report", fake_gen)
    monkeypatch.setattr(tc, "_send_coach_card", fake_send)
    await tc.run_trade_coach_weekly()
    assert sent == []
