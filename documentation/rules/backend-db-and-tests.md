---
paths:
  - "backend/alembic/**"
  - "backend/app/db/**"
  - "backend/tests/**"
  - "backend/app/api/deps.py"
  - "backend/app/api/params.py"
  - "backend/app/api/v1/api.py"
  - "backend/app/api/v1/endpoints/auth.py"
  - "backend/app/api/v1/endpoints/users.py"
  - "backend/app/api/v1/endpoints/projects.py"
  - "backend/app/core/**"
  - "backend/app/main.py"
  - "backend/pytest.ini"
  - "backend/ruff.toml"
  - "ssl-nginx.conf"
  - "scripts/check.sh"
  - "scripts/test-alembic-roundtrip.sh"
---

# Backend: schema, migrations, request plumbing, roles, tests

The root `AGENTS.md` holds FK `ondelete` parity, one head, real downgrades, no `CONCURRENTLY`, the timeout's home, the migrate step, the role axes and the test recipe; they are not repeated here.

## Models

- `backend/app/db/models.py` (`Host` = `hosts_v2`, `Port` = `ports_v2`), sibling `models_*.py`. Register a new module in `app/db/model_registry.py`, or `alembic check` drops its tables (`test_model_registry.py`).
- A FK into `scans`, `ports_v2`, `hosts_v2`, `api_keys` has an index led by it: `index=True` AND a revision (`test_fk_child_index_contract.py`).
- A CIDR is in a scope once (`uq_subnets_scope_cidr`, stored text): every `Subnet` writer normalises with `str(ipaddress.ip_network(v, strict=False))` (`scopes._validate_cidr`, `SubnetParser`) and insert via `scopes._insert_subnets` (savepoint; False = another request added it first) (`test_subnet_unique_per_scope.py`) (hazard — removable: a CHECK that the column equals its `cidr`-normalised text).
- `Project.is_archived` is a read-only hybrid over `status == 'archived'` (set `status`; SQL may filter `Project.is_archived.is_(False)`); `models_project.PROJECT_STATUSES` is the one list (makes `ck_projects_status`). `host_follows.status` has no default.
- **One scope per project** (`scopes.project_id` NOT NULL + `uq_scopes_project`), built only by `services/default_scope.py`: `get_or_create_default_scope` (writes, the page's read; survives two first requests), `project_scope` (non-creating — every agent read; none = empty, never 404). Code resolves it from the project, never a `scope_id`; no agent route, MCP tool, answer or people's route names one (`/scopes/subnets[/{subnet_id}]`, `/scopes/domains[/{domain_id}]`, `/export/scope`; a foreign id 404s); seeds reuse it. Only `/scopes/default`'s `id` and the upload's `scope_id` carry it (`test_migration_one_scope_per_project.py` — `e1b4c7d0a2f5`, `test_subnet_search.py::test_a_project_cannot_hold_a_second_scope`) (hazard — removable: an AST guard that `Scope(` is built only in `default_scope.py`).

## Alembic

- One chain in `backend/alembic/versions/` from `b46cd59c17f5_baseline_schema`; no startup DDL. Autogenerate (rarely; revisions are hand-written) in a one-off container with `backend/` mounted, never `docker compose exec`.
- `scripts/test-alembic-roundtrip.sh`: all revisions down, up, then `alembic check` (local). Frozen downgrade DDL comes from a chain-built database, never the drifting dev one.
- No concurrent build (`autocommit_block`, `postgresql_concurrently`) because it commits earlier revisions mid-chain, leaves an INVALID index `IF NOT EXISTS` skips, stalls on a snapshot (`test_migrations_build_indexes_in_the_transaction.py`; three frozen revisions). `init._failure_message`: `NOTHING WAS CHANGED` (roll back the build) or `schema left PARTWAY` (restore).
- Only a connection-class error is "Database not ready" (`init._database_not_ready`: no SQLSTATE, class 08, `57P03`); anything else, `OperationalError` included, logs `DATABASE MIGRATION FAILED` once and exits (`test_db_init_migration.py`).
- Boot lock: waiters POLL `pg_try_advisory_lock` on an autocommit connection (`init._migration_lock`), never a blocking `pg_advisory_lock` (it holds a snapshot) (`test_db_init_concurrent_boot.py`).
- `app/db/migrate.py`: backend CMD and worker command, NOT the report worker (`test_db_init_migration.py::test_importing_the_app_runs_no_migration`); `app.main` / `app.worker` import `app.db.model_registry`. A failure ends the container; a `compose run` command skips it.
- A data migration that drops says so in the upgrade table (`ops-and-deploy.md`), `scripts/backup-db.sh` first. `scripts/upgrade-refusals.sql` is the one refusal list (`revision | refused | detail`), read uncopied by `deploy.sh`, `collect-logs.sh`, `migration-failure-report.sh`.

## Request plumbing

- `get_db`: `SET LOCAL statement_timeout` (`API_STATEMENT_TIMEOUT_MS`, 30000), 57014 → 503; connections pin only `-c timezone=UTC` (`config.SQLALCHEMY_CONNECT_ARGS`) (`test_request_context.py`). Pool exhausted (`sqlalchemy.exc.TimeoutError`, `DB_POOL_TIMEOUT`) → 503 + `Retry-After: 5`; resize only on measured load.
- Signed-in routes resolve `require_password_changed` (mount in `api.py`; `/references` declares it); only `test_password_change_gate_coverage.py`'s `EXEMPT` is exempt (logout, GET profile, change-password, sessions, session/renew, 2fa/*); `POST /auth/register` is gated.
- Streamed routes and large cascades (project / scan / subnet / user delete, correlate-all) call `disable_statement_timeout(db)`; list them in `test_review_2026_10_01_integration.py` (hazard — removable: a test that discovers streaming routes).
- Catch-and-continue handlers use `session.recover_or_reraise` (re-raise `is_statement_timeout(exc)`, else `db.rollback()`), else the next query 500s (`test_review_2026_10_01_statement_timeout_paths.py`) (hazard — removable: an AST guard on `is_statement_timeout` outside `session`).
- **Bodies are capped BEFORE auth** (`core/body_limit.py`, `MAX_REQUEST_BODY_BYTES`, 32 MB → 413) bar the two scan uploads (`allows_large_body`, `_LARGE_BODY_PATHS`), which `deps.large_upload_refusal` → `(status, detail)` refuses unread (threaded) unless the route's own checks would admit the caller: page `_user_and_session_of` + `require_password_changed` + `check_project_role(ANALYST)`; agents `_load_agent_key` + `_key_expired` + `_issued_before_password_change` + `operator_access_refusal`. New rules go in those, not the door (`test_body_limit.py`, `test_review_2026_10_23b_platform.py`). `conftest.sign_page_uploads` gives `client` uploads a real `test_user` session row: dependency overrides do not reach middleware.
- Layering (`test_service_router_boundary.py`): `app/services` ↛ `app.api`; endpoints ↛ another's `_private`; `deps.py` / `params.py` ↛ routers. Auth dependencies live in `deps.py`, re-exported by `endpoints/auth.py`. Enumerate routes via `app.openapi()["paths"]`.
- Audit: project create / update / archive / unarchive / delete and member changes write `project_*` rows in their transaction; logout and revoke commit with theirs (`revoke_session` stages). No client route writes one (`test_review_2026_10_23b_platform.py::test_no_route_lets_a_client_write_an_audit_row`).
- Typed times are `services/instants.parse_instant` (no offset = UTC): DSL windows, Query `where`, `created_after`, RDAP; scanner timestamps `parser_utils.parse_rfc3339` (offset required) (hazard — removable: an AST guard that nothing else parses a typed instant).
- CSV rows go through `csv_utils.safe_csv_row`; only a constant header is raw (`test_review_2026_10_16_t4.py::test_csv_rows_are_guarded`).
- `parse_error_service.log_parse_error()` is a function; `SubnetCorrelationService` correlates hosts → scopes.
- Open-source libraries and services only (check a new dependency's licence); no Redis — Postgres-native or Valkey.

## Roles

- `UserRole` (`models_auth.py`): `ADMIN` (users, settings, audit) / `MEMBER`; `require_role` only for ADMIN.
- `ProjectRole` (`ProjectMembership.role`) via `require_project_role`; `check_project_role(db, project_id, user, required)` is its rule as a function. `deps.membership_role` is the one membership read (`has_project_role`, `is_project_admin`, `get_current_project`; named exceptions, e.g. `administered_project`) (`test_every_project_route_checks_membership.py`). CHECKs on `users.role`, `project_memberships.role`, `projects.status`. Analyst writes; auditor adds exports / reports; viewer reads.
- Removing a project's last admin is refused, its admin rows locked in id order (`remove_member`; `test_review_2026_10_23b_platform.py`).
- **LLM providers are per-user** (the drafter uses the signed-in user's), never gated on global admin: add / change / test / use need `deps.can_write_in_some_project`; list and delete-own are open (`test_llm_provider_account_floor.py`). The connection test says one fixed text when nothing answered (never open / refused / silent).
- **Scanner integrations are installation-wide**: `integration_credentials` has no owner or project; `created_by_id` is provenance (`SET NULL`); names NOT unique (no constraint, no merge by guess). `GET /integrations/` is everyone's, secret-free (`has_secret`, `created_by`); writes and test are `require_role(UserRole.ADMIN)` (`test_integration_admin_gate.py`). `IntegrationService` takes no user or project. `extra_config` is only `max_hosts_per_scan` / `gmp_port` (`integrations.IntegrationExtras`, `extra="forbid"`, 422) — it is served as not secret.
- Not under `/projects/{id}`: `/auth`, `/users`, `/projects`, `/portfolio`, `/oversight` (router-level `require_role(ADMIN)`), `/notifications`, `/integrations`, `/llm-providers`, `/audit`, `/feedback`, `/activity`, `/system`, `/mcp`, `/mcp-telemetry`, `/agent/*` (project from the key).

### Accounts

- `POST /api/v1/auth/register` (admin) creates, with `must_change_password` (a transit credential); a seed writes the `User` row.
- A project's notifications show to current members and global admins only (`NotificationService._shown`: bell, count, mark-all-read); a removed member's rows stay stored and return on re-add.
- Profile `GET /api/v1/auth/profile` / `PUT /api/v1/users/profile`; sessions `GET` / `DELETE /api/v1/auth/sessions[/{id}]` (the token's `sub`'s). A session ends `ACCESS_TOKEN_EXPIRE_MINUTES` after its last renewal; only `POST /api/v1/auth/session/renew` (`security.renew_session`) renews — `get_current_user` never moves `expires_at` (`test_session_renewal.py`); `POST /api/v1/auth/change-password` revokes all.
- `POST /users/{id}/reset-password`, `/reset-2fa`, change-password and deactivation end all sign-ins (`users._end_everything_signed_in_as`); keys predating `password_changed_at` are refused (`agents-and-mcp.md`).
- Sign-in limits read `audit_logs.username` (typed, from `details["username"]` by a `before_insert` listener), never `users` columns (`core/security.py`): 5 / 30 min per (username, address) → 429 there; 20 / address, 100 / username per 15 min. 2FA: 10 wrong codes, serialized by `security.lock_account_attempts(db, "2fa", user_id)` (`"reauth"` in `reauth_password`) until the failure row or session commits; `accept_code` and recovery codes commit only when spending. Challenges predating `password_changed_at` are refused; answers reveal no account (`test_review_2026_10_23b_platform.py`).
- Live-token re-auth (`/auth/change-password`, `/auth/2fa/setup`, `/auth/2fa/disable`, `/auth/2fa/recovery-codes`) is `security.reauth_password`: 5 wrong / 15 min → 429. Never `verify_password` in a route (`test_totp_replay_and_reauth_throttle.py`) (hazard — removable: an AST guard that `verify_password` is called only in `core/security.py`).
- TOTP is accepted only by `totp_service.accept_code` (conditional UPDATE of `users.totp_last_step` — replay protection) (hazard — removable: an AST guard that nothing else verifies a code or writes `totp_last_step`).
- Demoting / deactivating / deleting an admin: `users.keep_an_active_admin` (active admins locked in id order; 403 actor not admin, 400 none left) (`test_review_2026_10_23_auth.py`). Account audit rows stage with `commit=False`. `GET /users/`: `limit` / `offset` envelope, `(username, id)`.
- A user delete keeps the call log (`agent_api_calls.agent_id` SET NULL) and review marks (`host_follows.user_id` `SET NULL`, `User.host_follows` `passive_deletes`; `has:untouched` still excludes the host), and ends agent sessions in its own transaction (`end_sessions_of_operator`). Never CASCADE.
- An assignment needs a person, kept by the DATABASE: trigger `host_follows_assignment_needs_a_person` (BEFORE INSERT OR UPDATE) clears `assigned_at` / `assigned_by_id` when `user_id` is NULL (`ck_host_follows_assignment_needs_a_person`); `assigned:none` lists them. Never from the route (races) (`test_user_delete_keeps_review_marks.py`). Readers compare ids; `tester_rows`, notifications skip NULL; the UI says "a former member" (`utils/people.reviewerName`).
- First boot, no ACTIVE admin: create `DEFAULT_ADMIN_USERNAME` (`admin`), password `DEFAULT_ADMIN_PASSWORD` unless literally `admin`, else random in `./uploads/initial-admin-password.txt` (0600; NEVER logged; unwritable = fail). Forced password change; TOTP if `REQUIRE_2FA=true` (default). A taken name logs `NO ACTIVE ADMINISTRATOR, AND NONE WAS CREATED`. Only its account's password change deletes it (`startup.discard_admin_marker_of`) (`test_default_admin_seeding.py`).

## nginx (`ssl-nginx.conf`) — `proxyHeaders.test.ts` pins it

- `/docs`, `/redoc`, `/openapi.json`, `/health`, `/live`, `/ready` have own locations. `client_max_body_size 32m` (= `MAX_REQUEST_BODY_BYTES`; raise both). The two uploads take 2G in a location NESTED in `^~ /api/` (a top-level regex never runs under `^~`) matching `_LARGE_BODY_PATHS`, `proxy_request_buffering off`, so 401 / 413 come first; it repeats `set`, `proxy_pass` and headers (none inherited). `MAX_FILE_SIZE` (1 GB, `core/config.py`) rises with it; the lower wins. The dropzone enforces 2 GB.
- Single-file bind mount: replacing the file (editor, `git stash` / `checkout`) strands the OLD inode, which `nginx -s reload` re-reads: `docker compose up -d --no-deps --force-recreate frontend`, then `docker compose exec frontend grep … /etc/nginx/conf.d/default.conf`.
- `location ^~ /api/v1/auth/` caps `1m` (anti-DoS); `/health`, `/ready` time out at 5 s; raise neither. `/api/`: 120 s connect / 900 s send / 900 s read.
- Every `/api/` location has `^~` (else the static regex takes `.png` / `.js` / `.css`). A location with its own `add_header` repeats the full security-header set.

## Running tests

- Run the one-off container from the REPO ROOT: from `frontend/` or `backend/` it leaves a root-owned tree of mount-point stubs.
- `conftest.build_schema_from_chain` (~2 s per process; SQLite fallback keeps `create_all`): a fixture writing what a constraint refuses fails. `test_user` setvals the users id sequence.
- Gate (`./scripts/check.sh`, `make check`): ruff (F rules) from `/app` (else the `tests/**` ignores in `ruff.toml` fail); suite in the REPORT-WORKER image with templates and repo root; `lint -- --max-warnings 0`; FAILS on a skip naming Quarto, templates or a mount (`mount_skips`; `test_ops_scripts.py`).
- **Shards** (`CHECK_SHARDS`): own process and database each. A file must not need the SERVER: no fixed database name (derive from `suite_engine.url`), no server-wide setting, no `pg_stat_activity` / `pg_locks` count unfiltered by `current_database()`, no fixed port or path outside `/tmp` (advisory locks fine). One that cannot share runs serially, its reason above `SHARD_CONNECTIONS` in `check.sh` — never skipped or retried. File order is not fixed: a test that passes only because an earlier FILE left state behind is fixed.
- `scripts/check-shard-weights.txt` (seconds per file; new files need none): on drift, `./scripts/check.sh --update-shard-weights` from a passing run, commit; `test_ops_scripts.py` pins each file in exactly one shard.
- `docker compose config --images <service>` lists dependency images too, unordered; never take its first line.
- Lock / race tests: `tests/two_connections.py` (`two_sessions`); plain fixtures share one connection.
- No routine run enforces `pytest.ini`'s `--cov-fail-under` (`--no-cov`).
- Previews are tested against the whole list (fixtures exceed the cap); the test FAILS on the broken code.

## Guard tests — update them with the change, not around it

| Test | Pins |
|---|---|
| `test_schema_fk_ondelete_contract.py` | model FKs == chain-built catalog, both ways (no map) |
| `test_test_schema_is_the_chain.py` | the suite's schema is the chain's |
| `test_redundant_indexes.py` | no btree index duplicates another's leading columns |
| `test_fk_child_index_contract.py` | FKs into `scans` / `ports_v2` / `hosts_v2` / `api_keys` have a non-partial index led by the child column |
| `test_migrations_build_indexes_in_the_transaction.py` | no revision bar the frozen three uses CONCURRENTLY / `autocommit_block` / `postgresql_concurrently` (AST) |
| `test_migration_r7_schema.py` | `e3b7a1c9d5f0` / `f4c8b2d0e6a1` / `a5d9c3e1f7b2` / `b6e0d4f2a8c3`: each refusal names its rows, changes nothing; down, up |
| `test_migration_review_marks_outlive_their_reviewer.py` | `e8c4a2f6d1b9` up / down / up: marks kept with no reviewer, NULLs distinct in `uq_host_follow_user`, down deletes reviewer-less rows |
| `test_migration_run_commands_testssl_gvm.py` | `b3d9f1a7c5e2` up / down / up: fills only empty catalogue rows, keeps operator text |
| `test_migration_integrations_application_wide.py` | `c8f2d5a9e3b1` up / down / up: rows and secrets kept, orphans to the first active global admin, duplicate names legal |
| `test_migration_one_scope_per_project.py` | `e1b4c7d0a2f5`: second scope merged into the first; refused (nothing changed) on a project-less scope or a CIDR / domain in two scopes; the unique constraint; down, up |
| `test_migration_review_2026_10_10_schema.py` | `a4f8c2e6b0d1`: duplicate subnets merged losslessly, refused on differing descriptions / sites; four FK indexes; call log kept across a user delete; down, up; `scripts/upgrade-refusals.sql` the one list, read with no copy |
| `test_migration_role_and_status_checks.py` | `47e5e79b90a9` up / down / up: refuses unknown roles / statuses; the three CHECKs |
| `test_migration_drop_activity_cursors.py` | `7b83a791a783` up / down / up: table recreated as the chain left it; the two cursor routes stay gone |
| `test_migration_active_flags_not_null.py` | `9c4e2b7d1a3f` up / down / up: NULL `is_active` → false; NOT NULL + server default true on `users` / `api_keys` |
| `test_migration_removed_endpoint_columns.py` | `d4a8c2e6f1b3` up / down / up: removal records into typed columns and back to JSON; a deleted host leaves NULL |
| `test_migration_archived_status_and_dead_columns.py` | `c2a7e5f1d9b3` up / down / up: `status` from the dropped flag (flag wins), rows kept, `agent_sessions.workflow` untouched |
| `test_migration_hostname_changed.py` | `d7b3e9a1c5f2`: `hostname_changed` recorded |
| `test_every_import_records_the_hosts_it_touches.py` | every format records `host_scan_history` and `hostname_changed` |
| `test_scan_renamed_hosts.py` | a scan's `renamed_hosts` == the Hosts list it opens; both delete paths keep hosts a later scan only addressed |
| `test_finding_revision.py` | every finding write raises `findings.revision` once per commit; one writer |
| `test_finding_source_note_is_read_only.py` | nothing writes `findings.evidence_annotation_id` (legacy, read-only) |
| `test_report_template_copies.py` | Executive brief's `reference.docx` / `fix-docx-report.py` == pentest's |
| `test_resource_metrics.py` | a worker report carries its start; a rebuilt container is not a silent worker; a stuck replica is |
| `test_paging_one_envelope.py` | `has_more` only in `services/paging` (AST); one envelope on both doors |
| `test_agent_list_paging.py` | every paged agent list answers `{items, total, limit, offset, returned, has_more}` or is a named exception; no agent route takes `skip` |
| `test_wire_names_2026_10_22.py` | renamed Operations / Ingestion-results / catalogue wire names; old paths and MCP tool name gone (no alias) |
| `test_parser_dispatch_contract.py` | parser dispatch contract |
| `test_ingestion_format_chain.py` | the format chain |
| `test_phase1_regressions.py::test_v2_27_0_content_detection_module_surface` | `content_detection`'s public `looks_like_*` / `is_nessus_sample` == `format_registry`'s content-detection sniffers |
| `test_parser_coverage.py` | what the coverage page claims |
| `test_parser_registry_old_equals_new.py` | detection unchanged on a recorded corpus |
| `test_review_2026_10_23b_ingestion.py` | set-based form (masscan) == per-row; masscan on the one address rule; every format's hosts correlated by the dispatcher |
| `test_parsers_isolate_records_one_way.py` | no `begin_nested()` / bare `record_savepoint()` in `app/parsers/` (bar `parser_utils`) or the Nessus integration — a skipped record goes through `write_record`; no `begin_nested()` in `vulnerability_service` / `host_deduplication_service` bar the latter's two concurrent-insert savepoints |
| `test_ingestion_late_cleanup.py` | a sighting per scan per writer; cleanup keeps hosts with work |
| `test_scan_sightings.py` | `release_scan` deletes only what no other scan reported, hands the rest to the earliest other sighting; first-recorder `scan_id`s SET NULL, sightings cascade |
| `test_scan_delete_by_hand.py` | the hand delete removes all the scan brought; 409s `hosts_with_work` / `import_running` |
| `test_scan_deletion_impact.py` | every preview figure == what the delete does; the cleanup keeps every host the work warning names |
| `test_service_router_boundary.py` | services ↛ api; endpoints ↛ each other's privates; deps ↛ routers |
| `test_host_loading.py` | one statement for hosts; no implicit endpoint loads |
| `test_finding_loading.py` | one statement for findings; no implicit endpoint loads |
| `test_read_path_review.py` | old == new predicates, statement counts, revision indexes == models; no `Host.id IN (subquery)` in `app/services` (one named exception) |
| `test_agent_role_route_matrix.py` | each agent read's floor == its page's, both ways |
| `test_agent_host_enrichment.py` | agent host counts == the page's |
| `test_review_2026_10_01_integration.py` | every streamed route is exempt from the statement timeout |
| `test_operations.py::test_operations_query_count_is_bounded` | Operations statement bound |
| `test_operations_redesign.py` | each Operations count == the list it opens |
| `test_operations_tabs.py` | each Operations tab count == the list it opens, across pages |
| `test_remediation_deadlines.py` | each remediation count == the list it opens |
| `test_remediation_due_dates.py` | hand-set deadline and deferral review date via `due_expr` / `apply`'s plan; flag count == `?flag=` list; assign-from-report composes `apply`; policy preview writes nothing; cross-project follow-up is one transaction |
| `test_remediation_vocabulary.py` | one record / assessment gap (`verification_expr`); gap count == `?verification=` list on every mount; neither status written from the other |
| `test_host_query_suggest.py::test_every_value_source_is_enumerable_or_deliberately_not` | a new DSL field's `value_source` |
| `test_mcp_enum_contract.py` | an MCP overlay never widens its endpoint |
| `test_well_known.py` | published agent-surface safety claims |
| `test_agent_safety_policy.py` | the agent safety policy's claims |
| `test_agent_attribution.py` | agent attribution claims |
| `test_tool_registry.py` | the tool registry's claims |
| `test_scanner_integrations_agent.py` | no scanner credential in a list, prompt or MCP opening; the credential request's 422 / 404 / 403 + audit row |
| `test_integration_admin_gate.py` | the integrations list is everyone's; writes are the global admin's on any row |
| `test_assignee_rule_one_implementation.py` | one assignee rule on the finding and both bulk routes (old == new) |
| `test_agent_note_notifies.py` | an agent's note notifies as a person's, not the operator |
| `test_password_change_gate_coverage.py` | every signed-in route (included routers too) resolves `require_password_changed`; only named setup routes exempt |
| `test_every_project_route_checks_membership.py` | every `{project_id}` route resolves `get_current_project` / `require_project_role` or is named with its reason |
| `test_download_headers.py` | `Content-Disposition` only via `app.api.downloads.attachment(name)` (`filename*` for non-ASCII); empty allowlist |
| `test_docs_contract.py` | agent guide markers and endpoints; every `scripts/*.py|sh` named in `scripts/README.md` |
| `test_ops_scripts.py` | deploy / backup / restore vs `tests/ops_fake_docker.py`; `check.sh`'s shard plan (each file in exactly one shard) and summed tally |
| `test_finding_on_a_name.py` | `POST /findings` `name_id` and a host test's `target_fqdn` answer with ONE rule (`name_observed_at_host`, `NAME_NOT_AT_HOST`): accepted on the resolving host, refused elsewhere, one host only, no wildcard / unknown / foreign name |
| `test_webhook_outbox.py::test_a_reclaim_during_the_outcome_write_waits_and_stands_down` | the outcome write holds the row lock: a reclaim between its read and its commit blocks, then stands down (two connections) |
| `uploadFormats.test.ts` | upload formats |
| `uploadFormatContract.test.ts` | the upload-format contract |
| `versionConsistency.test.ts` | the frontend image's version build args == `platform_version.json`; no service sets `APP_VERSION` |
| `tunableSettings.test.ts` | a tunable's default is read, not typed; no secret-looking name |
| `proxyHeaders.test.ts` | the nginx section above |

## Monitoring queries (zero rows each)

- Host duplicates: `SELECT ip_address, project_id, COUNT(*) FROM hosts_v2 GROUP BY ip_address, project_id HAVING COUNT(*) > 1`
- Scanner-finding duplicates: revision `b2e5a8c1d4f6`'s docstring.
- Multi-scan hosts: `host_scan_history` grouped by `host_id`.

## Removed on purpose

- `BLUESTICK_SKIP_DB_INIT` and every migrate-on-import path.
- `scopes.name`, `{scope_id}` scope paths (404), `/scopes/host-mappings` (`/hosts?subnets=`).
- `POST /users/` (`POST /auth/register`); `POST /audit/log`.

## Decided — do not ask again

- One scope per project.
- LLM providers per-user; scanner integrations installation-wide.
- A viewer-only account cannot create, change, test or use an LLM provider.
- No allowlist of private LLM hosts (Ollama may sit elsewhere).
- Integrations are the operator's; a server-side external API call is a rare, maintainer-approved exception.
- A user delete keeps the call log (nulled) and review marks (no one), and ends agent sessions.
- Open-source dependencies only; no Redis (Postgres-native or Valkey).
- Demoting a project's only admin is allowed (confirmed on the page); removing the last is refused.
