# AGENTS.md — working on the BlueStick repository

Instructions for anyone, human or coding agent (Codex, Cursor, Copilot, Gemini, Claude Code via a local
`CLAUDE.md` holding `@AGENTS.md`), changing this codebase. The invariants below are the things that are not
obvious from the code and that, when lost, cause real defects.

**Not to be confused with** `documentation/AGENT_GUIDE.md`: that is the product's contract for the
*BlueStick agents* (terminal-side AI assistants that talk to a running installation over `/api/v1/agent/*`
and MCP). It is served by the backend and is never a root `AGENTS.md`.

Human-oriented orientation (dev servers, dependencies, migrations, lint) is `CONTRIBUTING.md`;
`documentation/ARCHITECTURE.md` is the map; `documentation/API_GUIDE.md` the route reference. This file is
the core; the detailed rules of each area are `documentation/rules/*.md` (the section "Area rules" below
says how to load them). Security problems go through `SECURITY.md`, never a public issue.

## What BlueStick is

BlueStick parses and visualises network scan results for security assessments: a FastAPI + PostgreSQL
backend (`backend/app`), a React 18 + TypeScript frontend (`frontend/src`; Radix, Tailwind 4, Vite), an
ingestion worker and a report worker (both on `app/worker_loop.py`, the latter rendering Quarto client
reports from `report-templates/`), all behind nginx in one `docker-compose.yml`.

## Commands

```bash
./scripts/deploy.sh                 # the one deployment entry point (interactive)
./scripts/check.sh [--fast]         # THE GATE before every push: backend suite with Quarto (test files dealt to
                                    # CHECK_SHARDS containers; =1 is one process with live output), ruff,
                                    # frontend tsc + lint + vitest + Playwright, Alembic round trip (--fast skips
                                    # the round trip). `--only backend` runs one step and is not the gate.
./scripts/collect-logs.sh           # anonymised diagnostics bundle
cd frontend && npx tsc --noEmit -p . && npm run lint && npx vitest run    # `npm test` is WATCH mode;
                                    # `npm run build` fails on an unused import; the gate allows no lint warning
curl -k https://localhost/docs      # the API is reachable only through nginx; :8000 is not published
```

There is no hosted CI: the gate runs locally and needs the stack up and the images built. It builds and
pulls nothing. Once: `cd frontend && npx playwright install chromium`.

**Targeted backend run** — a one-off container from the REPO ROOT (the host has no pytest; `app/` is baked
into the image; from `backend/` or `frontend/` Docker leaves a root-owned tree of mount-point stubs):

```bash
R=$PWD; docker compose -f "$R/docker-compose.yml" --project-directory "$R" run --rm --no-deps \
  -v "$R/backend:/app" -v "$R/documentation/AGENT_GUIDE.md:/app/AGENT_GUIDE.md:ro" -e COVERAGE_FILE=/tmp/.coverage backend \
  sh -c "cd /tmp && python -m pytest /app/tests -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
```

In this recipe the Quarto, template-mount and repo-root doc tests SKIP and the run is still green; only
`check.sh` runs them (in the report-worker image) and fails on such a skip. The gate deals test FILES to
shards that share one database server, so a test must not need the server to itself (no fixed database
name, no server-wide setting, no unfiltered `pg_stat_activity`); `scripts/check-shard-weights.txt` balances
them and `./scripts/check.sh --update-shard-weights` refreshes it from a passing run.

**A git worktree:** run the recipe from the MAIN checkout (`R` = its root, where the compose project with the
database is) with BOTH `-v <worktree>/backend:/app` and `-v <worktree>/backend/tests:/app/tests` (compose
binds `R/backend/tests` over `/app/tests`, so with the first alone you test the worktree's code with main's
tests). The frontend needs `ln -s "$R/frontend/node_modules" <worktree>/frontend/node_modules`.

**Only `python -m app.db.migrate` migrates** (`alembic upgrade head` against `DATABASE_URL`); the backend's
CMD and the ingestion worker's compose command run it before they start. Importing `app.main` — a script, a
probe, the suite — touches no schema. The test schema is built by the migration chain.

