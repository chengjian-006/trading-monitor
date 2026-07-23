import asyncio

import pytest
from fastapi import HTTPException

from backend.models.repo import backtest_jobs_db
from backend.routers import backtest as backtest_router
from backend.services import backtest_jobs


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

    async def no_stale_jobs(*_args):
        return None

    monkeypatch.setattr(
        backtest_router.backtest_jobs_db, "has_active_job", no_database_job, raising=False
    )
    monkeypatch.setattr(
        backtest_router.backtest_jobs_db, "expire_stale_running_jobs", no_stale_jobs
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


def test_memory_store_filters_jobs_by_owner():
    backtest_jobs._JOBS.clear()
    try:
        job_id = backtest_jobs.new_job(1, user_id=11)
        assert backtest_jobs.get_job(job_id, user_id=11) is not None
        assert backtest_jobs.get_job(job_id, user_id=22) is None
    finally:
        backtest_jobs._JOBS.clear()


@pytest.mark.parametrize(
    ("scope", "koujing"),
    [("pool", "5m")],
)
def test_regular_user_cannot_start_any_five_minute_job(monkeypatch, scope, koujing):
    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    request = backtest_router.ModelRunRequest(model_id="m1", scope=scope, koujing=koujing)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            backtest_router.model_run(
                request, {"id": 22, "username": "bob", "role": "user"}
            )
        )

    assert exc_info.value.status_code == 403


def test_administrator_can_reach_universe_loading_for_expensive_job(monkeypatch):
    calls = []

    async def fake_expire(user_id, stale_seconds):
        calls.append(("expire", user_id, stale_seconds))

    async def fake_active(user_id):
        calls.append(("active", user_id))
        return False

    async def fake_universe(spec):
        calls.append(("universe", spec))
        return []

    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    monkeypatch.setattr(
        backtest_router.backtest_jobs_db,
        "expire_stale_running_jobs",
        fake_expire,
        raising=False,
    )
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "has_active_job", fake_active)
    monkeypatch.setattr(backtest_router.backtest_jobs, "has_active_job", lambda _uid: False)
    monkeypatch.setattr(backtest_router, "universe_codes", fake_universe)

    result = asyncio.run(
        backtest_router.model_run(
            backtest_router.ModelRunRequest(model_id="m1", scope="all", koujing="5m"),
            {"id": 1, "username": "admin", "role": "admin"},
        )
    )

    assert result["ok"] is False
    assert calls[-1] == ("universe", "all")


def test_stale_systemd_job_is_expired_before_active_check(monkeypatch):
    stale = True
    calls = []

    async def fake_expire(user_id, stale_seconds):
        nonlocal stale
        calls.append(("expire", user_id, stale_seconds))
        stale = False

    async def fake_active(user_id):
        calls.append(("active", user_id))
        return stale

    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    monkeypatch.setattr(backtest_router.backtest_jobs, "has_active_job", lambda _uid: False)
    monkeypatch.setattr(
        backtest_router.backtest_jobs_db,
        "expire_stale_running_jobs",
        fake_expire,
        raising=False,
    )
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "has_active_job", fake_active)
    monkeypatch.setattr(backtest_router, "universe_codes", lambda _spec: _empty_codes())

    result = asyncio.run(
        backtest_router.model_run(
            backtest_router.ModelRunRequest(model_id="m1", scope="pool", koujing="daily"),
            {"id": 22, "username": "bob", "role": "user"},
        )
    )

    assert result["ok"] is False
    assert calls == [
        ("expire", 22, backtest_router._ZOMBIE_TIMEOUT_SEC),
        ("active", 22),
    ]


def test_database_stale_cleanup_uses_owner_and_heartbeat_threshold(monkeypatch):
    captured = {}

    async def fake_execute(sql, args):
        captured["sql"] = sql
        captured["args"] = args

    monkeypatch.setattr(backtest_jobs_db, "_execute", fake_execute)

    asyncio.run(backtest_jobs_db.expire_stale_running_jobs(22, 1200))

    assert "status='error'" in captured["sql"]
    assert "runner='systemd'" in captured["sql"]
    assert "updated_at" in captured["sql"]
    assert captured["args"][-2:] == (22, 1200)


