"""Test for GET /api/stock/{code}/review route with daily cap enforcement."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import backend.routers.stock_review as stock_review_router
from backend.models import repository
from backend.core import config as config_module


def _client_with_mocks(monkeypatch, review_count: int = 0, use_cap: int = 2):
    """Setup FastAPI app with stock_review router and mocked dependencies.

    Args:
        monkeypatch: pytest monkeypatch fixture
        review_count: count_reviews_today return value (0-based, so 2 means already reviewed twice)
        use_cap: daily cap limit (default 2 for testing)
    """
    # Mock generate_stock_review
    async def fake_generate(user_id, code, **kwargs):
        return {
            "facts": {"code": code, "signal_count": 3},
            "narrative": "This is a test narrative about " + code,
            "as_of": "2026-07-19",
            "cached": False
        }
    monkeypatch.setattr(stock_review_router.stock_review, "generate_stock_review", fake_generate)

    # Mock count_reviews_today
    async def fake_count(user_id):
        return review_count
    monkeypatch.setattr(repository, "count_reviews_today", fake_count)

    # Mock load_config
    def fake_config():
        class FakeConfig(dict):
            def get(self, key, default=None):
                if key == "ai_advisor_daily_cap":
                    return use_cap
                return default
        return FakeConfig()
    monkeypatch.setattr(stock_review_router, "load_config", fake_config)

    # Mock get_current_user
    async def fake_user():
        return {"id": 1, "username": "test_user", "role": "user"}

    app = FastAPI()
    app.include_router(stock_review_router.router)
    app.dependency_overrides[stock_review_router.get_current_user] = fake_user

    return TestClient(app)


def test_stock_review_success_normal_case(monkeypatch):
    """Test GET /api/stock/{code}/review returns 200 with review data when under cap."""
    # Setup: user has reviewed 0 stocks today, cap is 2
    client = _client_with_mocks(monkeypatch, review_count=0, use_cap=2)

    # Act
    response = client.get("/api/stock/600000/review")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["facts"]["code"] == "600000"
    assert "narrative" in data
    assert data["as_of"] == "2026-07-19"


def test_stock_review_at_cap_boundary(monkeypatch):
    """Test GET /api/stock/{code}/review returns 429 when at or exceeding cap."""
    # Setup: user has reviewed exactly cap number of stocks today
    client = _client_with_mocks(monkeypatch, review_count=2, use_cap=2)

    # Act: Try to review another stock after hitting cap
    response = client.get("/api/stock/600001/review")

    # Assert: Should get 429 Too Many Requests
    assert response.status_code == 429
    data = response.json()
    assert "今日研判次数已达上限" in data.get("detail", "")


def test_stock_review_exceeds_cap(monkeypatch):
    """Test GET /api/stock/{code}/review returns 429 when exceeding cap."""
    # Setup: user somehow has reviewed more than cap (edge case)
    client = _client_with_mocks(monkeypatch, review_count=5, use_cap=2)

    # Act
    response = client.get("/api/stock/600002/review")

    # Assert
    assert response.status_code == 429
    data = response.json()
    assert "今日研判次数已达上限" in data.get("detail", "")


def test_stock_review_just_under_cap(monkeypatch):
    """Test GET /api/stock/{code}/review succeeds when just under cap."""
    # Setup: user has reviewed cap-1 stocks today
    client = _client_with_mocks(monkeypatch, review_count=1, use_cap=2)

    # Act
    response = client.get("/api/stock/600003/review")

    # Assert: Should still succeed
    assert response.status_code == 200
    data = response.json()
    assert data["facts"]["code"] == "600003"