## How to work here

- **Remove the hazard before writing a rule.** A convention worth stating is worth enforcing: a constraint,
  a key, one implementation in place of two, a lint rule, a test. "Remember to…" is a defect report with no
  fix date. Many existing conventions are already pinned by guard tests (`backend/tests/test_*contract*.py`,
  `test_read_path_review.py`, `frontend/src/tests/*.test.ts`, `frontend/eslint-rules/`): **update them with
  the change, not around it**.
- **One implementation per rule.** When two paths do the same thing (a page door and an agent door, a single
  and a bulk action, two parsers' upsert, a server rule and its frontend twin), the fix for drift is to make
  one call the other, not to document that they must agree. Where a twin must exist in both languages, it
  shares one case table under `backend/tests/fixtures/twins/`.
- **Refactoring is welcome when a test proves old == new behaviour** (the pattern `test_read_path_review.py`
  uses). Consolidation that reduces net debt is a bonus, not scope creep; do the foundational refactor
  before stacking a feature on it. Do not split a cohesive file to shrink a number — size alone is never the
  reason.
- **Legacy names and dead code leave with the change that orphans them.** Keep a wire name or deprecated
  route only with a stated removal condition.
- **One owner per fact in the docs.** Say where the authoritative statement lives; other documents link to
  it rather than paraphrase it.
- **Two installation modes.** *Assessment*: a project is one assessment window ending at the report — no
  response tracking, no stale / current / age judgments; states are assessed / not assessed / not
  applicable, and dates are provenance. *Assessment + remediation tracking*: the installation's switch
  (`remediation_policy`, global admin); deadlines, follow-up, alerts and trends exist ONLY there, and never
  on Posture, Operations, the finding page, the client report or addenda. With the switch off nothing about
  remediation is shown or inferred.
- **"The server makes no network queries"** means none toward targets or the internet for inventory: no DNS
  resolution, no scanning, no email, no CDN in a report. Operator-configured outbound integrations
  (webhooks, LLM providers) are the only ones; scanner connections are made by the operator's agent.
- **Caps are for scale** (projects of ~70k entries): never remove a cap — page or link instead, and state the
  cap against the total ("the first N of M"). A preview is tested against the whole list.
- **No vanity metrics**: every number on a page is actionable or supports a judgment.
- Open-source dependencies only (check a new dependency's licence); no Redis — Postgres-native or Valkey.
- Do what was asked; prefer editing a file to creating one; create no documentation files unprompted.

## Invariants (losing one causes a real defect)

**Data and transactions**
- A parser that skips a failed record writes it with `parser_utils.write_record` (over `record_savepoint`),
  passing `dedup_service.discard_rolled_back_state` as `on_rollback`; otherwise the session is poisoned and
  the import fails after logging "skipped". `test_parsers_isolate_records_one_way.py` refuses a hand-written
  savepoint in `app/parsers/`. A parser raises on failure (`ValueError` = not this format, `ParseFailure` =
  terminal), never returns `success: False`, and never correlates (the dispatcher does, after commit).
- A failed import leaves nothing only it created. `ingestion_service.delete_partial_scan` is the one cleanup;
  when in doubt it KEEPS, and never a host with work on it (`host_work.work_kinds`, the one list). Every
  writer of a script, host attribute or scanner observation records a sighting per scan
  (`scan_sightings.see`), and `scan_sightings.release_scan` is the ONE step before any scan row is deleted —
  the cleanup and `DELETE /scans/{id}` share it. A new port insert site stamps `ports_v2.created_scan_id`, or
  no delete can find its ports.
- Every ingestion-job status write goes through `job_transitions` (checked under the row lock). An import is
  stopped by a stall (no progress for `INGESTION_STALL_TIMEOUT`), never by duration.
- A field is the TOOL's statement; a statement the tool did not make is never written (`source_statements.py`
  declares what each source can state; no rating → severity `unknown`; nothing about exploits → `exploitable`
  NULL, a tri-state). A new tool enters through the BlueStick observation format or a native parser
  (`documentation/NORMALIZATION.md`).
- Never hold an uncommitted write across an `await` in an async handler — it can hang the worker, `/health`
  included. An async route that touches the database does its work in `run_in_threadpool`.
- The API statement timeout lives in `get_db` only; a streamed or large cascading route calls
  `disable_statement_timeout(db)` first.
- A host predicate over a child table is a correlated `EXISTS`, never `IN (subquery)` / `NOT IN`; a DSL `NOT`
  goes through `host_query_dsl.negate` (an unknown child is false, not NULL). Rank and count in SQL; never
  bring every host to Python.
- **Column vs blob:** a value any view, filter, DSL predicate or insight queries, sorts or aggregates across
  hosts gets a typed column; a `raw` / JSON blob is only for opaque provenance kept for re-processing. A blob
  in a `WHERE` / `GROUP BY` is a signal to promote the field, not to add a functional index.
- A model `ForeignKey()` carries the `ondelete=` the DB enforces (`alembic check` compares them; the test
  schema is the chain). A new model module is registered in `app/db/model_registry.py`.
- One Alembic head; a column lives in the model AND a revision with a real `downgrade()` (the round trip
  runs it); migrations build indexes inside the transaction, never `CONCURRENTLY`. A data migration that
  drops or refuses says so in `README.md`'s "Upgrading across" notes and adds its refusal check to
  `scripts/upgrade-refusals.sql`.
- One `Host` per IP per project. A `DNSName` is an identity, never merged into a `Host`; a scope name never
  confers subnet scope. A project has exactly ONE scope, resolved from the project, never by id.

**Findings, reports, agents**
- User text never becomes `.qmd` source: values are escaped, written Markdown is a placeholder filled after
  Quarto's filters, and `test_hostile_text_stays_text_in_every_format` is the contract (it runs only in the
  report-worker image; the gate fails on its skip).
