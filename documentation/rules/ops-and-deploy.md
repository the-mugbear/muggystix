---
paths:
  - "scripts/**"
  - "docker-compose.yml"
  - ".env.example"
  - "ca/**"
  - "backend/Dockerfile"
  - "frontend/Dockerfile"
  - "backend/constraints.txt"
  - "backend/requirements.txt"
  - "platform_version.json"
  - "README.md"
---

# Operations: configuration, deploy, backup, upgrade notes, scripts

`README.md` and `scripts/README.md` own the operator-facing instructions and each script's options; this file holds the rules for changing them. The scripts are verified against a fake docker only: `tests/test_ops_scripts.py` runs the real scripts against `tests/ops_fake_docker.py`, and a change to a script gets a case there. A guard named below without a file is in `test_ops_scripts.py`.

## Configuration and versions

- One `.env` (from `.env.example`); `deploy.sh` detects the host IP, which a network deployment needs right for CORS.
- The backend version is `core/config.py`'s reading of the mounted `platform_version.json` (`PLATFORM_VERSION_FILE`; an `APP_VERSION` in `.env` overrides, compose sets none); `main.py` only consumes `settings.APP_VERSION`. After a rebuild `GET /` and the user menu's **About BlueStick** match the file.
- Builds install from `backend/constraints.txt` (regenerated on a dependency change); dev tools stay in the image (`INSTALL_DEV=true`) for the test recipe. Images are pinned, each overridable from `.env`. Postgres JIT is off (`PG_JIT`): 100–250 ms on every statement over the cost threshold.
- `INGESTION_STALL_TIMEOUT`: how long an import may go WITHOUT PROGRESS (`ingestion.md`). `INGESTION_MAX_RETRIES` counts the reaper's re-queues only (`reap_count`). `INGESTION_RETAIN_FILES_DAYS` (7): the worker sweeps finished jobs' older files from `uploads/ingestion_queue/`.
- `scripts/rdap-lookup.py` runs outside BlueStick (no network queries — `AGENTS.md`); its output is uploaded.
- Production deploys by file copy; copying is not deploying. Never rebuild during a live agent test, never pre-build before `deploy.sh` (nginx is in the frontend container).
- **Credential encryption key:** with `CREDENTIAL_ENCRYPTION_KEY` unset, stored LLM provider and scanner credentials use a key derived from `SECRET_KEY`, and ONE key is tried on read; so the advice everywhere (boot warning, `.env.example`, ARCHITECTURE) is to set it to the CURRENT `SECRET_KEY`, never a new one (`test_llm_provider_service.py::test_the_advice_to_set_the_key_keeps_stored_credentials`).

## Deploy (`./scripts/deploy.sh`, options 1–7)

1 Start / Rebuild · 2 First-time setup (detect IP, generate `.env` + SSL certs, start) · 3 Reconfigure IP · 4 Nuclear clean (destroys ALL data) · 5 Security status · 6 Back up `.env` + SSL to the parent folder · 7 Roll back to the previous build.

