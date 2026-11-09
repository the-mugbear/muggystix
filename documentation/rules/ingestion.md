---
paths:
  - "backend/app/parsers/**"
  - "backend/app/services/ingestion_service.py"
  - "backend/app/services/job_transitions.py"
  - "backend/app/services/staged_import_service.py"
  - "backend/app/services/host_deduplication_service.py"
  - "backend/app/services/vulnerability_service.py"
  - "backend/app/services/nessus_integration_service.py"
  - "backend/app/services/format_registry.py"
  - "backend/app/services/line_shapes.py"
  - "backend/app/services/dns_name_service.py"
  - "backend/app/worker.py"
  - "backend/app/worker_loop.py"
  - "backend/app/api/v1/endpoints/upload.py"
  - "backend/app/data/parser_coverage.json"
  - "frontend/src/components/scans/**"
  - "frontend/src/hooks/useUploadReview.ts"
  - "frontend/src/data/uploadFormats.ts"
  - "documentation/PARSERS.md"
  - "documentation/UPLOAD_FORMATS.md"
---

# Ingestion: parsers, import jobs, cleanup, staged import

One module per tool in `backend/app/parsers/`; shared: `content_detection.py`, `parser_utils.py`, `cut_short.py`, `streaming_json.py`, `xml_stream_helpers.py`, `nse_vulns.py`. Invariants: the root `AGENTS.md`.

## Parsers

- **ONE registration, `app/services/format_registry.py`**: a `FORMATS` row (class, `sniffer`, `hints`, …) + its key in each extension's `DETECTION` order (`.xml`: `XML_DECISIONS`). `format_registry.detect` (→ `(file_type, parser_class, description)`), `build_parser_dispatch_map`, `looks_like_*`, the review and chooser derive from it. Guards: `test_parser_dispatch_contract.py`, `test_ingestion_format_chain.py`, `test_phase1_regressions.py::test_v2_27_0_content_detection_module_surface` (public `looks_like_*` / `is_nessus_sample` == the registry's sniffers), `test_parser_coverage.py` (formats, levels, claimed == written, an entry per tool). A module failing import is skipped, warned.
- `test_parser_registry_old_equals_new.py` pins attempt lists; regenerate only on purpose.
- `app/data/parser_coverage.json` (`/reference/tool-coverage`, `stored_as` pinned) changes with its parser.
- **Structure first; a filename is a hint**, never attribution alone. `source_tool=` reaches `parse_file(**kwargs)`; order per `amass_parser.attribute_tool`: named tool, JSON shape, filename, `hostname-list` (`test_filename_independence.py`).
- Check a tool's source before naming a service on its port: nxc NFS logs mountd's (→ `mountd`; NFS on 2049/tcp, `netexec_parser.nfs_finding_port`).
- **A parser never correlates**; the dispatcher does after commit (`ingestion_service.correlate_imported_hosts`, best effort; `test_review_2026_10_23b_ingestion.py`). (hazard — removable: a scan of parsers for correlation calls)
- New `VulnerabilitySource` value → an `ALTER TYPE … ADD VALUE` revision.

## Records and text