- An issued report never changes (revise instead): issuing freezes its dataset, images, template and scope
  file in its own storage. Addenda compare by finding AND endpoint, never by date or remediation.
- Lock orders: evidence, then the finding (inside `FindingService.promote_vulnerability`, which also links
  the evidence); the finding, then its endpoints, then what hangs on them — a remediation record, a
  proposal (`FindingService.lock_finding` is the first step of every write to an existing finding;
  `proposal_service.lock_for_decision`, `remediation_service._lock_for_write`). Ids are locked in id order.
- An agent's change is a PROPOSAL when it alters what the team concluded or what the report says; everything
  else an agent writes is direct and attributed. Accepting a proposal runs the same function as the
  equivalent click.
- Agent authority = the operator's project role, re-resolved per request. The MCP layer makes no
  authorization decision. There are no rails, plans, runs or approval gates.
- Publish no safety claim the server cannot enforce (`test_well_known.py`, `test_agent_safety_policy.py`).
- Work is a host test and its evidence; a note is discussion; a finding is a promoted observation, a
  promoted test result, or written directly.
- Never read a finding's status as the state of one host's row; never read a list row's preview as its
  total.

**Roles and reads**
- Two role axes: global `ADMIN` / `MEMBER`, and per-project admin > analyst > auditor > viewer. There is no
  global "analyst". Every `{project_id}` route resolves membership (a guard test lists the exceptions).
- Every count on a page opens its exact list and is produced by the list's own function or the DSL's own
  predicate. "Untouched", "planned", "tested", "assessed", "judged" and "imports needing someone" have one
  definition each (`documentation/ARCHITECTURE.md` names them).
- An agent read wraps the SAME service as the page it mirrors, at the same role, declared on the route
  (`agent_read_floor`, listed in `test_agent_role_route_matrix.py`); a filter value that cannot be understood
  is a 422 on both doors, never dropped. Paged agent lists answer one envelope (`services/paging`).
- Never advise collecting on out-of-scope hosts; say "confirm in scope".

