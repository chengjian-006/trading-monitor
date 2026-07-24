# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Remove the identified public write, session-revocation, transport, token-leak, resource-isolation, dependency, and browser-rendering risks.

**Architecture:** Keep the existing FastAPI bearer-token model for HTTP requests while making token revocation authoritative in the database. Authenticate WebSockets with an initial message rather than a URL token. Serve the application solely from the HTTPS application hostname; route public extension ingestion through a dedicated rotatable secret.

**Tech Stack:** FastAPI, aiomysql, Vue 3, Vite, Chrome MV3 extension, Nginx, pytest, npm.

## Global Constraints

- Production Uvicorn listens only on `127.0.0.1:8888`.
- All public application traffic uses HTTPS/WSS; the bare IP is not an application origin.
- Security tests are written and observed failing before implementation changes.
- Secrets remain in ignored `config.json`; no production secret is added to Git.

---

### Task 1: Authoritative session revocation and password policy

**Files:**
- Modify: `backend/core/auth.py`, `backend/models/repo/users.py`, `backend/routers/users.py`
- Modify: `backend/tests/test_security_hardening.py`

- [x] Add failing tests proving role changes and password resets invalidate an existing token version.
- [x] Verify those tests fail against the current repository behavior.
- [x] Increment the database token version as part of security-sensitive user updates and load the live user role during token validation.
- [x] Require a bounded, minimum-length password and use constant-time password-hash comparison.
- [x] Run the focused security tests.

### Task 2: Protected extension opinion ingestion

**Files:**
- Modify: `backend/core/config.py`, `config.example.json`, `backend/routers/wencai.py`
- Modify: `extension/wencai-opinion/{background,content,options,popup}.js`
- Modify: `backend/tests/test_wencai_opinion.py`

- [x] Add a failing test that rejects absent or incorrect opinion-ingestion tokens.
- [x] Verify the test fails because the route presently accepts empty tokens.
- [x] Add a distinct ignored-config secret, compare it with `hmac.compare_digest`, and reject empty configuration or requests.
- [x] Send the configured extension token only over the HTTPS endpoint and document the required one-time configuration.
- [x] Run the focused ingestion tests.

### Task 3: WebSocket and job isolation

**Files:**
- Modify: `backend/routers/ws.py`, `backend/core/websocket.py`, `frontend/src/composables/useWebSocket.ts`
- Modify: `backend/routers/backtest.py`, `backend/services/backtest_jobs.py`, `backend/models/repo/backtest_jobs_db.py`
- Modify: `backend/tests/test_security_hardening.py`, `backend/tests/test_backtest*.py`

- [x] Add failing tests for WebSocket authentication without a URL token and for another user being unable to read a job.
- [x] Verify the ownership test fails against the current job lookup.
- [x] Move WebSocket authentication to the first client message with a short authentication timeout.
- [x] Carry job ownership in memory and database lookups; restrict expensive global and 5-minute jobs to administrators and enforce one active job per user.
- [x] Run focused WebSocket and backtest tests.

### Task 4: HTTPS-only deployment and HTTP security headers

**Files:**
- Modify: `nginx-default.conf`, `nginx-site.conf`, `README.md`, `extension/wencai-opinion/manifest.json`

- [x] Replace the default HTTP application server with a non-serving fallback.
- [x] Redirect the named HTTP hosts to HTTPS and add a TLS application server with HSTS and baseline security headers.
- [x] Remove HTTP and bare-IP extension permissions; set the extension server default to the HTTPS application host.
- [x] Correct deployment documentation so the backend is never started on `0.0.0.0` in production.
- [x] Validate Nginx syntax when an Nginx binary is available and check all source references for obsolete HTTP application URLs.

### Task 5: Browser rendering and dependency supply chain

**Files:**
- Modify: `frontend/src/views/SignalView.vue`, `frontend/src/components/chart/KLineChart.vue`, `frontend/src/utils/exportXlsx.ts`
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `requirements.txt`
- Create: `.github/workflows/security.yml`

- [x] Add tests or build checks demonstrating that untrusted chart/report text is escaped or sanitized.
- [x] Replace unsafe raw HTML handling with DOMPurify and avoid unescaped `innerHTML` values.
- [x] Replace the vulnerable spreadsheet package with a maintained export-only library; upgrade Axios and regenerate the package lock.
- [x] Pin direct Python dependency versions and add CI checks for `pip-audit` and `npm audit`.
- [x] Run frontend build, npm audit, Python tests, and dependency checks.

### Task 6: Final verification and operational handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-23-security-hardening.md`

- [x] Run the full backend test suite.
- [x] Run the frontend production build and dependency audit.
- [x] Inspect the final diff and working tree for unintended files or secrets.
- [x] Mark completed plan steps and document any environment-only deployment action.

---

## Verification Record (2026-07-24)

- Backend full suite: `python -m pytest backend/tests -q` → **1249 passed, 2 warnings** (both pre-existing: a Starlette/httpx deprecation and a benign never-awaited-coroutine warning inside a health-check test's own fixture).
- Frontend: `npm --prefix frontend run build` → **vue-tsc + Vite build ✓**; `node frontend/security-check.mjs` → injection checks passed; `npm --prefix frontend audit` → **0 vulnerabilities**.
- Python deps: `requirements.txt` fully pinned with `==`.
- Task 2 (protected opinion ingestion) note: the endpoint was already token-gated in production from H4 (v1.7.653); this branch tightens it to fail-closed on a blank/mismatched dedicated `wencai_opinion.ingest_token` via `hmac.compare_digest`.

## Environment-only deployment actions (must be done on the server, not in Git)

1. Set `wencai_opinion.ingest_token` to a fresh random secret in the production `config.json` (never committed). Uploads fail closed until this is set.
2. Configure the browser extension's options with the same token over the HTTPS endpoint (one-time).
3. `pip-audit` runs in the `.github/workflows/security.yml` CI gate (not installed in the local Windows workspace); confirm the CI run is green after push.
4. TLS/HSTS and the HTTPS-only Nginx server assume valid certificates on the application host; verify certs before reloading Nginx in production.
