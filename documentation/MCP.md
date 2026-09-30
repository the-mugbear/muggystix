# MCP — the Model Context Protocol surface

BlueStick serves its agent workflows over MCP at **`POST /api/v1/mcp`**, so an
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

**Why not the `mcp` SDK.** Originally a hard constraint — the package required
`starlette>=1.0` while the backend was pinned below it. That constraint is gone
(FastAPI 0.141 / starlette 1.6 since v2.267.0) and it stays unadopted on
purpose: the need is a small, frozen wire format, and the SDK would add a
dependency with its own transitive pins plus a second ASGI app to mount, for no
capability this doesn't have. Revisit if SSE streaming, sampling, or the
resources/prompts surfaces are ever needed.

**Every tool call loops back into the app's own `/api/v1/*` endpoint
in-process** (all but `read_agent_guide` → `/agents-guide` and
`list_tools` → `/references/tools` are under `/agent/*`) (ASGI transport, no socket), forwarding the caller's `X-API-Key`.
So authentication, the operator-role gate, the agent-API audit log, and
the streaming caps all run **unchanged**. The MCP layer makes no security
decision of its own — that invariant is stated in `mcp_tools.py` and pinned by
`test_hiding_a_tool_is_presentation_not_authorisation`.

---

## 2. One endpoint, one session, four kinds of work (v2.337.0)

`tools/list` returns the **whole** tool catalogue: a key binds to one
project-scoped `AgentSession` that does every kind of work, so there is no
per-workflow filtering any more (it was always presentation — the endpoint
behind each tool is the decider). The kinds of work are not a sequence
(v2.433.0): the operator drives the agent, and the agent registers and executes
its own plans — there is no approval step, approved-tool list or required order.

| Work | Opened by | Tools |
|---|---|---|
| Query / report | (default — no phase) | `assist_*` reads, `assist_count_hosts`, `assist_list_findings`, … |
| Scope reads, scanning and uploads | (default — no phase) | `assist_list_scopes`, then `scope_list_subnets` / `scope_list_domains` for one scope's CIDRs and names; the target files (`/agent/scopes/{scope_id}/hosts.ndjson`, `live-hosts.txt`, `web-targets.txt`) and the upload itself (`POST /agent/uploads`) are curl; `get_upload_job` polls an upload. There is no run to open — recon runs were removed (v2.433.0) |
| Test plan | `create_test_plan {title}` | entry drafting, validation (advice, not a gate) |
| Execution | `start_execution {plan_id}` (a draft or in-progress plan) | execution context, optional target checks (evidence), test results, completion |

Every session starts by probing its environment once (`record_environment` →
`POST /agent/session/environment`); the probe rides into every run it opens.

### Assist: answering questions, and filling in a report

The assist surface is meant to answer whatever an analyst asks about a project.
What that takes, beyond "which hosts match X":

| Question | Tool |
|---|---|
| "How many hosts …?" | `assist_count_hosts` — a total, not a page |
| "What are our critical findings?" | `assist_list_findings` — project-wide, with `severity_counts` |
| "What has nobody picked up?" | `assist_list_findings?unowned=true`, `assist_count_hosts` with `assigned:none` |
| "What do we already know about this host?" | `assist_get_host_notes` (paged — read `has_more`) |
| "Every web interface on this host, not just the ten on host detail?" | `assist_list_host_web_interfaces` — the continuation when `web_interfaces_truncated` is set (v2.343.3) |
| "What did NetExec find on this host, and was it read right?" | `assist_list_host_access` — each result's interpreted fields beside the tool's line (v2.418.0) |
| "Which lines did BlueStick not read?" | `assist_list_uninterpreted_lines` — per import, as redacted shapes (v2.418.0); see `documentation/PARSE_AUDIT_BRIEF.md` |
| "Which tags/sites/people exist here?" | `assist_get_vocabulary` |
| "How much of this did we actually assess?" | `assist_get_coverage` |
| "Has anyone tested this host, and what happened?" | `assist_get_host_testing` |
| "Which segment is worst?" | `assist_list_segments` — ranked worst-first |
| "What has the team been working on?" | `assist_list_recent_notes` (`status=open` = outstanding work) |
| "Where is this project overall?" | `assist_get_posture` — the headline condition, and why |
| "What does this estate have a *problem* with?" | `assist_get_patterns` — blind spots, segment outliers, root causes |
| "What's the evidence behind this finding?" | `assist_get_finding` — the note, the thread, the screenshot references, the report text and status history; `assist_get_image` to look at one |
| "What's worth a look? What's mine? What changed since I was last here?" | `assist_list_worth_a_look` · `assist_get_workbench` (v2.428.0) |
| "Which ranges has nobody touched?" | `assist_get_terrain` |
| "What is still unassessed in segment Y?" | `assist_list_evidence_gaps` |
| "What changed between these two scans?" | `assist_compare_scans` |
| "Which issues are widespread but not yet findings?" | `assist_list_scanner_observations` → `assist_list_observation_hosts` |
| "What did we report to the client?" | `assist_list_client_reports` → `assist_get_client_report` (operator needs `auditor`) |

