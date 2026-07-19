import backend.services.ai_advisor.ai_client as ac


async def test_narrate_returns_none_when_llm_empty(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {"ai_advisor_enabled": True, "ai_advisor_provider": "deepseek", "ai_model": "x"})
    monkeypatch.setattr(ac, "_call_provider", lambda *a, **k: "")   # 模拟空返回
    out = await ac.narrate("sys", {"a": 1})
    assert out is None


async def test_narrate_passes_factsheet_as_json(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {"ai_advisor_enabled": True, "ai_advisor_provider": "deepseek", "ai_model": "x"})
    captured = {}
    def fake_call(provider, model, system_prompt, user_content, max_tokens):
        captured["user"] = user_content
        captured["sys"] = system_prompt
        return "这是一段复盘。" * 15   # >100 字
    monkeypatch.setattr(ac, "_call_provider", fake_call)
    out = await ac.narrate("守红线", {"追高占比": 0.6})
    assert out and "复盘" in out
    assert "追高占比" in captured["user"]      # 事实清单以中文 JSON 进 prompt
    assert captured["sys"] == "守红线"


async def test_narrate_none_on_exception(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {"ai_advisor_enabled": True, "ai_advisor_provider": "deepseek", "ai_model": "x"})
    def boom(*a, **k): raise RuntimeError("api down")
    monkeypatch.setattr(ac, "_call_provider", boom)
    assert await ac.narrate("s", {}) is None
