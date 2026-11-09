---
paths:
  - "backend/app/services/finding_service.py"
  - "backend/app/services/finding_actions.py"
  - "backend/app/services/proposal_service.py"
  - "backend/app/services/host_test*.py"
  - "backend/app/services/agent_evidence_service.py"
  - "backend/app/services/scanner_observation_service.py"
  - "backend/app/services/notification_service.py"
  - "backend/app/services/vuln_identity.py"
  - "backend/app/services/misconfig_backfill.py"
  - "backend/app/services/report_draft_service.py"
  - "backend/app/db/models_findings.py"
  - "backend/app/db/models_host_tests.py"
  - "backend/app/db/models_proposals.py"
  - "backend/app/api/v1/endpoints/findings.py"
  - "backend/app/api/v1/endpoints/findings_bulk.py"
  - "backend/app/api/v1/endpoints/host_tests.py"
  - "backend/app/api/v1/endpoints/host_notes.py"
  - "backend/app/api/v1/endpoints/proposals.py"
  - "backend/app/api/v1/endpoints/agent_proposals.py"
  - "backend/app/api/v1/endpoints/scanner_observations.py"
  - "backend/app/api/v1/endpoints/notifications.py"
  - "backend/app/api/v1/endpoints/report_drafts.py"
  - "frontend/src/components/findings/**"
  - "frontend/src/components/observations/**"
  - "frontend/src/pages/ScannerObservationsPage.tsx"
  - "frontend/src/components/proposals/**"
  - "frontend/src/components/host-inspector/**"
  - "frontend/src/components/HostInspector.tsx"
  - "frontend/src/pages/Findings.tsx"
  - "frontend/src/pages/FindingDetail.tsx"
  - "frontend/src/pages/Proposals.tsx"
  - "frontend/src/utils/hostTests.ts"
  - "frontend/src/utils/findingStatus.ts"
  - "frontend/src/utils/mentions.ts"
---

# Findings, scanner observations, host tests, evidence, notes, proposals

Routes and fields: `documentation/API_GUIDE.md`; model, vocabulary (`utils/findingStatus.ts`), lock orders, proposal rule: the root `AGENTS.md`.

## Findings and endpoints

- One finding per issue (`dedup_key`); each `finding_hosts` row has its own state (open "Still present" / retest "Retest pending" / remediated / false_positive). Host scanner rows carry `finding_endpoint_status` + `finding_on_this_host` (`false` = untriaged).
- **`FindingService._move_endpoint` is the one write of `finding_hosts.host_status` on an existing row** (validation, history line, `remediated_at` set / cleared); the request field is the status enum (422). (hazard — removable: a source-scan test.)
- **`FindingService.lock_finding` first on an existing finding** (`FOR NO KEY UPDATE`): joins, `add_hosts`, `restore_endpoint`, removals, `set_endpoint_status(es)`, `record_retest`, `delete_finding`, `set_status` / `set_statuses`, `PATCH`, `assign_findings`. All are `FindingService.lock_finding_ids` (also behind `lock_findings`, `proposal_service.lock_for_decision`, `remediation_service._lock_for_write`): id order, columns re-read, so an edit's "before" is what it replaced. `test_review_2026_10_22_finding_lock.py`. (hazard — removable: a source-scan test for `with_for_update` on `Finding`.)
- `promote_vulnerability` locks the issue's evidence before the finding, as `create_finding_from_evidence` does; new paths making an issue's finding use one of them.
- **Every removal of a finding from a host, or deletion, calls `remediation_service.before_endpoints_removed` first** (`remediation.md`). (hazard — removable: a test enumerating removal paths.)
- Reports count `remediated` and `false_positive` endpoints inactive.

## Retests

