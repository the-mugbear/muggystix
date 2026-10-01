# AI Assist — the tool surface, derived from the analyst's job

The assist surface exists so a security analyst can review and interact with a
project through an agent: understand where the project *is*, decide where to
focus next, see the patterns in it, and — when the work concludes — write the
engagement up against their own report template, citing the notes and
screenshots recorded on promoted findings.

This document derives the tool set from that job rather than adding tools as
questions come up. It records what exists, what's queued, what is deliberately
*not* a tool, and why.

Mechanically, a tool is a declarative mapping onto an HTTP endpoint
(`mcp_tools.py` → an `/api/v1/agent/*` route — reads under `/agent/assist/*`,
the three writes under `/agent/hosts/*`); the MCP layer makes no authorization
decision. See [MCP.md](MCP.md) §1 for the dispatch path.

**Scope of this document:** the assist READ surface. The three writes —
`assist_add_note`, `assist_set_follow`, `assist_patch_host` — exist and are
allowed exactly when the operator's project role permits writes; they were not
derived by this exercise and are not analysed here.

---

## The governing constraint: context is not free

Every tool is text in the model's context on every session.

| | tools | payload |
|---|---|---|
| The catalogue — what EVERY session sees | 67 | ~62 KB (~15k tokens) |
| of which `assist_*` | 36 (33 reads + 3 writes) | — |

*(Measured at v2.428.0 via `tool_list_payload()`, not estimated; tokens ≈ bytes/4.)*

**This constraint got tighter, not looser, in v2.337.0.** When this was first
written a session saw only its own workflow's tools (27 for assist, ~22 KB of a
48-tool catalogue). The per-workflow filter was removed with the per-workflow
keys: one session does every kind of work, so every session now pays for every
tool. A tool added for assist is context spent by a session that only scans too.

That is affordable now and it grows linearly with the tool count. So the test
for a new tool is **"is this a distinct question shape?"** — not "is this a
question someone might ask".

**Not a tool** — these are already answerable and adding a tool for them makes
the surface worse:

* Anything expressible as a `q=` predicate. "Findings on hosts tagged prod",
  "EOL hosts in this subnet", "hosts I have in review" are filters, not
  features. The DSL is the general query tool; `assist_get_vocabulary` exists so
  the agent can use it without guessing values.
* Anything file-shaped. NDJSON dossiers, target lists, screenshots: the agent
  fetches those to disk with `curl` and reads them there. Materialising a 40k-row
  stream or a PNG into a tool result spends context on data the agent should be
  handling as a file.
* Anything with an obvious aggregate already served. Prefer one endpoint that
  returns the rollup over five tools the agent has to combine — the agent doing
  arithmetic across calls is where silent wrong answers come from.

---

## Stage 1 — Orient: "where is this project?"

The first question of any session, and the one an analyst asks a colleague
returning from leave.

