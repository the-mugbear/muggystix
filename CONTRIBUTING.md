# Contributing / Maintaining BlueStick

> **Verified against:** backend 2.370.2 / frontend 5.248.1 (2026-09-19).

This is the orientation a developer needs to maintain BlueStick safely. It captures the
project's **conventions and invariants** — the things that aren't obvious from reading the
code and that, if violated, cause subtle breakage. For system topology see
[documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md); for the API see
[documentation/API_GUIDE.md](documentation/API_GUIDE.md); for the agent contract see
[AGENTS.md](AGENTS.md).

---

## Development

```bash
# Backend (dev server, hot reload)
cd backend && pip install -r requirements.txt
export DATABASE_URL=... SECRET_KEY=...        # both required; see the warning below
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (dev server)
cd frontend && npm install && npm start     # alias for `npm run dev` (vite)
npm run build                                # tsc --noEmit && vite build — an unused import FAILS it

# Full stack
./scripts/deploy.sh                          # interactive; option 2 = first-time setup
# or, manually:
cp .env.example .env                         # set SECRET_KEY first
docker compose up --build -d
```

> **Importing `app.main` migrates the database.** `initialize_database()` runs at import, i.e.
> `alembic upgrade head` against `DATABASE_URL` — for the dev server above and for any script or
> `python -c` probe that imports the app. Never point it at a database you do not want migrated.

The deployed backend version surfaces at `GET /` and in startup logs; both versions are shown
in the user menu (top-right) under **About BlueStick**. They must match
`platform_version.json` after a rebuild — they're the visual confirmation that the running app
includes your changes.

## Running tests (and CI)

CI (`.github/workflows/ci.yml`) runs on push-to-main + PRs — three jobs. **alembic-roundtrip**
(`scripts/test-alembic-roundtrip.sh`) boots a throwaway Postgres and walks every revision down
and back up, so a migration needs a genuinely reversible `downgrade()`. A **backend** job (Postgres
service → `alembic upgrade head` → `alembic check` → `pytest -q`, **with the coverage floor
enforced** — `--cov-fail-under` lives in `backend/pytest.ini`) and a **frontend** job
(`tsc --noEmit` → `vitest run` → `npm run build`, on the Node major the image builds with). Keep all three green. `tsc` runs with
`noUnusedLocals` / `noUnusedParameters`, so an unused import fails the frontend job and the
image build. The local recipes below pass `--no-cov` for speed; CI does not.

- **Frontend** tests run on the host: `cd frontend && npx vitest run` / `npx tsc --noEmit`.
- **Backend** tests do **not** run on the host — there's no host `pytest`, and `app/` is baked
  into the backend image (compose bind-mounts only `tests/`, `pytest.ini`, `scripts/`, `artifacts/`,
  `uploads/`, `AGENTS.md` and `platform_version.json`), so edits to `backend/app/**` aren't
  live in the running container. To run the suite against **uncommitted** changes, mount the
  host source into a one-off container:

  ```bash
  # FROM THE REPO ROOT — run it from backend/ or frontend/ and Docker creates a
  # root-owned stray tree of mount-point stubs there.
  R=$PWD; docker compose -f "$R/docker-compose.yml" --project-directory "$R" run --rm --no-deps \
    -v "$R/backend:/app" -v "$R/AGENTS.md:/app/AGENTS.md:ro" -e COVERAGE_FILE=/tmp/.coverage backend \
    sh -c "cd /tmp && python -m pytest /app/tests -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
  ```

  Running from `/tmp` with the cache plugin off keeps root-owned `.pytest_cache` / `.coverage`
  files out of your working tree.

  `--no-deps` starts no other service. The suite's DATA is isolated: when the compose `db` is
  reachable it creates and drops its own `<database>_test_<pid>` database, otherwise it falls
  back to in-memory SQLite (`tests/conftest.py`). It also does not migrate your dev database:
  importing the app normally runs `alembic upgrade head` against `DATABASE_URL`, and the
  harness turns that off with `BLUESTICK_SKIP_DB_INIT=1` (since v2.370.1 — before that, a test
  run with an unmerged migration in the tree applied it to the real dev database). Anything
  ELSE that imports `app.main` — a script, a `python -c` probe — still migrates; a data
  migration gets applied the moment you do that, so export first.
  Mounting `AGENTS.md` keeps the docs-contract tests from skipping.