- **Per HOST**: no `retest` in `FindingStatus` (revision `e5c1a7b3d9f2`); both doors refuse it.
- Asked by hand or by "Reported fixed" (`remediation.md`); `_move_endpoint` stamps `retest_requested_at` on the DATABASE clock.
- **Told:** the owner, else (none, left, deactivated, admin without membership) every active member; never the asker; nobody if the owner asked. ONE notification per PERSON per action (`finding_retest`; several: `finding_retests`, opening the retest list) via `FindingService.announce_retests` → `NotificationService.notify_retests_requested`, called by each writer into `retest` after its moves; the call to act to analysts / admins only (`test_review_2026_10_23b_triage.py`). (hazard — removable: call it from `_move_endpoint`.)
- **"A retest stands" = `finding_needs.retest_stands`**: requested AND not judged false positive (`test_retest_workflow.py`) — read by the guard, `record_retest`, every list / count, each endpoint's `retest_stands`; `host_status = 'retest'` is shown, never counted. `retest_stands_on` is its row twin (hazard — removable: one test over both).
- `ck_finding_hosts_retest_requested`: a `retest` row has its request (older: the epoch); fixtures stamp it.
- **Undo of a removal** restores the request from the removal's history line (`removed_host_id` / `removed_name_id` / `removed_retest_requested_at`, revision `d4a8c2e6f1b3`; never JSON) via `restore_endpoint` → `_removed_retest_request` (latest removal), set BEFORE the state; the body has no such field (`test_migration_removed_endpoint_columns.py`).
- **Seen by all**, kept requests too: `host_state=retest` (`has_retest_standing`), `compute_retests_pending`, `need=retest`.
- **Closing:** while one stands, → `remediated` is `_move_endpoint`'s 409 unless `retest_result_recorded` (`no_finding` evidence on that endpoint since the request). `FindingService.record_retest` = evidence + move, one transaction, on any standing endpoint (a "still present" that moves nothing clears the request with a history line). A hand move to Still present keeps the request; `record_retest`, `remediated`, `false_positive` clear it; with no request, remediated is free.
- `hosts[]` carry `retest_requested_at` + `retest_stands` (`FindingEndpoints.awaitsRetest`, "Record retest…"); the picker sends Remediated and the 409 opens the retest dialog.

## Host tests

- `proposed → in_progress → done | dismissed` (dismissal: a reason; `done`: evidence or a summary). No server ranking.
- **One router factory** for JWT routes and `/agent/host-tests`; people use `POST /host-tests` (`AddTestPanel`), never a person-only route; "Ask agent" always has the plain action beside it.
- `POST /host-tests`: ≤200, all-or-nothing, idempotent by `request_key` (other content → 409). `PATCH` conditional in the UPDATE's WHERE (`expected_revision`, 409) (`test_host_tests.py`). `target_fqdn` (a test aimed at a NAME at this host) resolves through `dns_name_service.name_observed_at_host` — the ONE rule, shared with a finding's `name_id`; the Add test panel's "Aimed at" picker (`components/names/HostNameSelect`) offers exactly those names.
- **A RESULT is an evidence record** (`evidence_records.host_test_id`). `POST /host-tests/{id}/result` = evidence + move, one transaction (finding / no_finding → done; inconclusive / failed → in_progress; stale revision refused first); agents use `POST /agent/evidence` + PATCH.
- Replays: `record_result` → `record_evidence` (`server_timed=True`): same → stored, changed → 409; `update_test` always runs. `record_evidence_once` → `(record, created)`.
- `vulnerability_id` (same host, else 422) → `host_tests.issue_key` + `issue_title`, never the row id.
- **ONE promotion from a test:** `POST /evidence/{id}/finding` (locked; 409 if linked; `FindingService.create_finding`); linked → `promote_or_dismiss_vulnerability(scope="host", on_join="confirm")` → `(finding, created)`, `created` never a prior lookup (`joined_issue: true`, no severity). Title / severity only when unlinked.
- Over 5 MB: the service's 413, before hashing; no `max_length` on `raw_output`.
- Serializers cost the same after `commit()` (`db.session.persisted_ids`): `test_host_tests.py`, `test_subnet_detail_proposals.py`. Keep `expire_on_commit`. Owed: `findings.py` (status), `findings_bulk.py`, `assist.py`, `proposals.py`.
- Assignee: `project_assignee.load_project_assignee(roles=admin/analyst)` (`test_assignee_rule_one_implementation.py`); notifies `type: "assignment"`, `source_type: "host_test"`.
- `last_outcome` / `unpromoted_findings` / `finding_ids`: one evidence query a page (`host_test_service._serialize_loaded`); `list_tests` = three statements (`_serialized_loads()`, never `serialize_many`) (`tests/test_read_cost_2026_10_10.py`).

