import backend.services.ai_advisor.stock_review as sr


def _stub_gathered(**overrides):
    base = {"name": "银之杰", "signals": [], "winrate": {}, "fin_risk": None,
            "sector": {"board_strength": None, "sector_rank": None, "theme_heat": []},
            "holding": None, "near_buy": None}
    base.update(overrides)
    return base


async def test_generate_assembles_and_degrades(monkeypatch):
    monkeypatch.setattr(sr, "_gather", lambda uid, code: _stub_gathered())

    async def none_narrate(sys, facts, **k):
        return None
    monkeypatch.setattr(sr.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(sr, "_get_cached", lambda *a: None)
    monkeypatch.setattr(sr, "_save_cache", lambda *a: None)
    out = await sr.generate_stock_review(1, "300085", use_cache=False)
    assert out["narrative"] is None
    assert out["facts"]["code"] == "300085"


async def test_llm_success_returns_narrative(monkeypatch):
    monkeypatch.setattr(sr, "_gather", lambda uid, code: _stub_gathered())

    async def fake_narrate(sys, facts, **k):
        return "研判正文" * 30
    monkeypatch.setattr(sr.ai_client, "narrate", fake_narrate)
    monkeypatch.setattr(sr, "_get_cached", lambda *a: None)
    save_calls = []
    monkeypatch.setattr(sr, "_save_cache", lambda *a: save_calls.append(a))
    out = await sr.generate_stock_review(1, "300085", use_cache=False)
    assert out["narrative"] and "研判" in out["narrative"]
    assert out["cached"] is False
    assert len(save_calls) == 1


async def test_llm_failure_does_not_write_cache(monkeypatch):
    """LLM失败(narrative=None)不该写缓存, 否则当天再请求还是拿到 None, LLM恢复也救不回来。"""
    monkeypatch.setattr(sr, "_gather", lambda uid, code: _stub_gathered())

    async def none_narrate(sys, facts, **k):
        return None
    monkeypatch.setattr(sr.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(sr, "_get_cached", lambda *a: None)
    save_calls = []
    monkeypatch.setattr(sr, "_save_cache", lambda *a: save_calls.append(a))
    out = await sr.generate_stock_review(1, "300085", use_cache=False)
    assert out["narrative"] is None
    assert save_calls == []


async def test_cache_hit_returns_cached_without_gather(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("命中缓存不该再 gather/narrate")
    monkeypatch.setattr(sr, "_gather", boom)
    monkeypatch.setattr(sr.ai_client, "narrate", boom)
    import json as _json
    cached_row = {"facts_json": _json.dumps({"code": "300085", "name": "银之杰"}),
                  "narrative": "缓存的研判正文"}
    monkeypatch.setattr(sr, "_get_cached", lambda *a: cached_row)
    out = await sr.generate_stock_review(1, "300085", use_cache=True)
    assert out["cached"] is True
    assert out["facts"]["code"] == "300085"
    assert out["narrative"] == "缓存的研判正文"


async def test_holding_and_near_buy_passthrough_into_facts(monkeypatch):
    """holding/near_buy 若 gather 给出非空 dict, 应原样透传进最终 facts。"""
    monkeypatch.setattr(sr, "_gather", lambda uid, code: _stub_gathered(
        holding={"cost": 12.3, "float_pct": 5.0, "entry_model": "回踩MA10"},
        near_buy={"model": "回踩MA10", "gap_pct": 1.2}))

    async def none_narrate(sys, facts, **k):
        return None
    monkeypatch.setattr(sr.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(sr, "_get_cached", lambda *a: None)
    monkeypatch.setattr(sr, "_save_cache", lambda *a: None)
    out = await sr.generate_stock_review(1, "300085", use_cache=False)
    assert out["facts"]["holding"]["is_holding"] is True
    assert out["facts"]["holding"]["cost"] == 12.3
    assert out["facts"]["near_buy"]["approaching"] is True
    assert out["facts"]["near_buy"]["model"] == "回踩MA10"


async def test_count_reviews_today(monkeypatch):
    from backend.models.repo import stock_review as repo_sr

    async def fake_fetchone(sql, args=None):
        assert "COUNT(*)" in sql
        return {"n": 3}
    monkeypatch.setattr(repo_sr, "_fetchone", fake_fetchone)
    n = await repo_sr.count_reviews_today(1)
    assert n == 3


async def test_pick_near_buy_and_pick_holding_helpers():
    """_pick_holding/_pick_near_buy 是内部按 code 摘取的纯逻辑, 覆盖非持仓/无价格/未在榜三种降级。"""
    assert sr._pick_holding({}, {}, {"price": 10}, "300085") is None
    got = sr._pick_holding({"300085": 8.0}, {"300085": "BUY_MA10"}, {"price": 10.0}, "300085")
    assert got["cost"] == 8.0
    assert got["float_pct"] == 25.0
    assert got["entry_model"] == "BUY_MA10"
    assert sr._pick_holding({"300085": 8.0}, {}, None, "300085")["float_pct"] is None

    assert sr._pick_near_buy(None, "300085") is None
    assert sr._pick_near_buy({"items": [{"code": "600000"}]}, "300085") is None
    snap = {"items": [{"code": "300085", "dist": 1.5,
                       "hits": [{"buy_name": "回踩MA10"}]}]}
    nb = sr._pick_near_buy(snap, "300085")
    assert nb == {"model": "回踩MA10", "gap_pct": 1.5}