### One stack, one staged start (`scripts/stack-lib.sh`)
- **A folder of another name is another installation**: compose names the project and its volume `<project>_postgres_data` after the folder unless `COMPOSE_PROJECT_NAME` pins it. `stack_identity_ok` / `print_stack_identity_problem` / `other_bluestick_stacks` are the ONE check (a BlueStick stack = a project with a `report-worker` container): options 1–2 stop on it (`DEPLOY_SECOND_STACK=1` overrides), so does `backup-db.sh`; option 5 and `status.sh` say it; `collect-logs.sh` counts it, never naming a project or folder (it can be a client's). Only `compose_project_name` derives the name (hazard — removable: a test that no other script reads `COMPOSE_PROJECT_NAME` or the folder name); `upgrade-instance.sh` keeps its own volume check (it reads the OLD folder's `.env`).
- **`stack-lib.sh` is the ONE staged start and backend wait** (sourced by `deploy.sh`, `restore-db.sh`, `status.sh`): workers stopped → db + backend alone → wait → workers → frontend. Never a bare `$DC up` (hazard — removable: a test grepping the scripts for `up` outside `stack-lib.sh`). It is not in the git-ignored `scripts/lib/`.
- It holds the ONE copy of `stack_env_val` (compose's reading of `.env`: one pair of quotes removed), `key_fingerprint` / `key_fingerprint_matches` (also accepts an older fingerprint over a quoted key's quotes), `resolve_volume`, `helper_image`, `service_image_ref` — the image named for the service, since `docker compose config --images` lists them in no fixed order (`test_backup_and_restore_share_one_copy_of_the_helpers`).
- **Settings readout** (end of options 1–2, in `status.sh`): `print_tunable_settings` (`TUNABLE_SETTINGS`) — each knob, its value in force, its source. Defaults are READ from `docker-compose.yml`, the scripts or `.env.example`'s commented `# NAME=value`, never typed into the list; a new tunable goes on it AND gets its default in one of those (`frontend/src/tests/tunableSettings.test.ts`, which also refuses a secret-looking name).
- **Never a bare `read`**: every prompt goes through `ask` (a stated default, or a message at end of input) (hazard — removable: a test grepping for `read` outside `ask`).

