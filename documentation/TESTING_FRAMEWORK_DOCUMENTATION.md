# BlueStick Testing Guide

> **Last verified against:** backend 2.370.2 / frontend 5.248.1 (2026-09-19)

## Current Test Stack

- Backend: `pytest` with FastAPI `TestClient`, dual SQLite-or-Postgres fixtures, and coverage enforcement from [`backend/pytest.ini`](/home/charles/Projects/Tools/NetworkMapper/backend/pytest.ini). The suite runs **~1,850 tests** across ~200 modules (v2.370) — the number moves; `pytest --collect-only -q | tail -1` is the source.
- Frontend: `vitest` + Testing Library from [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests).

## The gates (run locally — there is no hosted CI)

The GitHub Actions workflow was removed in 2026-09: it had never run on this repository, so it
enforced nothing. The same three gates exist as local commands; run them before a push.

- **Migrations** — `scripts/test-alembic-roundtrip.sh` boots a throwaway Postgres and walks EVERY
  revision down and back up, so a migration with a broken or no-op `downgrade()` is caught.
  `alembic check` (model-vs-migration drift) is what catches a model module missing from
  `app/db/model_registry.py`.
- **Backend** — `python -m pytest -q` in a one-off container (recipe below). The recipes pass
  `--no-cov` for speed; drop it to check the `--cov-fail-under=68` floor in `backend/pytest.ini`.
- **Frontend** — Node 22 (the image's major): `tsc --noEmit` → `vitest run` → `npm run build`.
  `tsconfig.json` has `noUnusedLocals` / `noUnusedParameters` on, so an unused import fails it
  (and fails the image build).

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
# FROM THE REPO ROOT. Run it from backend/ or frontend/ and Docker creates a root-owned
# stray tree of mount-point stubs there.
R=$PWD; docker compose -f "$R/docker-compose.yml" --project-directory "$R" run --rm --no-deps \
  -v "$R/backend:/app" -v "$R/AGENTS.md:/app/AGENTS.md:ro" -e COVERAGE_FILE=/tmp/.coverage backend \
  sh -c "cd /tmp && python -m pytest /app/tests -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
```

Running from `/tmp` with the cache plugin off keeps root-owned `.pytest_cache` / `.coverage`
files out of the working tree (the older `-w /app` form left them in `backend/`).

**The suite does not touch your dev database's schema or data.** Data: it creates and drops its
own `<database>_test_<pid>` database when the compose `db` is reachable, else uses in-memory
SQLite. Schema: importing the app normally runs `alembic upgrade head` against `DATABASE_URL`,
and `tests/conftest.py` sets `BLUESTICK_SKIP_DB_INIT=1` before that import (v2.370.1) — before
then, a run with an unmerged migration in the tree applied it to the real dev database. Any
OTHER command that imports `app.main` with the tree mounted still migrates.

Mounting `AGENTS.md` keeps the docs-contract tests from skipping (they read it from disk).

Coverage has a `68%` ratchet floor (`--cov-fail-under` in `backend/pytest.ini`) and emits terminal + HTML reports. Measured coverage is **70%** as of v2.232.0; the floor sits just under so ordinary diffs don't trip it on rounding. Raise the floor as coverage climbs — never lower it to turn a red build green. (Before v2.232.0 the gate was configured but CI ran `--no-cov`, so it enforced nothing.)

## Frontend Tests

Location: [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests)

Frontend coverage has grown well beyond the original dashboard/version smoke tests. It now spans page-level views (`Hosts`, `Operations`, `ProjectActivity`, `ExecutionDetail`, `ExecutionsList`, `ReconRunDetail`, `ReconRunsList`, the compare views), shared components (`HostFilters`, `HostCommandBar`, `HostLineagePanel`, `ExecutionSession`), and pure utilities (`dslFromFilters`, `toolReadyOutput`, `navigation`, `versionConsistency`). Tests assert visible outcomes and the host query-DSL translation rather than implementation details.

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

## Guard tests — they fail on drift, on purpose

Update these WITH the change, never around it:

| Test | What it pins |
|---|---|
| `test_schema_fk_ondelete_contract.py` | every `ForeignKey(ondelete=…)` against the ground-truth map (tests build the schema from the models, so a missing `ondelete` makes tests and prod diverge) |
| `test_parser_dispatch_contract.py`, `test_ingestion_format_chain.py`, `test_phase1_regressions.py::test_v2_27_0_content_detection_module_surface` | the three-place parser registration: detection → dispatch → `format_registry.FORMATS` |
| `test_service_router_boundary.py` | a module under `app/services` never imports from `app.api` |
| `test_workbench.py::test_workbench_query_count_is_bounded` | the Operations surface's statement count (add grouped queries, never per-row ones) |
| `test_note_serialization_loads.py`, `test_host_detail_loads.py` | serialising notes / opening a host costs a fixed number of queries; `note_load_options()` loads everything `_serialize_note` reads |
| `test_docs_contract.py`, `test_mcp_tool_endpoint_contract.py` | `AGENTS.md` section markers and slices; every documented agent route and every MCP tool maps to a real endpoint |
| `test_db_init_migration.py` | boot-migration error handling, and that the harness runs with `BLUESTICK_SKIP_DB_INIT=1` |
| `uploadFormats.test.ts`, `uploadFormatContract.test.ts` | the advertised upload formats against `documentation/UPLOAD_FORMATS.md`, and the dropzone allowlist against the backend's |
| `versionConsistency.test.ts` | every version fallback in `docker-compose.yml` against `platform_version.json` |

A regression test earns its place by FAILING on the code it guards: run it against the pre-fix
code before keeping it (`git stash push <the fixed files>`, run, `git stash pop`). Size fixtures
past any UI preview cap, and give fixtures distinct related rows (assignees, promotions) so the
session's identity map cannot hide a per-row query.

## Practical Guidance

- Keep parser tests fixture-backed and deterministic.
- Prefer API-level tests for route behavior and permission checks.
- Prefer frontend tests that assert visible outcomes instead of implementation details.
- When adding new protected endpoints, extend the backend test harness rather than bypassing auth in the application code.
- When adding an agent endpoint, the API call log middleware will capture it automatically — extend `_collect_referenced_ids` in `app/services/agent_api_log_service.py` if the call carries host/entry references the helper doesn't already pick up, and add a regression test.
