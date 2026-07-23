# Task 3 — WebSocket and backtest job isolation

Date: 2026-07-23

## Scope implemented

- WebSocket `/ws` no longer accepts a JWT in the URL. The server accepts the socket, waits up to 5 seconds for `{ "type": "auth", "token": "..." }`, validates the JWT plus the live database user and token version with the same rules as HTTP authentication, and registers the client only after authentication.
- The server sends `{ "type": "auth_ok" }` after registration. The Vue composable sends the auth frame immediately on open and exposes `connected=true` only after this acknowledgement.
- Invalid, missing, timed-out, deleted-user, and stale-token sessions close with code `4001` before registration.
- In-memory backtest jobs now store `user_id`; in-memory and database job retrievals accept an owner filter. `GET /api/backtest/model-job/{jid}` scopes both sources to the current user and returns 404 for another user's identifier.
- Each user can have only one active job. In-process jobs are checked in memory; active systemd jobs are checked in the database. Stale DB rows from an in-process fallback are deliberately excluded from the DB active check because memory is authoritative for those jobs.
- `scope=all` and `koujing=5m` are administrator-only and return 403 before universe loading. Regular users retain `scope=pool`, `koujing=daily` access.

## RED evidence

Command:

`python -m pytest backend/tests/test_websocket_security.py backend/tests/test_backtest_job_security.py -q`

Observed before production changes:

- 3 failed: WebSocket still expected the query token and called the old manager `connect`; memory-backed cross-user lookup returned the job; DB-backed cross-user lookup returned the job.
- After adding access-policy cases, 4 failed: both ownership cases, regular-user global job restriction, and second-active-job rejection were all absent.
- A later focused regression test proved the fallback edge case: `test_database_active_check_ignores_inprocess_fallback_rows` failed because every DB `running` row was treated as active, including an in-process fallback row that would otherwise block the user indefinitely.

All RED failures were caused by the missing required behavior; test setup was adjusted once to avoid accidentally touching a real DB during the active-job RED run.

## GREEN evidence

Focused Task-3 tests after implementation:

`python -m pytest backend/tests/test_websocket_security.py backend/tests/test_backtest_job_security.py -q`

Result: `9 passed` (URL-token-free first-frame auth, stale token rejection, non-auth first frame rejection, memory/DB cross-user 404, DB owner-scoped query, administrator restriction, one-active-job rule, fallback-row handling).

Expanded WebSocket/backtest regression run:

`python -m pytest backend/tests/test_websocket_security.py backend/tests/test_backtester.py backend/tests/test_backtester_5m_honest.py backend/tests/test_backtest_claims.py backend/tests/test_backtest_job_security.py -q`

Result: `42 passed, 1 warning in 3.48s`. The warning is pytest's pre-existing inability to create `.pytest_cache` in this managed Windows workspace; it does not affect test execution.

Python syntax verification:

`python -m compileall -q backend/routers/ws.py backend/core/websocket.py backend/routers/backtest.py backend/services/backtest_jobs.py backend/models/repo/backtest_jobs_db.py`

Result: exit code 0, no output.

Frontend type-check and production build:

`npm.cmd --prefix frontend run build`

Result: exit code 0; `vue-tsc` passed and Vite built 4477 modules. The first sandboxed attempt failed with Windows `EPERM` while resolving the Chinese parent path, so the same build was rerun outside the sandbox and completed successfully.

## Notes / residual concerns

- The one-active-job check is process-safe for the current single FastAPI process and persistent systemd jobs. It is not a database uniqueness constraint, so a future multi-worker deployment could require an atomic DB constraint or transaction to prevent two simultaneous creates for the same user.
- Tests use fakes for database calls as required; no real database or external service was used.
- Tests were placed in new dedicated files because `backend/tests/test_security_hardening.py` already contains uncommitted Task-1 changes and was intentionally not modified.