### Accounts and permissions (`scripts/permissions-lib.sh`)
- **`deploy.sh` runs as ONE ordinary docker-group account, never root** (Decided). `permissions_gate` (options 1–3, before any write; `report_permissions` in option 5) runs `audit_permissions`: a root run, a `.env` or TLS key others can read, a `.env` / `ssl/` of another account, an `uploads/` neither UID 999's nor the caller's — each with one fix command. It fixes only what the caller can (`chmod600:<path>`; `chown-uploads` when root), never runs `sudo`, and at end of input takes `c` and says so. Root on `ssl/` is never required (nginx's master reads the key as container root).
- **The ownership check is ONE file** (`audit_permissions` → `PERM_KIND` / `PERM_WHAT` / `PERM_FIX` / `PERM_AUTO`), sourced by `deploy.sh` (asks) and `collect-logs.sh` (writes `permissions.txt`: the KIND and owner CLASS, `perm_owner_class`, never sentences or owner names). A new file a script or container depends on gets a kind there, never a check in a caller (`test_the_bundle_reports_ownership_by_class_and_the_findings_by_kind`).
- **No script makes a folder world-writable** (`test_no_script_opens_a_folder_to_every_account`): `backend/docker-entrypoint.sh` gives the uploads tree to UID 999 at start, closes (750) a managed folder an earlier deploy opened, and is the one place its sub-folders are made; `deploy.sh` makes only `uploads/` itself (so Docker does not make it root's).
- **`uploads/` is the containers' alone: mode 700, UID 999**; option 5 describes every state, the correct one included (`perm_describe_uploads`). A host script never reads, lists, moves or deletes inside it as the deploy account: it goes through a container (`backup-db.sh`, `restore-db.sh`, `collect-logs.sh`, `upgrade-instance.sh`'s `mv -T` / `cp -aT` with `--copy-uploads` in the database image) or, where none can (a move across parents or with no image; Nuclear clean's password file), says the one `sudo` command (hazard — removable: a test that no host-side command in `scripts/` names a path under `uploads/`).
- **An unreadable folder is never treated as an empty one** (`test_the_upgrade_does_not_read_an_unreadable_uploads_folder_as_empty`). The first-boot password: `docker compose exec backend cat /app/uploads/initial-admin-password.txt`.
- **UID 999 has a host name that is not ours** (often `dnsmasq`, GID 999 `systemd-journal`): a message naming the containers' user says what the host calls it and that it is expected (`perm_container_uid_note`; `upgrade-instance.sh`'s move message).

### Database start, TLS and migrations
- **The database's TLS is the `db` container's own job on every start** (entrypoint `scripts/postgres/start-with-tls.sh`: copies `ssl/certs` to a `postgres`-only folder, `-c ssl=on …`); no script copies a key into the data directory or `ALTER SYSTEM`s TLS. Without a certificate and its own key it starts with `-c ssl=off` (over a stale `ssl = on` in `postgresql.auto.conf`), saying so (`[db-tls]`): a bad certificate never stops the database — why the backend's default stays `sslmode=prefer`.
- **A failed migration ends the backend container and the restart policy reruns it** (the start step; the ingestion worker runs it too, the report worker none). `wait_for_backend_healthy` greps the backend log for `DATABASE MIGRATION FAILED` (`backend_migration_failure`); never judge a migration by container state or by "something is active in the database".
- **Option 1 works on the OLD database before backing up or building**: `ensure_database_up` starts `db` ALONE when the volume exists and it does not answer (refusing only if that fails); `upgrade_refusal_gate` runs **`scripts/upgrade-refusals.sql`, the ONE list of the rows a revision refuses**, and stops, unchanged, on a non-zero `refused` or a check that could not run (`DEPLOY_SKIP_REFUSAL_CHECK=1` overrides). `collect-logs.sh` (`versions_and_schema.txt`, "Upgrade refusals") and `migration-failure-report.sh` print the same file. A new refusing revision adds ONE line there (`revision | refused | detail`, one statement, counts only, true before and after the revision), never a copy in a script (`test_migration_review_2026_10_10_schema.py::test_the_refusal_list_is_one_file_every_reader_runs`, `…::test_the_refusal_checks_count_every_clash_the_upgrade_refuses`). Only a dump is a rollback point.
- Option 1 waits `DEPLOY_HEALTH_TIMEOUT`; only a crash-loop points to rollback — a migration holding the advisory lock is "still migrating".

### Rollback, certificates, clean
- `.deploy-rollback-state` keys: `<service>|<ref>`, `PREDEPLOY_DB_DUMP`, `PREDEPLOY_ALEMBIC_REVISION`, `DEPLOY_IN_PROGRESS|<when>|<built backend image id>` (while present, option 1 keeps the existing snapshot — `test_redeploy_after_a_failed_deploy_keeps_the_rollback_point`).
- **Option 7** stops the app and RESTORES FIRST when the schema revision moved from the recorded one (`restore-db.sh --yes --no-start`, no second implementation); never the previous build on a moved schema without `START ANYWAY` (`test_rollback_*`).
- Option 3 keeps a CA-issued certificate (`cert_is_ca_issued`: issuer ≠ subject), refusing, unchanged, when it does not name the new address. Nothing regenerates over a CA certificate (`generate-ssl-cert-simple.sh`: a CA cert with its key missing stops the deploy — `test_a_ca_certificate_whose_key_is_missing_is_never_replaced`).
- Nuclear Clean selects by the compose-project label, never by a `name=` substring.

## Worker and host figures (`services/resource_metrics.py`)

- System settings' host and process figures come from `/proc`, the container's own cgroup and Postgres — never a new dependency, a sampling thread or the Docker socket.
- "Near its limit" is the processes' OWN memory (`memory.stat` `anon`) against the container's limit; kills are the kernel's count (`memory.events` `oom_kill`). Never the peak (it counts reclaimable file cache); the peak is shown "since start".
- A worker's report is written beside its heartbeat (`worker_loop.write_heartbeat` → `write_worker_report`, `uploads/.worker-status/<queue>.<container>.json`): one per INSTANCE, even when memory could not be read (`memory_read`), so no replica overwrites another and a silent one shows.
- A report that stops is "not reporting"; a day old, forgotten; one whose container was REPLACED (another of its queue `started` after it last spoke) is dropped with its file, no alert (`resource_metrics._replaced`; a stuck replica still shows). A queue with NO report once a worker ran past the silence threshold raises `worker_missing:<queue>` (`operational_alerts.alert_silent_workers`). Guards: `test_resource_metrics.py`, `test_review_2026_10_16_t8.py`.
- **Nothing called from the heartbeat may raise or write to the database**: the healthcheck reads that file (`test_resource_metrics.py::test_the_heartbeat_is_written_whatever_the_memory_report_does`).

## Backups and restore

- **A failing backup step prints what the tool said** (`STEP_ERR` + `explain_step_error` in `backup-db.sh`): never send pg_dump / pg_restore / tar / docker stderr to `/dev/null` there (`test_a_failed_uploads_archive_prints_what_tar_said`). It checks compose and the backup folder BEFORE dumping, and takes a raw volume copy only of a stopped or absent db container.
- **Which artifact a backup wrote is `backup-db.sh`'s answer** (`BACKUP_RESULT_FILE`: `type=` / `artifact=` / `dir=`), read by `deploy.sh`'s `run_backup` and `restore-db.sh`'s safety backup; no caller recomputes the folder or looks for the newest file (`test_a_deploy_records_the_dump_backup_db_reports`).
- Backups are private (umask 077) and read back TO THE END. **`pg_restore --list` is not a completeness check** (the table of contents precedes the data): verification is `pg_restore -f /dev/null` plus `bytes=` / `sha256=` in the `.meta`, compared by restore before it drops anything (`test_backup_refuses_a_truncated_dump_that_still_lists`, `test_restore_refuses_*`).
- Uploads are archived with the db image; a failed archive fails the backup (`--allow-missing-uploads`). **A failed uploads EXTRACTION fails the restore's ending**: the database is restored, it ends `PARTLY RESTORED` with the `tar xzf` retry command, exit **4**; option 7 then starts the previous build and ends `RUNNING — PARTLY RESTORED`, non-zero (`test_restore_whose_uploads_fail_ends_partly_restored`, `test_rollback_whose_uploads_fail_…`).
- **`restore-db.sh` refuses a dump whose `alembic_revision` this build lacks** (the backend image's `/app/alembic/versions`, else the folder's) before the safety backup and the DROP (`--allow-unknown-revision` overrides), reading it from the dump itself (`pg_restore -a -t alembic_version -f -`) (`test_restore_refuses_a_dump_whose_schema_the_backend_image_does_not_know`).
- **Installed report-template files are backed up** (`nm-templates-*.tar.gz`, `templates_bytes=` / `templates_sha256=`): `template_assets.py` is the ONE list, which `upgrade-instance.sh` carries, `backup-db.sh` archives and `restore-db.sh` puts back (`test_report_template_assets.py`). A failed archive fails the backup (no python3 = warned and recorded). Restore verifies it before dropping, refuses a member outside `report-templates/`, puts back only what the folder lacks, and a failure ends PARTLY RESTORED / exit 4 (`test_restore_keeps_a_template_file_this_folder_already_has`).
- Files of ingestion jobs waiting on one are not restored (`ingestion_queue` is re-uploadable). Retention keeps the newest `BACKUP_KEEP` (10; 0 = all), deleting at most `BACKUP_PRUNE_MAX` (3) per run unless `BACKUP_PRUNE_BACKLOG=1`.

## Data repairs

- `data_repairs` is the ledger of one-off repairs (`scripts/data_repairs.py`; deploy and status name the pending ones). A new repair script records its run there (hazard — removable: a test that every `scripts/repair_*.py` / `backfill_*.py` is a known repair; `test_data_repairs.py` checks only the reverse).
- The misconfig backfill (`backfill_misconfigs.py`) writes through the application-level vulnerability dedup and must not run beside a live import.
- `merge_duplicate_scanner_findings.py` (so `b2e5a8c1d4f6` can run) uses plain SQL + the FK catalog, never the models (it must work on the OLD schema while the new backend crash-loops); its `PREDICATE` is the migration's (`test_merge_duplicate_scanner_findings.py::test_the_scripts_predicate_is_the_migrations`).
- It, `repair_netexec_results.py` and `apply_scope_labels.py` are dry runs by default (`--apply`); `repair_netexec_results.py` never touches another tool's identification.

## Removals owed once every known installation has upgraded

The ONE list of compatibility kept for an installation that has not upgraded yet. Read it in the close-out of every production deployment: once every known installation runs a release, each line below whose condition that release meets is removed in the next release, with its line here. Anything kept for old clients or bookmarks gets a line here, never a "remove later" sentence in another file.
- (empty since 2.527.1 / 5.422.1, 2026-10-23)

## Upgrade notes

Rows for releases newer than the oldest known installation's (2.527.0 as of 2026-10-23); a row goes once every installation has crossed it (its text stays in the commit history and README's upgrade notes). The operator-facing words are README's "Upgrading across" bullets — this table adds revisions and what a maintainer checks.