Several of these exist because their absence produced *confident wrong answers*
rather than errors: rebuilding the findings spine from per-host calls counts one
finding once per affected host, and a guessed tag name returns zero hosts rather
than failing, so "nothing is tagged production" looks like an answer.

Two payload fields carry that same hazard and are called out in their tool
descriptions, because the reassuring reading of each is the wrong one:
`assist_get_posture`'s `label=insufficient_evidence` means the estate has not
been assessed enough to judge, not that it is clean; `assist_get_patterns`'
`adopted=false` means the analysis could not run for want of scoped subnets, not
that it ran and found nothing.

**`assist_get_patterns` compares across the estate, not over time.** Its
outliers and blind spots are cross-sectional — *this* subnet against the others,
*this* condition's spread — because an engagement runs weeks and there is no
baseline to compare a quarter against. Nothing in the assist surface answers
"what changed since last week"; an agent that phrases these as trends is making
a claim the data does not support.

The full derivation of this tool set from the analyst's workflow — including
what is deliberately *not* a tool, and what is still queued — is in
[ASSIST_TOOLS.md](ASSIST_TOOLS.md).

Two things an assist agent is routinely asked for, and how each is served:

* **"How many hosts …?"** — `assist_count_hosts` takes the same `q=` DSL and
  returns a total. Counting a page of `assist_list_hosts` is the wrong answer to
  a counting question: a page is not a total, and an agent that stops at the
  first one reports a confident wrong number. `assigned:` accepts
  `me` / `any` / `none` / a username, so *"critical findings nobody owns"* is
  `has:critical AND assigned:none`.
* **"Fill in this report template."** — the template is a file **on the
  operator's machine**, in the working directory the agent already reads and
  writes. BlueStick hosts no templates and stores no finished report; its job is
  the data (`assist_count_hosts` for numbers, `assist_list_hosts` with a `q=` to
  isolate a set, `assist_get_host_vulnerabilities` for the evidence behind a claim
  (raw scanner observations on the host — not triaged findings; it was named
  `assist_get_host_findings` before the vocabulary was fixed), and
  the `report-context.ndjson` download when the report spans more hosts than is
  sensible one at a time). The finished document is written next to the template.
  Copyable starting points live in [report-templates/](report-templates/).

  A placeholder the agent could not source is left visibly unfilled rather than
  invented — a number nobody can trace is worse than a gap somebody can see.