## Versioning (keep four files in sync)

After any change that ships, bump the version so a rebuilt container visibly reflects it.
**All four must agree** — `frontend/src/tests/versionConsistency.test.ts` fails on drift:

1. `platform_version.json` — source of truth (`backend`, `frontend`)
2. `frontend/package.json` — `version`
3. `docker-compose.yml` — EVERY `APP_VERSION` / `FRONTEND_VERSION` fallback and the
   `BACKEND_VERSION` build arg (six occurrences today)
4. `frontend/package-lock.json` — its two `version` fields (`npm install --package-lock-only`
   after bumping; CI's `npm ci` fails when the lock disagrees with `package.json`)

Format `MAJOR.MINOR.PATCH`. Bump **patch** for fixes, **minor** for features/refactors,
**major** for breaking changes. Bump **backend** when backend code changes, **frontend** when
frontend changes, **both** when a change spans both. Test-only changes don't need a bump.

## Database schema & migrations

**Alembic owns the schema.** Every backend/worker container runs `alembic upgrade head` on
boot before serving traffic. There is no startup-DDL / `create_all` path — if a column needs to
exist, it lives in the model **and** in an Alembic revision under `backend/alembic/versions/`.
Never run `Base.metadata.create_all` against a live database.

- **Authoring a migration.** Revisions here are mostly hand-written (copy the newest file in
  `backend/alembic/versions/` for the shape; `alembic heads` names the current head). To
  autogenerate, use a ONE-OFF container with `backend/` mounted — `docker compose run --rm
  --no-deps -v "$PWD/backend:/app" backend alembic revision --autogenerate -m '…'` — because the
  running container's `alembic/` is baked into the image, so a file generated by `docker compose
  exec` never reaches your tree. READ what it generated; register any new model module in `app/db/model_registry.py` (or
  CI's `alembic check` proposes dropping its tables); write a real `downgrade()` (the round-trip
  job runs it). A DATA migration is applied to your dev database the moment anything imports the
  app with it in the tree — export the affected rows first.
- **FK `ON DELETE` contract.** A model's `ForeignKey(...)` MUST declare the same `ondelete=`
  the database enforces — the test schema is built from the models via `create_all()`, so a
  missing `ondelete` makes tests and prod diverge on cascade behaviour.
  `tests/test_schema_fk_ondelete_contract.py` pins every FK and fails on drift; update its map
  in the same commit you change an FK.
- **Column-vs-blob policy.** Give a parser value a **typed column** if any view, filter, DSL
  predicate, dashboard, or insight needs to query/sort/aggregate on it across hosts. Keep it in
  a `raw`/JSON blob only if it's opaque provenance never queried by a column predicate. A blob
  that ends up in a `WHERE`/`GROUP BY` is a signal to promote the field, not to add a functional
  index.
- **Dedup invariants worth monitoring** (should always return zero rows):
  - hosts: `SELECT ip_address, project_id, COUNT(*) FROM hosts_v2 GROUP BY ip_address, project_id HAVING COUNT(*) > 1`
    (the `Host` model's table is `hosts_v2`; `Port` is `ports_v2`)
  - vulnerabilities: group by `host_id, source, plugin_id, port_id, title HAVING COUNT(*) > 1`
    (vuln dedup is application-level — every insert path must `db.flush()` after `db.add()` so an
    in-scan repeat is found by its own existence check; the session runs `autoflush=False`).

## Roles — two layers

`UserRole` (`app/db/models_auth.py`) is **binary**: `ADMIN` (user management, system settings,
audit log) or `MEMBER`. All granular capability lives on `ProjectMembership.role`
(`models_project.py::ProjectRole`: admin > analyst > auditor > viewer), checked with
`require_project_role`. Never add a capability tier to the global role, and never gate a
feature on a global "analyst" — it does not exist.

## Host deduplication (the production model)

Every IP within a project owns a **single `Host` row**; repeated scans update it rather than
creating parallel copies. Full history is preserved in `HostScanHistory`; per-attribute
confidence + conflicts are tracked by the confidence service
(`app/services/host_deduplication_service.py`, called from every parser). Ports from all scans
are aggregated per host with conflict-resolution rules.

## File-size policy

The target is **monoliths** — unfocused files with multiple unrelated responsibilities — not
large files per se. A big file with one cohesive, deliberate purpose is **not** tech debt;
carving it to shrink a number makes it worse. When a feature next lands in a file at/above
**~1,500 LOC**, evaluate a split, and carve only if **all** hold: (1) there's a genuine seam
(2+ distinct responsibilities not sharing much state), (2) the feature is actually landing in
that file now, and (3) the split reduces real conflict or cognitive load. Otherwise leave it.

## Agent workflows & the agent contract

BlueStick exposes a `/api/v1/agent/*` surface for terminal-side AI agents, **physically
separate** from the JWT user API (different auth — `X-API-Key`; different dependency chain;
different router files). Since v2.337.0 an operator starts **one project-scoped agent session**
with one time-limited, renewable key, and the session opens a *phase* for each kind of work —
assist reads by default, then reconnaissance, plan drafting or execution. There is no
per-workflow key, no workflow guard (`require_*_scope` are gone) and no capability grant
(deleted v2.309.0): every agent request is checked against its **operator's project role**,
re-resolved per request (`enforce_agent_operator_access` in `app/api/deps.py`). What keeps the
record trustworthy is object-level — a plan must be human-approved, one active run per plan,
and a run belongs to the session that opened it.

- **`AGENTS.md`** (repo root) is the contract every agent reads at startup, served sliced by
  workflow at `GET /api/v1/agents-guide?workflow=…` via the `<!-- agents:section -->` markers.
- **Bump `PROMPT_VERSION`** whenever the agent's instructions change materially: PREPEND an entry
  to `PROMPT_VERSION_HISTORY` in `app/services/agent_prompt_history.py`. The version is computed
  from element 0, so appending does nothing.
- **`tests/test_docs_contract.py`** guards both surfaces: AGENTS.md section markers stay balanced
  and every workflow slice keeps its body; every described OpenAPI tag is used by a route; and
  every agent endpoint documented in AGENTS.md's API-reference tables exists. If you rename or
  remove an agent route, update AGENTS.md (and the OpenAPI tags in `app/main.py`) in the same
  commit or this test fails.

## Frontend UI

All frontend changes **must** comply with [documentation/UI_STYLE_GUIDE.md](documentation/UI_STYLE_GUIDE.md)
— a behavioral contract, not optional guidance. Key rules: no page-level horizontal overflow;
every text-bearing component defines overflow behaviour (truncate/wrap/clamp/collapse); handle
null/empty/loading/error states with safe fallbacks; tables use `tableLayout: 'fixed'` with
explicit column widths; flex children that truncate include `minWidth: 0`. Verify changes with
worst-case data (200-char hostname, long filename, null values) at mobile and desktop widths.
The app is **desktop-first** — don't build mobile-mirrored layouts.

## Changelog

`CHANGELOG.md` is maintained in the repo root (kept local-only / gitignored in the public
mirror — maintain it regardless). Each entry: ISO-8601 date + `HH:MM UTC`, a category
(`fix`/`feat`/`refactor`/`security`/`cleanup`/`docs`/`chore`), and a short summary of what
changed and why. Most-recent date first.