| Across | Revisions | What it does | Back up first |
|---|---|---|---|
| v2.527.1 (frontend 5.422.1) | — (head stays `d4a8c2e6f1b3`) | no schema change. Removes every redirect kept for bookmarks (`/dashboard`, `/test-plans/*`, `/executions/*`, bare `/agent-sessions`, `/insights`, `/insights/systemic`, `/ingestion-results`, `/parse-errors` are not-found; `/scans?view=results`, `/findings?view=observations` are plain pages); the Agents hub moves `/workflows` → `/agents`. `INGESTION_JOB_TIMEOUT` is no longer read (rename it in `.env`) | no |
| v2.535.0 → v2.537.0 (frontend 5.427.2 → 5.428.1) | `a3f1c7e9d2b4`, `b7d2e4f8a1c3`, `c9e1a5b3d7f2`, `d4f7b2c8e1a9`, `e5a8c3d1f7b4` | data: unstated `vulnerabilities.exploitable = false` → NULL (OpenVAS `E:U` and nmap vulners rows keep false); adds `vulnerabilities.cvss_version` backfilled from stored vectors; creates `vulnerability_cves` backfilled from `cve_id`; `vulnerabilitysource` gains `EXTERNAL`; Nikto rows rated LOW with no catalog check → UNKNOWN, empty `cve_id` → NULL. Nothing refused, nothing deleted; a Nessus re-upload records explicit "no exploit" statements. Downgrades: the flags and ids are not invertible (left), the column and table drop, EXTERNAL rows are deleted on the enum rebuild | yes (a data change on every observation row) |
| v2.531.0 (frontend 5.425.0) | `e7c3a9d1b5f2` | creates `presentation_agendas`, `presentation_agenda_versions` and adds the agenda state (`agenda_id` — FK NO ACTION, cleared by the agenda delete —, `agenda_version`, `agenda_mode`, `agenda_item_index`, `ck_presentations_agenda`) and the spotlight (`spotlight_seq` default 0, `spotlight_revision`, `spotlight_x`, `spotlight_y`, `spotlight_at`) on `presentations`; nothing refused | no |
| v2.530.0 (frontend 5.424.0) | `d2d7730acb94` | creates `presentations`, `presentation_viewers`, `presentation_layout_reports` (presentation mode) and adds `users.profile_image_sha256` / `profile_image_type` with `ck_users_profile_image`; no existing row changes, nothing refused. Pictures are stored under `uploads/profile_images/` (in the uploads backup) | no |

