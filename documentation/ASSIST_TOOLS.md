# AI Assist — the tool surface, derived from the analyst's job

The assist surface exists so a security analyst can review and interact with a
project through an agent: understand where the project *is*, decide where to
focus next, see the patterns in it, and — when the work concludes — write the
engagement up against their own report template, citing the notes and
screenshots recorded on findings.

This document derives the tool set from that job rather than adding tools as
questions come up. It records what exists, what is deliberately *not* a tool,
and why.

Mechanically, a tool is a declarative mapping onto an HTTP endpoint
(`mcp_tools.py` → an `/api/v1/agent/*` route — reads under `/agent/assist/*`,
the three writes under `/agent/hosts/*`); the MCP layer makes no authorization
decision. See [MCP.md](MCP.md) §1 for the dispatch path.

**Scope of this document:** the assist READ surface — the 35 `assist_*` reads.
The three `assist_*` writes (`assist_add_note`, `assist_set_follow`,
`assist_patch_host`) are allowed exactly when the operator's project role
permits writes and are not analysed here. Host tests, evidence, proposals and
remediation tracking are named where a question needs them; [MCP.md](MCP.md)
§2 lists them.

---

## The governing constraint: context is not free

Every tool is text in the model's context on every session. One session does
every kind of work, so `tools/list` returns the whole catalogue: a tool added
for assist is context spent by a session that only uploads scans too.

The catalogue is 69 tools, 38 of them `assist_*` (35 reads and 3 writes);
`GET /api/v1/references/mcp-tools` is the live list. Their descriptions come to
about 5,000 words, and that number is watched: a description says what the tool
returns and the one or two things an agent gets wrong without being told, and
nothing else.

So the test for a new tool is **"is this a distinct question shape?"** — not "is
this a question someone might ask".

**Not a tool** — these are already answerable and adding a tool for them makes
the surface worse:

* Anything expressible as a `q=` predicate. "Findings on hosts tagged prod",
  "EOL hosts in this subnet", "hosts I have in review" are filters, not
  features. The query language is the general query tool;
  `assist_get_vocabulary` exists so the agent can use it without guessing
  values.
* Anything file-shaped. NDJSON dossiers, target lists, report files: the agent
  fetches those to disk with `curl` and reads them there. Materialising a
  40k-row stream into a tool result spends context on data the agent should be
  handling as a file.
* Anything with an obvious aggregate already served. Prefer one endpoint that
  returns the rollup over five tools the agent has to combine — the agent doing
  arithmetic across calls is where silent wrong answers come from.

---

## Stage 1 — Orient: "where is this project?"

The first question of any session, and the one an analyst asks a colleague
returning from leave.