**Frontend**
- Components import, and tests mock, the `services/api` barrel — never a submodule (a lint rule).
- Controls follow the PROJECT role (`hooks/useProjectRole`); write controls are hidden, not disabled.
- Server state is TanStack Query and nothing else: an API function is called only inside a `queryFn` or a
  `mutationFn` (a lint rule; `src/lib/query.ts`, style guide §48), the project id is the first argument and
  part of the key, and a write refreshes what it changed by key. List state a reader can return to
  (filters, tab, page) lives in the URL (a lint rule); a record-scoped panel is keyed by its record.
- Sections, not cards; a page explains itself once; no page-level horizontal overflow; every external value
  is unbounded (verify with worst-case data); desktop only. `documentation/UI_STYLE_GUIDE.md` is the
  contract.
- Vocabulary: *scanner observations* (not yet judged), *under investigation*, *confirmed*, *closed*.
  Scanner rows are not "findings". Severity has its own colour tokens.

## Process

### Versions and release notes
Bump after every code change so a rebuilt container shows it. Two independent streams (backend, frontend);
the current numbers are in `platform_version.json`. PATCH for fixes, MINOR for features and significant
refactors, MAJOR for breaking changes. Backend code → backend; frontend code → frontend; docs-only and
test-only → none. **Bump with `./scripts/bump-version.py --backend|--frontend patch|minor|major`, never by
hand**: it writes the four places (`platform_version.json`, `frontend/package.json`,
`frontend/package-lock.json`, the frontend image's build args in `docker-compose.yml`), refuses a tree that
already disagrees, and adds the release's empty entry to `backend/app/data/release_notes.json`; then run
`frontend/src/tests/versionConsistency.test.ts`.

**Every change a user would notice gets a line in `backend/app/data/release_notes.json`** (Reference →
Release notes in the app) — what changed for the person using BlueStick, with no file names or internal
reasons. Kinds `new`, `changed`, `fixed`, `before_upgrading`, in that order; `tests/test_release_notes.py`
fails the gate until the running version has a line. There is no tracked changelog file: the commit message
carries the why, the release notes carry what the user sees.

A changed agent guide or session prompt also bumps `PROMPT_VERSION` in
`app/services/agent_prompt_service.py`, and nothing beside it.

### Documentation is part of every change
A change is not finished until every document describing the changed behaviour says the new thing, in the
same commit. Before committing, grep `documentation/`, `README.md`, `scripts/README.md`, `scripts/*.md` and
`frontend/src/data/` for every name the change touched.

| Change | Documents |
|---|---|
| Parser / import format / what a tool's line becomes | `app/data/parser_coverage.json` (test-pinned), `documentation/PARSERS.md`, `documentation/UPLOAD_FORMATS.md` **and** `frontend/src/data/uploadFormats.ts` (pinned to each other), `documentation/PARSE_AUDIT_BRIEF.md` + `scripts/parse-audit-agent-prompt.md` when what counts as correct changes |
| Misconfiguration check (`misconfig_checks.py`) | the tool's `parser_coverage.json` rows, `PARSERS.md`, `UPLOAD_FORMATS.md` |
| Agent instructions / agent endpoint / MCP tool | `documentation/AGENT_GUIDE.md`, `PROMPT_VERSION`, `documentation/API_GUIDE.md`, `documentation/MCP.md`, `documentation/ASSIST_TOOLS.md` |
| API endpoint or router | `documentation/API_GUIDE.md` |
| Service, subsystem, data flow | `documentation/ARCHITECTURE.md` |
| Script in `scripts/` | `scripts/README.md` (test-pinned), `README.md` when operators run it |
| Deployment, upgrade, configuration | `README.md`, `scripts/README.md`, `.env.example` |
| Report template capability | `report-templates/README.md`, `pages/ReportTemplateGuide.tsx` |
| UI rule | `documentation/UI_STYLE_GUIDE.md` |
| A rule in this file or in `documentation/rules/` | first ask whether the hazard can be removed in code instead; a new rule names the guard that pins it or carries a `(hazard — removable: …)` tag |
| Always, when the version moves | the release's lines in `backend/app/data/release_notes.json` |

### Git
Work on a branch. Before a push or a pull request: gate green, versions bumped, release notes written,
documents updated. A commit message says what changed and why.

Working files — plans, review notes, acceptance-run reports (`documentation/MCP_*_RUN_*.md`,
`documentation/acceptance/`), scan fixtures in `artifacts/` — are git-ignored on purpose and never
committed; what lasts is the fix, its regression test and its release-notes line.

## Area rules (`documentation/rules/`)

Nine files, one per area, hold what this page cannot at one page: the one definition of each count and
predicate, lock orders, the guard test that pins each rule, what was decided and why, and what was removed
on purpose. They are dense and reference code by name on purpose — read the file for the area BEFORE
editing anything its `paths:` frontmatter lists, and update it in the same change.

How to read them:
- A rule followed by a test name is pinned by that test: change both together, never work around the test.
- **`(hazard — removable: …)`** marks a rule that is NOT yet enforced by code and says what would enforce
  it. These are debt, visible; writing that test or lint rule and deleting the tag is always in scope and
  is a good first contribution. `backend-db-and-tests.md` carries the table of guard tests.
- **"Decided — do not ask again"** lists settled design decisions with their date; do not reopen one
  without new evidence. **"Removed on purpose"** lists what used to exist, so it is not rebuilt.
- A date beside a decision is when it was made; a version beside a rule is when it landed.

How to load them: Claude Code reads `.claude/rules/*.md`, which is git-ignored here, so point it at the
public copies once per checkout — `mkdir -p .claude/rules && for f in documentation/rules/*.md; do ln -s
"../../$f" .claude/rules/; done` — and the `paths:` frontmatter makes each file load when a matching file is
touched. Other tools read them as ordinary documents: open the area's file before the first edit.

## Where to read more — working on X, read Y

| Working on | Rules | Other documents |
|---|---|---|
| Parsers, detection, import jobs, cleanup, heartbeats, staged import, upload formats | `documentation/rules/ingestion.md` | `documentation/PARSERS.md` (Part 2 before adding a parser), `documentation/NORMALIZATION.md`, `documentation/UPLOAD_FORMATS.md` |
| Hosts, the DSL, the Query page, Scans, Posture, Evidence, Coverage, Operations, cross-project counts, query performance | `documentation/rules/inventory-reads.md` | `documentation/ARCHITECTURE.md`, `documentation/API_GUIDE.md` |
| Findings, scanner observations, host tests, evidence, notes, proposals, notifications | `documentation/rules/triage-tests-proposals.md` | `documentation/API_GUIDE.md` |
| Remediation tracking, deadlines, follow-up, the contact document | `documentation/rules/remediation.md` | `documentation/API_GUIDE.md` |
| Client reports, Quarto, templates, report images, presentation mode, inventory downloads | `documentation/rules/client-reports.md` | `report-templates/README.md` |
| The `/agent/*` surface, sessions, MCP, safety claims, tool registry, the agent guide | `documentation/rules/agents-and-mcp.md` | `documentation/AGENT_GUIDE.md`, `documentation/MCP.md`, `documentation/ASSIST_TOOLS.md`, `documentation/MCP_ACCEPTANCE_QUESTIONS.md` |
| Models, Alembic, request plumbing, roles and accounts, nginx, running tests, guard tests | `documentation/rules/backend-db-and-tests.md` | `CONTRIBUTING.md`, `documentation/TESTING_FRAMEWORK_DOCUMENTATION.md` |
| Anything under `frontend/src` | `documentation/rules/frontend.md` | `documentation/UI_STYLE_GUIDE.md`, `frontend/eslint.config.mjs` |
| Scripts, compose, deploy / backup / restore, upgrade notes, the local CA | `documentation/rules/ops-and-deploy.md` | `scripts/README.md`, `README.md`, `ca/README.md` |

A task that crosses areas reads each file. A rule file that names another by its bare name
(`ingestion.md`, `frontend.md`) means its sibling in the same folder.
