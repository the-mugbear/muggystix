# Contributing / Maintaining BlueStick

> **Verified against:** backend 2.201.0 / frontend 5.106.0 (2026-06-13).

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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (dev server)
cd frontend && npm install && npm start     # alias for `npm run dev` (vite)
npm run build                                # production build

# Full stack
./scripts/deploy.sh                          # interactive; option 2 = first-time setup
# or, manually:
cp .env.example .env                         # set SECRET_KEY first
docker compose up --build -d
```

The deployed backend version surfaces at `GET /` and in startup logs; the frontend version
renders in the VersionFooter (bottom-right). Both must match `platform_version.json` after a
rebuild — they're the visual confirmation that the running app includes your changes.

## Running tests (and CI)

CI (`.github/workflows/ci.yml`) runs on push-to-main + PRs: a **backend** job (Postgres
service → `alembic upgrade head` → `pytest -q --no-cov`) and a **frontend** job
(`tsc --noEmit` → `vitest run` → `npm run build`). Keep both green.

- **Frontend** tests run on the host: `cd frontend && npx vitest run` / `npx tsc --noEmit`.
- **Backend** tests do **not** run on the host — there's no host `pytest`, and `app/` is baked
  into the backend image (only `tests/` is bind-mounted), so edits to `backend/app/**` aren't
  live in the running container. To run the suite against **uncommitted** changes, mount the
  host source into a one-off container:

  ```bash
  docker compose run --rm --no-deps \
    -v "$PWD/backend:/app" -v "$PWD/AGENTS.md:/app/AGENTS.md:ro" \
    backend python -m pytest -q --no-cov
  ```

  `--no-deps` skips Postgres (the suite uses its own isolated engine via `tests/conftest.py`).
  Mounting `AGENTS.md` keeps the docs-contract tests from skipping.

## Versioning (keep three files in sync)

After any change that ships, bump the version so a rebuilt container visibly reflects it.
**All three must agree:**

1. `platform_version.json` — source of truth (`backend`, `frontend`)
2. `frontend/package.json` — `version`
3. `docker-compose.yml` — `APP_VERSION` / `FRONTEND_VERSION` defaults (several occurrences)

Format `MAJOR.MINOR.PATCH`. Bump **patch** for fixes, **minor** for features/refactors,
**major** for breaking changes. Bump **backend** when backend code changes, **frontend** when
frontend changes, **both** when a change spans both. Test-only changes don't need a bump.

## Database schema & migrations

**Alembic owns the schema.** Every backend/worker container runs `alembic upgrade head` on
boot before serving traffic. There is no startup-DDL / `create_all` path — if a column needs to
exist, it lives in the model **and** in an Alembic revision under `backend/alembic/versions/`.
Never run `Base.metadata.create_all` against a live database.

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
  - hosts: `SELECT ip_address, project_id, COUNT(*) FROM hosts GROUP BY ip_address, project_id HAVING COUNT(*) > 1`
  - vulnerabilities: group by `host_id, source, plugin_id, port_id, title HAVING COUNT(*) > 1`
    (vuln dedup is application-level — every insert path must `db.flush()` after `db.add()` so an
    in-scan repeat is found by its own existence check; the session runs `autoflush=False`).

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
different router files). There are **four** workflows: plan generation, execution,
reconnaissance, and read-only assist — each minting a scope-bound, time-limited key that is
rejected on the other workflows' endpoints.

- **`AGENTS.md`** (repo root) is the contract every agent reads at startup, served sliced by
  workflow at `GET /api/v1/agents-guide?workflow=…` via the `<!-- agents:section -->` markers.
- **Bump `PROMPT_VERSION`** (prepend an entry to `app/services/agent_prompt_history.py`) whenever
  the agent's instructions change materially.
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