| Question | Tool | Status |
|---|---|---|
| Totals, scopes, recent scans | `assist_get_context` | **have** |
| Which scopes / scans exist, in full | `assist_list_scopes`, `assist_list_scans` (an nmap scan's rows carry `scan_info`: the port list it was asked to probe) | **have** |
| Which hosts one scan saw, and whether it authenticated to them | `assist_list_scan_hosts` — the scan page's "As scanned" rows from the same `scan_snapshot_service`; `credentialed` is true / false when the scanner said so (Nessus), null when it did not | **have** (2026-10-01) |
| Which session am I, for whom (what `assigned:me` means) | `assist_session_info` | **have** |
| How much has actually been assessed | `assist_get_coverage` | **have** |
| Of the hosts assessed for vulnerabilities, how many the scanner logged in to | `assist_get_coverage` → the `vuln_assessment` domain's `credentialed` (`credentialed` / `not_credentialed` / `credentials_not_stated`, adding up to the assessed count; the same `evidence_service` rule as the Evidence page); `assist_list_hosts q=vulnscan:credentialed\|uncredentialed\|unstated` lists each; one host: `assist_get_host` → `assessment.vuln_scan_credentialed` (`yes` / `no` / `not_stated`, null when not assessed) | **have** (2026-10-01) |
| What's the headline condition, and why | `assist_get_posture` | **have** (2.294.0) |
| How many hosts match X | `assist_count_hosts` | **have** |

**`assist_get_posture`** wraps `posture_service` — the executive condition, the
signals behind it, and the finding disposition (no remediation flow since
v2.372.0 — the engagement ends at the report). It is the single call that answers
"where are we?" with the same numbers the Posture page shows a manager, which
matters: an agent and a page disagreeing about the headline is worse than the
agent not having one.

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

| Question | Tool | Status |
|---|---|---|
| Which segment is worst, and what is wrong with it | `assist_list_segments` | **have** (rebuilt 2.297.0) |
| What has nobody picked up | `assist_list_findings?unowned=true`, `assist_count_hosts` with `assigned:none` | **have** |
| What is the project's attention profile — exposure vs neglect | `assist_get_posture` | **have** (2.294.0) |
| What's gone stale — untriaged backlog, unreviewed hosts | `assist_get_posture` | **have** (2.294.0) |
| Which site is worst (multi-site engagements) | `assist_get_posture` → `sites` | **have** (2.294.0) |

### `assist_get_attention` was planned, then dropped — deliberately

The first cut of this document queued a separate attention tool wrapping
`compute_project_attention` / `compute_site_attention`. Building it showed that
`compute_posture` **already folds both of them in**: exposure by severity,
unowned backlog, review coverage, scan staleness and the per-site decomposition
are all in the posture payload, and posture's `priorities` are a strictly richer
version of attention's single `recommended_action`.

A second endpoint would have been the same numbers under a second name — the
exact "five tools the agent has to combine" failure this document's review rule
exists to prevent. One tool, and the review rule caught its own violation.

### `assist_get_subnet_insights` was queued too — and became a rewrite instead

P2 item 5 was written as a new per-subnet tool. Rule 3 killed it the same way:
`assist_list_segments` already answered "which segment is worst", so shipping
both would have left two tools ranking subnets with **different numbers** —
and the older one was the wrong one:

* It counted raw `Vulnerability` rows. Posture, the Subnet Insights page and
  the reports all count **active Findings**, the triaged spine. The agent was
  quoting a figure no page would ever show.
* It read `HostSubnetMapping` directly, and `find_matching_subnets` returns
  *every* containing subnet. Scope a /16 and a /24 inside it — an ordinary way
  to scope an engagement — and every host in the /24 was counted twice.
  `compute_subnet_insights` resolves each host to its most-specific subnet.
* It fired three queries per subnet, then sorted only the arbitrary first
  `limit` subnets it happened to load — so "worst-first" was worst-of-a-slice.

So the endpoint's body was replaced with `compute_subnet_insights` and the
hygiene, neglect and exposure blocks came along with it. Tool count unchanged;
one wrong answer retired. The rule was written to prevent surface sprawl, and
it keeps finding correctness bugs instead — a hand-rolled duplicate of a
service is where a page and an agent quietly start disagreeing.

---

## Stage 3 — Understand state: "what do we know about this?"

Largely done. This is the stage the surface was originally built for.

| Question | Tool | Status |
|---|---|---|
| Which hosts match | `assist_list_hosts` (`q=` DSL) | **have** |
| One host in detail | `assist_get_host` | **have** |
| Scanner observations on a host / findings across the project | `assist_get_host_vulnerabilities` (raw scanner rows, not triaged findings — named `assist_get_host_findings` until the vocabulary was fixed), `assist_list_findings` | **have** |
| What the team said | `assist_get_host_notes`, `assist_list_recent_notes` | **have** |
| What the team tested, and what it showed | `host_tests_list` (`host_id=` — the tests proposed for the host, with status and evidence counts) and `list_evidence` (`host_id=` or `host_test_id=` — what was run and what came back); `assist_get_host` → `assessment.tests_executed` / `last_tested_at`. `assist_get_host_testing` went with test plans in v2.442.0 | **have** |
| What values this project uses | `assist_get_vocabulary` | **have** |
| Named assets (FQDNs), whether they are in scope, and what they resolve to | `assist_list_names` | **have** |
| Which uploads failed to parse | `assist_list_ingestion_issues` | **have** (2.297.0) |
| What a host is actually serving on the web | `assist_get_host` → `web_interfaces` | **have** (2.297.0) |
| Every web interface on a host, past the cap on host detail (`web_interfaces_truncated`) | `assist_list_host_web_interfaces` | **have** (2.343.3) |
| What NetExec / SMBMap recorded on a host, beside the tool's own line | `assist_list_host_access` | **have** (2.418.0) |
| Which lines an import did not interpret (redacted shapes) — a parse audit's starting point | `assist_list_uninterpreted_lines` | **have** (2.418.0) |

**`assist_list_ingestion_issues`** matters more than it sounds: without it, "no
data for that range" is indistinguishable from "the upload didn't parse", and
the agent reports the first with no way to suspect the second.

Building it turned up a third case worth its own kind. A job that **completed**
having silently dropped rows (`skipped_count`) is in the project and reads as
healthy everywhere else, so counts drawn from it are undercounts with nothing
anywhere to say so. `kind=degraded` names it. A failed job and its `ParseError`
are folded into one row — they are one upload, and listing both would report
two broken files where there is one.

---

## Stage 4 — Patterns: "what does this project have a *problem* with?"

The stage that turns an inventory into an assessment, and the one assist cannot
reach at all today. `systemic_insight_service` already computes it for the
Posture hub.

| Question | Tool | Status |
|---|---|---|
| Estate-wide weaknesses, worst-first ("everything is on an EOL OS") | `assist_get_patterns` → `blind_spots` | **have** (2.294.0) |
| Subnets whose issue density is an outlier ("this subnet is worse than the rest") | `assist_get_patterns` → `segment_outliers` | **have** (2.294.0) |
| How far each condition has spread (systemic vs isolated) | `assist_get_patterns` → `conditions` | **have** (2.294.0) |
| Root cause + recommended control, per condition family | `assist_get_patterns` → `family_summary` | **have** (2.294.0) |
| Per-subnet diagnostic profile | `assist_get_patterns` → `diagnostic_profiles` | **have** (2.294.0) |
| Per-subnet hygiene detail — EOL OS, weak TLS, SMB signing, weak auth | `assist_list_segments` | **have** (2.297.0) |

One tool (`assist_get_patterns`) rather than five: `compute_systemic_insights`
returns all of it in one pass, and splitting it would make the agent issue five
calls to reassemble a single analysis. `family_matrix` is omitted — like
posture's `heatmap`, it is the UI's grid.

`adopted=false` (no scoped subnets) means the analysis **could not run**. Both
the endpoint and the tool description spell out that this is "not assessable"
rather than "no patterns found"; the two are indistinguishable to an agent
otherwise, and the wrong one is reassuring.

### A distinction worth being honest about

What the request calls "trends" is **cross-sectional comparison** — *this*
subnet versus the others, *this* condition's spread across the estate — and that
is what these services compute, deliberately (see the systemic-insights design:
engagements run 6–8 weeks, so "compared to last quarter" has no data behind it).

**Trends over time barely exist.** Scans carry timestamps, `HostScanHistory`
records what each scan saw, and findings have status history — so
"what changed between scan A and scan B" is *buildable*, but nothing computes it
today, for humans or agents. If time-series is genuinely wanted, it is a feature
with its own design, not a tool wrapping an existing service. **Queued P3, and
flagged as build-not-wrap.**

---

## Stage 5 — Write it up: "produce the deliverable"

The template lives on the operator's machine and the agent fills it there
(§ [MCP.md](MCP.md)). What it needs from BlueStick is the material.

| Need | Tool | Status |
|---|---|---|
| Every host's full dossier, at scale | `report-context.ndjson` (curl to disk) | **have** |
| Findings with severity/status/owner | `assist_list_findings` | **have** |
| The numbers a summary quotes | `assist_count_hosts`, `assist_get_coverage` | **have** |
| The synopsis material — condition, patterns | `assist_get_posture`, `assist_get_patterns` | **have** (2.294.0) |
| **A promoted finding's write-up: its evidence note, comment thread, and attachments** | `assist_get_finding` | **have** (2.294.0) |
| **The screenshots themselves** | `GET /agent/assist/attachments/{id}` — curl, not a tool | **have** (2.294.0) |
| **Write or improve a finding's report text** | `propose_finding_text` — a proposal per field, accepted or rejected by a person in the app | **have** (2.436.0) |
| **Record what was run and what it showed** | `record_evidence` / `list_evidence` | **have** (2.436.0) |

**`assist_get_finding`** is the tool the report stage turns on. A promoted
finding made from a note before v2.446.0 carries an `evidence_annotation_id` (the note that justified it; notes can no longer be promoted),
a comment thread, and note attachments — which is where screenshots live. Today
an agent can list findings and read per-host notes, but cannot reach the
evidence attached to a specific finding, which is precisely the material a
write-up cites.

**Screenshots are references, and — since v2.428.0 — optionally payloads.** The
tool returns filename, media type, size and a `download_path`; the agent
downloads what it needs to its working directory and references the file from
the report, which needs a *file on disk* next to it regardless. Until v2.428.0
images were refused as tool results on the grounds that base64 spends thousands
of tokens "on an image the model cannot usefully read" — that reason no longer
holds (current models read images), so `assist_get_image` returns one as MCP
image content: opt-in, one image per call, capped at 2 MB inline, looping back
through the same download routes so role, scope and audit are unchanged. The
token cost is real, which is why it is a separate call and never inlined into
`assist_get_finding` or `assist_get_host`.

**Images in report text.** `assist_get_finding` also returns `images`: the
finding's images as the client report sees them — `id`, `caption` (what the
report prints under it), `in_report`, `printable` and `placed_in`, the
report-text fields whose Markdown places the image with
`![caption](evidence:<id>)`. It wraps the service the finding page's own list
uses (`report_images.finding_images`, `GET /findings/{id}/images`), so the two
cannot disagree. An agent that rewrites a section with `propose_finding_text`
keeps the references that section holds (a dropped one moves the image back
under Evidence) and may reference only ids from that list: any other id is a
422 naming it, and a reference to an image nobody ticked "In report" is refused
at accept until a person ticks it. The agent's report read
(`assist_get_client_report`) lists each finding's `images` (with `placed_in`)
beside `evidence`, which is now only the images no section places. There is no
agent write for a caption or the tick: both are decisions of the person who
attached the image.

