from fastapi import FastAPI
from fastapi.testclient import TestClient
import backend.routers.coach as coach_router


def _client(monkeypatch):
    async def fake_gen(user_id, start, end, **k):
        return {"facts": {"n_closed": 3}, "narrative": "x"*120, "as_of": "2026-07-19", "cached": False}
    monkeypatch.setattr(coach_router.trade_coach, "generate_coach_report", fake_gen)
    async def fake_user(): return {"id": 1, "username": "u", "role": "user"}
    app = FastAPI(); app.include_router(coach_router.router)
    app.dependency_overrides[coach_router.get_current_user] = fake_user
    return TestClient(app)


def test_report_requires_dates_defaults_ok(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/api/coach/report?start=2026-06-01&end=2026-07-19")
    assert r.status_code == 200
    assert r.json()["facts"]["n_closed"] == 3