def test_runner_transition_failure_returns_503_without_fallback_launch(monkeypatch):
    launched = []
    terminal = []

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fail_runner(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    async def fake_set_error(job_id, message):
        terminal.append((job_id, message))

    async def fake_active(_user_id):
        return False

    async def fake_universe(_spec):
        return ["000001"]

    backtest_jobs._JOBS.clear()
    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "has_active_job", fake_active)
    monkeypatch.setattr(
        backtest_router.backtest_jobs_db,
        "expire_stale_running_jobs",
        fake_noop,
        raising=False,
    )
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "create_job", fake_noop)
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "set_runner", fail_runner)
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "set_error", fake_set_error)
    monkeypatch.setattr(backtest_router, "universe_codes", fake_universe)
    monkeypatch.setattr(backtest_router.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        backtest_router.backtest_jobs, "launch", lambda *args: launched.append(args)
    )

    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                backtest_router.model_run(
                    backtest_router.ModelRunRequest(
                        model_id="m1", scope="all", koujing="daily"
                    ),
                    {"id": 1, "username": "admin", "role": "admin"},
                )
            )
        assert exc_info.value.status_code == 503
        assert launched == []
        assert terminal
        assert list(backtest_jobs._JOBS.values())[0]["status"] == "error"
    finally:
        backtest_jobs._JOBS.clear()


def test_concurrent_requests_create_only_one_job_for_user(monkeypatch):
    async def scenario():
        first_in_universe = asyncio.Event()
        release_first = asyncio.Event()
        universe_calls = 0

        async def fake_universe(_spec):
            nonlocal universe_calls
            universe_calls += 1
            if universe_calls == 1:
                first_in_universe.set()
                await release_first.wait()
            return ["000001"]

        async def fake_noop(*_args, **_kwargs):
            return None

        async def fake_active(_user_id):
            return False

        monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
        monkeypatch.setattr(backtest_router, "universe_codes", fake_universe)
        monkeypatch.setattr(backtest_router.backtest_jobs_db, "has_active_job", fake_active)
        monkeypatch.setattr(
            backtest_router.backtest_jobs_db,
            "expire_stale_running_jobs",
            fake_noop,
            raising=False,
        )
        monkeypatch.setattr(backtest_router.backtest_jobs, "launch", lambda *_args: None)

        request = backtest_router.ModelRunRequest(
            model_id="m1", scope="pool", koujing="daily"
        )
        user = {"id": 22, "username": "bob", "role": "user"}
        first = asyncio.create_task(backtest_router.model_run(request, user))
        await first_in_universe.wait()
        second = asyncio.create_task(backtest_router.model_run(request, user))
        await asyncio.sleep(0)
        release_first.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    backtest_jobs._JOBS.clear()
    try:
        results = asyncio.run(scenario())
        successes = [result for result in results if isinstance(result, dict)]
        conflicts = [
            result
            for result in results
            if isinstance(result, HTTPException) and result.status_code == 409
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert len(backtest_jobs._JOBS) == 1
    finally:
        backtest_jobs._JOBS.clear()


def test_inprocess_fallback_marks_database_job_done(monkeypatch):
    launched = []
    completed = []

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_active(_user_id):
        return False

    async def fake_universe(_spec):
        return ["000001"]

    async def fake_run(*_args, **_kwargs):
        return {"summary": {"trades": 0}}

    async def fake_done(job_id, result):
        completed.append((job_id, result))

    backtest_jobs._JOBS.clear()
    monkeypatch.setattr(backtest_router, "MODEL_IDS", ["m1"])
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "has_active_job", fake_active)
    monkeypatch.setattr(
        backtest_router.backtest_jobs_db,
        "expire_stale_running_jobs",
        fake_noop,
        raising=False,
    )
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "create_job", fake_noop)
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "set_runner", fake_noop)
    monkeypatch.setattr(backtest_router.backtest_jobs_db, "set_done", fake_done)
    monkeypatch.setattr(backtest_router, "universe_codes", fake_universe)
    monkeypatch.setattr(backtest_router, "run_model_backtest", fake_run)
    monkeypatch.setattr(backtest_router.bt_runs_repo, "save_run", fake_noop)
    monkeypatch.setattr(backtest_router.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        backtest_router.backtest_jobs,
        "launch",
        lambda jid, factory: launched.append((jid, factory)),
    )

    try:
        result = asyncio.run(
            backtest_router.model_run(
                backtest_router.ModelRunRequest(
                    model_id="m1", scope="all", koujing="daily"
                ),
                {"id": 1, "username": "admin", "role": "admin"},
            )
        )
        job_id, factory = launched[0]
        callback = lambda *_args, **_kwargs: None
        asyncio.run(factory(callback))

        assert result["job_id"] == job_id
        assert completed == [(job_id, {"summary": {"trades": 0}})]
    finally:
        backtest_jobs._JOBS.clear()