### On the frontend

- No project-wide Tests page (`HostTestsSection.tsx`, Operations, sessions). One load per host page (`hostTestsController.tsx`); `hostTestsContext.ts` stays free of the API client (or unmocked `VulnerabilityGroup` tests break).
- `HostInspector`, `HostFindingsCard`, `HostTestsSection` key their inner component by host id (`['getHost', projectId, hostId]`, `['listHostTests', projectId, {host_id}]`); new panels go inside.
- A row: one line, ONE primary action (Record result), the rest in its menu. "To do" = `utils/hostTests.testNeedsWork`. Evidence lists `?unlinked=true` only.
- "Ask agent" = `hooks/useAgentTask`, decided by a read AT THE CLICK (`queryClient.fetchQuery` of `myAssistSessionsRead`; dialog `useMyAssistSessions`).
- Counts: `utils/hostTests.hostTestCounts` (`planned_test_count`, `tested_record_count`).

## Notes and comments

- A thread: body, replies, mentions, attachments, `note_type`, `pinned` — never work state or a finding source. Types `observation` / `question` / `decision` / `handoff`; `finding` / `action` refused on write.
- `note_status_change` stays in `RETIRED_WEBHOOK_EVENTS`. Screenshots go on the finding's comments (`POST /findings/{id}/notes/{note_id}/attachments`).
- A comment is its author's (admins refused), kept once replied to (409); an image its uploader's or a project admin's (`mayDeleteAttachment`, `NoteAttachments.tsx`, `reportMarking.canMark`).
- A comment carries `author_profile_image` (a person's note only — an agent's note never shows its operator's face; `host_serialization._serialize_note`).
- **Mentions:** `notification_service.find_mentions` over usernames (never `@\w+`), twin `utils/mentions.ts`, one case table `backend/tests/fixtures/twins/mentions.json`; agents get `unmatched_mentions`.
- The mentioned, then the discussion, no one twice (`test_mentions_and_discussion.py`) — one function, `NotificationService.announce_note`. (hazard — removable: a source-scan test.)

## Finding writes

- **`POST /findings`** (`components/findings/AddFindingForm`: the host page's Findings section and the Names sheet's dialog). `name_id` (2.538.0) with exactly one host makes the endpoint `(host, name)`; the name must be observed at that host — `dns_name_service.name_observed_at_host`, the ONE rule shared with a host test's `target_fqdn` (`test_finding_on_a_name.py`: both doors answer `NAME_NOT_AT_HOST`); `finding_service.named_endpoint` is the one caller of it for a finding — the route, and an agent's `propose_finding` `name_id` (2.539.0) at propose AND at accept (`promote_evidence(more_names=)` when a result is cited; `test_agent_proposals.py`). Accepted `finding_create` = `FindingService.create_finding`, or `agent_evidence_service.promote_evidence` when it cites an unlinked outcome-`finding` result on its host; report text for an issue with a finding is a 409.
- **Fields are any analyst's** (`FindingActor.may_modify` = `is_project_analyst`, `can_modify`). `DELETE`: author or admin, never the owner (`may_delete`, `can_delete`); audit `finding_deleted`.
- **Title, severity, owner, report text, CVSS edits write ONE row via `finding_actions.record_edit`** (`field`, `before_value`, `after_value`, `summary`; unchanged → none), from `apply_report_text` (takes `db`), the `PATCH`, `finding_actions.assign_findings` (`set_owner` + ONE notice to the new owner: `source_type "finding"`, several `"findings"` → `/findings?owner=me`). Create / promote set `owner_id` silently. The list carries `field` only; values at `GET …/history/{entry_id}` (`components/findings/FindingEditChange`). `test_finding_edit_history.py`. (hazard — removable: a source-scan test.)
- **`findings.evidence_annotation_id` is read-only legacy** (`test_finding_source_note_is_read_only.py`), kept for `evidence_host_id`, `finding_image_attachments`, `summary.evidence_notes`, the agents' read, the note's `finding_id` badge.
- **Closing reasons are asked, never required** (`set_status` never refuses); the one dialog is `frontend.md`'s (`components/findings/StatusReasonDialog`).
- `watching` is offered nowhere nor accepted as a Hosts filter; the enum stays for old rows. A host's `watching` review state: `agents-and-mcp.md`.

## Finding reads

- `Finding.hosts` / `Finding.vulnerabilities` plain lazy, loads named (`tests/test_finding_loading.py`).
- List rows: `hosts` PREVIEW ≤5, `host_count`, `endpoint_status_counts` (`FindingService.endpoint_summaries`); agents `FindingService.address_summaries`; never load a page's every endpoint.
- **Filters = `FindingService._selection`** for the list, `severity_counts`, `owner_counts` (`tests/test_finding_owner_counts.py`) and the finding page's Previous / Next (`queue_neighbours`, `GET …/neighbours`; `tests/test_finding_queue_neighbours.py` walks every sort); the agents' read has no `owner_counts` (filters `owner=<username>`). The list's query parameters are declared once (`findings.finding_list_query`); a Findings address is read once (`utils/findingsReturn.findingFiltersOf`), by the list and by `FindingsQueueBar`.
- **Needs = `services/finding_needs.py`** (`missing_text`, `uncaptioned_images`, `proposals`): `need_condition(kind)` (`_selection(needs=)`, `api/params.FindingNeedsParam`), `needs_of(db, ids)` (`needs[{kind, text, count}]`); both lists, Operations (`with_status`), `summary.uncaptioned_images` (`tests/test_finding_needs.py`); `utils/findingNeeds.rowNeeds` → `NEED_SECTION[kind]`.
- `PATCH /findings/{id}/endpoints` (1..500): `set_endpoint_status`, all-or-nothing, people only (agents propose).
- `uq_finding_scanner_issue (project_id, dedup_key)`: savepoint insert, join on `IntegrityError`; `row:` keys lock via `_scanner_finding_for` (`test_finding_issue_race.py`).
- Single-finding answers carry `hosts[].segment` (`finding_service.endpoint_segments` → `subnet_insight_service.group_hosts_into_segments`, PROJECT hosts + `unmapped`; `tests/test_finding_endpoint_segments.py`); list previews send `segment: null`; never grouped in the browser.

### The finding page

- Endpoints follow style guide §47 (`components/findings/FindingEndpoints.tsx`; `ENDPOINT_CAP` 100, `ENDPOINT_MOUNT_MAX` 500, `ENDPOINT_GROUPS_OPEN_MAX` 25; `hash={focusEndpointId == null}`; `?endpoint=<finding_host_id>`).
- **Answers land via `findingCache.putFindingAnswer` / `putFindingAnswerInRows`** (never `setQueryData` / bare `putFinding`) over a LOWER `revision`; `structuralSharing: keepNewerFinding`. `findings.revision` (revision `f2b8d4a6c1e3`) only by `models_findings._count_finding_revisions` (`before_commit`, last statement); a pre-commit serializer sets it after (`set_finding_endpoint_statuses`). `test_finding_revision.py`, `FindingDetail.test.tsx`, `FindingEndpoints.test.tsx`. (hazard — removable: a lint rule.)
- One endpoint-change queue (no sequence counters); bulk acts on rows matching the CURRENT filter.
- Discussion: `useListQuery` over `GET …/findings/{id}/notes`, (`{items, total}`), never numbered pages (`#note-<id>` must mount); the count shown is `total`.
- `hooks/useFindingImages`: one object-URL cache (`resolver`, `thumbnails`, `listStatus`); "not this finding's image" only when `ready`.

## Scanner observations

- By `Vulnerability.issue_key`; judged = `engagement_metrics_service.observation_judged_on_host`.
- **A row's fields are the tool's statements** (`ingestion.md`): `exploitable` is tri-state — the issue's flag is `bool_or` over its rows (true / false / NULL), `exploitable=true` keeps only stated ones, every "has an exploit" reader tests `IS TRUE`; the inspector says "No exploit reported" only for `false` (`IssueMeta`); `severity` `unknown` ranks below info (Nikto's rows) and is not informational; `cvss_score` is the max over rows whose `cvss_version` may differ (the Query column says so); `search` matches any CVE the tool named (`vulnerability_cves`). The Query page's examples group `by issue` with `dc(host)` — the page's own grouping and unit — never `by title` / `count` (`test_query_observations_2026_10_22.py`, `test_exploitable_tristate.py`).
- `/observations` lists every issue (`judged=any`; API default `waiting`), `scanner_observation_service.JUDGED_STATES` both doors (`test_query_observations_2026_10_22.py`); `include_judged=true` + another `judged` → 422; promoting there confirms. The Findings page has no observations view (`?view=observations` is just the findings list; `Findings.test.tsx`).
- `components/observations/ObservationInspector.tsx`: `issue_host_rows` → `serialize_vulnerability` only; its actions are the list's; `observations/IssueMeta.tsx`; `host-inspector/ScannerReferences`. `HOST_IDS_CAP` 100 = `HOST_CAP` (`observationHostIdsLimit.test.ts`).
- **`FindingService.promote_vulnerability(..., host_ids=None, on_join="set"|"confirm"|"keep") -> (finding, created)`**: `None` = rows whose STORED `issue_key` matches (`_issue_host_ids` → `scanner_observation_service.issue_rows_condition`; `test_review_2026_10_22_issue_fanout.py`), never `plugin_id` / `cve_id`; `"confirm"` open → confirmed only; `"keep"` for bulk (`POST /scanner-observations/promote`, `created` from the return; `test_scanner_observations.py`).
- **Evidence links inside `promote_vulnerability`** (`agent_evidence_service.lock_issue_evidence`, `link_issue_evidence`), every host, not on a false-positive dismissal. (hazard — removable: a source-scan test.)
- `false_positive` defaults to `scope: "host"` (`dismiss_vulnerability_on_host`, status untouched); accepted risk is issue-wide only.
- List dismiss: one observation (`POST /scanner-observations/dismiss`, `scanner_observation_service.dismiss_issue`) — `promote_or_dismiss_vulnerability(scope="host")` per ticked host, id order, or `promote_vulnerability` with the list's host set.
- Host-scoped promotion (inspector default; API `"issue"`) attaches only that host and reopens its false-positive row (`reopen_false_positive_endpoints`).
- `misconfig_backfill` never re-keys onto a check that has a scanner finding (`UNMERGED_KEY`).

