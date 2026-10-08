# BlueStick Testing Guide

> **Last verified against:** backend 2.450.0 / frontend 5.329.0 (2026-10-02)

## Current Test Stack

- Backend: `pytest` with FastAPI `TestClient`, dual SQLite-or-Postgres fixtures, and coverage measurement configured in [`backend/pytest.ini`](/home/charles/Projects/Tools/NetworkMapper/backend/pytest.ini) (a `--cov-fail-under` floor that no routine run enforces — every recipe and `check.sh` pass `--no-cov`; see "The gates"). For the size of the suite, `pytest --collect-only -q | tail -1` is the source.
- Frontend: `vitest` + Testing Library from [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests).

## The gates (run locally — there is no hosted CI)

The GitHub Actions workflow was removed in 2026-09: it had never run on this repository, so it
enforced nothing. **One command runs all three gates — `./scripts/check.sh` (or `make check`;
`--fast` skips the migration round trip) — and it is what to run before a push.** It runs the
backend suite in the **report-worker** image (the backend image plus Quarto, `report-templates/`
mounted) and **fails if any test skipped for want of Quarto or the templates**: with the plain
`backend` recipe below, the client-report tests — the hostile-text contract among them — skip,
and the run is green without them. Its second step is `ruff check` over `backend/` in the same
image (pyflakes rules only, `backend/ruff.toml`): any finding fails the gate. It ends with a
one-screen summary and a non-zero exit on any failure. The gates, for running one alone:

- **Migrations** — `scripts/test-alembic-roundtrip.sh` boots a throwaway Postgres and walks EVERY
  revision down and back up, so a migration with a broken or no-op `downgrade()` is caught.
  `alembic check` (model-vs-migration drift) is what catches a model module missing from
  `app/db/model_registry.py`.
- **Backend** — `python -m pytest -q` in a one-off container (recipe below). The recipes and
  `check.sh` pass `--no-cov` for speed, so the `--cov-fail-under=68` floor in
  `backend/pytest.ini` is **not enforced by any routine run**; drop `--no-cov` to measure it.
- **Frontend** — Node 22 (the image's major): `tsc --noEmit` → `vitest run` (what `check.sh`
  runs, on the host); `npm run build` is the image build's step and runs `tsc --noEmit` again.
  `tsconfig.json` has `noUnusedLocals` / `noUnusedParameters` on, so an unused import fails it
  (and fails the image build).

## Backend Tests

Location: [`backend/tests`](/home/charles/Projects/Tools/NetworkMapper/backend/tests)

Key behavior of the current backend harness:

- **Database selection.** The fixture prefers a real PostgreSQL test DB and falls back to in-memory SQLite when no Postgres server is reachable. Resolution order:
  1. `$TEST_DATABASE_URL` if set (explicit override).
  2. A `<app-db>_test_<host>_<pid>` database on the app's own Postgres server, created for the run and dropped at session end (a hard-killed run leaves it behind).
  3. In-memory SQLite.
  The Postgres path lets Postgres-only code (`pg_advisory_lock`, masscan batch-upserts, the raw `pg_catalog` SQL in `delete_scan`) actually run; the SQLite fallback skips those tests cleanly via `USING_POSTGRES`.
- **Transactional isolation.** `conftest.py::db_session` uses the SQLAlchemy join-to-outer-transaction + nested-savepoint pattern so services that commit internally (integration credentials, LLM providers, the agent API log middleware) still leave the test in a clean state. The v2.24.0 middleware writes via its own `SessionLocal()`; the fixture rebinds that to the test connection so middleware-written rows roll back at teardown — no cross-test leakage.
- **Auth.** `get_current_user` is overridden with a persisted admin row so protected JWT routes accept the test client without a real login flow.
- **Coverage.** Covers parsers (every supported scanner), services (deduplication, subnet correlation, SBOM cache, posture, finding correlation, agent attribution), the agent surface (browse, host tests and evidence, proposals, scope reads and uploads, **assist incl. the query-DSL**, API audit log), upload flow, prompt sanitisation, URL validation, and cross-user isolation invariants.

Run locally after installing backend dependencies:

```bash
cd backend
pip install -r requirements-dev.txt -c constraints.txt
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
  -v "$R/backend:/app" -v "$R/documentation/AGENT_GUIDE.md:/app/AGENT_GUIDE.md:ro" -e COVERAGE_FILE=/tmp/.coverage backend \
  sh -c "cd /tmp && python -m pytest /app/tests -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
```

Running from `/tmp` with the cache plugin off keeps root-owned `.pytest_cache` / `.coverage`
files out of the working tree (the older `-w /app` form left them in `backend/`).

**The suite does not touch your dev database's schema or data.** Data: it creates and drops its
own `<database>_test_<host>_<pid>` database when the compose `db` is reachable, else uses in-memory
SQLite. Schema: importing the app normally runs `alembic upgrade head` against `DATABASE_URL`,
and `tests/conftest.py` sets `BLUESTICK_SKIP_DB_INIT=1` before that import (v2.370.1) — before
then, a run with an unmerged migration in the tree applied it to the real dev database. Any
OTHER command that imports `app.main` with the tree mounted still migrates.

Mounting the agent guide keeps the docs-contract tests from skipping (they read it from disk).

Coverage has a `68%` ratchet floor (`--cov-fail-under` in `backend/pytest.ini`) and emits terminal + HTML reports. Coverage was last measured at **70%** at v2.232.0 (not re-measured since); the floor sits just under so ordinary diffs don't trip it on rounding. Raise the floor as coverage climbs — never lower it to turn a red build green. (Before v2.232.0 the gate was configured but CI ran `--no-cov`, so it enforced nothing.)

## Frontend Tests

Location: [`frontend/src/tests`](/home/charles/Projects/Tools/NetworkMapper/frontend/src/tests)

Frontend coverage has grown well beyond the original dashboard/version smoke tests. It now spans page-level views (`Hosts`, `Operations`, `ProjectActivity`, `AgentSessionDetail`, the scan compare view), shared components (`HostFilters`, `HostCommandBar`, `HostInspector`, `ProposeTestsDialog`), and pure utilities (`dslFromFilters`, `toolReadyOutput`, `navigation`, `versionConsistency`). Tests assert visible outcomes and the host query-DSL translation rather than implementation details.

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

Lint (ESLint, `frontend/eslint.config.mjs`):

```bash
cd frontend
npm run lint
```

A small rule set on purpose: the Rules of Hooks (error), effect dependencies (warning), the
`services/api` barrel import (a component or page that imports a submodule bypasses a test's
mock of the barrel), no hand-set `.download =` (use `utils/download.saveBlob`) and no bare
`new Date(x).toLocaleString()` (use `formatTimestamp`). The gate runs it with
`--max-warnings 0`. The last two were tests that searched the source; they are lint rules now,
and those tests are gone.

## Regression-pin file

`backend/tests/test_phase1_regressions.py` is the home for regressions that pin specific past bugs. It currently holds ~39 tests covering: the content-detection module surface, cross-project host-test visibility (GET and PATCH 404), an unknown host-test status refused on both route families, SBOM cache invalidation on app-version change, the prompt-version floor, the v2.24.0 agent API call log helpers + middleware + retention, the agent rate limit, the unified agent-session timeline and the coverage summary's planned / tested definitions. Add to this file when fixing a regression so it can't silently come back.

## Docs-vs-code contract tests

`backend/tests/test_docs_contract.py` keeps the documentation tied to the code so it can't drift
silently (it has before — the guide, then named `AGENTS.md`, once ran ~120 releases stale). It asserts: the guide's
`<!-- agents:section -->` markers stay balanced and every workflow slice keeps its body; every
OpenAPI tag described in `app/main.py` is used by a real route (and the agent-workflow tags are
all described); and every agent endpoint documented in the guide's API-reference tables exists as
a route. **If you rename or remove an agent route or an OpenAPI tag, update `documentation/AGENT_GUIDE.md` / `main.py`
in the same commit or this test fails.**

## Guard tests — they fail on drift, on purpose

Update these WITH the change, never around it:

