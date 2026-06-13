# BlueStick Testing Guide

> **Last verified against:** backend 2.201.0 / frontend 5.106.0 (2026-06-13)

## Current Test Stack

- Backend: `pytest` with FastAPI `TestClient`, dual SQLite-or-Postgres fixtures, and coverage enforcement from [`backend/pytest.ini`](/home/charles/Projects/Tools/NetworkMapper/backend/pytest.ini). The suite currently runs **~750 tests** across ~100 files.
- Frontend: `vitest` + Testing Library from [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests).

## Continuous Integration

`.github/workflows/ci.yml` runs the gates on push-to-main and PRs — these tests are no longer
local-only:

- **Backend job** — spins up a Postgres service, runs `alembic upgrade head` (so a broken or
  irreversible migration fails CI), then `python -m pytest -q --no-cov`.
- **Frontend job** — `tsc --noEmit` → `vitest run` → `npm run build`.

Keep both green; a red gate blocks the merge. The backend job runs from `backend/` and resolves
repo-root files (e.g. `AGENTS.md`) via `..`, so the docs-contract tests run rather than skip.

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
- **Coverage.** Covers parsers (every supported scanner), services (deduplication, subnet correlation, SBOM cache, posture, finding correlation, environment probe), the agent surface (browse, plan generation, execution + sanity-check enforcement, recon, **assist incl. the query-DSL**, API audit log), upload flow, bundle import, prompt sanitisation, URL validation, and cross-user isolation invariants.

Run locally after installing backend dependencies:

```bash
cd backend
pip install -r requirements.txt
python -m pytest --no-cov -q          # quick smoke
python -m pytest                       # full run with coverage
```

Inside the Docker stack, with source mounted for fast iteration (this is the **canonical way to
run the backend suite against uncommitted `app/` changes** — there is no host `pytest`, and `app/`
is baked into the image so only mounting the host source picks up your edits):

```bash
docker compose run --rm --no-deps \
  -v "$PWD/backend:/app" -v "$PWD/AGENTS.md:/app/AGENTS.md:ro" -w /app backend \
  python -m pytest --no-cov -q
```

Mounting `AGENTS.md` keeps the docs-contract tests from skipping (they read it from disk).

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

`backend/tests/test_phase1_regressions.py` is the home for regressions that pin specific past bugs. It currently holds ~89 tests covering: recon-session FK race, sanity-check uniqueness widening, cross-project plan visibility, brief-mode policy parity, multibyte byte-cap truncation, SBOM cache invalidation on app-version change, sanity-check enforcement on `/complete` (with override-reason audit), environment probe round-trip (recon + execution), cross-user environment isolation, and the v2.24.0 agent API call log helpers + middleware + retention. Add to this file when fixing a regression so it can't silently come back.

## Docs-vs-code contract tests

`backend/tests/test_docs_contract.py` keeps the documentation tied to the code so it can't drift
silently (it has before — AGENTS.md once ran ~120 releases stale). It asserts: AGENTS.md
`<!-- agents:section -->` markers stay balanced and every workflow slice keeps its body; every
OpenAPI tag described in `app/main.py` is used by a real route (and the agent-workflow tags are
all described); and every agent endpoint documented in AGENTS.md's API-reference tables exists as
a route. **If you rename or remove an agent route or an OpenAPI tag, update AGENTS.md / `main.py`
in the same commit or this test fails.**

## Practical Guidance

- Keep parser tests fixture-backed and deterministic.
- Prefer API-level tests for route behavior and permission checks.
- Prefer frontend tests that assert visible outcomes instead of implementation details.
- When adding new protected endpoints, extend the backend test harness rather than bypassing auth in the application code.
- When adding an agent endpoint, the API call log middleware will capture it automatically — extend `_collect_referenced_ids` in `app/services/agent_api_log_service.py` if the call carries host/entry references the helper doesn't already pick up, and add a regression test.