- **`parser_utils.write_record(db, write, observed=…, on_rollback=…)`** (`test_parsers_isolate_records_one_way.py`): opens `record_savepoint` (usable alone when not skipping), restores `observed`, runs `on_rollback` (`dedup_service.discard_rolled_back_state()`: its caches would name discarded rows), tolerates an inner rollback, RAISES `ParseFailure` on cancel / shutdown / stall.
- `retry_races=True` (nmap) retries once after `IntegrityError` / 40P01, for a `write` safe twice. `persist_host_observation(isolate=True)` and the import services use it (Nessus attributes / informational ports / retried findings, dedup name observations; a retried Nessus batch holds `record_savepoint`); the only other `begin_nested()` are dedup's in `_find_or_create_host_inner` / `find_or_create_port`.
- **Hosts by address outside dedup: `parser_utils.ProjectHostsByAddress`** (ids, no ORM hosts). Web parsers: `resolve_host_cached(db, hosts, ip, hostname=…)` (one `HostDeduplicationService.address_is` per miss).
- No blanket `except` swallows `ParseFailure` / `ShutdownRequested` (the restart would fail, keeping a partial scan). (hazard — removable: an AST test)
- Text columns: `parser_utils.read_tool_text` / `open_tool_lines` (`tool_text_encoding` for PowerShell UTF-16, NUL and first-line BOM stripped; `test_streamed_text_readers.py`). Line parsers stream (or `without_nul`); NetExec reads whole, refusing > `NETEXEC_MAX_BYTES` (100 MB). (hazard — removable: a scan for raw `open(` in parsers)
- **Failure is RAISED** (`ValueError` = not this format, `ParseFailure` = terminal), never `success: False` (`test_nessus_failure_reaches_the_dispatcher.py`). Nessus write failures → job `partial` + `skipped_count`.
- **One observation per `(host_id, source, plugin_id, port_id, name_id, title)`** (`uq_vulnerability_identity`, NULLS NOT DISTINCT): `VulnerabilityService._create_vulnerability_from_nessus` / `parser_utils.upsert_vulnerability` look up that strictly and `db.flush()` after `db.add()` (`autoflush=False`), so an in-scan repeat finds its row. Revision `b6e0d4f2a8c3` refuses on duplicates, naming them; `collect-logs.sh` counts them.
- Both refresh via `parser_utils.refresh_reobserved_vulnerability` (severity always, text when non-empty, never `scan_id`). (hazard — removable: an old == new test of both)
- `ports_v2.created_scan_id` stampers: `_create_new_port`, `upsert_ports_bulk` (not its `DO UPDATE`), `vulnerability_service._get_or_create_port` (`test_review_1008_ingestion_ports.py`). (hazard — removable: a scan for `Port` inserts)

## Heartbeats and stalls

