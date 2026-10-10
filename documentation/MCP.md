# MCP — the Model Context Protocol surface

BlueStick serves its agent endpoints over MCP at **`POST /api/v1/mcp`**, so an
MCP-capable client calls them as native tools instead of shelling `curl`. This
document covers what is exposed, how a client connects, and the decisions behind
both — the parts that are easy to get wrong and expensive to rediscover.

The agent-facing *contract* (how to behave in a session) is
[the agent guide](AGENT_GUIDE.md); this is the operator-facing description of the
transport. The in-app equivalent is **`/reference/mcp`**, which reads the live
server registry, so it can't drift from what a deployment actually serves.

---

## 1. What it is

A hand-rolled, in-process implementation of the **tools-only subset of the
Streamable HTTP transport**: JSON-RPC 2.0 over a single POST endpoint, plain
`application/json` responses, no SSE. `initialize`, `tools/list`, `tools/call`
and `ping`. Protocol revisions `2025-06-18` (preferred) and `2025-03-26`.

**Why not the `mcp` SDK.** The need is a small, frozen wire format, and the SDK
would add a dependency with its own transitive pins plus a second ASGI app to
mount, for no capability this doesn't have. Revisit if SSE streaming, sampling,
or the resources/prompts surfaces are ever needed.

**Every tool call loops back into the app's own `/api/v1/*` endpoint
in-process** (ASGI transport, no socket), forwarding the caller's `X-API-Key`.
All but two tools map to a route under `/agent/*`; `read_agent_guide` maps to
`/agents-guide` and `list_tools` to `/references/tools`. So authentication, the
operator-role gate, the agent-API audit log and the streaming caps all run
**unchanged**. The MCP layer makes no security decision of its own — that
invariant is stated in `mcp_tools.py` and pinned by
`test_hiding_a_tool_is_presentation_not_authorisation`.

---

## 2. One endpoint, one session, every kind of work

`tools/list` returns the **whole** tool catalogue to every session: a key binds
to one project-scoped `AgentSession` that does every kind of work with its
operator's project role. The kinds of work are not a sequence — the operator
drives the agent, in whatever order the work needs, and nothing has to be
opened first.

How many tools there are changes with most releases, so no number is kept here:
`GET /api/v1/references/mcp-tools`, which the **Reference → MCP** page shows, is
read off the server's registry and is the list.