| Question | Tool |
|---|---|
| Engagement dates, members, totals, scopes, the latest scans | `assist_get_context` |
| Which scopes / scans exist, in full | `assist_list_scopes`, `assist_list_scans` (an nmap scan's rows carry `scan_info`: the port list it was asked to probe) |
| Which hosts one scan saw, and whether it authenticated to them | `assist_list_scan_hosts` — the scan page's "As scanned" rows; `credentialed` is true / false when the scanner said so (Nessus), null when it did not |
| Which session am I, for whom (what `assigned:me` means) | `assist_session_info` |
| How much has actually been assessed | `assist_get_coverage` |
| Of the hosts assessed for vulnerabilities, how many the scanner logged in to | `assist_get_coverage` → the `vuln_assessment` domain's `credentialed` (`credentialed` / `not_credentialed` / `credentials_not_stated`, adding up to the assessed count); `assist_list_hosts q=vulnscan:credentialed\|uncredentialed\|unstated` lists each; one host: `assist_get_host` → `assessment.vuln_scan_credentialed` (`yes` / `no` / `not_stated`, null when not assessed) |
| What's the headline condition, and why | `assist_get_posture` |
| How many hosts match X | `assist_count_hosts` |
| Which scanners this installation has configured | `list_scanner_integrations` — name, type and address, never credentials (not an `assist_*` tool: the list is the installation's, not the project's). Using one means asking the operator first and then `request_scanner_credentials`; [MCP.md](MCP.md) §6 |

**`assist_get_posture`** wraps `posture_service` — the executive condition, the
signals behind it, and the finding disposition. It is the single call that
answers "where are we?" with the same numbers the Posture page shows a manager,
which matters: an agent and a page disagreeing about the headline is worse than
the agent not having one.

Trimmed against what the UI receives: the condition-family × site `heatmap` is
dropped (it is a picture, and describing it in JSON spends context on something
the agent cannot show anyone), as is the full `systemic` block, which
duplicates `assist_get_patterns`. The counts survive in `headline.systemic`.

Watch `label`: `insufficient_evidence` means the estate has not been assessed
enough to judge. The prompt and the tool description both say so, because
reporting it as "no issues found" is the most damaging wrong sentence an agent
could write about an engagement.

---

## Stage 2 — Focus: "where do I go next?"

| Question | Tool |
|---|---|
| Which segment is worst, and what is wrong with it | `assist_list_segments` |
| What has nobody picked up | `assist_list_findings` with `unowned=true` (findings nobody owns); `assist_count_hosts q=has:critical AND assigned:none` is a different question — hosts with a critical scanner observation and nobody assigned |
| The project's attention profile — exposure against neglect | `assist_get_posture` |
| What is untriaged or unreviewed | `assist_get_posture` |
| Which site is worst (multi-site engagements) | `assist_get_posture` → `sites` |
| What waits on my operator, by kind | `assist_get_workbench` → `my_work` |
| Which findings are waiting on my operator — all of them | `assist_list_my_findings` (`need=decide\|write`; `total`) |
| What should we look at next | `assist_list_worth_a_look` — Operations' "Untouched, with a reason" queue |
| Which ranges has nobody touched | `assist_get_terrain` |
| What is still unassessed, per domain and segment | `assist_list_evidence_gaps` |

**There is no separate attention tool.** `compute_posture` already folds the
project and site attention services in: exposure by severity, the unowned
backlog, review coverage and the per-site decomposition are all in the posture
payload, and posture's `priorities` are a richer version of attention's single
recommended action. A second endpoint would have been the same numbers under a
second name.

**`assist_list_segments` is the Subnet Insights page's own service**
(`compute_subnet_insights`), not a rollup of its own. That matters for three
reasons: it counts **active findings** (the judged spine), as posture and the
reports do, not raw scanner rows; it resolves each host to its most-specific
subnet, so a /24 scoped inside a /16 is not counted twice; and it sorts the
whole estate before taking a page, so "worst first" is not worst-of-a-slice.
Per subnet it carries `exposure` (active findings by severity, tier-weighted),
`neglect` (unowned active findings, unreviewed hosts), `hygiene` (end-of-life
OS, certificate problems, weak auth, risky services) and a recommended action.

**`assist_get_workbench` is the operator's own Operations page**, by the same
service:

* `my_work` — the operator's queue by kind: `findings_to_decide`,
  `findings_to_write` (they add up to `findings_needing_me`; a finding with a
  decision AND missing text is a `decide`), `tests_assigned`, `hosts_in_review`,
  `tests_on_hosts_in_review`. `total` is those four parts; `to_claim` is shared
  work and is not in it. An agent asked "what is waiting on me?" answers by
  kind.
* Preview lists — `my_queue` (10), `my_tasks` (10 per group, with
  `group_counts` counting each test once), `my_findings` and `followups` (15),
  `recent_notes` (8). The whole lists are their own reads:
  `assist_list_hosts q=follow:mine` (hosts in review), `q=follow:revisit`
  (changed since review), `host_tests_list mine=true active_only=true` (tests
  assigned), `host_tests_list q=follow:mine active_only=true` (tests on hosts in
  review), `assist_list_my_findings` (findings that need the operator) and
  `assist_list_worth_a_look` (the untouched queue).
* `my_findings` lists only the findings that NEED their owner, each with
  `needs` — a confirmed, written-up finding is not listed, so "findings I own"
  is `assist_list_findings owner=me`.
* `followups` — the OPERATOR'S OWN finished reviews whose host gained an open
  port or a critical / high observation afterwards, one row per host. The
  team-wide question is `q=has:changed_since_review`.
* `setup` — whether the project has anything in it yet (`has_hosts`,
  `has_scopes`, `scope_rows`, `only_scope_id`); `since_last_visit`; `blockers`.
* No project-wide measures and no team roster: "hosts tested", "untouched with
  a critical observation" and "what the team is already reviewing" are the
  `total` of `assist_list_hosts q=has:tested`, `q=has:untouched has:critical`
  and `q=follow:in_review` (any teammate's).

**`assist_list_my_findings`** is the Operations "Findings" tab as a whole list:
the page's own function (`operations_read_service.compute_my_findings`), keyed
to the session's operator, at the page's role (any member), in the page's
order. It answers the page's body (`items`, `total_open`, `need_counts`) plus
`total` — the size of the list THIS call pages, i.e. `need_counts[need]`, or
`total_open` with no `need` — and `limit`, `offset`, `has_more`.

---

## Stage 3 — Understand state: "what do we know about this?"

| Question | Tool |
|---|---|
| Which hosts match | `assist_list_hosts` (`q=`) |
| One host in detail | `assist_get_host` |
| Scanner observations on a host | `assist_get_host_vulnerabilities` — raw scanner rows, not findings; each row carries `finding_id`, `finding_status`, `finding_on_this_host` and `finding_endpoint_status` (the inspector's rule for "is this judged on this host"); `cve` / `plugin_id` / `search` narrow to one issue's rows |
| Findings across the project | `assist_list_findings` — `total` and `severity_counts` over the filter; each row's `hosts` is a sample of at most 10 addresses (`hosts_truncated`) |
| Scanner issues not yet judged, across the project | `assist_list_scanner_observations` → `assist_list_observation_hosts` |
| What the team said | `assist_get_host_notes`, `assist_list_recent_notes` |
| What the team tested, and what it showed | `host_tests_list` (`host_id=` — the tests proposed for the host, with status and evidence counts) and `list_evidence` (`host_id=` or `host_test_id=` — what was run and what came back) |
| Who was told about a finding on a host, and where the fix stands | the `remediation_*` tools, where the installation has turned remediation tracking on ([MCP.md](MCP.md) §2); 404 where it has not |
| What values this project uses | `assist_get_vocabulary` |
| Named assets (FQDNs), whether they are in scope, and what they resolve to | `assist_list_names` |
| Which uploads failed to parse, or landed incomplete | `assist_list_ingestion_issues` |
| What a host is actually serving on the web | `assist_get_host` → `web_interfaces` (the first 10) |
| Every web interface on a host | `assist_list_host_web_interfaces` |
| What NetExec / SMBMap recorded on a host, beside the tool's own line | `assist_list_host_access` |
| Which lines an import did not interpret (redacted shapes) — a parse audit's starting point | `assist_list_uninterpreted_lines` |
| What changed between two scans | `assist_compare_scans` |

**One host filter for the page and the agents.** `assist_list_hosts` and
`assist_count_hosts` call the Hosts page's own
`host_query.build_filtered_host_query`, so a filter word means to an agent what
it means on the page: `search` matches what the page's search box matches;
`services` is the service identified on an open port, on any port number;
`ports` with `services` must be met by ONE open port. A value that cannot be
understood — a port range, an unknown `state`, a `subnets` value that is not a
network, a list that names nothing — is a 422 naming it, never the whole
project. In `q=`, `NOT` counts a host with no value recorded (`NOT os:windows`
includes hosts with no OS).

**`assist_list_ingestion_issues`** matters more than it sounds: without it, "no
data for that range" is indistinguishable from "the upload didn't parse", and
the agent reports the first with no way to suspect the second. A job that
**completed** having silently dropped rows (`skipped_count`) is in the project
and reads as healthy everywhere else, so counts drawn from it are undercounts;
`kind=degraded` names it. A failed job and its parse error are one row — they
are one upload.

---

## Stage 4 — Patterns: "what does this project have a *problem* with?"

The stage that turns an inventory into an assessment. `systemic_insight_service`
computes it for the Posture hub, and one tool returns all of it.

| Question | Tool |
|---|---|
| Estate-wide weaknesses, worst-first ("everything is on an EOL OS") | `assist_get_patterns` → `blind_spots` |
| Subnets whose issue density is an outlier ("this subnet is worse than the rest") | `assist_get_patterns` → `segment_outliers` |
| How far each condition has spread (systemic vs isolated) | `assist_get_patterns` → `conditions` |
| Root cause + recommended control, per condition family | `assist_get_patterns` → `family_summary` |
| Per-subnet diagnostic profile | `assist_get_patterns` → `diagnostic_profiles` |
| Per-subnet hygiene detail — EOL OS, certificates, weak auth, risky services | `assist_list_segments` |

One tool rather than five: `compute_systemic_insights` returns all of it in one
pass, and splitting it would make the agent issue five calls to reassemble a
single analysis. `family_matrix` is omitted — like posture's `heatmap`, it is
the UI's grid.

`adopted=false` (no scoped subnets) means the analysis **could not run**. Both
the endpoint and the tool description spell out that this is "not assessable"
rather than "no patterns found"; the two are indistinguishable to an agent
otherwise, and the wrong one is reassuring.

### A distinction worth being honest about

What a request calls "trends" is **cross-sectional comparison** — *this* subnet
versus the others, *this* condition's spread across the estate — and that is
what these services compute, deliberately: an engagement runs weeks, so
"compared to last quarter" has no data behind it.

**Change over time has two narrow answers and no series.** "What changed between
scan A and scan B" is `assist_compare_scans`, and "new / changed / newly
critical since X" is a `q=` time window (`firstseen:` / `changedsince:` /
`vulnsince:`). Nothing computes a project-level series over time, for people or
agents. If that is wanted it is a feature with its own design, not a tool
wrapping an existing service.

---

## Stage 5 — Write it up: "produce the deliverable"

The template lives on the operator's machine and the agent fills it there
([MCP.md](MCP.md) §2). What it needs from BlueStick is the material.

| Need | Tool |
|---|---|
| Every host's full dossier, at scale | `report-context.ndjson` (curl to disk) |
| Findings with severity/status/owner | `assist_list_findings` |
| The numbers a summary quotes | `assist_count_hosts`, `assist_get_coverage` |
| The synopsis material — condition, patterns | `assist_get_posture`, `assist_get_patterns` |
| A finding's write-up: its notes, comment thread, endpoints, evidence and images | `assist_get_finding` |
| Look at one image | `assist_get_image` (inline); the bytes to save beside a report are a `download_path` (curl) |
| How this installation wants report text written | `assist_get_writing_guidance` (also `writing_guidance` on `assist_get_finding`) |
| Write or improve a finding's report text | `propose_finding_text` — a proposal per field, accepted or rejected by a person in the app |
| Record what was run and what it showed | `record_evidence` / `list_evidence` |
| What we told the client | `assist_list_client_reports` → `assist_get_client_report` |

**`assist_get_finding`** is the tool the report stage turns on. It carries the
finding's comment thread, the note that justified it and that note's replies
where there is one, the affected endpoints (the first 100, with the totals),
the scanner rows and evidence records behind it, its report text and status
history. Each note says whether a person or an agent wrote it. A scanner's
output and a command a tester ran are different assertions, and a write-up
says which one a claim rests on.

**Screenshots are references, and optionally payloads.** The tool returns
filename, media type, size and a `download_path`; the agent downloads what it
needs to its working directory and references the file from the report, which
needs a *file on disk* next to it regardless. `assist_get_image` returns one as
MCP image content: opt-in, one image per call, capped at 2 MB inline, looping
back through the same download routes so role, scope and audit are unchanged.
The token cost is real, which is why it is a separate call and never inlined
into `assist_get_finding` or `assist_get_host`.

**Writing guidance.** `assist_get_finding` returns `writing_guidance`: how this
installation wants report text written — `general`, and `sections` with
instructions per field. It is the record a global admin edits in System
settings and the in-app draft's prompt is built from
(`services/report_writing_guidance`), so an agent's proposal and an in-app draft
are written to the same instructions. `assist_get_writing_guidance` returns the
same block on its own, for a NEW finding proposed with report text.

**Images in report text.** `assist_get_finding` returns `images`: the finding's
images as the client report sees them — `id`, `caption`, `in_report`,
`printable` and `placed_in`, the report-text fields whose Markdown places the
image with `![caption](evidence:<id>)`. It wraps the service the finding page's
own list uses (`report_images.finding_images`), so the two cannot disagree. An
agent that rewrites a section with `propose_finding_text` keeps the references
that section holds (a dropped one moves the image back under Evidence) and may
reference only ids from that list: any other id is a 422 naming it, and a
reference to an image nobody ticked "In report" is refused at accept until a
person ticks it. There is no agent write for a caption or the tick: both are
decisions of the person who attached the image.

**The client report.** `assist_get_client_report` returns each finding as the
report states it. Its `confirmations` are the test results the report prints
as how the finding was confirmed (at most 10, with `confirmations_omitted`);
`by` is the operator's full name even when an agent recorded the result — the
report names no session. `confirmations` and `summary.evidence_records` are
only what the report PRINTS (`summary.evidence_records_not_printed` counts the
rest). Each finding's `images` is every image ticked for the report, with
`placed_in`, `printed` and `printed_in`; `summary.images_printed`,
`images_trailing` and `images_not_printed` add up to `summary.images`, and all
are `null` when printing could not be measured. In an addendum each finding
carries its `change` (`new`, `new_hosts`, `severity_changed` with
`previous_severity`).

Web-interface screenshots (EyeWitness) are a second, separate store:
`assist_get_host` returns a `screenshot_download_path` per captured interface.
The distinction is worth keeping in mind when writing up — note attachments are
evidence an analyst *chose* to record, while these are captured automatically at
ingest, so they exist for hosts nobody has written a note about yet.

---

## Review rule

Before adding a tool, check it isn't:

1. a `q=` filter (→ document the predicate instead),
2. file-shaped (→ a download the agent curls),
3. a rollup an existing service already computes (→ wrap that service, don't
   recompute it in the endpoint — an agent and a page disagreeing on a number is
   worse than the agent not having it).

And when it is added: declare the read's role floor on the route
(`dependencies=[Depends(agent_read_floor(ProjectRole.…))]` — the role its page
asks of a person; nothing for a read any member may make) and add it to
`tests/test_agent_role_route_matrix.py`; in `mcp_tools.py` author the tool's
description and, where the endpoint's own text is not enough, a per-argument
description — the arguments themselves come from the route. A filter the
endpoint cannot understand is a 422 naming the value, never an ignored filter.
A new payload field on an existing read is preferred to a new tool.