- **No progress stops an import; duration never** (`services/ingestion_stall.py`). `INGESTION_STALL_TIMEOUT` (the old `INGESTION_JOB_TIMEOUT` is no longer read) is ONE window: the `statement_timeout` (`stall_statement_timeout`); `StallWatchdog` marks silence, the next beat raises `stall_failure()`; `_attempt_stalled` is terminal; after `STALL_GRACE_S`, fail and `exit_worker` (70). A long step with no beat IS a stall.
- `_discard_partial_scan` runs in `StallWatchdog.suspended()` (no verdict; window and grace restart after; a mark stays), `SET LOCAL statement_timeout = 0`, lock waits bounded (`lock_timeout_s`, else the window) (`test_cleanup_outside_the_stall_window.py`).
- `parser_utils.ProgressBeat` per record OUTSIDE the savepoint; `beat_while_reading(items, label)` for collect-then-write (`test_parser_followups_heartbeat.py`); nmap, gnmap, NetExec call `ingestion_service.report_progress`; Nessus beats in its service, not `nessus_parser.py`; RDAP once before writing. (hazard — removable: a per-format beat test)
- `report_progress` commits: never between writes the cleanup cannot find by scan (rdap's `NetworkAttribution`). `expunge_all()` → `dedup_service.forget_session_rows()`; nmap / gnmap `release_committed_history()` per beat.
- Cancel / shutdown / stall arrive only via a beat or the cancelled statement; test under `tests/ingestion_job_harness.py`.
- A beat is one fenced UPDATE (`status = 'processing' AND started_at = :claimed_at`); zero rows → `ParseFailure` before commit; fencing needs `claimed_at` (`test_job_transitions.py`). Cancel reads only the job row.
- Liveness: a per-attempt advisory lock (`hold_attempt_liveness`, own connection, taken before the claim commits); free = dead (`attempt_is_over`), else 1.5 × window; the worker reaps at boot.
- `ingestion_jobs.reap_count` = reaper re-queues (`INGESTION_MAX_RETRIES`; an operator queue / retry resets it); `retry_count` = failures + retries.

## Cleanup of a failed import

- A scan is named at once (`ensure_scan`, `announce_scan`, Nessus `ingestion_service.note_scan_created`) → `ingestion_jobs.in_progress_scan_id` via `job_transitions.stamp`, the ONLY record of an attempt's scan.
- **`ingestion_service.delete_scan_row` is the ONE scan-row delete**; files go at COMMIT only; no one else calls `remove_scan_files`; a project delete uses `ingestion_service.remove_scan_files_on_commit`. (hazard — removable: a scan for other `Scan` deletes)
- `ingestion_service.delete_partial_scan` (in `_process_job` before any fallback; on re-claim) removes what only the attempt created or reported. Neither it nor a hand delete restores overwritten values or conflict history (the dialog says so).
- A claim unable to remove the prior attempt's scan FAILS (`tests/test_ingestion_late_cleanup.py`).
- **Cancel removes nothing**: `IngestionService.cancel_job(db, job_id)` writes the row; the attempt cleans at its next beat; orphans go to `_discard_failed_jobs_leftovers` (the ONE place; `only_if_failed=True` reads pointer with status, which a second worker needs).
- `project_import_lock` SHARED per import transaction; `lock_project_for_cleanup` EXCLUSIVE (READ COMMITTED does not re-check `NOT EXISTS`).

### Hosts a scan touched

- **Writing about a host records it in `host_scan_history`**, all that Scans figures and deletes read (`tests/test_every_import_records_the_hosts_it_touches.py`: every format, or a reasoned exception). Handles: `find_or_create_host`, or `ScanHostObservations.note` + `parser_utils.record_addressed_hosts` (dnsx, DNS CSV; no state — a DNS answer does not say the host is up). `host_change_service` ignores no-state / `unknown` rows.
- `host_scan_history.hostname_changed` has two writers: `HostDeduplicationService._record_host_scan_history` (`_update_and_record_existing_host`; `note_hostname_changed` for NetExec) and `parser_utils.record_hosts_in_scan` (`ScanHostObservations.note(..., renamed=…)`, from `apply_hostname_candidate` / `ResolvedHost.renamed`). Not on the creating scan, never reset; a new `Host.hostname` writer uses one (`tests/test_scan_renamed_hosts.py::test_every_format_marks_exactly_the_known_hosts_it_named`). DNS results count `ScanHostObservations.renamed_host_ids`.
- NetExec column one is a service only if an nxc protocol (`netexec_parser.nxc_service_name`); `ZEROLOGON … 445` = open port, no service.

### Sightings and release

- `scripts_v2`, `host_scripts_v2`, `host_attributes`, `vulnerabilities`: one row per thing; `scan_id` = first recorder (`SET NULL`, moved only by release; history / web `scan_id` is NOT NULL). Sightings: `script_sightings`, `host_script_sightings`, `host_attribute_sightings`, `vulnerability_sightings` (`tests/test_scan_sightings.py`).
- `scan_sightings.see` / `see_many` on insert AND re-observation, in the row's savepoint, batched (Nessus per 100, masscan per batch); read only by deletes and the preview.
- **`scan_sightings.release_scan(db, scan_id)`**, one mode: drops rows only it reported; hands shared ones to the EARLIEST other sighting (`last_seen_scan_id`: latest); keeps a vulnerability a finding / proposal refers to (`kept_without_scan`, else `scan_sightings.removed_with_scan`); moves confidence rows, `host_created` / `port_created`; re-points `hosts_v2` / `ports_v2.last_updated_scan_id` to the newest other scan with history or NULL (`test_both_delete_paths_hand_last_updated_to_the_newest_other_scan`). A row it did not sight or first record is never dropped.
- A new thing-per-scan table needs a sighting table, a kind and a `release_scan` line. (hazard — removable: a schema test over `scan_id` tables)

### Keep guards

- **In doubt, KEEP** (`_host_is_only_this_attempts` / `_port_is_only_this_attempts`): another scan's or source's row, an operator's host name. A port under an observation a finding / proposal refers to stays (`scan_sightings.work_refers_to_it`).
- **Work = `host_work.work_kinds`** (note, review, tag, finding endpoint, test, evidence, remediation entry, proposal, `hostname_source` / `os_source`): cleanup `host_work.no_work_conditions`, warning `host_work.work_on_hosts`. New cascading-`host_id` work goes there + `frontend/src/utils/scanDeletion.ts` (`test_the_registry_names_every_kind_the_client_is_told_about`, `scanDeletion.test.ts`). (hazard — removable: a schema test over cascading `host_id` FKs)
- **Attached elsewhere = `ingestion_service.host_provenance_conditions`** (cleanup with work; hand delete, refusal, preview alone, via `hosts_scan_brought`).
- **NULL pointers per column** (that docstring is the table), never blanket `IS DISTINCT FROM`: a NULL first recorder keeps the host (`scan_sightings.first_recorded_elsewhere`, `_seen_by_another_scan`) bar rows release drops; a NULL `last_updated_scan_id` does not (`test_deleting_the_later_scan_and_then_the_first_removes_the_host`). A new nullable pointer declares its kind.
- Guards asked before release pass `before_release=True` (hand delete, preview); a new sighted-table guard has both. (hazard — removable: a before/after test per guard)

### Ports and names

- **`ingestion_service.delete_ports_scan_created`, ONE port step**: `created_scan_id = <scan>` or `port_scan_history`-created, under `_port_is_only_this_attempts`; only deletes and the preview read the stamp (Nessus's sole record).
- **`dns_name_service.delete_scan_observations(db, scan_id)`, ONE names step**, after hosts, before the row: the scan's own `dns_records` and names only it observed (`names_only_scan_holds_conditions`). KEPT on another observation (scan, import, `TESTED`, orphan), `created_by_id`, a test / endpoint / observation / other web interface naming it, or `scope_domain_match_condition`. A new `dns_names.id` FK needs a guard there. (hazard — removable: a schema test over those FKs)

### Hand delete (`DELETE /scans/{id}`)

- `hosts_scan_brought`, `release_scan`, `delete_ports_scan_created`, `delete_scan_observations`, the row; nothing re-implemented (`tests/test_scan_delete_by_hand.py`).
- It starts from every host with history (the cleanup: hosts it CREATED) and takes worked hosts: locked, counted → 409 `{error: "hosts_with_work", hosts_with_work, message}` unless `confirm_hosts_with_work=true`.
- `ingestion_service.refuse_hand_delete_while_importing`: a live `processing` attempt (`running_import`; a dead one never blocks) → 409 `{error: "import_running", message}`; else `lock_project_for_cleanup`, whose timeout is the same 409.
- **Preview == delete**: `GET /scans/{id}/deletion-impact` uses `hosts_scan_brought`, `ports_scan_created_condition`, `removed_with_scan` / `kept_without_scan`, `dns_name_service.scan_observation_counts`; a new removal adds a figure to `test_the_preview_says_exactly_what_the_delete_then_does`. `vulnerabilities_removed` / `vulnerabilities_kept` / `ports_removed_on_kept_hosts` are staying hosts'; `ports_removed` removed hosts'. No lock; flat statement count.

## Cut-short files

- **Asked about, never kept silently, where the reader can tell.** `app/parsers/cut_short.py`: readers call `settle_cut(records_read, label=…)` (raises `TruncatedFile` or returns), deciding nothing. `options.keep_truncated`: set only by `staged_import_service.queue_job`, cleared on other starts, read via `begin_read`'s ContextVar, never a `parse_file` arg.
- **Cut vs invalid = WHERE the reader failed**, never a library's error: JSON `EndTracking` on the text as the reader decoded it (never the file again); XML `xml_cut_short` (after the last read AND expat accepts every byte); JSON lines `streaming_json.is_cut_last_line`.
- JSON lines split only by `streaming_json.json_lines`, never `str.splitlines` (U+0085 / U+2028 / U+2029). (hazard — removable: a `splitlines` scan)
- `truncated_file` (`ingestion_service.TRUNCATED_FILE_ERROR`; `import_attention_service.failure_kind`) only after a complete record; normal cleanup; kept = PARTIAL. The Scans upload row offers it (analyst+); agents ask first (`agents-and-mcp.md`).
- **Invalid content** (`INVALID_CONTENT_KEEPS_WHAT_PRECEDES`): nmap / masscan XML keep what preceded, partial (`&#x1b;` case: `test_nmap_truncated_xml_quality.py`); masscan JSON too, decided at that line; JSON lines skip and count; others fail, keeping nothing.
- New formats go in `tests/test_every_format_at_a_cut.py` (`CUT_IS_DETECTABLE` / `A_CUT_CANNOT_BE_TOLD` + reason); also `tests/test_cut_short_detection.py`, `tests/test_truncated_file_keep.py`.

## Tool shapes and dedup

- Masscan refuses another scanner's XML. dnsx reads from `all` only array-less types (zone-transfer owners live there); `dns_zone_transfer_allowed` goes on an existing name-server host only.
- **A field is the TOOL's statement; a statement the tool did not make is never written** (2.535.0–2.537.0): `services/source_statements.py` declares per `VulnerabilitySource` what it can state (severity, CVSS, CVE, exploitability) and `upsert_vulnerability` / the Nessus path refuse an undeclared one (`UndeclaredStatement`; a catalog check's severity is the catalog's, not checked). No rating → `VulnerabilitySeverity.UNKNOWN` (Nikto); nothing about exploits → `exploitable` NULL (tri-state: true / false / NULL; readers test `IS TRUE`; OpenVAS `False` only from `E:U`, `True` from `E:P/F/H`, Nessus from its tags, vulners `is_exploit` both ways); `cvss_version` derived from the vector by `cvss_service.cvss_version_of` in the writer, never a parser; every CVE through `cve_ids=` → `vulnerability_cves` (`parser_utils.record_cves`, the one writer), `cve_id` the first. A new parser adds its declaration first (`test_source_statements_and_normalization.py`). (element names of the OpenVAS `E:` metric unverified on a real export.) `HostScanHistory.credentialed`: NULL = unsaid.
- **The tool-agnostic format** `observations_jsonl` (`parsers/observations_parser.py`, `source=EXTERNAL`, sniffer `looks_like_observations` on the `schema` key, first in the JSON order): one JSON object per observation, every field the uploader's statement, absent = not stated; a line that cannot be placed or read is skipped and counted. It is how a tool with no parser, or an agent's own output, enters; in `CASES` like every format. No real OpenVAS / Nessus capture is available as a fixture (`backend/tests/fixtures/native/` holds the other tools' samples).
- `dns_name_service.record_observation` is the one `DNSRecord` writer, uploads only. (hazard — removable: a scan for other `DNSRecord(`)
- `last_parse_stats["uninterpreted"]`: REDACTED shapes (`app/services/line_shapes.py`, `ShapeTally`; NetExec `uninterpreted_kind`) → `ingestion_jobs.uninterpreted_lines`, `assist_list_uninterpreted_lines`, `collect-logs.sh`. No command line, credential or cell; samples stay on site (`documentation/PARSE_AUDIT_BRIEF.md`).
- `uq_project_ip`; conflicts in `app/db/models_confidence.py`. `find_or_create_host` (`app/services/host_deduplication_service.py`) needs the project, refuses non-IPs, stores `normalize_ip`'s spelling, matches others (`HostDeduplicationService.host_address`). Nessus: `host-ip`, an address-shaped name, else skip and log.
- An operator OS (`Host.os_source == 'operator'`) survives imports (`_update_existing_host`), conflict recorded (`test_review_2026_10_23_agents.py`).
- Masscan writes via `upsert_hosts_bulk` / `upsert_ports_bulk`, the same rules (bulk == per-row, `tests/test_review_2026_10_23b_ingestion.py`); no rules copy in a parser.

## Staged import and upload jobs

- **Upload jobs need PROJECT analyst+** (router dependency), any submitter; the queue strip is read at analyst+ and lists the project's jobs (`test_upload_job_project_access.py`).
- `POST /upload` `stage=true` → `staged`. Parsers never dry-run: preview = `staged_import_service.detect_for_job` (detection + read-only sample).
- Basis `structure` vs `filename`; `fallback` = merely tried, a `format_registry.FallbackAttempt` (`*` in `DETECTION`), else any `.xml` reads as Nmap (`test_staged_import.py`). `needs_choice`: no candidate, fallback / filename-only primary, several structural non-XML matches.
- **Only `structure` is ready**: `chosen` null, the match in `suggested` until confirmed; fallbacks never suggested; `FormatRetryDialog` alike ("Let detection decide" only if confident).
- `GET /upload/formats` needs no detection (Retry inspection, except on 409: file gone). `POST /upload/jobs/discard-staged {job_ids}`; a removed review row is discarded (hidden, its file would resurface or trip the duplicate guard).
- **`format_override` is the whole attempt list** (`_build_parsing_attempts`); wrong → "Review the format and retry"; only a real string counts (test doubles answer every attribute truthily). Columns: `detected_file_type`, `format_override`, `final_file_type`, `source_tool`.
- `staged_import_service.sweep_orphan_upload_dirs` owns a file whose row never landed: job-named (`uuid4().hex`), idle `ORPHAN_UPLOAD_GRACE` (1 h), under no `ingestion_jobs.storage_path`; `reprocess_job` cleans its failed copy. (hazard — removable: one storage-path helper)
- `INGESTION_RETAIN_FILES_DAYS` (7); staged jobs expire at 24 h; removal stamps `ingestion_jobs.file_removed_at` (the sweep skips it, `file_retained()` reads it; start / retry / reprocess still check the disk).
- Re-process: new job, copied file, prior scan untouched, `options.allow_duplicate` (retries pass too), warned (`test_retention_and_reprocess.py`).
- `job_transitions` checks under the row lock (`_locked`: `populate_existing`); files go under it, stamped in that transaction (`test_every_ingestion_job_status_write_is_the_transition_layers`).
- `staged` ∈ `DUPLICATE_BLOCKING_STATUSES`. **`/upload/jobs/{id}/start` = `/retry`** (`staged_import_service.queue_job`): guard (409 `duplicate_scan`) unless `allow_duplicate`; bumps `retry_count` from `failed`; clears `dismissed_at`.
- `get_or_create_session_batch` commits before returning; `create_job` locks after its last await; `_register_job` (`_find_duplicate`, insert, commit) and `enqueue_job` run in `run_in_threadpool` (`test_registration_runs_off_the_event_loop`), as any async DB route must. (hazard — removable: a test that no `async def` route takes a `Session`)
- `GET /upload/jobs?ids=` (≤200): one poll per tick. Frontend: `components/scans/UploadReviewDialog.tsx` + `hooks/useUploadReview.ts`; `FormatRetryDialog.tsx` on `UploadHistoryRow.tsx`; `ImportResult.tsx` is the one summary.
- `documentation/UPLOAD_FORMATS.md` ↔ `frontend/src/data/uploadFormats.ts` (`tests/uploadFormats.test.ts`) ↔ `ALLOWED_UPLOAD_EXTENSIONS` / `ACCEPTED_EXTENSIONS` (`tests/uploadFormatContract.test.ts`).
- < 1 GB free → 507 (`queue_metrics_service.ensure_room_for_upload`); `warn_if_disk_low` bells admins daily (`operational_alerts`).

## Decided — do not ask again

- Stall, not duration, stops imports. Kept observations keep ports. No `PortScanHistory` for Nessus (counts would move). Offer every detectable cut. Hand delete takes all a scan alone brought. No `hostname_changed` back-fill. DNS CSV A / AAAA only note known hosts (more undecided). Upload jobs follow project role.

## Removed on purpose

- In-memory cancel set; "discard everything"; `success: False`; wall-clock cap; route-side `DISTINCT ON`.
