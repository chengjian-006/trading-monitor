import asyncio

import pytest
from fastapi import HTTPException

from backend.models.repo import backtest_jobs_db
from backend.routers import backtest as backtest_router


def _memory_job(user_id: int, runner: str = "inproc") -> dict:
    return {
        "user_id": user_id,
        "status": "running",
        "progress": {"done": 0, "total": 1},
        "result": None,
        "error": None,
        "meta": {"runner": runner},
    }


def test_other_user_cannot_read_memory_backtest_job(monkeypatch):
    monkeypatch.setattr(
        backtest_router.backtest_jobs,
        "get_job",
        lambda job_id, user_id=None: _memory_job(user_id=11),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            backtest_router.model_job(
                "job-owned-by-11", {"id": 22, "username": "bob", "role": "user"}
            )
        )

    assert exc_info.value.status_code == 404


def test_other_user_cannot_read_database_backtest_job(monkeypatch):
    monkeypatch.setattr(backtest_router.backtest_jobs, "get_job", lambda *args: None)

    async def fake_get_job(job_id, user_id=None):
        return {
            **_memory_job(user_id=11, runner="systemd"),
            "job_id": job_id,
            "updated_at": None,
        }

    monkeypatch.setattr(backtest_router.backtest_jobs_db, "get_job", fake_get_job)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            backtest_router.model_job(
                "db-job-owned-by-11", {"id": 22, "username": "bob", "role": "user"}
            )
        )

    assert exc_info.value.status_code == 404


def test_regular_user_cannot_start_global_or_five_minute_job(monkeypatch):
    universe_called = False

    async def fake_universe_codes(_spec):
        nonlocal universe_called
        universe_called = True
        return []

    monkeypatch.setattr(backtest_router, "universe_codes", fake_universe_codes)
    request = backtest_router.ModelRunRequest(model_id="m1", scope="all", koujing="daily")
    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            backtest_router.model_run(
                request, {"id": 22, "username": "bob", "role": "user"}
            )
        )

    assert exc_info.value.status_code == 403
    assert universe_called is False


def test_user_cannot_start_second_active_backtest_job(monkeypatch):
    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    monkeypatch.setattr(backtest_router, "universe_codes", lambda _spec: _empty_codes())
    monkeypatch.setattr(
        backtest_router.backtest_jobs, "has_active_job", lambda user_id: user_id == 22,
        raising=False,
    )

    async def no_database_job(_user_id):
        return False

    monkeypatch.setattr(
        backtest_router.backtest_jobs_db, "has_active_job", no_database_job, raising=False
    )

    request = backtest_router.ModelRunRequest(model_id="m1", scope="pool", koujing="daily")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            backtest_router.model_run(
                request, {"id": 22, "username": "bob", "role": "user"}
            )
        )

    assert exc_info.value.status_code == 409


async def _empty_codes():
    return []


def test_database_lookup_scopes_job_id_to_owner(monkeypatch):
    captured = {}

    async def fake_fetchone(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return None

    monkeypatch.setattr(backtest_jobs_db, "_fetchone", fake_fetchone)

    assert asyncio.run(backtest_jobs_db.get_job("job-123", user_id=22)) is None
    assert "user_id=%s" in captured["sql"]
    assert captured["args"] == ("job-123", 22)


def test_database_active_check_ignores_inprocess_fallback_rows(monkeypatch):
    async def fake_fetchone(sql, _args):
        # Simulate a stale in-process fallback row. Only a query restricted to
        # systemd-owned work correctly excludes it.
        return None if "runner='systemd'" in sql else {"job_id": "stale-fallback"}

    monkeypatch.setattr(backtest_jobs_db, "_fetchone", fake_fetchone)

    assert asyncio.run(backtest_jobs_db.has_active_job(22)) is False