| Test | What it pins |
|---|---|
| `test_schema_fk_ondelete_contract.py` | every `ForeignKey(ondelete=…)` against the ground-truth map (tests build the schema from the models, so a missing `ondelete` makes tests and prod diverge) |
| `test_parser_dispatch_contract.py`, `test_ingestion_format_chain.py`, `test_phase1_regressions.py::test_v2_27_0_content_detection_module_surface` | the three-place parser registration: detection → dispatch → `format_registry.FORMATS` |
| `test_parser_coverage.py` | every format has an entry in `app/data/parser_coverage.json` (the "What BlueStick reads" page), and what it claims matches what the parsers write |
| `test_host_list_query_budget.py`, `test_ingestion_query_budget.py`, `test_host_loading.py` | the /hosts list does not issue a query per host; ingestion dedup stays linear in host count; querying hosts is one statement |
| `test_host_query_suggest.py::test_every_value_source_is_enumerable_or_deliberately_not` | every `/hosts` DSL field has a value source the autocomplete enumerates, or is `enum`/`window`/`free` on purpose |
| `test_quarto_render.py::test_hostile_text_stays_text_in_every_format`, `test_report_templates_shipped.py`, `test_report_templates_escaping.py`, `test_report_template_assets.py` | every template in `report-templates/` keeps hostile text as text (run in the report-worker image — Quarto tests skip in the backend image); every shipped template is offered with no problems |
| `test_service_router_boundary.py` | a module under `app/services` never imports from `app.api`; an endpoint module never imports another endpoint module's `_private` name; `app/api/deps.py` / `app/api/params.py` never import a router |
| `test_finding_loading.py`, `test_read_path_review.py` | querying findings is one statement and loads no endpoints; rewritten Hosts predicates return the same hosts as the old ones, within a statement budget, the revision's indexes match the models, and no host predicate is an `IN (subquery)` (`test_no_host_predicate_is_an_in_subquery_any_more`) |
| `test_agent_role_route_matrix.py` | every agent read's role floor, declared on its route with `agent_read_floor(...)`, equals the role its page asks of a person — a table of every such read, checked both ways |
| `test_review_2026_10_01_integration.py` | every route that queries while streaming is exempt from the API statement timeout (add a new streamed route there) |
| `test_mcp_enum_contract.py` | an MCP tool's schema is derived from its endpoint, and what the registry authors over it never widens it: every value an endpoint restricts to an enum is advertised as an enum no wider than the endpoint's |
| `test_ingestion_partial_scan.py`, `test_ingestion_late_cleanup.py` | a failed, cancelled or killed import leaves nothing only it created, and a late cleanup never deletes what a later import re-observed (run parser heartbeat tests under `tests/ingestion_job_harness.py` — without an active job `report_progress` is a no-op) |
| `test_db_init_concurrent_boot.py` | five real processes booting together finish their migrations (no hang on the migration lock) |
| `test_ops_scripts.py` | the real deploy / backup / restore / rollback scripts against a fake `docker` (`tests/ops_fake_docker.py`); a change to those scripts gets a case there |
| `test_workbench.py::test_workbench_query_count_is_bounded` | the Operations surface's statement count (add grouped queries, never per-row ones) |
| `test_note_serialization_loads.py`, `test_host_detail_loads.py` | serialising notes / opening a host costs a fixed number of queries; `note_load_options()` loads everything `_serialize_note` reads |
| `test_docs_contract.py`, `test_mcp_tool_endpoint_contract.py` | the agent guide's section markers and slices; every documented agent route and every MCP tool maps to a real endpoint |
| `test_db_init_migration.py` | boot-migration error handling, and that the harness runs with `BLUESTICK_SKIP_DB_INIT=1` |
| `uploadFormats.test.ts`, `uploadFormatContract.test.ts` | the advertised upload formats against `documentation/UPLOAD_FORMATS.md`, and the dropzone allowlist against the backend's |
| `versionConsistency.test.ts` | every version fallback in `docker-compose.yml` against `platform_version.json` |
| `themeAlphaTokens.test.ts` | a theme token that carries its own alpha (`--muted`, `--accent`, `--border`, `--sidebar-accent`) is not wrapped with `/ <alpha-value>` in `tailwind.config.ts` |
| `proxyHeaders.test.ts` | nginx: `X-Forwarded-For` is always the peer address (never appended to a client's); every `/api/` location carries `^~`; the auth surface stays capped at 1m; every location that declares an `add_header` repeats the full security-header set |
| `navigation.test.ts` | a page's role in `config/navigation.tsx` equals its route's in `App.tsx` |

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
