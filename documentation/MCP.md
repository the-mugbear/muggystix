# MCP — the Model Context Protocol surface

BlueStick serves its agent workflows over MCP at **`POST /api/v1/mcp`**, so an
MCP-capable client calls them as native tools instead of shelling `curl`. This
document covers what is exposed, how a client connects, and the decisions behind
both — the parts that are easy to get wrong and expensive to rediscover.

The agent-facing *contract* (how to behave in a session) is
[AGENTS.md](../AGENTS.md); this is the operator-facing description of the
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

**Every tool call loops back into the app's own `/api/v1/agent/*` endpoint
in-process** (ASGI transport, no socket), forwarding the caller's `X-API-Key`.
So authentication, the capability gate, row-scope, the agent-API audit log, and
the streaming caps all run **unchanged**. The MCP layer makes no security
decision of its own — that invariant is stated in `mcp_tools.py` and pinned by
`test_hiding_a_tool_is_presentation_not_authorisation`.

---

## 2. One endpoint, four workflows

`tools/list` returns **only the tools belonging to the caller's own workflow**,
resolved from their key via `GET /api/v1/agent/identity`:

| Workflow | Key minted by | Sees |
|---|---|---|
| `recon` | Scopes → Start Agentic Recon | scope context, subnets, upload-job polling, summary, completion |
| `plan_generation` | Test Plans → Generate with AI | planning context, entry drafting, validation, submit-for-approval |
| `execution` | Execute with AI on an approved plan | execution context, sanity checks, test results, entry/session completion |
| `assist` | Operations → AI Assist | interactive reads over the inventory, plus the three capability-gated writes |

### Assist: answering questions, and filling in a report

The assist surface is meant to answer whatever an analyst asks about a project.
What that takes, beyond "which hosts match X":

| Question | Tool |
|---|---|
| "How many hosts …?" | `assist_count_hosts` — a total, not a page |
| "What are our critical findings?" | `assist_list_findings` — project-wide, with `severity_counts` |
| "What has nobody picked up?" | `assist_list_findings?unowned=true`, `assist_count_hosts` with `assigned:none` |
| "What do we already know about this host?" | `assist_get_host_notes` |
| "Which tags/sites/people exist here?" | `assist_get_vocabulary` |
| "How much of this did we actually assess?" | `assist_get_coverage` |
| "Has anyone tested this host, and what happened?" | `assist_get_host_testing` |
| "Which segment is worst?" | `assist_list_segments` — ranked worst-first |
| "What has the team been working on?" | `assist_list_recent_notes` (`status=open` = outstanding work) |

Several of these exist because their absence produced *confident wrong answers*
rather than errors: rebuilding the findings spine from per-host calls counts one
finding once per affected host, and a guessed tag name returns zero hosts rather
than failing, so "nothing is tagged production" looks like an answer.

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
  isolate a set, `assist_get_host_findings` for the evidence behind a claim, and
  the `report-context.ndjson` download when the report spans more hosts than is
  sensible one at a time). The finished document is written next to the template.
  Copyable starting points live in [report-templates/](report-templates/).

  A placeholder the agent could not source is left visibly unfilled rather than
  invented — a number nobody can trace is worse than a gap somebody can see.

