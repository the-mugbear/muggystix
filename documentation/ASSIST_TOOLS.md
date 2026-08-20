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
| Full catalog (all workflows) | 48 | ~40 KB (~10k tokens) |
| An assist session sees | 27 | ~22 KB (~5.4k tokens) |

*(Measured after P2 via `tool_list_payload(workflow="assist")`, not estimated.)*

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
| What's the headline condition, and why | `assist_get_posture` | **have** (2.294.0) |
| How many hosts match X | `assist_count_hosts` | **have** |

**`assist_get_posture`** wraps `posture_service` — the executive condition, the
signals behind it, and the remediation flow. It is the single call that answers
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
| Findings on a host / across the project | `assist_get_host_findings`, `assist_list_findings` | **have** |
| What the team said | `assist_get_host_notes`, `assist_list_recent_notes` | **have** |
| What the team tested, and what it showed | `assist_get_host_testing` | **have** |
| What values this project uses | `assist_get_vocabulary` | **have** |
| Which uploads failed to parse | `assist_list_ingestion_issues` | **have** (2.297.0) |
| What a host is actually serving on the web | `assist_get_host` → `web_interfaces` | **have** (2.297.0) |

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

**`assist_get_finding`** is the tool the report stage turns on. A promoted
finding carries an `evidence_annotation_id` (the note that justified promotion),
a comment thread, and note attachments — which is where screenshots live. Today
an agent can list findings and read per-host notes, but cannot reach the
evidence attached to a specific finding, which is precisely the material a
write-up cites.

**Screenshots are references, not payloads.** The tool returns filename, media
type, size and a `download_path`; the agent downloads what it needs to its
working directory and references the file from the report. Base64 in a tool
result would spend thousands of tokens on an image the model cannot usefully
read anyway, and the finished report needs a *file on disk* next to it
regardless.

`GET /agent/assist/attachments/{id}` is that download, and it exists because the
operator-facing equivalent under `/projects/...` requires a JWT — an agent has a
key, not a session. It is project-scoped and path-checked against the
attachments root, and deliberately **not** an MCP tool: it returns an image, and
the agent's job with it is to save it, not to read it into context.

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
3. ✅ `assist_get_posture` — headline condition + signals + remediation flow + per-site decomposition.
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
   attachments: a path to curl, never bytes in a tool result.

**P3 — needs design, not a wrapper.**

8. Time-series: "what changed since the last scan / last week". Buildable from
   `HostScanHistory` + finding status history; nothing computes it today.

The surface sits at **27 tools / ~22 KB** — under the ~29 this section
predicted, because two of the three P2 items turned out not to be tools at all:
one folded into an existing endpoint, one is a payload field plus a download.
That is the ceiling I would want to stop at without revisiting the
specific-vs-general trade-off in §"context is not free". **P3 must not be a
28th tool by reflex** — check first whether "what changed" belongs on
`assist_get_posture` as a delta block.

---

## Review rule

Before adding anything to this list, check it isn't:

1. a `q=` filter (→ document the predicate instead),
2. file-shaped (→ a download the agent curls),
3. a rollup an existing service already computes (→ wrap that service, don't
   recompute it in the endpoint — an agent and a page disagreeing on a number is
   worse than the agent not having it).