Every session sees the WHOLE catalogue (66 tools at v2.433.0, about 60 KB /
~15k tokens as `tools/list` returns it) — nothing is filtered by workflow since
v2.337.0. Nine of those belong to the session rather than to any phase:
**`agent_identity`** (what am I, what may I write, when does my key expire),
**`session_renew`** (same key, later deadline), **`end_session`** (only when
the operator says they are finished — it revokes the key),
**`record_environment`** (the one probe), **`read_agent_guide`**,
**`list_tools`** (the tool catalogue — a reference, not a permission list; it was
`list_approved_tools` before v2.433.0), **`suggest_tool`** (propose a tool the
catalogue lacks, for a curator — it grants nothing), **`get_upload_job`** (poll
an upload's parse) and **`submit_feedback`**.

Each tool also carries a `workflows` grouping tag — `assist`, `plan_generation`,
`execution` or `scope` (scope reads and uploads; it was `recon` until the recon
runs were removed) — which the tool reference page groups by. It is
presentation, never a filter.

**The MCP layer makes no authorisation decision.** A `tools/call` loops back
into the real `/agent/*` route forwarding the caller's key; that endpoint
decides, checked against the operator's project role and the run state.
v2.337.0 removed the per-workflow `tools/list` filter entirely — one project
session does everything, so the whole catalogue is listed and whether a given
call succeeds is settled at the endpoint (a plan you have not opened an
execution run on, a write your role does not allow).

**Bulk data is deliberately not a tool.** `report-context.ndjson`,
`scopes/{scope_id}/hosts.ndjson`, `scopes/{scope_id}/live-hosts.txt`,
`scopes/{scope_id}/web-targets.txt` (auditor role or above),
`assist/attachments/{id}` and `POST uploads` (`/agent/uploads`, no run needed)
are file-shaped: they belong
on disk, not materialised into a model's context. A 40k-host target list read
through a tool call is the same data, minus the ability to pipe it into the next
scanner, plus the token bill. Images are the exception since v2.428.0: to look
at one, `assist_get_image` returns it as MCP image content (opt-in, one per call,
2 MB cap, through the same download routes); to put one in a report, the
`download_path` references from `assist_get_finding` / `assist_get_host` save
the file beside it.
The server `instructions` point at the NDJSON, the scope target files
(`/agent/scopes/{scope_id}/live-hosts.txt`, `web-targets.txt`, `hosts.ndjson`)
and the upload with `curl`; attachment paths come back in `assist_get_finding`'s
`download_path`.

---

## 3. Connecting a client

**Configure ONE server, named `bluestick`, with one key (`BLUESTICK_API_KEY`
where the client reads it from the environment).** Before v2.337.0 an operator
connected up to four (`bluestick-recon` / `-plan` / `-exec` / `-assist`); those
names are gone, and a stale entry under one of them will simply fail to
authenticate. Three clients have a recipe — VS Code Copilot, Claude Code and
Codex. Cursor's was removed in v2.275.0 because its config shape was never
verified against a real install.

The Start Agent Session dialog (v2.433.0 — the per-object recon / plan
generation / execution mints are gone; a page's "with your agent" button opens
the same dialog) emits ready-to-paste config per client, built by `app/services/mcp_client_setup_service.py`. The
reference page shows the same recipes with `<your-session-key>` in place of a
key — served from that same builder, because the page previously kept its own
copy and the two drifted twice.

Clients disagree on config shape, which is why one blob can't serve them:

| Client | Shape | Notes |
|---|---|---|
| VS Code Copilot | `.vscode/mcp.json`, servers under **`servers`** | the emitted file embeds a LIVE key — keep `.vscode/mcp.json` out of version control |
| Claude Code | `claude mcp add --transport http …`, config uses **`mcpServers`** | `-s local` keeps the key out of the repo |
| Codex | `codex mcp add --url … --bearer-token-env-var` | the only client where the key never touches a config file |

### The certificate (read this first — it is where every client fails)

BlueStick defaults to a **self-signed certificate**, and no public CA will issue
one for a private address. Every client refuses the connection until it trusts
that certificate, and **the mechanism differs per client**:

| Client | Variable | Takes |
|---|---|---|
| VS Code, Claude Code (Node) | `NODE_EXTRA_CA_CERTS` | a PEM **file** |
| Codex (Rust / native-tls) | `SSL_CERT_DIR` | a **directory** of hash-named symlinks |

Node ignores the OS trust store, so installing the certificate system-wide does
nothing for it. Codex reads neither `NODE_EXTRA_CA_CERTS` **nor**
`SSL_CERT_FILE` — both verified against codex 0.147.0.

```bash
curl -sk https://<host>/api/v1/references/trust-cert-script -o trust-cert.sh
less trust-cert.sh          # it installs a trust anchor — read it first
bash trust-cert.sh --url https://<host>
```

The script installs both shapes, mirrors the system trust anchors into the
directory (so `SSL_CERT_DIR` *adds* this certificate rather than replacing your
trust), and prints the certificate's SHA-256 — compare it against the
fingerprint on `/reference/mcp` before relying on a downloaded copy.

**Both variables are read at process start.** Export them, then restart the
client; setting them inside a running client changes nothing, which is the usual
reason a pin looks like it "didn't work".

**Windows without WSL (PowerShell 7).** The script is bash, and the profile
exports it prints would not reach a client launched from the Start menu anyway.
Do the same by hand — this covers VS Code and Claude Code:

```powershell
New-Item -ItemType Directory -Force "$HOME\.bluestick" | Out-Null
curl.exe -sk https://<host>/api/v1/references/tls-certificate -o "$HOME\.bluestick\bluestick.pem"
$c = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new("$HOME\.bluestick\bluestick.pem")
$c.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256) -replace '(..)(?!$)', '$1:'   # compare with the fingerprint
setx NODE_EXTRA_CA_CERTS "$HOME\.bluestick\bluestick.pem"   # per-user, for every process started from now on
$env:NODE_EXTRA_CA_CERTS = "$HOME\.bluestick\bluestick.pem"   # this shell too — setx does not update it
```

`curl.exe`, not `curl`: in PowerShell the bare name is an `Invoke-WebRequest`
alias that rejects these flags. `setx` stores a per-user variable that every
process started from now on reads — a client launched from the Start menu or a
new terminal — but it does not change the window you ran it in, so the `$env:`
line covers a client launched from that same shell. A `$PROFILE` export only
reaches clients started from that shell. Launch the client from a new terminal
or the Start menu afterwards. Codex's pin
(`SSL_CERT_DIR`) has only been verified on Linux and macOS, and its `read -rs`
key-entry line is bash too — on Windows, run Codex inside WSL and follow the
bash steps there. The same guidance is in the start dialog's certificate step,
on `/reference/mcp`, and in the Assist entry of the User Guide.

Deployments running an internal-CA or DNS-validated certificate need none of
this, and the reference page detects that and says so. **On a closed network,
a local CA is the better setup:** `ca/local-ca.sh` creates a name-constrained
root that each analyst machine trusts once, and issues BlueStick's certificate
from it, so redeploys and address changes stop forcing every client to re-trust.
Some `rustls`-based clients reject the self-signed default even when it is
trusted. The walkthrough, for a remote host, is [`ca/README.md`](../ca/README.md).

---

## 4. Auth, and the 401/403 split

`initialize` / `tools/list` / `ping` need no key — they are static and leak
nothing. `tools/call` reads `X-API-Key` or `Authorization: Bearer` and forwards
it. What comes back depends on *why* a call was refused:

* **No usable credential** → a real **HTTP 401** with a plain RFC 6750 challenge
  (`WWW-Authenticate: Bearer realm="BlueStick assist"`, plus
  `error="invalid_token"` when a key was sent — no `resource_metadata`). That is a fact about the connection, and a client can act
  on it: prompt for a key, show a connection error, stop retrying.
* **A valid key that may not do this** (the operator's project role is
  read-only, or the target does not exist)
  → an `isError` tool result carrying the endpoint's status. That is a fact about
  one call, which the model should read and work around; re-authenticating
  would not change it.

The challenge is deliberately bare. MCP's authorization spec uses 401 plus
`resource_metadata` to bootstrap OAuth 2.1 discovery; this server is not an
OAuth resource server, and advertising discovery it doesn't implement would send
capable clients into a dead end. Authorization is **OPTIONAL** in MCP, so
header auth is outside the optional profile rather than non-conformant.

**Pre-auth ceilings.** The endpoint is unauthenticated at the FastAPI layer, so
everything before a key is checked is bounded: the body is read through a capped
stream (1 MiB — never `request.json()`, which would let an anonymous caller
materialise nginx's 2 GB limit per worker) and a JSON-RPC batch is capped at 50
messages (batching is refused outright under protocol 2025-06-18, which removed
it; it is allowed only when a client declares 2025-03-26).

---

## 5. The tool registry

`app/api/v1/endpoints/mcp_tools.py` is the declarative map: tool → endpoint,
schema, workflow, annotations. `mcp_assist.py` is the transport. They are split
because they change for different reasons — adding a tool touches only the
registry.

Entries carry MCP **annotations** (`readOnlyHint`, `destructiveHint`,
`idempotentHint`) so a client can offer "always allow" on reads without the
operator classifying them by hand. `destructiveHint` follows the spec's meaning:
false only for genuinely additive writes (a note, a test result), true for ones
that replace stored values. `idempotentHint` answers "is a retry safe?": true
for writes that converge (set follow, patch a host, complete a run, re-probe the
environment), false for anything that creates a row per call — every additive
tool, and the creators `create_test_plan`, `start_execution` and
`submit_feedback`, which say so explicitly with `"idempotent": False`
(v2.343.2; the inferred value had advertised them as safe to retry).

The transport validates `tools/call` arguments against the advertised schema
(type, enum, bounds, no unknown properties) **before** building the endpoint
URL, and renders every path parameter as a single encoded segment. A mistyped
argument is a JSON-RPC `-32602`, never a call to a different route (v2.343.2 —
a string `host_id` on `assist_add_note` used to be interpolated into the path
as-is).

### The tool catalogue

Separate from the MCP registry, `tool_registry` is the table of **tools BlueStick
knows about** — seeded from `app/data/tool_registry_seed.json` (63 entries at
v2.370; the number moves, the file is the source), rendered for
humans at `/tool-reference` and served to agents, unfiltered, by `list_tools`
(`GET /api/v1/references/tools`).

**It is a catalogue, not a permission list (v2.433.0).** It used to carry an
`approved` status that agents were told was the only set they could run without
asking; that allowlist went with the rest of the "agent on rails" model (and the
server never enforced it). Migration `c7d2e9f4a1b6` turned every `approved` row
into `reference`. What an agent runs is between it and its operator.

* **`status`** says only where a row stands in the catalogue: `reference` (in
  it), `suggested` (an agent proposed it; awaiting a curator), `rejected` (a
  declined suggestion).
* **`ingestible`** is an *engineering* fact: does a parser exist for its output.
  A tool can be worth running without BlueStick parsing a word of its output.

Seeding is **additive**: a curator's decision or edited description survives a
redeploy. A correction to a shipped seed row therefore needs a migration, not a
seed edit (see `c9a4e70b5d18`).

An agent that used or needed a tool the catalogue lacks calls `suggest_tool`;
the row lands as `suggested` and an admin curates it from the Tool Reference
page (`PATCH /references/tools/{name}` → `reference` or `rejected`). Declining
keeps the row, so the next agent that proposes it gets the same answer.

---

## 6. Guardrails, and what the server can't do

The operator drives the agent (v2.433.0; `safety_properties.command_approval:
"operator_driven"` on `/.well-known/networkmapper.json`, which no longer
publishes `plan_execution_requires_human_approval`). The agent's rules
(`agent_policy.SAFETY_RULES`): show the operator every command before running
it; propose next steps rather than taking them unasked; stay inside the
project's declared scope — a target outside it needs the operator's explicit
go-ahead, and a name in scope does not put the address it resolves to in scope;
write output into the working directory — reading or writing outside it,
installing software or changing settings or credentials needs explicit
go-ahead; record every command and its outcome verbatim and upload scanner
output. There is no approved-tool allowlist, no plan approval, no mandatory
per-host sanity check and no required order — those were the retired "rails".

**BlueStick cannot enforce any of this.** The commands run on the operator's
machine and the server sees only what the agent reports. The real boundary is
the client's sandbox — `codex --sandbox workspace-write --ask-for-approval
on-request`, or Claude Code's default prompting — and every connect recipe the
session dialog emits carries those flags.

What the server contributes is the record, and one requirement: **every
workflow's prompt opens with a mandatory read-back**, where the agent states the
bounds of the session in its own words before its first call. That is the one
moment a human sees the agent's *understanding* rather than its output, and it
makes the agent's own words part of the audit trail.

---

## 7. Reviewing what happened

* **Workflows → Agent Sessions** (`/agent-activity`) — what is live (with the
  plans and execution runs each session opened, and Resume / End) and every
  session in the project. Ending a session — End here, the agent's
  `end_session`, or the hourly lapse sweep — marks its open execution runs
  (active or paused) `abandoned` with their results kept, so nothing is "left
  open" behind an ended session. **A session's page**
  (`/agent-sessions/{id}`, by the session id; the older `/assist-sessions/{id}`
  links redirect there) has its controls, its work, the notes it wrote (the
  durable output) and its API-call feed (the read trail).
* **Agent API activity** — per plan and per session.
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
| `GET /api/v1/references/trust-cert-script` | none | the certificate-trust installer |
| `GET /api/v1/references/tls-certificate` | none | the deployment certificate (PEM) |
| `GET /api/v1/references/tools` | none | the tool catalogue (`?status=reference\|suggested\|rejected`) |
| `PATCH /api/v1/references/tools/{name}` | admin | curate a suggested tool (`reference` / `rejected`) |
| `GET /api/v1/agent/identity` | agent key | what this key is |
| `POST /api/v1/agent/uploads` · `GET /api/v1/agent/uploads/{job_id}` | agent key | upload scanner output (curl, multipart) · poll its parse (`get_upload_job`) |
| `POST /api/v1/agent/tool-suggestions` | agent key | propose a tool for the catalogue |
| `GET /api/v1/agents-guide?workflow=…` | none | the agent guide, sliced |
