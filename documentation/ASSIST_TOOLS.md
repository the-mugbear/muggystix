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
(`mcp_tools.py` → an `/agent/assist/*` route); the MCP layer makes no
authorization decision. See [MCP.md](MCP.md) §1 for the dispatch path.

---

## The governing constraint: context is not free

Every tool is text in the model's context on every session.

| | tools | payload |
|---|---|---|
| Full catalog (all workflows) | 44 | ~37 KB (~9.3k tokens) |
| An assist session sees | 23 | ~18 KB (~4.5k tokens) |

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
| How much has actually been assessed | `assist_get_coverage` | **have** |
| What's the headline condition, and why | `assist_get_posture` | **queue P1** |
| How many hosts match X | `assist_count_hosts` | **have** |

**`assist_get_posture`** wraps `posture_service` — the executive condition, the
signals behind it, and the remediation flow. It is the single call that answers
"where are we?" with the same numbers the Posture page shows a manager, which
matters: an agent and a page disagreeing about the headline is worse than the
agent not having one.

---

## Stage 2 — Focus: "where do I go next?"

| Question | Tool | Status |
|---|---|---|
| Which segment is worst | `assist_list_segments` | **have** |
| What has nobody picked up | `assist_list_findings?unowned=true`, `assist_count_hosts` with `assigned:none` | **have** |
| What is the project's attention profile — exposure vs neglect | `assist_get_attention` | **queue P1** |
| What's gone stale — untriaged backlog, unreviewed hosts | `assist_get_attention` (same call) | **queue P1** |
| Which site is worst (multi-site engagements) | `assist_get_attention?by=site` | **queue P2** |

**`assist_get_attention`** wraps `compute_project_attention` /
`compute_site_attention`, which already score exposure (severity-weighted active
findings) against neglect (staleness, untriaged backlog, unreviewed hosts).
"Where do I focus?" is exactly what that computation was built to answer, and an
agent recomputing it from raw counts would get a different number than the UI.

---

## Stage 3 — Understand state: "what do we know about this?"

Largely done. This is the stage the surface was originally built for.

| Question | Tool | Status |
|---|---|---|
| Which hosts match | `assist_list_hosts` (`q=` DSL) | **have** |
| One host in detail | `assist_get_host` | **have** |
| Findings on a host / across the project | `assist_get_host_findings`, `assist_list_findings` | **have** |
| What the team said | `assist_get_host_notes`, `assist_list_recent_notes` | **have** |
| What the team tested, and what it showed | `assist_get_host_testing` | **have** |
| What values this project uses | `assist_get_vocabulary` | **have** |
| Which uploads failed to parse | `assist_list_ingestion_issues` | **queue P2** |

**`assist_list_ingestion_issues`** matters more than it sounds: without it, "no
data for that range" is indistinguishable from "the upload didn't parse", and
the agent reports the first with no way to suspect the second.

---

## Stage 4 — Patterns: "what does this project have a *problem* with?"

The stage that turns an inventory into an assessment, and the one assist cannot
reach at all today. `systemic_insight_service` already computes it for the
Posture hub.

| Question | Tool | Status |
|---|---|---|
| Estate-wide weaknesses, worst-first ("everything is on an EOL OS") | `assist_get_patterns` → `blind_spots` | **queue P1** |
| Subnets whose issue density is an outlier ("this subnet is worse than the rest") | `assist_get_patterns` → `segment_outliers` | **queue P1** |
| How far each condition has spread (systemic vs isolated) | `assist_get_patterns` → `conditions` | **queue P1** |
| Per-subnet diagnostic profile | `assist_get_patterns` → `diagnostic_profiles` | **queue P1** |
| Per-subnet hygiene detail — EOL OS, weak TLS, SMB signing, weak auth | `assist_get_subnet_insights` | **queue P2** |

One tool (`assist_get_patterns`) rather than four: `compute_systemic_insights`
returns all of it in one pass, and splitting it would make the agent issue four
calls to reassemble a single analysis.

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
| The synopsis material — condition, patterns | `assist_get_posture`, `assist_get_patterns` | **queue P1** |
| **A promoted finding's write-up: its evidence note, comment thread, and attachments** | `assist_get_finding` | **queue P1** |
| **The screenshots themselves** | attachment metadata + download URL on `assist_get_finding`; the agent `curl`s each to disk | **queue P1** |

**`assist_get_finding`** is the tool the report stage turns on. A promoted
finding carries an `evidence_annotation_id` (the note that justified promotion),
a comment thread, and note attachments — which is where screenshots live. Today
an agent can list findings and read per-host notes, but cannot reach the
evidence attached to a specific finding, which is precisely the material a
write-up cites.

**Screenshots are references, not payloads.** The tool returns filename, media
type, size and a URL; the agent downloads what it needs to its working directory
and references the file from the report. Base64 in a tool result would spend
thousands of tokens on an image the model cannot usefully read anyway, and the
finished report needs a *file on disk* next to it regardless.

Web-interface screenshots (Eyewitness) are a second, separate store
(`web_interfaces.screenshot_path`) — same treatment, queued with P2.

---

## The queue

**P1 — the analyst's actual loop, and the report stage.** Six tools, all
wrapping services that already exist:

1. `assist_get_patterns` — systemic insights: blind spots, segment outliers, condition spread.
2. `assist_get_finding` — one finding with its evidence note, thread and attachment references.
3. `assist_get_posture` — headline condition + signals + remediation flow.
4. `assist_get_attention` — exposure vs neglect, project-level.

**P2 — completeness of the picture.**

5. `assist_get_subnet_insights` — per-subnet EOL / TLS / SMB-signing / weak-auth detail.
6. `assist_list_ingestion_issues` — parse failures, so "no data" can be told from "no successful upload".
7. `assist_get_attention?by=site` — site-level, for multi-site engagements.
8. Web-interface screenshot references.

**P3 — needs design, not a wrapper.**

9. Time-series: "what changed since the last scan / last week". Buildable from
   `HostScanHistory` + finding status history; nothing computes it today.

That lands the assist surface at roughly **31 tools / ~24 KB**, which is the
ceiling I would want to stop at without revisiting the specific-vs-general
trade-off in §"context is not free".

---

## Review rule

Before adding anything to this list, check it isn't:

1. a `q=` filter (→ document the predicate instead),
2. file-shaped (→ a download the agent curls),
3. a rollup an existing service already computes (→ wrap that service, don't
   recompute it in the endpoint — an agent and a page disagreeing on a number is
   worse than the agent not having it).