## Proposals

- Kinds: report text, new finding, promote / dismiss, endpoint status, subnet description + site (`subnet_details`: one per subnet, `agent_proposals.subnet_id` CASCADE, accept = `subnet_details.set_subnet_details`, SUBNET locked first, `subnet_changed_since_proposed`, text only, admins told (`agent_session_scope`); `test_subnet_detail_proposals.py`).
- Accept runs as the accepter, savepointed (`finding_actions.apply_report_text`, `promote_or_dismiss_vulnerability`, `create_finding`, `set_endpoint_status`).
- **`proposal_service.lock_for_decision` inside `_decide`** only: report text FINDING → proposal (`get_proposal(for_update=True)` + `populate_existing`); endpoint FINDING → ENDPOINT → proposal; else proposal, a deadlock = 409 (`session.is_deadlock`). `test_proposal_decide_locking.py`.
- "Needs review" is derived (`proposal_service._about_findings`, `pending_per_finding`, via `finding_vulnerabilities`).
- **Bulk accept** skips text changed since `base_value` (`proposal_service.text_changed_since_proposed`) or without one (`base_recorded` false → `base_not_recorded`; `TextComparison` `baseUnknown`), left pending in `skipped` — `proposal_service.left_for_one_at_a_time` (`test_agent_proposals.py`); never picks between drafts (`competing_drafts`); ≤ `PROPOSAL_BULK_MAX` 200 = `BulkBody`, unchunked (`tests/proposalBulkLimit.test.ts`).
- `replaces`: `proposal_service.claim_replaced` (own, same source, pending, `_same_target`) locks via `lock_for_decision` before writing, by target key then id; `mark_replaced` → `superseded`, `replaced_by_id`, `decided_by` empty (`test_agent_feedback_gaps.py`, `test_review_2026_10_23_agents.py`).
- `_provenance` on the change's own history row; values are whole sections (32,768), never critiques.
- **"Not enough to write this" is an answer** (`rationale`, `declined`), never written into a section (`test_agent_safety_policy.py`).
- `report_text.internal_references`: 422 on proposals (`_refuse_internal_references`), drafter declines, `summary.internal_references` warns, a person's save passes; `session ID 4821`, `user_id=5` legal (`tests/test_report_text_internal_references.py`).
- `proposal_service.serialize_created` gives lengths (`value_chars`, `base_value_chars`, `report_text_chars`), the list `proposal_service.without_texts`; texts at `GET /agent/proposals/{id}` (`get_proposal`).
- `payload.base_value` server-set, `changed_since_proposed` derived; the drafter (`/reports/draft/finding-text`) makes `llm_draft` through the same service.
- **Writing guidance: `services/report_writing_guidance` only** (a global admin's) — the drafter, `writing_guidance`, `GET /agent/assist/writing-guidance` (`propose_finding`); `FIXED_RULES` code, after it; `SECTIONS` = `report_draft_service.FINDING_TEXT_FIELDS` (`test_report_writing_guidance.py`).
- Image references: `client-reports.md`.

### Who is told

- Finding: author, owner; promote / dismiss: the evidenced finding's people, else admins (`source_type: "agent_session_new"`, `/proposals?agent_session_id=…&scope=all` — `scope=mine` would hide them).
- `mine` / `pending_mine` / badge = `proposal_service.mine` (`findings_of` OR `proposed_by_user_id`); never told of one's own agent's proposals.
- A proposed finding says where it waits: `serialize_created`'s `note` (`NEW_FINDING_NOTE`); Findings' `by_kind.finding_create` → `/proposals?kind=finding_create&scope=all`.
- `NotificationService.current_members`; an agent's assigned tests, one unread per (assignee, session).
- `utils/notificationLinks.ts` decides links; opening in their `project_id`: `frontend.md` (`hooks/useOpenInProject`).

### On the frontend

- `components/proposals/ProposalItem.tsx` + `hooks/useProposalDecision` everywhere; reviewed where it applies (§43, `FieldDraftsReview` / `TextComparison`). Proposals and Agent Sessions reads are every member's.

## Decided — do not ask again

- Retests: per host; no reachable owner → all members; anyone closes with evidence.
- Any analyst edits and assigns; author or admin deletes. Closing reasons optional.
- Source-note pointer kept; re-homing declined (revisit if many).
- Mention highlighting stays in the browser.
- Observations promotes as confirmed, dismisses one at a time (no multi unasked).
- Subnet site / description by proposal, bulk-acceptable; bulk skips rewritten or base-less text.

## Removed on purpose

- Test plans, runs, approval gates; note status, assignee, due, resolution, `status_change`, `my_notes`, `POST /annotations/{id}/promote`, `FindingService.promote_annotation`.
- `list_findings(with_endpoints=)`; `selectin` on `Finding.hosts`; a separate drafts list, collapsed text; echoed proposal texts; direct agent routes for proposal kinds.