| Work | Tools |
|---|---|
| The session itself | `agent_identity` (what am I, what may I write, when does my key expire), `assist_session_info` (the session's purpose and operator), `session_renew` (same key, later deadline), `end_session` (only when the operator says they are finished — it revokes the key), `read_agent_guide`, `submit_feedback` |
| The tool catalogue | `list_tools` (a reference for people — never a permission list), `suggest_tool` (propose a tool the catalogue lacks, for a curator; it grants nothing) |
| Query and report | the `assist_*` reads — the next section lists each against the question it answers |
| Scope reads and uploads | `assist_list_scopes`, then `scope_list_subnets` / `scope_list_domains` for one scope's CIDRs and names; the target files and the upload itself are curl (see "Bulk data" below); `get_upload_job` polls an upload |
| Host tests | `host_tests_list` (read first — do not duplicate), `host_tests_propose` (up to 200 individual tests, each with its own `request_key`; they appear on each host's page at once), `host_tests_get`, `host_tests_update` (409 when `expected_revision` is stale) |
| Running a test | `host_tests_update` → `in_progress`, run it, `record_evidence` naming the `host_test_id` (with a `request_key`), then `host_tests_update` → `done`. Evidence with outcome `finding`, `no_finding` or `inconclusive` is what marks the host tested |
| Evidence and proposals | `record_evidence`, `list_evidence`; `propose_finding_text`, `propose_finding`, `propose_observation`, `propose_endpoint_status`, `list_proposals` |
| Direct writes on a host | `assist_add_note` (discussion), `assist_set_follow` (the operator's review status), `assist_patch_host` (hostname / OS corrections) |
| Remediation tracking | `remediation_list`, `remediation_contacts`, `remediation_teams`, `remediation_trend`, `remediation_follow_up`, `remediation_timeline` (reads — the operator needs `auditor`); `remediation_apply`, `remediation_assign_from_report`, `remediation_add_note`, `remediation_record_follow_up` (writes — the operator must be a project admin) |

There is no setup call. The session records its **client** from the `initialize`
handshake — `clientInfo` name and version, when the handshake carries the key
(curl agents: the first call's `User-Agent`) — and its prompt version at start
or resume. The **model** is the agent's own report: an optional `agent_model`
argument on `host_tests_propose`, `record_evidence`, the four `propose_*` tools,
`remediation_apply`, `remediation_assign_from_report` and `end_session`. The
session keeps the last one, and a host test, an evidence record or a proposal
snapshots the attribution when it is written (`API_GUIDE.md` §6.8).

Each tool also carries a `workflows` tag that the reference page groups by:
`assist` (the `assist_*` and `remediation_*` tools), `testing` (`host_tests_*`)
or `scope` (`scope_*`). Every other tool — the session and catalogue tools,
`get_upload_job`, evidence and proposals — carries all three and is shown as
shared. The tag is presentation, never a filter.

### Remediation tracking

Remediation tracking is an installation switch a global admin turns on in System
settings. **Where it is off, every `remediation_*` tool answers 404 "not enabled
on this installation"** — the agent says so and does not retry — and nothing
about remediation appears in any other answer.

Where it is on, `remediation_list` returns one row per finding on a host: the
contact, the team, the assigned date, the contact's progress (`status` open /
closed / deferred) and where the row stands against its deadline (`state`,
`due_on`, `days_left`). Two things are easy to get wrong:

* **`closed` is the contact's claim, not the assessor's conclusion.** It is said
  "reported fixed". The assessor's conclusion is the endpoint's own status on
  the same row (`remediated`), and neither is ever written from the other. Where
  the two disagree the row's `verification` says so
  (`reported_fixed_not_retested` / `remediated_record_open`).
* **The deadline is the server's**: the assigned date plus the installation's
  days for the finding's severity, unless a project admin set one by hand. The
  agent never computes or sends it.

Every count the list returns (`state_counts`, `status_counts`,
`verification_counts`, `flag_counts`, `severity_counts`, `overdue_ages`,
`not_followed_up`) is over the selection and equals the size of the list its
filter opens. `remediation_follow_up` returns one contact's overdue and due-soon
rows and a plain-text message for the operator to send — the server sends
nothing — and `remediation_record_follow_up` is called only after the operator
says it was sent. `remediation_apply` and `remediation_assign_from_report` are
always called with `dry_run: true` first; the agent reads the operator's file
locally and nothing is uploaded. The cross-project "Remediation deadlines" page
has no tool: a key is bound to one project.

### Assist: answering questions, and filling in a report

The assist surface is meant to answer whatever an analyst asks about a project.

| Question | Tool |
|---|---|
| "What is in this project?" | `assist_get_context` — engagement dates, members and roles, totals, scopes, the latest scans |
| "How many hosts …?" | `assist_count_hosts` — a total, not a page |
| "Which hosts match …?" / "What is on this host?" | `assist_list_hosts` (the Hosts page's `q=` language) · `assist_get_host` (by id or address) |
| "What are our critical findings?" | `assist_list_findings` — project-wide, with `severity_counts` |
| "What has nobody picked up?" | `assist_list_findings` with `unowned=true` (findings nobody owns) · `assist_count_hosts` with `q=has:critical AND assigned:none` (hosts with a critical scanner observation and nobody assigned — a different question) |
| "What do we already know about this host?" | `assist_get_host_notes` (paged — read `has_more`) |
| "Which scanner rows are on this host?" | `assist_get_host_vulnerabilities` — raw scanner observations, not findings; each row says whether a finding covers it on this host |
| "Every web interface on this host, not just the ten on host detail?" | `assist_list_host_web_interfaces` |
| "What did NetExec find on this host, and was it read right?" | `assist_list_host_access` — each result's interpreted fields beside the tool's line |
| "Which lines did BlueStick not read?" | `assist_list_uninterpreted_lines` — per import, as redacted shapes; see `documentation/PARSE_AUDIT_BRIEF.md` |
| "Did every upload actually land?" | `assist_list_ingestion_issues` — failed, in-flight and partial imports; check it before reporting that something is absent |
| "Which names do we know, and are they in scope?" | `assist_list_names` |
| "Which tags/sites/people exist here?" | `assist_get_vocabulary` |
| "How does this installation want report text written?" | `assist_get_writing_guidance` — before `propose_finding` with `report_text`; `assist_get_finding` carries the same block for a finding that exists |
| "How much of this did we actually assess?" | `assist_get_coverage` |
| "Has anyone tested this host, and what happened?" | `host_tests_list {host_id}` (the tests and their status), `list_evidence {host_id}` (what was run and what came back) |
| "Which segment is worst?" | `assist_list_segments` — ranked worst-first |
| "What has the team been discussing?" | `assist_list_recent_notes` (discussion; outstanding work is `host_tests_list` with `active_only`) |
| "Where is this project overall?" | `assist_get_posture` — the headline condition, and why |
| "What does this estate have a *problem* with?" | `assist_get_patterns` — blind spots, segment outliers, root causes |
| "What's the evidence behind this finding?" | `assist_get_finding` — the notes and thread, the affected endpoints, scanner rows and evidence records, the report text and status history, `writing_guidance`, and the finding's `images`; `assist_get_image` to look at one |
| "What is mine? What changed since I was last here?" | `assist_get_workbench` — the operator's Operations page (see below) |
| "Which findings are waiting on me — all of them?" | `assist_list_my_findings` — whole and paged, `need=decide\|write`; `total` is the size of the list the call pages |
| "What should we look at next?" | `assist_list_worth_a_look` — Operations' "Untouched, with a reason" queue |
| "Which hosts do I have in review? What is the team already reviewing? Which reviewed hosts changed since?" | `assist_list_hosts q=follow:mine` (the operator's own) · `q=follow:in_review` (ANY teammate's) · `q=follow:revisit` (the operator's own finished reviews that are not done) · `q=has:changed_since_review OR conclusion:needs_evidence` (the same for any teammate's reviews) |
| "Where does the engagement stand?" | `assist_list_hosts q=has:tested` and `q=has:untouched has:critical` → `total` · `assist_get_terrain` by address block · `assist_get_posture` / `assist_get_coverage` for the assessment |
| "Which ranges has nobody touched?" | `assist_get_terrain` |
| "What is still unassessed in segment Y?" | `assist_list_evidence_gaps` |
| "What changed between these two scans?" | `assist_compare_scans` |
| "Which hosts did this scan see, and did it log in to them?" | `assist_list_scan_hosts` (`credentialed`: true / false / null = the scan did not say) |
| "Which ports did that nmap scan actually probe?" | `assist_list_scans` (`scan_info`) |
| "Which issues are widespread but not yet findings?" | `assist_list_scanner_observations` → `assist_list_observation_hosts` |
| "What did we report to the client?" | `assist_list_client_reports` → `assist_get_client_report` (the operator needs `auditor`) — an issued report is its frozen text, a draft what it would say now |

**The workbench.** `assist_get_workbench` is the operator's own Operations page:

* `my_work` — what waits on them **by kind**: `findings_to_decide`,
  `findings_to_write` (the two add up to `findings_needing_me`),
  `tests_assigned`, `hosts_in_review`, `tests_on_hosts_in_review`. `total` is
  those four parts added up; `to_claim` (unassigned critical / high tests) is
  shared work and is not in it.
* Its lists are **previews**: `my_queue` 10, `my_tasks` 10 per group,
  `my_findings` and `followups` 15, `recent_notes` 8. "How many" is answered
  from the counts, never from a list's length; the tool description names the
  read that returns each whole list.
* `setup` — whether the project has anything in it yet: `has_hosts`,
  `has_scopes` (a scope has at least one subnet entry), `scope_rows`,
  `only_scope_id`.
* `since_last_visit`, `blockers` (imports that need someone) and `followups`
  (the operator's own finished reviews that changed or still need evidence).
* There are no project-wide measures and no team roster in it: those are the
  `total` of a Hosts query, as the table above says.

Several of these tools exist because their absence produced *confident wrong
answers* rather than errors: rebuilding the findings spine from per-host calls
counts one finding once per affected host, and a guessed tag name returns zero
hosts rather than failing, so "nothing is tagged production" looks like an
answer.

Two payload fields carry that same hazard and are called out in their tool
descriptions, because the reassuring reading of each is the wrong one:
`assist_get_posture`'s `label=insufficient_evidence` means the estate has not
been assessed enough to judge, not that it is clean; `assist_get_patterns`'
`adopted=false` means the analysis could not run for want of scoped subnets, not
that it ran and found nothing.

**`assist_get_patterns` compares across the estate, not over time.** Its
outliers and blind spots are cross-sectional — *this* subnet against the others,
*this* condition's spread — because an engagement runs weeks and there is no
baseline to compare a quarter against. An agent that phrases these as trends is
making a claim the data does not support. "What changed" has its own, narrower
answers: `assist_compare_scans` (two scans), the `q=` time windows
(`firstseen:` / `changedsince:` / `vulnsince:`), and the workbench's
`since_last_visit` and `followups` — none of them is a trend.

The derivation of the assist tool set from the analyst's workflow — including
what is deliberately *not* a tool — is in [ASSIST_TOOLS.md](ASSIST_TOOLS.md).

Three things an assist agent is routinely asked for, and how each is served:

* **"How many hosts …?"** — `assist_count_hosts` takes the same `q=` language
  and returns a total. `assist_list_hosts` returns `{items, total, has_more,
  limit, offset}`: quote `total`, never the length of `items` — a page is not a
  total.
* **A filter the server cannot understand is refused, never ignored.** A
  discrete filter value that cannot be read (`ports=80-90`, an unknown `state`,
  a `subnets` value that is not a network, a list that names nothing) is a 422
  naming the value; a malformed `q=` is a 400. `services=` matches the service
  identified on an open port (the Hosts page's rule), on any port number.
* **"Fill in this report template."** — the template is a file **on the
  operator's machine**, in the working directory the agent already reads and
  writes. BlueStick hosts no templates for this and stores no finished report;
  its job is the data (`assist_count_hosts` for numbers, `assist_list_hosts`
  with a `q=` to isolate a set, `assist_get_host_vulnerabilities` for the
  scanner evidence behind a claim, and the `report-context.ndjson` download
  when the report spans more hosts than is sensible one at a time). The
  finished document is written next to the template. Copyable starting points
  live in [report-templates/](report-templates/). A placeholder the agent could
  not source is left visibly unfilled rather than invented.

### Evidence and proposals

`record_evidence` / `list_evidence` record and read what the agent ran against a
host and what came back (the full raw output: `curl`
`GET /agent/evidence/{id}/raw`). `record_evidence` takes `host_test_id` (with a
`request_key`) when the record answers a proposed test, and stands alone when it
does not. It is text only: an MCP request is limited to 1 MiB, larger output (up
to 5 MB) goes by `curl`, and an image is attached to the finding by a person.

A change to what the team concluded, or to what the client report says, is a
**proposal** a person accepts or rejects in the app: `propose_finding_text`,
`propose_finding`, `propose_observation`, `propose_endpoint_status`;
`list_proposals` shows the decisions. Each creates a row per call, so none is
marked idempotent.

* **A create is acknowledged, not echoed.** `propose_finding_text` answers each
  proposal's id, field and status with `value_chars` / `base_value_chars`;
  `propose_finding` answers `report_text_chars` per section. The texts are in
  `list_proposals`.
* **A report-text value is the complete replacement for its section**, never a
  critique or a list of suggestions.
* **"Not enough to write this" is a valid answer**: a section the finding's data
  does not support is left out — never a guess or a placeholder — and what is
  missing goes in `rationale` and to the operator.
* **The reader has never seen BlueStick.** A section that names a BlueStick
  record by its number ("Finding #277", "evidence record 57") or names BlueStick
  is refused with a 422 quoting the phrase; code spans and fenced blocks are not
  checked.
* A section may place one of the finding's own images with
  `![caption](evidence:<id>)`: `assist_get_finding` lists them under `images`
  (`id`, `caption`, `in_report`, `placed_in`), and an id that is not on that
  list is a 422.

`submit_feedback` is likewise acknowledged: it answers the row's id, status and
source with `friction_notes_chars`, `api_critique_count` and
`tool_suggestion_count`, not the entry. An administrator reads the entry on the
Feedback page.

### What decides a call

**The MCP layer makes no authorisation decision.** A `tools/call` loops back
into the real route forwarding the caller's key; that endpoint decides, checked
against the operator's project role on every request. So whether a given call
succeeds is settled at the endpoint (a write the role does not allow, a host
test whose revision has moved on), never by what `tools/list` showed.

### Bulk data is deliberately not a tool

These are file-shaped: they belong on disk, not materialised into a model's
context. A 40k-host target list read through a tool call is the same data,
minus the ability to hand the file to the next program, plus the token bill.
The server `instructions` point at them with `curl`.

| Route (under `/api/v1/agent`) | What it is | Operator needs |
|---|---|---|
| `GET /assist/report-context.ndjson` | every matching host's report dossier, one JSON object per line | auditor |
| `GET /assist/hosts.ndjson` | the host list, uncapped, one JSON object per line | auditor |
| `GET /scopes` | every scope with ALL its subnets and its declared domains (`assist_list_scopes` is the same data capped at 100 per list) | any member |
| `GET /scopes/{scope_id}/hosts.ndjson` · `live-hosts.txt` · `web-targets.txt` | the scope's hosts, addresses and web URLs (subnet scope) | auditor |
| `GET /scopes/{scope_id}/named-targets.ndjson` | one row per in-scope name, with its current addresses and whether each is in subnet scope | auditor |
| `POST /uploads` (multipart) | scanner output; poll with `get_upload_job` | analyst |
| `GET /evidence/{id}/raw` | an evidence record's whole raw output | any member |
| `GET /assist/client-reports/{id}/files/{fmt}` · `/scope.csv` | a report's rendered file and its scope file | auditor |
| `GET /assist/attachments/{id}` · `/assist/web-interfaces/{id}/screenshot` | an image's bytes | any member |

Images are the one exception: to *look at* one, `assist_get_image` returns it as
MCP image content (opt-in, one per call, 2 MB cap, through the same download
routes); to put one in a report, the `download_path` references from
`assist_get_finding` / `assist_get_host` save the file beside it.

---

## 3. Connecting a client

**Configure ONE server, named `bluestick`, with one key (`BLUESTICK_API_KEY`
where the client reads it from the environment).** Three clients have a recipe —
VS Code Copilot, Claude Code and Codex.

The Start Agent Session dialog (a page's "with your agent" button opens the
same dialog) emits ready-to-paste config per client, built by
`app/services/mcp_client_setup_service.py`. The reference page shows the same
recipes with `<your-session-key>` in place of a key, served from that same
builder.

Clients disagree on config shape, which is why one blob can't serve them:

| Client | Shape | Notes |
|---|---|---|
| VS Code Copilot | `.vscode/mcp.json`, servers under **`servers`** | the emitted file embeds a LIVE key — keep `.vscode/mcp.json` out of version control |
| Claude Code | `claude mcp add --transport http …`, config uses **`mcpServers`** | `-s local` keeps the key out of the repo |
| Codex | `codex mcp add --url … --bearer-token-env-var` | the only client where the key never touches a config file |

A client reads `tools/list` when it connects and may keep that list. After a
BlueStick upgrade, reconnect the client (or restart it) so its tool list is the
server's current one.

### The certificate

BlueStick's certificate is issued by your organisation's **local root CA**
(`ca/local-ca.sh`: a name-constrained root that the administrator creates on
their own workstation). Install that root **once** on the machine running your
agent client — the administrator gives you `rootCA.crt` and its fingerprint,
and `ca/local-ca.sh trust-help` prints the trust-store steps for each system,
plus the one-line environment variables for clients that ignore the system
store. After that the client trusts BlueStick with no per-client pinning, and
redeploys or address changes do not force anyone to re-trust. The walkthrough
is [`ca/README.md`](../ca/README.md) (step 6 for analyst machines).

If an earlier installer set `NODE_EXTRA_CA_CERTS` or `SSL_CERT_DIR` on your
machine to a self-signed certificate, point them at the root instead (as
`trust-help` shows).

**Fingerprint check.** `/reference/mcp` shows the SHA-256 of the certificate
BlueStick presents (`tls_fingerprint_sha256` in `/references/mcp-tools`; the
PEM itself is `GET /references/tls-certificate`). Compare it with what your
client receives to confirm you reached the right server. If the deployment
still presents a self-signed certificate, the page says so — ask the
administrator to issue one from the local root.

---

## 4. Auth, and the 401/403 split

`initialize` / `tools/list` / `ping` need no key — they are static and leak
nothing. When `initialize` does carry a live key, its `clientInfo` names the
session's client; recording it can never fail the handshake. `tools/call` reads
`X-API-Key` or `Authorization: Bearer` and forwards it. What comes back depends
on *why* a call was refused:

* **No usable credential** → a real **HTTP 401** with a plain RFC 6750 challenge
  (`WWW-Authenticate: Bearer realm="BlueStick assist"`, plus
  `error="invalid_token"` when a key was sent — no `resource_metadata`). That is
  a fact about the connection, and a client can act on it: prompt for a key,
  show a connection error, stop retrying.
* **A valid key that may not do this** (the operator's project role is
  read-only, or the target does not exist) → an `isError` tool result carrying
  the endpoint's status. That is a fact about one call, which the model should
  read and work around; re-authenticating would not change it.
* **An argument the tool's schema does not allow** (a wrong type, a value
  outside an enum, an unknown property) → a JSON-RPC `-32602` from the
  transport, before any endpoint is called. The same value sent by `curl`
  reaches the endpoint, which answers its own 422 or 400.

The endpoint's own 401 reaches the client as that real HTTP 401, a JSON-RPC
error (`-32001`) whose message is the endpoint's detail. One of its reasons is
`operator_credentials_changed` — the key was issued before its operator's
password was last changed or reset. It is not recoverable by renewing; the
operator starts a new session.

`read_agent_guide` called without `workflow` returns the whole guide; with it,
one part (`assist`, `reconnaissance`, `testing` or `remediation`).

The challenge is deliberately bare. MCP's authorization spec uses 401 plus
`resource_metadata` to bootstrap OAuth 2.1 discovery; this server is not an
OAuth resource server, and advertising discovery it doesn't implement would send
capable clients into a dead end. Authorization is **OPTIONAL** in MCP, so
header auth is outside the optional profile rather than non-conformant.

**Pre-auth ceilings.** The endpoint is unauthenticated at the FastAPI layer, so
everything before a key is checked is bounded: the body is read through a capped
stream (1 MiB — never `request.json()`; every other route's body is capped
before authentication too, at `MAX_REQUEST_BODY_BYTES`, 32 MB by default) and a
JSON-RPC batch is capped at 50 messages (batching is refused outright under
protocol 2025-06-18, which removed it; it is allowed only when a client declares
2025-03-26).

---

## 5. The tool registry

`app/api/v1/endpoints/mcp_tools.py` is the declarative map: tool → endpoint,
description, annotations. `mcp_assist.py` is the transport. They are split
because they change for different reasons — adding a tool touches only the
registry.

**A tool's arguments are its endpoint's.** A registry entry authors only what
the endpoint cannot say: `description`, `method`, `path`, `params` (an
argument's description, or something that NARROWS it), `hidden` (endpoint
parameters the tool does not offer), `defaults` (MCP-side defaults, e.g. a
smaller page), the write flags (`additive` / `idempotent` / `metadata_write`),
`retired_params` (arguments a tool used to take: accepted and dropped) and
`path_alternatives`. Which arguments exist, whether each goes in the path, the
query or the body, and their types, enums, bounds, defaults and required-ness
are read from the endpoint's OpenAPI operation (`derive_tool`) when the registry
is built: once per backend worker, just after startup
(`main._warm_mcp_registry`). So a parameter added to an endpoint is offered by
its tool with no registry edit (list it under `hidden` to keep it off), and the
advertised schema cannot be wider than what the endpoint accepts —
`tests/test_mcp_enum_contract.py` pins that an authored overlay never widens it.
A route parameter whose pattern only lists its accepted words (`^(asc|desc)$`)
is offered as that enum.

**What a description says.** What the tool returns and the one or two things an
agent gets wrong without being told. Not the argument types the schema carries,
not history, not a rule the session prompt or the guide owns. A description
never names a security tool or flags to use — the agent chooses those — and
never tells the agent to source or cite the tool catalogue.

Entries carry MCP **annotations** (`readOnlyHint`, `destructiveHint`,
`idempotentHint`) so a client can offer "always allow" on reads without the
operator classifying them by hand. `destructiveHint` follows the spec's meaning:
false only for genuinely additive writes (a note, a test result), true for ones
that replace stored values. `idempotentHint` answers "is a retry safe?": true
for writes that converge (set follow, patch a host, update a host test), false
for anything that creates a row per call — every additive tool
(`host_tests_propose` among them — conservatively: a retry with the same
`request_key` is in fact safe), and the appenders that say so explicitly with
`"idempotent": False`, `submit_feedback` among them.

The transport validates `tools/call` arguments against the advertised schema
(type, enum, bounds, no unknown properties) **before** building the endpoint
URL, and renders every path parameter as a single encoded segment. A mistyped
argument is a JSON-RPC `-32602`, never a call to a different route.

### The tool catalogue

Separate from the MCP registry, `tool_registry` is the table of **tools BlueStick
knows about** — seeded from `app/data/tool_registry_seed.json`, rendered for
people at `/tool-reference` and readable by agents, unfiltered, through
`list_tools` (`GET /api/v1/references/tools`).

**It is a catalogue for people, not a permission list and not a recommendation.**
The agent chooses its tools and parameters from its own judgment and its
operator's direction; nothing in BlueStick's instructions tells it to consult or
cite the catalogue.

* **`status`** says only where a row stands in the catalogue: `reference` (in
  it), `suggested` (an agent proposed it; awaiting a curator), `rejected` (a
  declined suggestion).
* **`ingestible`** is an *engineering* fact: does a parser exist for its output.
  A tool can be worth running without BlueStick parsing a word of its output.
* **`run_command`** / **`run_note`**, on a tool BlueStick parses: an invocation
  that writes a file it can ingest (with placeholders) and what to upload — the
  Tool Reference page's "Run for BlueStick". Reference text, null for a tool
  with no parser.

Seeding is **additive**: a curator's decision or edited description survives a
redeploy. A correction to a shipped seed row therefore needs a migration, not a
seed edit (see `c9a4e70b5d18`).

`suggest_tool` records a proposed addition; the row lands as `suggested` and an
admin curates it from the Tool Reference page
(`PATCH /references/tools/{name}` → `reference` or `rejected`). Declining keeps
the row, so the next proposal of the same tool gets the same answer.

---

## 6. Guardrails, and what the server can't do

The operator drives the agent (`safety_properties.command_approval:
"operator_driven"` on `/.well-known/networkmapper.json`). The agent's rules
(`agent_policy.SAFETY_RULES`): show the operator every command before running
it; propose next steps rather than taking them unasked; stay inside the
project's declared scope — a target outside it needs the operator's explicit
go-ahead, and a name in scope does not put the address it resolves to in scope;
write output into the working directory — reading or writing outside it,
installing software or changing settings or credentials needs explicit
go-ahead; record every command and its outcome verbatim and upload scanner
output. The server prescribes no tool list and no order of work.

**BlueStick cannot enforce any of this.** The commands run on the operator's
machine and the server sees only what the agent reports. The real boundary is
the client's sandbox — `codex --sandbox workspace-write --ask-for-approval
on-request`, or Claude Code's default prompting — and every connect recipe the
session dialog emits says so.

What the server contributes is the record, and one requirement: **the session
prompt opens with a mandatory read-back**, where the agent states the
bounds of the session in its own words before its first call. That is the one
moment a human sees the agent's *understanding* rather than its output, and it
makes the agent's own words part of the audit trail.

---

## 7. Reviewing what happened

* **Agents → Agent Sessions** (`/agent-activity`) — what is live (with the
  tests each session proposed and the evidence it recorded, and Resume / End)
  and every session in the project. A session is **live** (its key is valid
  now), **resumable** (the key ran out inside the session's lifetime — the
  agent can still renew it, or the operator resumes with a new key) or
  **ended**. A key lasts 24 h (`AGENT_KEY_TTL_HOURS`) and is renewable until
  the session's maximum lifetime (`AGENT_SESSION_MAX_LIFETIME_HOURS`, 168 h).
  Ending a session — End here, the agent's `end_session`, or the hourly lapse
  sweep, which ends a session only after its key has expired AND that renewal
  deadline has passed — revokes its key; its tests and evidence stay, for
  another session or a person to carry on. **A session's page**
  (`/agent-sessions/{id}`, by the session id — its only id, the one
  `agent_identity` reports as `session_id`) has its controls, its work, the
  notes it wrote (the durable output) and its API-call feed (the read trail).
* **Agent API activity** — per session, on its page.
* **`GET /api/v1/mcp-telemetry/summary`** (admin) — per-tool call counts,
  outcomes, and `unknown_tools_called`, which is how a client calling a tool
  this deployment doesn't serve becomes visible.

`agent_api_calls` rows are purged after 90 days (`AGENT_API_CALL_RETENTION_DAYS`,
0 disables). Session records and agent feedback are kept indefinitely.

---

## 8. Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/v1/mcp` | key on `tools/call` | the transport |
| `GET /api/v1/references/mcp-tools` | none | live tool catalog + connect recipes + certificate info |
| `GET /api/v1/references/tls-certificate` | none | the deployment certificate (PEM), for inspection and the fingerprint check |
| `GET /api/v1/references/tools` | none | the tool catalogue (`?status=reference\|suggested\|rejected`) |
| `PATCH /api/v1/references/tools/{name}` | admin | curate a suggested tool (`reference` / `rejected`) |
| `GET /api/v1/agents-guide?workflow=…` | none | the agent guide, whole or one part |
| `GET /api/v1/agent/identity` | agent key | what this key is |
| `POST /api/v1/agent/session/renew` | agent key (an expired one too) | the same key, a later deadline |
| `POST /api/v1/agent/session/end` | agent key | end the session and revoke the key |
| `POST /api/v1/agent/feedback` | agent key | file feedback; answers an acknowledgement |
| `POST /api/v1/agent/uploads` · `GET /api/v1/agent/uploads/{job_id}` | agent key | upload scanner output (curl, multipart) · poll its parse (`get_upload_job`) |
| `POST /api/v1/agent/tool-suggestions` | agent key | propose a tool for the catalogue |