`GET /agent/assist/attachments/{id}` is that download, and it exists because the
operator-facing equivalent under `/projects/...` requires a JWT — an agent has a
key, not a session. It is project-scoped and path-checked against the
attachments root. It is not itself an MCP tool (it returns bytes to save);
`assist_get_image` is the MCP way to look at one.

Web-interface screenshots (EyeWitness) are a second, separate store
(`web_interfaces.screenshot_path`), and got the same treatment in 2.297.0:
`assist_get_host` returns a `screenshot_download_path` per captured interface,
served by `GET /assist/web-interfaces/{id}/screenshot`. The distinction is
worth keeping in mind when writing up — note attachments are evidence an
analyst *chose* to record, while these are captured automatically at ingest, so
they exist for hosts nobody has written a note about yet.

---

## The queue

**P1 — the analyst's actual loop, and the report stage. ✅ Shipped in 2.294.0**
(backend 2.294.0, prompt 1.55.0), as three tools rather than four:

1. ✅ `assist_get_patterns` — systemic insights: blind spots, segment outliers, condition spread, family root causes.
2. ✅ `assist_get_finding` — one finding with its evidence note, thread and attachment references, plus `GET /assist/attachments/{id}` to fetch the images.
3. ✅ `assist_get_posture` — headline condition + signals + disposition + per-site decomposition.
4. ❌ `assist_get_attention` — **dropped**, subsumed by posture (see Stage 2).

