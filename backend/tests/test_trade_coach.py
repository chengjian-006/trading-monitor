import backend.services.ai_advisor.trade_coach as tc


async def test_generate_assembles_facts_and_narrative(monkeypatch):
    monkeypatch.setattr(tc, "_load_rounds", lambda uid: [
        {"status": "closed", "realized_pnl_pct": 5.0, "holding_days": 3,
         "entry_model_name": "回踩MA10", "entry_deviation_pct": 0.0, "exit_reason": None,
         "close_date": "2026-06-15"}])
    monkeypatch.setattr(tc, "_load_winrate", lambda: {})
    async def fake_narrate(sys, facts, **k): return "复盘正文" * 30
    monkeypatch.setattr(tc.ai_client, "narrate", fake_narrate)
    monkeypatch.setattr(tc, "_get_cached", lambda *a: None)
    monkeypatch.setattr(tc, "_save_cache", lambda *a: None)
    out = await tc.generate_coach_report(1, "2026-06-01", "2026-07-10", use_cache=False)
    assert out["facts"]["n_closed"] == 1
    assert out["narrative"] and "复盘" in out["narrative"]


async def test_llm_failure_still_returns_facts(monkeypatch):
    monkeypatch.setattr(tc, "_load_rounds", lambda uid: [])
    monkeypatch.setattr(tc, "_load_winrate", lambda: {})
    async def none_narrate(sys, facts, **k): return None
    monkeypatch.setattr(tc.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(tc, "_get_cached", lambda *a: None)
    monkeypatch.setattr(tc, "_save_cache", lambda *a: None)
    out = await tc.generate_coach_report(1, "s", "e", use_cache=False)
    assert out["narrative"] is None
    assert "facts" in out and out["facts"]["n_closed"] == 0


async def test_llm_failure_does_not_write_cache(monkeypatch):
    """LLM失败(narrative=None)不该写缓存, 否则当天再请求还是拿到 None, LLM恢复也救不回来。"""
    monkeypatch.setattr(tc, "_load_rounds", lambda uid: [])
    monkeypatch.setattr(tc, "_load_winrate", lambda: {})
    async def none_narrate(sys, facts, **k): return None
    monkeypatch.setattr(tc.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(tc, "_get_cached", lambda *a: None)
    save_calls = []
    monkeypatch.setattr(tc, "_save_cache", lambda *a: save_calls.append(a))
    out = await tc.generate_coach_report(1, "2026-06-01", "2026-07-10", use_cache=False)
    assert out["narrative"] is None
    assert save_calls == []
