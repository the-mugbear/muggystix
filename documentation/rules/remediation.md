---
paths:
  - "backend/app/services/remediation*.py"
  - "backend/app/db/models_remediation.py"
  - "backend/app/api/v1/endpoints/remediation.py"
  - "frontend/src/components/remediation/**"
  - "frontend/src/pages/Remediation*.tsx"
  - "frontend/src/utils/remediation.ts"
  - "frontend/src/hooks/useRemediationPolicy.ts"
  - "report-templates/contact-report/**"
---

# Remediation tracking

The client's fixing progress, kept by project admins **per FINDING ON A HOST** (`finding_host_remediation`, 1:1 with `finding_hosts`). Routes: `documentation/API_GUIDE.md`.

## The switch

- `remediation_policy`: ONE row (id 1; none = defaults, OFF): `enabled`, days per severity (30/30/90/120, info none), `due_soon_days` (7), `time_zone` (IANA, UTC). `GET /remediation-policy` anyone; `PUT` GLOBAL admin, audited `remediation_policy_updated` (`RemediationSettingsSection`, System settings).
- **OFF:** every remediation route (JWT, agent, cross-project) 404s via `remediation_policy.require_enabled`, the router factory's dependency; a new route goes on such a router (`test_remediation_due_dates.py::test_off_means_none_of_it_answers`, listed URLs) (hazard — removable: walk every `/remediation*` path in `app.openapi()["paths"]` with the switch off). Oversight's `remediation` blocks `null`; nav hidden (`NavPage.feature: 'remediation'`, `hooks/useRemediationPolicy`: OFF until answered, a failure is `error` + `retry`); no alert; MCP tools stay listed and 404. **Never infer opt-in from "has records".**
- **A side record:** never synced with `Finding.status` / `FindingHost.host_status`. **The ONE crossing:** a record BECOMING `closed` moves its still-`open` endpoint to `retest` (`FindingService.request_retest`, same transaction, `announce_retests`, `summary.retests_requested`; an agent's `remediation_apply` too, with no proposal — `agents-and-mcp.md`) unless no defect (endpoint or finding false positive: `remediation_service._no_defect` on `fh.finding.status` as `_lock_for_write` re-read it; twin of `remediation_policy.concluded_no_defect`) (`test_remediation_vocabulary.py`, `test_remediation_defect_metrics.py`, `test_review_2026_10_23b_triage.py`) (hazard — removable: the retest step selecting through `concluded_no_defect` in SQL). Nothing else crosses: a claim never makes an endpoint `remediated`; an endpoint never writes a record.

## Vocabulary

- Three layers, no shared word: finding status (team; no finding-level "Retest"), endpoint state (assessor: Still present / Retest pending / Remediated / False positive), record (contact's claim). Neither remediated nor deferred = "Outstanding".
- Record `closed` = "Reported fixed", `open` = "Awaiting fix" (never "Closed" / "Open"), `deferred` = "Deferred" (`utils/remediation.REMEDIATION_STATUS_LABEL` / `REPORTED_FIXED`), wherever shown; endpoint `remediated` (`finding_hosts.host_status`) stays "Remediated". **Labels only:** enums unchanged (an agent sends `closed`); `heldRecordText` maps old timeline text.
- **The gap is its own state**, `remediation_policy.verification_expr` (SQL beside `state_expr`; never on the frontend): `reported_fixed_not_retested` (record `closed`, endpoint neither `remediated` nor `false_positive`); `remediated_record_open` (endpoint `remediated`, a record EXISTS `open` / `deferred`); else NULL.
- Rows carry `verification`; `verification_counts` are taken BEFORE the `verification` / `flag` / `state` / `status` filters; `verification=` on every mount; state counts and at-risk follow it (`tests/test_remediation_vocabulary.py`). **The gap never widens the list.**
- Page: a Deadline-cell line (`verificationNote`, never a column); count buttons set `?verification=` (`RemediationVerificationCounts`, hidden at 0/0, `InfoTip`) and drop `state` / `band` / `stale`; a clearable chip.

## The record

- Contact email (trimmed + lowercased — IS the identity; no contacts table), name, `team` (free text, caseless; `GET /remediation/teams`; follow-up stays by contact), `notified_on` ("Assigned on"), status, `closed_on`, `due_override_on`, `deferred_review_on`; dates hand-entered.
- Reads auditor, writes project admin (agent: `remediation.agent_admin` checks the key's operator too), direct and attributed. Reads take a SET of project ids (`project_id`, index `(contact_email, status)`). One router factory at `/projects/{id}` and `/agent`; `remediation.READ_ROLE` is both floors.
- **Every writer locks through `_lock_for_write`** (findings via `FindingService.lock_finding_ids` in id order, then `finding_hosts`, then records; `test_review_2026_10_23b_triage.py`) (hazard — removable: a test refusing a `finding_host_remediation` write outside callers of `_lock_for_write`).
- **A filled-in record is the admins' to give up:** before an endpoint / host removal or finding delete cascades it, `remediation_service.before_endpoints_removed`: a record holding anything → 409 for non-admins; an admin's removal writes a `change` entry `field: "finding"` with what it held. Off: no-op.

## Deadlines, states, flags

- `services/remediation_policy.py` is the ONE definition: SQL `state_expr` / `due_expr`, no Python copy. **The frontend never computes a state** (`utils/remediation.previewDueOn` / `policyDueBySeverity` are dialog previews) (hazard — removable: a lint rule refusing date arithmetic in `components/remediation` outside those two).
- **"Today" is `Policy.today()`** (day in `remediation_policy.time_zone`), never `date.today()` / `datetime.utcnow().date()`, no allowance (hazard — removable: a source scan of `remediation*.py`); a later `followed_up_on` is 422. Dialogs default to the server's `as_of`, never `new Date()`.
- Deadline = assigned date + days for the CURRENT severity, **derived while open, never stored**; FROZEN at close in `closed_due_on` (`_freeze_closed_deadlines`, SQL `due_expr`, on status / assigned-date change; cleared when not closed). No `closed_due_on` → neither on time nor late.
- States, exclusive, in order: `closed`, `deferred` (counted BESIDE overdue), `no_deadline`, `not_assigned` (never overdue), `overdue`, `due_soon`, `on_track`. `state_counts` before state / status filters, `status_counts` before status (`tests/test_remediation_deadlines.py`: count == list across pages).
- **Hand-set deadline:** `due_expr` = `coalesce(due_override_on, policy_due_expr)`, the only reader. It IS the deadline: runs without assigned date or timeline (`state_expr` skips `no_deadline` / `not_assigned`), frozen on close, resets alert bookkeeping; any change needs a note in the `apply` row. Rows carry `due_on`, `policy_due_on` (policy's, any status), `due_override_on`, `deadline_source` (`policy` / `override`).
- **Deferral = review date + reason:** entering `deferred` or moving the date carries `deferred_review_on` (≥ `Policy.today()`) and a note; leaving clears it (`ck_remediation_deferred_review_date`). A NULL date is never back-filled; it reads as due for review.
- **Flags** (`remediation_policy.flag_exprs`): `deferral_review_due` (null or ≤ today), `deadline_overridden`; `flag=`; `flag_counts` before `flag` / `verification` / `state` / `status`; state counts and at-risk follow it (`tests/test_remediation_due_dates.py`).
- **Search** `q=` (2–200): title, address, host name; caseless, LIKE-escaped; counts follow it.
- **Severity change:** `PATCH /findings/{id}` calls `remediation_service.severity_changed`: ONE `INSERT … SELECT` of `change` entries (`field: "severity"`, deadlines in `body`) for assigned, open, non-overridden rows (`test_remediation_due_dates.py`). A new severity-changing path calls it too (hazard — removable: the call inside the one writer of `Finding.severity`).

## Follow-up — by contact

- `GET /remediation/contacts` (state counts, most overdue first, `last_follow_up_on`) and `/teams`: first 200 of `total` (`{items, total, limit}`), said as "The first N of M"; "A new contact" only against the whole list.
- `GET /remediation/follow-up?contact_email=` (at-risk rows + message); `POST /remediation/follow-up`: one immutable `follow_up` entry per row + `last_follow_up_on`, not twice a day; a past day = noon in the policy's zone.
- Preview ≤ `FOLLOW_UP_MAX_ROWS` (500, all projects): `total`, `not_listed`; **recording covers every at-risk row**, never the previewed ids. `upcoming_days` (0–365) adds `on_track` rows due within N (`upcoming`; `remediation_service._to_remind`, read and POST).
- At risk → "Follow up"; only on track → "Remind" = the same dialog on 90 days (`RemediationWorkList.UPCOMING_REMINDER_DAYS`, in `HORIZONS`).
- **The message is for an outside reader** (`follow_up_text`): titles and addresses, no record number or product name; one opening line, projects by name, most urgent first.
- **Several projects:** `POST /remediation-overview/follow-up` records in every administered project with listed rows (`record_follow_up_across`, one transaction, `_lock_for_write`). `contacts()` answers `work_project_ids` (cross-project Document asks which; narrowed, "1 of 3" is a second read, not a second definition). The read answers `other_projects` (`remediation_service._STILL_WORK`; null when cut).
- A contact's name in the rows view opens that dialog (none without an address). "No contact yet" (`unassigned`) = no address, no name.

## Alerts

- `remediation_alerts.sweep` (ingestion worker, ≤ every 15 min) tells project and global admins of rows entering due-soon / overdue (`type: remediation`, `source_type: remediation_due_soon | remediation_overdue`, `source_id` = project), once per deadline per kind (`due_soon_alerted_for` / `overdue_alerted_for`, cleared by `apply` when status, assigned date or hand-set deadline changes); one notification per recipient / project / kind per sweep.
- `remediation_deferral_review`: once per `deferred_review_on` reached (`deferral_alerted_for`); no date → flag only. Body names ≤ `NAMED_ROWS` (3) "<finding title> on <address>", worst first. The frontend links from `source_type` + `source_id` (`flag=deferral_review_due`); no stored link.

## Across projects

- `/remediation-overview` (+ `/projects`, `/contacts`, `/follow-up`): projects the caller ADMINISTERS, ARCHIVED INCLUDED; archived project routes 410, so the factory is mounted again at `/remediation-overview/projects/{project_id}/remediation…` (`administered_project`), which `pages/RemediationDeadlines.tsx` uses. Rows open in their project (`hooks/useOpenInProject`) only when switchable. No agent twin.
- Oversight's strip opens `/remediation-deadlines`, gap counts too (`remediation_service.verification_counts_by_project` over `verification_expr` → `/remediation-deadlines?verification=<key>`); the per-project table is that page's. Averages = summed days ÷ summed rows; none = `null`.

## Defects, MTTR, insights, trend

- **`remediation_policy.defect_expr`**: `remediated` (ENDPOINT `remediated`), else `deferred`, else `open`; false-positive endpoint or finding → NULL. `defect=` / `severity=` repeatable on every mount.
- Projects table: Total + Remediated with the states, one statement (`overview_counts_by_project`); "All" opens every project summed. `GET /remediation-overview/defect-metrics` (`remediation_service.defect_metrics`): `?project=` or every administered NON-archived project (`active_only=true`) (`tests/test_remediation_defect_metrics.py`).
- **MTTR from the ASSIGNED date** to `finding_hosts.remediated_at` (only `FindingService._move_endpoint` writes it; `test_migration_finding_hosts_remediated_at.py`), a deferral's last status change, or today; `mttr_closed_days` = remediated + deferred; `not_assigned` / `not_measured` excluded and counted.
- ONE grouped statement: `severity_counts` (BEFORE the severity filter), `overdue_ages` (`remediation_policy.OVERDUE_BANDS` 1-7/8-30/31-90/90+), `not_followed_up` (`not_followed_up_days`); `overdue_band=` / `no_follow_up_days=` open them. Oversight: `overdue_breakdown_by_project` (`overdue_<severity>` / `overdue_age_*`). `components/remediation/RemediationInsights.tsx`: text + bars, numbers are buttons.
- **History RECORDED, never rebuilt:** `remediation_daily` (project × day × states), upserted by `remediation_service.snapshot` in the sweep; no back-fill, no unrecorded day as zero (`test_remediation_deadlines.py`). `GET /remediation/trend`: twelve months, on time / late / no deadline, in SQL; `RemediationTrend.tsx` (Plot small multiples + table) says when history starts, no line through one point.

## `POST /remediation/apply` — the ONE write path

- Page bulk edit and agent fill (`rows` ≤ 500, by `finding_host_id` or `finding_id` [+ `host_id`]; `dry_run`); no upload or spreadsheet matching. A different value conflicts without `overwrite`; `open` never does; leaving `closed` clears `closed_on`.
- **PLAN the whole call, then write:** a refusal on row 40 leaves 1–39 unwritten; the dry run refuses exactly what the call does (`test_remediation.py`, `test_remediation_due_dates.py`).
- **Status and its dates are ONE decision** (`_decide`, `_DATE_OF_STATUS`; `ck_remediation_closed_date`, `ck_remediation_deferred_review_date`): `closed_on` only beside `closed`, `deferred_review_on` beside `deferred`; a date cleared by leaving its status is no conflict. **Becoming `closed` undated dates it `today`**; the dialog pre-fills ONE row, never a selection.
- Note keys across the call (`keyed`): twice = one; two texts, one key → 422. `add_note`: a retry only for same text, host, `finding_host_id`, sent time; keyed insert in a savepoint, a lost unique race answers the stored note (`test_remediation_races.py`).
- `_resolve_targets`: SQL bounded at 501, hydrates accepted targets only; `finding_id` alone over > 500 endpoints is refused ("name hosts in batches"). Change entries take `created_at` from `occurred_at`'s clock.
- **`POST /remediation/assign-from-report`** (project admin): an ISSUED report's frozen `reported` map (`client_report_service.told_by_report`; addendum = what it added), still listed, unassigned, `open` → assigned date (default issue day; never future). COMPOSES `apply` (never a second writer) in batches, one transaction, after `_lock_for_write` over its WHOLE set once (per batch breaks the lock order; `test_review_2026_10_23b_triage.py`); dry run alike. Systems by host id + DNS-name id; no match → `not_in_list`.
- **`POST /remediation-policy/preview`** (global admin, on or off): `state_expr` under stored vs proposed policy, one statement, no write.

## Timeline and pages

- `GET /remediation/hosts/{id}/events` + `POST|PATCH|DELETE /remediation/events`: immutable change entries from `apply`, notes their author's; `occurred_at` backdatable, `created_at` not; entries outlive the finding (title kept).
- `/remediation` (route + nav `auditor`, writes `isProjectAdmin`): `pages/Remediation.tsx` on `usePagedList` + `QueueParts` (`ListBody`, `PagedFooter`, `useRowSelection`); `/remediation-deadlines` keeps filters across project switches; both `components/remediation/RemediationWorkList.tsx` (`scope: 'project' | 'all'`, `RemediationWorkList.VIEW_TITLE`). Lists what a client report includes plus anything tracked.
- **`RemediationEditDialog` is the one editor:** opens on what rows agree on, sends only changed fields (`utils/remediation.draftChanges`) with `overwrite: true`. Address: `datalist` over `listRemediationContacts` (known one brings its name), "A new contact" said, never refused. `emailProblem` / `reviewProblem` once left; `draftProblem` decides saving.
- `RemediationTimeline`'s sheet stays mounted, `TimelineBody` keyed by host. Writes call `utils/remediation.invalidateRemediationReads`; a new read joins `REMEDIATION_READS` (`frontend.md`). **ONE Deadline column** (`utils/remediation.deadlineCell`).
- "Copy summary" (`remediationSummary`) and a browser CSV (`remediationCsv`, `csvCell` formula-escapes, `utils/download.saveBlob`) from ONE request: `GET /remediation/export` (and `/remediation-overview/export`) → `remediation_service.export_rows` = `list_rows(with_counts=False)` ≤ `EXPORT_MAX_ROWS` (20,000) + true `total` — **ROWS, not a file**; JSON, not a stream (`tests/test_remediation_export.py`).

## One contact's document (`services/remediation_report.py`)

- ON DEMAND report-worker job (`report_type='remediation'`, `contact-docx` / `contact-html`, `client_report_render.run_client_job`). **Never a `reports` row** (not issued, frozen, numbered, addended); expires like an export; served only by remediation routes with tracking on (`test_review_2026_10_23_reports.py`).
- Dataset: `ClientReportService.build` of a TRANSIENT `Report`, narrowed to the contact's open work, + `deadline` / `deadline_state` / `due_on`; `_path` renumbered after narrowing (text is found by path).
- **EXTENDS `pentest`:** `report-templates/contact-report` (`REMEDIATION_REPORT_TEMPLATE`; `"kind": "contact"`, `"extends": "pentest"` in `template.json`) holds only `report.qmd`, `partials/_finding.qmd` (no author TODO), `partials/_affected.qmd` (Due + Deadline); own branding; no `assets` in its manifest, read alike by `scripts/upgrade-instance.sh` (`test_ops_scripts.py::test_upgrade_carries_the_installed_files_of_a_template_that_extends_another`). **Never add contact or deadline logic to `report-templates/pentest`** (`test_the_penetration_test_template_itself_is_untouched`).
- **Whose a system is never depends on what is listed:** `_build` asks `remediation_service.findings_wholly_of` (every state, SQL, no cap) (`test_narrowing_the_document_does_not_change_whose_a_system_is`). Confirmations only for their systems; an unplaced image withheld unless the whole finding is theirs (`images_withheld`); no issued number → no F-number; ASCII name (`client_report_render.file_slug`).
- `list_templates()` = client templates; `client_template()` guards defaults and drafts; `GET /client-reports/templates` lists all with `kind`, choosers filter by `isClientTemplate`. Hostile-text test, plus the contact's fields.
- `POST /remediation/contact-report` (project admin) queues; `report` timeline entries on COMPLETION, on listed hosts (`remediation_report.record_prepared`); status + download in the factory. Omitted findings → `remediation.not_listed`.
- MOST URGENT FIRST (`group="due"`, stable). `upcoming_days` narrows by the LIST's selection (`contact_rows`: `None` all open, `0` at risk, `N` + due within N), stated as `remediation.selection` / `listed` beside `open`; deferred prints its review date; status carries `findings`, `without_recommendation`; dates ISO. `ContactReportJob.status` is typed to server values; `failed` shows why; `completed` with no file is said apart.

## Decided — do not ask again

- Becoming `closed` requests the retest, an agent's directly; `remediation_apply` keeps a different value without `overwrite` (the page always sends it).
- "Remediated", not "Verified fixed"; defects and MTTR count the endpoint after retest, MTTR from the assigned date, both headings with an InfoTip.
- "Remediated, record still open" counts only endpoints with a record; `deferred` counts as open. Contact and status may differ per host of one finding.
- The reminder is copied, never sent. No cross-project contact document until asked; it extends the pentest format, restylable.
- No one-off alert for rows deferred before the review date existed. A severity change writes an entry only when the deadline moved. An addendum's start-the-clock dates only what it added. A hand-set deadline survives an assigned-date change. `status: deferred` alone is 422. A closed row's corrected assigned date re-freezes against today's policy.
- No median or other derived figure; one trend day is a sentence; Oversight shows both gap counts. "Copy summary" opens `RemediationSummaryDialog`. A timeline note is its author's to edit and remove.

## Removed on purpose

- `projects.remediation_in_oversight` (dropped by `b5e2c9d7f4a6`); a State column beside Due; a second per-project table on Oversight; any outbound send (email, webhook) for follow-up.