**P2 — completeness of the picture. ✅ Shipped in 2.297.0** (backend 2.297.0,
prompt 1.56.0), as **one** new tool rather than three:

5. ✅ Per-subnet EOL / TLS / weak-auth / exposure / neglect detail — **not** a new
   tool. `assist_list_segments` stopped hand-rolling its rollup and now wraps
   `compute_subnet_insights` (see Stage 2). Tool count unchanged.
6. ✅ `assist_list_ingestion_issues` — failed, in-flight and *degraded* uploads,
   so "no data" can be told from "no successful upload".
7. ✅ Web-interface screenshots — **not** a tool either. `assist_get_host` now
   returns each interface (url, title, server banner, technologies) with a
   `screenshot_download_path`; `GET /assist/web-interfaces/{id}/screenshot`
   serves the PNG to a key-authenticated caller. Same contract as note
   attachments: a path to save; `assist_get_image` (v2.428.0) shows one inline.

**P3 — needs design, not a wrapper.**

8. Time-series: "what changed since the last scan / last week". Buildable from
   `HostScanHistory` + finding status history. **Partly answered without a tool
   (v2.363.0):** the host query DSL gained time windows — `firstseen:"A..B"`,
   `changedsince:`, `vulnsince:"critical@A..B"` — so "which hosts are new /
   changed / newly critical since X" is a `q=` filter on `assist_list_hosts`
   (review-rule case 1). What is still uncomputed is a project-level delta
   summary.