## Scripts

- Every `scripts/*.py|sh` is described in `scripts/README.md`, which owns each script's options and job (`test_docs_contract.py::test_every_script_is_described_in_the_scripts_readme`; an untracked local script fails it too); `ca/local-ca.sh` in `ca/README.md`.
- Seeds and repair scripts run INSIDE the backend container (`docker compose exec backend python scripts/…`); `bump-version.py`, `scrub_logs.py`, `diag_summaries.py` run on the host (stdlib `python3`).
- `scrub_logs.py` fails closed. `migration-failure-report.sh` drops parameters and IPv4 but is NOT anonymised. `migration-status.sh` is read-only, safe DURING a migration. `collect-logs.sh` never discards a finished bundle (unwritable folder → project folder → `$HOME`, said).
- `seed_acceptance_fixtures.py` (the one "Acceptance" project every agent acceptance run uses — a feature a run could not exercise for want of data is fixed in this seed, never in the run's notes; `--rebuild` deletes the project first) composes `seed_demo_data.seed`, `seed_named_assets.seed`, `seed_eval_scenarios.populate`, then role accounts and fixtures through the real routes; it and `seed_finding_owners.py` are dev only.
- `parse-audit-agent-prompt.md` stays in step with `documentation/PARSE_AUDIT_BRIEF.md` and the assist tool names. `ca/local-ca.sh`: the root key never goes to the server; `ca.conf` values are mandatory (placeholders, documentation ranges, too-broad ranges refused).

### `collect-logs.sh` and the migration scripts

Guards here without a file are in `test_collect_logs_summaries.py`.
- **Anything the bundle adds must be scrubbable**: aggregate counts over row values, file names as `<job N>.<ext>`, never raw (hazard — removable: a test that runs the bundle over a seeded database and finds no seeded name). The one exception is `feedback.txt` (free text: scrubbed, flagged for the operator, omitted with `--no-feedback`).
- **The scrubber removes Postgres statement text in every logged form** (`STATEMENT:`, `duration: … statement:` / `parse|bind|execute`, `DETAIL:`, `CONTEXT:`, tab-led continuation lines) (`test_every_form_of_a_postgres_statement_loses_its_values`, `test_a_statement_continued_on_later_lines_loses_them_too`); an all-digit `.env` secret is harvested from 6 digits (`test_ops_scripts.py::test_a_numeric_secret_in_env_is_removed_from_the_bundle_and_a_short_number_is_not`).
- **Never harvest a date- or version-shaped value** (digits and punctuation only, `DATE_OR_VERSION`): it would blank that date from every timestamp (hazard — removable: a case harvesting "2026-09-30").
- A new table holding names (hosts, users, domains, orgs) goes in the `HARVEST` list (hazard — removable: a test comparing `HARVEST` with the models' name-bearing columns); statement text never does (`test_collect_logs_writes_lists_and_guards_both_files`).
- The last boot's migration lines from the backend and worker logs are extracted regardless of `--since` (a rebuilt container's log starts at that boot).
- `versions_and_schema.txt` names the frontend build nginx SERVES (`/etc/bluestick-frontend-build.json`, outside the web root) beside the backend's `/` `frontend_version` (the deployed FILES'; they differ after a copy with no rebuild) (`test_the_bundle_says_which_agent_routes_are_in_use_and_which_frontend_is_served`).
- Facts only, never row values: `permissions.txt` (plus Docker rootless / remapped, the database's TLS, the `[entrypoint]` / `[db-tls]` lines regardless of `--since`), `tls.txt` (derived certificate facts), `agent_surface.txt` (agent calls by route template with EVERY outcome over 30 days — what a route is retired on —, the log's reach, refused calls, MCP outcomes, proposals, evidence), `error_analysis.txt` (nginx status counts; 499 = a cancelled read, ordinary), `request_timing.txt` / `sql_statements.txt` (normalised text).
- **Any new query in `collect-logs.sh` or `migration-status.sh` stays behind the `lock_timeout`** (5 s, `PGOPTIONS`) or it hangs behind a migration's locks (hazard — removable: a test that every `psql` call there goes through the one wrapper setting it).
## Removed on purpose

- `transfer-images.sh` (and any pre-built-image path); `trust-cert.sh` / per-client certificate pinning; `scripts/preflight.sh`; `generate-ssl-cert.sh`; the `chmod 1777` uploads fallback; the hosted CI workflow (never ran).

## Decided — do not ask again

- A production host upgrades by moving its folder aside and extracting the new source into a new folder of the SAME name.
- `deploy.sh` is run by one ordinary docker-group account, never root.
- No pre-built-image deploy path: option 1 always builds, and the build needs package indexes.
- No permissions finding stops the application; the gate fixes what the caller can and says the rest.
- Installed report-template files are part of every backup.
- Tool-found credentials are never masked or scrubbed in the app (isolated network; analysts need them); the bundle's anonymisation is a separate matter.

