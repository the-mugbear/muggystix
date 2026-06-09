# BlueStick Testing Guide

> **Last verified against:** backend 2.115.0 / frontend 5.25.1 (2026-06-07)

## Current Test Stack

- Backend: `pytest` with FastAPI `TestClient`, dual SQLite-or-Postgres fixtures, and coverage enforcement from [`backend/pytest.ini`](/home/charles/Projects/Tools/NetworkMapper/backend/pytest.ini). The suite currently runs **~640 tests** across ~70 files.
- Frontend: `vitest` + Testing Library from [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests).

## Backend Tests

Location: [`backend/tests`](/home/charles/Projects/Tools/NetworkMapper/backend/tests)

Key behavior of the current backend harness:

- **Database selection.** The fixture prefers a real PostgreSQL test DB and falls back to in-memory SQLite when no Postgres server is reachable. Resolution order:
  1. `$TEST_DATABASE_URL` if set (explicit override).
  2. A `<app-db>_test` database on the app's own Postgres server, auto-created if absent.
  3. In-memory SQLite.
  The Postgres path lets Postgres-only code (`pg_advisory_lock`, masscan batch-upserts, the raw `pg_catalog` SQL in `delete_scan`) actually run; the SQLite fallback skips those tests cleanly via `USING_POSTGRES`.
- **Transactional isolation.** `conftest.py::db_session` uses the SQLAlchemy join-to-outer-transaction + nested-savepoint pattern so services that commit internally (integration credentials, LLM providers, the agent API log middleware) still leave the test in a clean state. The v2.24.0 middleware writes via its own `SessionLocal()`; the fixture rebinds that to the test connection so middleware-written rows roll back at teardown — no cross-test leakage.
- **Auth.** `get_current_user` is overridden with a persisted admin row so protected JWT routes accept the test client without a real login flow.
- **Coverage.** Covers parsers (every supported scanner), services (deduplication, subnet correlation, risk, SBOM cache, environment probe), the agent surface (browse, plan generation, execution + sanity-check enforcement, recon, API audit log), upload flow, bundle import, prompt sanitisation, URL validation, and cross-user isolation invariants.

Run locally after installing backend dependencies:

```bash
cd backend
pip install -r requirements.txt
python -m pytest --no-cov -q          # quick smoke
python -m pytest                       # full run with coverage
```

Inside the Docker stack, with source mounted for fast iteration:

```bash
docker compose run --rm --no-deps \
  -v $PWD/backend:/app -w /app backend \
  python -m pytest --no-cov -q
```

Coverage is enforced at `70%` and configured to emit terminal and HTML reports.

## Frontend Tests

Location: [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests)

Frontend coverage has grown well beyond the original dashboard/version smoke tests. It now spans page-level views (`Hosts`, `Operations`, `ProjectActivity`, `ExecutionDetail`, `ExecutionsList`, `ReconRunDetail`, `ReconRunsList`, the compare views), shared components (`HostFilters`, `HostCommandBar`, `HostLineagePanel`, `ExecutionSession`, `VersionFooter`), and pure utilities (`dslFromFilters`, `toolReadyOutput`, `navigation`, `versionConsistency`). Tests assert visible outcomes and the host query-DSL translation rather than implementation details.

Run locally:

```bash
cd frontend
npm install
npm test -- --run
```

Type-check the whole frontend without running tests:

```bash
cd frontend
npx tsc --noEmit
```

Strict-mode TypeScript is enforced; every PR should typecheck clean before merge.

## Regression-pin file

`backend/tests/test_phase1_regressions.py` is the home for regressions that pin specific past bugs. It currently holds ~86 tests covering: recon-session FK race, sanity-check uniqueness widening, cross-project plan visibility, brief-mode policy parity, multibyte byte-cap truncation, SBOM cache invalidation on app-version change, sanity-check enforcement on `/complete` (with override-reason audit), environment probe round-trip (recon + execution), cross-user environment isolation, and the v2.24.0 agent API call log helpers + middleware + retention. Add to this file when fixing a regression so it can't silently come back.

## Practical Guidance

- Keep parser tests fixture-backed and deterministic.
- Prefer API-level tests for route behavior and permission checks.
- Prefer frontend tests that assert visible outcomes instead of implementation details.
- When adding new protected endpoints, extend the backend test harness rather than bypassing auth in the application code.
- When adding an agent endpoint, the API call log middleware will capture it automatically — extend `_collect_referenced_ids` in `app/services/agent_api_log_service.py` if the call carries host/entry references the helper doesn't already pick up, and add a regression test.