**P4 — parity with the pages. ✅ Shipped in 2.428.0** (prompt 2.11.0). A review
asked whether an agent could answer every question a person answers from the
app; it could not. Ten tools, each wrapping the service its page uses (rule 3):
`assist_get_workbench`, `assist_list_worth_a_look`, `assist_get_terrain`
(Operations), `assist_list_evidence_gaps` (Evidence), `assist_compare_scans`
(item 8's scan-to-scan delta), `assist_list_scanner_observations` +
`assist_list_observation_hosts` (the Findings page's issue view),
`assist_list_client_reports` + `assist_get_client_report` (Reports), and
`assist_get_image`. The rest was payload, not tools: context gained the
engagement dates and members; host detail the inspector's fields (names — which
had always been empty — tags, assignees, scope membership, assessment, weakness
labels, certificates, NSE output, conflicts); notes their threads, labels,
attachments and targets; a finding its report text and status history. Read
roles now equal the page's.

**Payload follow-ups from acceptance feedback #23/#24 (v2.433.0, prompt 3.0.0)**
— fields, not tools: a finding comment in `assist_get_finding` carries
`parent_id` / `thread_root_id`, as host notes already did, so its thread can be
rebuilt; an attachment carries
`include_in_report` (the operator's opt-in for report images) and its `caption`; a web interface
(`assist_get_host`, `assist_list_host_web_interfaces`) carries `source` (the tool
that observed it), `scan_id`, `observed_at` with `observed_at_basis` (`scan` =
the scan's own time, `import` = the import time), `imported_at` and `is_latest`
(false = an earlier scan's row of the same URL by the same tool — history, not
current state).

**Payload follow-ups from the 2026-10-01 review (prompt 4.3.0)** — fields, not
tools, each read from the page's own data (rule 3):
`assist_get_client_report` findings carry `confirmations` (the test results the
report prints as how the finding was confirmed — tool, host, command, summary,
output excerpt, date, who; at most 10) with `confirmations_omitted`, and in an
addendum `previous_severity` / `previous_severity_label` beside the new
`change` kind `severity_changed`; `delta` carries
`findings_with_changed_severity`, and `summary` carries `evidence_records` /
`agent_evidence_records`. `assist_get_finding` lists at most 100
`scanner_evidence` rows and says so with `scanner_evidence_total` /
`scanner_evidence_truncated`.

The read surface is **32 `assist_*` reads** (35 `assist_*` tools with the
three writes) inside a 56-tool catalogue (v2.442.0 removed the plan and
execution tools and `assist_get_host_testing`, and added four `host_tests_*`
tools). Two of
the three P2 items turned out not to be tools at all: one folded into an
existing endpoint, one is a payload field plus a download. With the
per-workflow filter gone there is no longer an "assist budget" to stay under —
the ceiling is the whole catalogue, shared with scope reads, host tests, evidence and proposals.
**P3 must not be another tool by reflex** — check first whether "what changed"
belongs on `assist_get_posture` as a delta block.

---

## Review rule

Before adding anything to this list, check it isn't:

1. a `q=` filter (→ document the predicate instead),
2. file-shaped (→ a download the agent curls),
3. a rollup an existing service already computes (→ wrap that service, don't
   recompute it in the endpoint — an agent and a page disagreeing on a number is
   worse than the agent not having it).