Two tools are offered to every workflow: **`agent_identity`** (what am I, what
may I write, when does my key expire) and **`suggest_tool`** (record a request
for a tool the approved set doesn't cover). `read_agent_guide` and
`list_approved_tools` are likewise universal.

**Workflow filtering is presentation, not authorisation.** Hiding a tool stops a
model from making a call whose 403 it would read as its own bug. It decides
nothing: a `tools/call` for an unlisted tool still reaches the real endpoint and
gets that endpoint's answer. Three separate entry points exist because the
operator starts each session deliberately — there is no key that spans them.

**Bulk data is deliberately not a tool.** `report-context.ndjson`,
`recon/hosts.ndjson`, `recon/live-hosts.txt`, `recon/web-targets.txt` and
`POST recon/upload` are file-shaped: they belong on disk, not materialised into
a model's context. A 40k-host target list read through a tool call is the same
data, minus the ability to pipe it into the next scanner, plus the token bill.
The server `instructions` point at them with `curl`.

---

## 3. Connecting a client

The Start dialogs (assist, recon, plan generation, execution) emit ready-to-paste
config per client, built by `app/services/mcp_client_setup_service.py`. The
reference page shows the same recipes with `<your-session-key>` in place of a
key — served from that same builder, because the page previously kept its own
copy and the two drifted twice.

Clients disagree on config shape, which is why one blob can't serve them:

| Client | Shape | Notes |
|---|---|---|
| VS Code Copilot | `.vscode/mcp.json`, servers under **`servers`** | supports `${input:…}` to keep the key out of the file |
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

Deployments running an internal-CA or DNS-validated certificate need none of
this, and the reference page detects that and says so.

---

## 4. Auth, and the 401/403 split

`initialize` / `tools/list` / `ping` need no key — they are static and leak
nothing. `tools/call` reads `X-API-Key` or `Authorization: Bearer` and forwards
it. What comes back depends on *why* a call was refused:

* **No usable credential** → a real **HTTP 401** with a bare `WWW-Authenticate:
  Bearer` challenge. That is a fact about the connection, and a client can act
  on it: prompt for a key, show a connection error, stop retrying.
* **A valid key that may not do this** (capability missing, host not assigned)
  → an `isError` tool result carrying the endpoint's 403. That is a fact about
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
messages.

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
that replace stored values.

### The approved-tool set

Separate from the MCP registry, `tool_registry` is the table of **tools BlueStick
knows about** — 61 seeded from `app/data/tool_registry_seed.json`, rendered for
humans at `/reference/tools` and filtered to the `approved` subset for agents at
`GET /api/v1/references/tools?status=approved`.

* **`status`** is a *policy* fact: may an agent run it.
* **`ingestible`** is an *engineering* fact: does a parser exist for its output.

They are deliberately independent — fusing them would either block approval
until someone writes a parser, or approve tools whose upload then fails.

Seeding is **additive**: an operator's approval decision or edited description
survives a redeploy. A correction to a shipped seed row therefore needs a
migration, not a seed edit (see `c9a4e70b5d18`).

An agent that needs something outside the set calls `suggest_tool`; the row lands
as `suggested` (which no rule reads) and an admin vets it from the Tool Reference
page. Declining keeps the row, so the next agent that asks gets the same answer.

---

## 6. Guardrails, and what the server can't do

An agent may run a command **without waiting for approval** when three things
hold: the tool is approved, the target is a host already in the inventory, and
every file it writes lands in the session's working directory. Everything else
stops and asks. The command is shown either way.

**BlueStick cannot enforce any of this.** The commands run on the operator's
machine and the server sees only what the agent reports. The real boundary is
the client's sandbox — `codex --sandbox workspace-write --ask-for-approval
on-request`, or Claude Code's default prompting — and the session dialogs hand
the operator those flags for the two workflows that execute things.

What the server contributes is the record, and one requirement: **every
workflow's prompt opens with a mandatory read-back**, where the agent states the
bounds of the session in its own words before its first call. That is the one
moment a human sees the agent's *understanding* rather than its output, and it
makes the agent's own words part of the audit trail.

---

## 7. Reviewing what happened

* **`/assist-sessions`** — every assist session in the project, what it was
  allowed to do, and what it produced (notes first — they are the durable
  output; the API feed is the read trail).
* **Agent API activity** — per plan, recon session, and assist session.
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
| `GET /api/v1/references/tools` | none | the tool registry (`?status=approved`) |
| `PATCH /api/v1/references/tools/{name}` | admin | vet a suggested tool |
| `GET /api/v1/agent/identity` | agent key | what this key is |
| `POST /api/v1/agent/tool-suggestions` | agent key | record a tool request |
| `GET /api/v1/agents-guide?workflow=…` | none | AGENTS.md, sliced |
