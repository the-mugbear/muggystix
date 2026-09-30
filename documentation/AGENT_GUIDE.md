# BlueStick AI Agent Guide

**Prompt version:** 3.0.0 · **Verified against:** backend 2.433.0 (2026-09-29)

> **Version & compatibility (read this).** The number that matters is the **Prompt version** above — stamped live from the running deployment when this guide is fetched, and identical to the `prompt_version` in your instructions block (echoed on every `/context` response). If the two **match**, your prompt and this guide are the same contract — proceed; if they **differ**, the deployment changed mid-session, so **re-fetch this guide and prefer it**. Ignore the "Verified against backend X" stamp for compatibility — it's a different numbering scheme and won't equal the Prompt version.

You are an AI assistant (Claude Code, Codex, ChatGPT, etc.) assigned to a workflow in BlueStick. This file is the entire surface you are authorized to use. Follow it literally — the surrounding scaffolding (your operator's project role checked on every call, the project's declared scope, the audit trail) depends on you behaving as described.

**The operator drives; you do the work.** You act as the operator who started your session, with their project permissions. You scan, upload, register a test plan, execute it and record what you found — in whatever order the work needs. Nothing waits on a separate human approval: there is no approved-tool list, no plan approval and no mandatory sanity check (all retired in v2.433.0). What you owe the operator is in [§ Safety rules](#safety-rules-mandatory): show every command, propose rather than act unasked, stay in the declared scope, keep to the working directory, and record everything.

Everything below is reachable through one auth mechanism: the API key the user pasted to you. Do not attempt to log in, reach admin surfaces, mint new keys, create new agents, or touch endpoints outside `/api/v1/agent/*`. The only other surfaces that are yours are the public reference ones this guide sends you to — `/.well-known/networkmapper.json`, `/api/v1/agents-guide`, `/api/v1/references/*`, and `/api/v1/mcp` (the same agent endpoints as MCP tools, authenticated by the same key). Everything else is not available to you and will return 401/403.

One exception, and it matters: you **can** extend your own key's deadline via `POST /api/v1/agent/session/renew`. That is renewal, not rotation — same key, later expiry — and it is how you survive a long-running scan outliving your credential. See **If your key expires** below.

Your key carries the permissions of the operator who started your session, re-checked on every call. If you get a 403 saying the key is read-only or the operator has left the project, that is not a bug and retrying will not help — tell the user.

> **Context optimization:** a unified project session receives this full guide. You may request `?workflow=plan_generation|execution|reconnaissance|assist` for a focused phase reference, but the key is not restricted to that phase.

---

<!-- agents:section tags="shared" -->

## Instance Identity (verify once before acting)

BlueStick publishes its identity at an unauthenticated well-known URI so you can verify that the host you're being asked to curl is the same instance that generated your prompt.

```
curl -sk https://<host>/.well-known/networkmapper.json
```

The response contains `instance_id`, `name`, `version`, `purpose`, and a `safety_properties` block. Your instructions block includes the `instance_id` the prompt was generated with — cross-check the two values match **once** at the start of your session. If they match, the session is trusted for the duration of your API key; you do not need to re-check on every request. If they do not match, stop and alert the user — the prompt may have been tampered with, copied from a different instance, or served by an unrelated host.

You can also read `safety_properties`. It states the model BlueStick operates under and — since v2.371.0 — is explicit about which parts the server enforces and which are yours:

Enforced by the server:
- `server_executes_commands: false` — BlueStick is a coordinator. It never runs a command; everything runs on the operator's machine.
- `agent_authority: "operator_project_role"` — your key may do exactly what your operator's project role allows, re-checked on every request.
- `agent_key_binding: "project_session"` — your key is bound to one project session and one operator. (Not to a single plan or scope — that was the pre-v2.337.0 model.)
- `agent_keys_time_limited` / `agent_keys_renewable` — your key expires (24 h by default); you can renew it yourself while the session is under its lifetime cap. Ending the session, not expiry, is what revokes it.
- `audit_trail_persistent: true` — every `/agent/*` request you make is recorded and shown to the operator.

Yours to uphold — the server cannot see your terminal:
- `command_approval: "operator_driven"` — the operator drives: you show every command, and a target outside the declared scope, anything outside the working directory, or a change to the operator's machine waits for their explicit go-ahead (see [§ Safety rules](#safety-rules-mandatory)). There is no approved-tool allowlist.
- `command_approval_enforced_by: "agent_and_client_sandbox"` — holding to those bounds is your discipline plus your client's sandbox. The server contributes the record of what you report and the read-back of your bounds; it cannot stop a command.

If a deployment still publishes `"plan_execution_requires_human_approval": true` or `command_approval: "by_exception"`, it predates v2.433.0; `all_commands_require_user_approval`, `no_autonomous_execution` or `agent_keys_scope_bound` predate v2.371.0 — follow this guide, not those flags.

## Quick Start

The user will give you an **API key** and an **instructions block** copied from the BlueStick UI. If you don't have one, ask the user to start an agent session from the BlueStick UI — **Operations → Start Agent Session** — and paste you what it produces. Pages about one object (a scope, a plan, a host selection) open the same dialog with a one-line task to hand you, such as `Work test plan #12 in BlueStick.`; the key is the same kind either way: one session for the project. You open whatever work you need yourself (`POST /agent/uploads`, `POST /agent/test-plans`, `POST /agent/execution-sessions/start`).

### Authentication

Every request to `/api/v1/agent/*` carries your API key in a header:

```
X-API-Key: nm_agent_abc123...
```

No login, no password, no `project_id` in the URL — the key is bound to exactly one project session, and every read and write auto-scopes to that project. Which execution run a call is about is resolved from the session (pass the run id when the session has more than one open). A 403 means your operator's project role does not permit that action.

> Both `X-API-Key: nm_agent_...` and `Authorization: Bearer nm_agent_...` are accepted. Prefer `X-API-Key`.

If your key stops working, read the 401 body before doing anything else. It tells you which case you are in:

* `recoverable: true` — your key expired but the session is still open. `POST` to the `renew_path` in that body **with the same key**, then **retry the exact request that failed**. Do not re-run a scan or command whose output you are already holding, and do not ask the user for a new key.
* `recoverable: false` — the session ended or passed its maximum lifetime. Save anything you are holding to a file in your working directory, then ask the user to start a new session.

A 403 is different: it means your key is valid but not allowed to do that. Do not retry it.

### Ending the session (MANDATORY last step)

Your session does not end on its own. Execution runs have their own `/complete`; the **session** ends only when you call `POST /api/v1/agent/session/end` (MCP `end_session`), the operator ends it from Agent Activity, or it lapses days later. Until one of those happens the operator's Agent Activity page shows it as running. However the session ends, any execution run it still has open (active or paused) is marked `abandoned`, with its recorded results kept; a later session opens a fresh run on the same plan and continues from them.

End it only when the operator tells you they are finished. Finishing a task is not that: report what you did and wait for the next instruction. A session opened with no task yet is waiting for one, not done — after the setup steps, say you are ready and wait. Ending early revokes your key, and the operator's next question fails with a `401` they cannot recover from without starting a new session.

When the operator says they are finished:

1. Close every execution run you opened — `POST /agent/execution-sessions/{id}/complete`. `/session/end` refuses with `409` while any is open and names the ids.
2. If you have filed no feedback in this session yet, file it now (`POST /agent/feedback` / `submit_feedback`). Feedback belongs at the moment of friction (see below), so by this point there is usually nothing left to add.
3. `POST /agent/session/end` with a line of `notes` (and `agent_model` — see [§ Attribution](#attribution--what-the-record-says-about-you)). It revokes your key; nothing you call afterwards authenticates, so it is the last call.

### Feedback — file it when the friction happens

**The trigger is an event, not the end of the session.** The moment you retry a call, guess at a field, work around a tool, or go back to this guide to make something work, file a short critique right then — `POST /agent/feedback` (MCP `submit_feedback`), one line per item, naming the endpoint or tool, what you expected, what happened, and the exact error or missing field. Several small submissions during a session are the norm. Most sessions never reach a tidy ending — the terminal closes, the operator moves on — so feedback saved for the end is feedback that is never filed.

The checkpoint, if you filed nothing along the way: `/execution-sessions/{id}/complete` answers with `feedback_recorded`; when it is `false`, file before you go on. The session end is the last resort, not the plan. Payload shape: `## Feedback Requested` block at the end of your session prompt.

### Long-running commands — never block a single tool call on one

Your client has its own tool timeout. A scan that outlives it ends *your process* while the scan keeps running: the output is orphaned, any open run never gets its `/complete`, and the operator is left with a session that looks alive but has no agent behind it. Run anything that may take more than a minute or two in the background from the working directory, capture its PID at launch (`nmap … & echo $!`), and poll **that PID** — see *Working directory & concurrent agents*. Upload each output file as it finishes rather than holding everything for one upload at the end; an upload that already landed is answered `409 duplicate_scan`, which is safe.

If the operator resumes a session a previous agent process died in, your prompt carries a `⟳ RESUMED SESSION` notice: the previous key is revoked, and the working directory may hold output that never got uploaded. Look there first.

### MCP (optional — same endpoints, native tools)

If your client speaks MCP, the operator can hand you the same session as a set of **tools** instead of curl recipes; the session-start dialog emits the config for VS Code, Claude Code and Codex. Every tool call loops back into the endpoints described in this guide with the same key and the same checks, so nothing here changes — you just stop shelling out for the interactive calls.

Two things to know:

- **You see the whole catalogue.** One session does every kind of work, so `tools/list` is not filtered (v2.337.0). A tool being listed does not mean a call will succeed: the endpoint behind it decides on every call — by your operator's project role, and by whether you have the run open (recon and execution tools need one). `agent_identity` tells you what you may write, which runs you have open (`open_phases`), and when your key expires.
- **Bulk data is not a tool.** The NDJSON streams, target-file downloads and the upload itself (`POST /agent/uploads`) stay curl on purpose — they belong in a file on disk, not in your context. `GET /api/v1/references/mcp-tools` lists everything the server exposes.

### HTTPS / self-signed certs

The API uses HTTPS with a self-signed certificate. All `curl` commands require `-sk` (silent + insecure) to skip verification. If your execution environment blocks localhost/HTTPS, request approval once for the first `curl` call and continue automatically after approval. If that isn't possible, ask the user to run the commands for you or to provide an alternate reachable URL.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Attribution — what the record says about you

BlueStick does not inspect the operator's machine and does not choose your tools or commands: which tools are installed and which command fits this host is yours to work out with the operator. What the server records is who did the work, from three sources:

| Recorded | Source |
|---|---|
| **Client** (the harness — `generated_by_tool`) | Over MCP, the `initialize` handshake's `clientInfo` (name and version, e.g. `claude-code 2.1.0`), when the handshake carries your key. A curl agent: the first call's `User-Agent` (e.g. `curl/8.5.0`); a handshake name replaces it, never the other way round. You send nothing for this. |
| **Prompt version** | Set by the server when the session starts or is resumed — the version of the instructions you were given. You send nothing for this. |
| **Model** (`generated_by_model`) | Your own report, the one thing no protocol carries. Pass the optional `agent_model` (e.g. `"claude-opus-5-5"`) on `POST /agent/test-plans` (MCP `create_test_plan`), `POST /agent/execution-sessions/start` (`start_execution`) and `POST /agent/session/end` (`end_session`). |

The session keeps the last model reported; a test plan and an execution run each take a snapshot of the session's attribution (client, model, prompt version) when they are created. **Pass `agent_model` on those three calls** — a plan or run registered without it is recorded with no model.

### Plans describe intent — you translate at execution time

A plan entry's `proposed_tests` may include a sample command, but treat the description and `expected_evidence` as authoritative; pick the command that fits the operator's machine. Two examples of the same intent, two valid translations:

| Intent | Kali Linux (recon from Kali, executing from Kali) | Windows + RemoteSigned, no Python, no WSL |
|---|---|---|
| Enumerate SMB sessions on 10.0.0.5 | `enum4linux -S 10.0.0.5` | `powershell -Command "Get-SmbSession -CimSession 10.0.0.5"` |
| DNS reverse lookup for 10.0.0.5 | `dig -x 10.0.0.5` | `nslookup 10.0.0.5` |
| List listening ports on the local host | `ss -tlnp` | `powershell -Command "Get-NetTCPConnection -State Listen \| Format-Table"` |

When you record the test result, put the *actual command you ran* in `command_run` so a reviewer can correlate the plan's intent with what happened on this operator's machine. The audit trail is then full: BlueStick has the inbound API calls, the session's attribution, and the agent-reported `command_run` per test.

### What the user sees

Every `/api/v1/agent/*` request is recorded and surfaced to the operator under "Agent API activity" (the test plan's API-calls tab for calls about a plan, your agent session's page otherwise), filterable by host, target IP, and status code. Don't try to obscure activity by routing around the API — you'd only be visible-but-suspicious instead of visible-and-correct. Operate transparently.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Safety rules (MANDATORY)

These are the rules your session prompt carries, in the same order. They protect the client's network, the operator's machine and the record; nothing else stands between you and the work.

1. **Show the operator every command before you run it.** The exact invocation, every time — routine scans included.
2. **The operator drives.** Do what they ask within their project role, and propose next steps rather than taking them unasked. "I'd follow this with an SMB sweep of the 14 hosts with 445 open — shall I?" is right; starting that sweep because it seemed like the obvious next step is not.
3. **Stay inside the project's declared scope.** A target outside it — an address outside the scope's ranges, or a name no declared domain covers — needs the operator's explicit go-ahead first. A name being in scope does not put the address it resolves to in scope (see [§ Name scope](#name-scope--what-a-declared-domain-does-and-does-not-mean)). Read the scopes with `GET /agent/scopes` (MCP `assist_list_scopes`); if the project declares none, ask the operator what you may touch before touching anything.
4. **Write output into the working directory you were started in.** Reading or writing outside it, installing software, or changing machine settings or credentials needs the operator's explicit go-ahead first.
5. **Record every command and its outcome** (executed, skipped, or failed) as you go, verbatim and including where its output was written, and upload scanner output so it lands in the inventory.

There is no list of tools you may or may not run: `GET /api/v1/references/tools` (MCP `list_tools`) is a catalogue — what each tool is for, its ports, whether it is intrusive, whether BlueStick parses its output — not a permission list. A tool flagged `intrusive: true` (exploit checks, brute force, heavy crawling) is worth naming as such when you show the command, so the operator weighs it knowingly. If you used or needed a tool the catalogue lacks, propose it with `suggest_tool` (`POST /agent/tool-suggestions`); that is catalogue intake and grants or withholds nothing.

**Do not expect BlueStick to stop you.** The commands run on the operator's machine; the server sees only what you report. The real boundary is your client's sandbox — the operator was given the flags that set it when they started this session. Report accurately, including when you went outside the bounds and why: the audit trail is the thing a human actually reviews.

## Say the rules back before you start (MANDATORY)

Your **first message** to the operator states the bounds of this session in your own words: which project you are in and as whom, the scope you will work within, where anything you produce will go, and what you will ask about before acting. A few lines. Then start — you are not asking permission to begin; you are giving them the chance to say "that's the wrong scope".

Do not paste the rules verbatim. A recital is something you can produce without having read anything, and it gives the operator nothing to check. Restating means resolving the rules against *this* session — this directory, these CIDRs, this project — which is the part that can be wrong. Call `agent_identity` and `assist_list_scopes` (or `GET /agent/identity` and `GET /agent/scopes`) for the specifics rather than guessing at them. Before scanning a scope, read its CIDRs and names (`GET /agent/scopes/{scope_id}/subnets` and `/domains`, MCP `scope_list_subnets` / `scope_list_domains`) and state them. Opening an execution run returns a `read_back` with that run's concrete bounds (the plan's hosts); state those too, when you open one.

A scanning read-back:

> I'm working scope **acme-dmz** (`10.10.0.0/24`, `10.10.4.0/24`; names: `portal.acme.com` exactly and anything under `*.lab.acme.com`). Everything runs from `./networkmapper-acme-dmz` and every output file lands there. I'll show you each command as I go. I'll ask before touching an address outside those two ranges or a name those domains don't cover, writing anywhere but that folder, or installing or changing anything on your machine. An address one of those names resolves to isn't in scope just because the name is.

A planning read-back — no commands yet, so it's about data:

> I'm writing test plan #8 for **acme-internal** against the 14 hosts you have in review. I'll read their ports and findings and register the tests I intend to run; nothing runs until you tell me to work the plan, and then I'll show you each command from `./networkmapper-acme-execution-<run>`.

Why this is a rule and not a nicety: **BlueStick cannot enforce the bounds.** Commands run on the operator's machine and the server sees only what you report. The read-back is the one moment a human sees your *understanding* of the bounds rather than your output — a wrong scope costs a sentence to fix there and a great deal more after the scan. It also makes your own words the record: an agent that said it would write to one directory and then wrote elsewhere has visibly contradicted itself, which an operator notices.

If the operator corrects you, take the correction as binding and say what changed before continuing.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance,execution" -->

## Working directory & concurrent agents (MANDATORY)

One operator often runs **two agentic workflows at once** (e.g. one agent scanning a scope while another works a test plan). BlueStick isolates those server-side (per-session key + audit trail; the ingestion worker serializes uploads safely), but the **operator's machine is shared** — two agent processes see the same filesystem and process table, and nothing isolates them there unless you do.

**Before running any tool, create a session-scoped working directory
and `cd` into it:**

```
mkdir -p networkmapper-<project_slug>-<workflow>-<session_id>
   # e.g. networkmapper-homenetwork-recon-42
cd networkmapper-<project_slug>-<workflow>-<session_id>
```

Build the name yourself: the project slug from `GET /agent/project`, the kind of work, and the run id that `/execution-sessions/start` returned — or your `session_id` (from `GET /agent/identity`) when you scan a scope or work without a run. Neither your prompt nor the read-back names the directory for you — you choose it, and your read-back states it. The qualified path self-documents which project a folder belongs to and survives a Nuclear-Clean reset (session ids restart at 1) without colliding with leftover folders.

Run **every** tool from inside this directory; every output file, target list, and result directory (`nmap.xml`, `httpx.jsonl`, `targets.txt`, `eyewitness-results/`, …) lives here.

Why this matters: two agents both writing `targets.txt`/`nmap.xml` into a shared cwd silently overwrite each other, and `pgrep nmap` can't tell your scan from another agent's. Because every command runs from `networkmapper-<project>-recon-42/`, that path is in each process's argv — `ps aux | grep networkmapper-homenetwork-recon-42` matches **only your session's** processes. Never identify a process by tool name alone when other agents may be running. **For a backgrounded scan, capture its PID at launch** (`nmap … & echo $!`) and poll *that PID*, not the name.

Do not delete the directory when you finish — the operator may want the raw tool output. Cleanup is their call.

### The working directory and the scope are the bounds

That directory is not just for tidiness — with the declared scope, it is where "go ahead" stops (safety rules 3 and 4):

1. **The output lands here.** Every file a command writes goes into this working directory. `-oX nmap.xml`, not `-oX /tmp/nmap.xml`; no writes to a home directory, a system path, or another session's folder.
2. **The target is in the declared scope.** An address inside the scope's subnets, or a name a declared domain covers — exactly, or as a subdomain of an `include_subdomains` entry (`GET /agent/scopes/{id}/domains`, or a `names[]` entry on a host you read). Resolving or probing any other name, or touching an address outside the ranges, waits for the operator's explicit go-ahead. And the address a name resolves to is **not** thereby in scope: `portal.acme.com` in scope + resolves to `203.0.113.7` outside every CIDR = you may probe the name; you may not sweep the address's neighbours or treat `203.0.113.7` as subnet-in-scope.

**Outside those bounds, ask first.** Reading or writing outside this directory, installing software, changing settings or credentials, a target outside the scope — present it, explain why you need it, and wait for an explicit yes.

Inside them you still show every command (rule 1) and still work on what the operator asked for (rule 2): being in bounds makes a command permissible, not requested.

<!-- agents:end -->

---

<!-- agents:section tags="plan_generation" -->

## Workflow A — Build a Test Plan

A test plan is **the record of what you set out to test** — and, once you work it (Workflow B), of what you ran and found. You register it and fill it with structured test entries; nothing waits on a human approving it (the approval step was retired in v2.433.0). The operator reads the plan, and it is what a reader of the engagement sees later, so write it for them. When the operator hands you an existing plan (`Work test plan #12`), skip to Workflow B; when they ask for a new one, open it yourself (step 0).

```bash
# 0. Register the plan.
POST /agent/test-plans   {"title": "..."}        # optional: description, filter_criteria
#    → 201 { id, ... }   use that id as {plan_id} below.   (MCP: create_test_plan)
#    To plan an EXACT set of hosts, add "host_ids": [..] OR "q": "<host query>"
#    (e.g. "follow:in_review OR assigned:me") — resolved to its matching hosts
#    NOW, so /context offers only those. 422 names unknown ids / an empty match.

# 1. Review candidate hosts (services, vulnerabilities, port data).
#    /context is PAGINATED — at most `limit` hosts per call (default 500).
#    Page until you've seen every candidate; has_more: true means
#    "fetch the next page", NOT "the rest are off-limits".
GET /agent/test-plans/{plan_id}/context
#    → while summary.has_more is true:
GET /agent/test-plans/{plan_id}/context?after_host_id={summary.next_cursor}

# 2. Set the plan description — scope, prioritisation, methodology; it is what a
#    reader of the plan sees first
PATCH /agent/test-plans/{plan_id}
{"description": "🤖 **Agent-generated** — {agent_name}\n\nScope, methodology, and prioritization summary..."}

# 3. Add structured test entries for candidate hosts (≤500 per call —
#    POST in multiple batches if you selected more than 500 hosts)
POST /agent/test-plans/{plan_id}/entries
{"entries": [{"host_id": ..., "priority": "...", "test_phase": "...",
              "proposed_tests": [...], "rationale": "..."}, ...]}
#    All FIVE are required — a missing `rationale` is a 422. Optional: notes,
#    target_fqdn. /context hands you ready-made `entry_template`,
#    `entry_batch_example` and `entry_schema`; copy those, do not guess the shape.
#    If /context carries a `source` block (kind: "manual_hosts"), the plan
#    targets a FIXED host list — the operator's pick on the Hosts page, or the
#    host_ids / q it was created with (the description records which):
#    `candidate_hosts` IS the permitted set. The server does not stop you
#    adding other host ids — do not.

# 4. Check the plan for gaps (advice, not a gate — nothing is refused)
GET /agent/test-plans/{plan_id}/validate
```

The plan stays `draft` until its first execution run moves it to `in_progress`; there is no submit step. Lifecycle: `draft` → `in_progress` → `completed`, or `archived` by the operator at any point.

**Control flow:** After step 1, report a brief summary to the user (e.g. "Found 36 actionable hosts, 12 with critical vulnerabilities"), then **continue** through steps 2-4 without waiting — writing the plan runs nothing. Fix what validate warns about where you can. Then tell the operator what the plan holds (e.g. "Plan #8: 28 entries, 9 critical") and propose working it; open the execution run (Workflow B) when they say so.

**Selection policy:** Create entries for all hosts with `meets_policy: true` across **every page** of the context response (page with `after_host_id` until `summary.has_more` is false — don't stop at the first 500). The policy: all critical/high-vuln hosts are included; medium-vuln hosts qualify if they expose multiple services or high-value ports (SMB, RDP, databases). Hosts with zero open ports are excluded from context by default. The summary includes `policy_match_count` (hosts you should create entries for) vs `candidates_reviewed` (total hosts returned for context). `candidates_reviewed` and `policy_match_count` count the **current page** — accumulate them yourself as you page.

### Entry-generation rubric

> **Build on recon — these hosts are already scanned.** Every candidate's open ports, services, and versions are in `candidate_hosts[].ports` / `.services` (recon records a service-version scan for every live host). Each entry must be **targeted validation/exploitation against the KNOWN open ports**, not rediscovery. Do **not** propose discovery or service-version scans (`nmap -sn`, `nmap -sV`, `--top-ports …`, `-p-`) as tests — recon already ran them, so they're wasted work and IDS noise, not a test. The `nmap --script` entries below mean a **specific named NSE script** against the known port (e.g. `--script ssl-enum-ciphers -p 443`), never a port/version sweep.

Map observed data to `priority`, `test_phase`, and tools:

| Condition | priority | test_phase | Recommended tools (targeted at the known port) |
|-----------|----------|------------|-------------------|
| Critical vuln (confirmed RCE/SQLi/auth bypass) | `critical` | `exploitation` | `nuclei -t cves/<cve-id>`, named `nmap --script <vuln>`, `curl`, exploit-specific tools |
| High vuln (TLS weakness, known CVE without confirmed exploit) | `high` | `enumeration` | `testssl.sh`, `nuclei`, `nikto`, named `nmap --script vuln` |
| Web services (HTTP/HTTPS) | `high` | `enumeration` | `whatweb`, `gobuster`/`ffuf` (content discovery on the known web port), `nuclei -t exposures/`, `nikto` |
| SMB/file shares (445, 139) | `medium` | `enumeration` | `netexec smb`, `smbclient`, `enum4linux` |
| Remote access (SSH, RDP, VNC) | `medium` | `enumeration` | `netexec ssh/winrm/rdp`, service clients (named `nmap --script` only as fallback) |
| Databases (MySQL, MSSQL, PostgreSQL, etc.) | `medium` | `enumeration` | `netexec mssql`, service-specific clients (named `nmap --script` only as fallback) |
| Multiple services, no vulns | `low` | `enumeration` | Targeted default-cred / config checks on the *identified* products (`netexec`, `nuclei -t default-logins/`); skip the host if there is nothing to validate — do **not** add a generic `nmap -sV` re-scan |

Use the highest-severity condition that applies. Always include `{ip}` placeholder in commands, and scope each command to the host's already-known open port(s).

**Tool notes:**
- **`nuclei`** — template-based scanner for CVE validation, exposed panels, default configs, and tech fingerprinting. Use `-t cves/<cve-id>` for confirmed-vuln validation, `-t exposures/` on web surfaces, `-t technologies/` for breadth. Honors rate limits; prefer `-rl 50` in shared environments.
- **`testssl.sh`** — TLS weakness validation (cipher strength, protocol downgrades, cert chain, HSTS). Use whenever the context surfaces TLS findings or the port is 443/8443/993/465/etc. Non-intrusive.
- **`netexec`** (formerly crackmapexec) — modern successor for SMB/WinRM/RDP/MSSQL/SSH enumeration. Supports null-session checks (`--shares -u '' -p ''`), default-credential sweeps (restrict to sanctioned wordlists only), and remote command execution for sanctioned testing. **Credentials come from the operator:** a credential-bearing run uses only what they gave you, against the hosts they named — say so in the plan entry.

<!-- agents:end -->

---

<!-- agents:section tags="execution" -->

## Workflow B — Execute a Plan

You work a plan — one you registered, or one the operator points you at — while it is `draft` or `in_progress`; nothing waits on a human approving it (v2.433.0). The first execution run moves a draft to `in_progress`. You run the plan's tests from the working directory, showing the operator every command, and record each command, its output and what it found.

> **Key principle:** The operator drives; the operator's terminal is the executor. Show each command, run it (if you have shell access) or ask the user to run it, then record the result verbatim. Work the plan when the operator asked you to; when a result points somewhere the plan did not (a new host, a follow-up exploit), propose it rather than taking it.

### Execution flow

```bash
# 0. Open the execution run. WITHOUT an open run, every call below — starting
#    with execution-context — answers 409 "No active execution run for this plan"
#    (execution-progress answers 400 "No active execution session").
#    (If a run is already open — after a resume, GET /agent/identity lists it
#    under open_phases — calling start again is harmless: a run THIS session
#    already has open on the plan is reused.)
POST /agent/execution-sessions/start   {"plan_id": N}      # MCP: start_execution
#    → 201: the execution context, plus `read_back` — the concrete bounds of this
#      run (the plan's hosts, and a reminder to state the working directory you
#      chose — the server does not name it). State it before you act.
#    → 404 unknown plan · 409 the plan is completed/archived, or has no entries
#    One run per plan is active at a time: opening yours PAUSES any other
#    session's active run on the same plan. Do not open a plan someone else is
#    executing unless the operator told you to take it over.

# 1. Fetch execution context — hosts, tests, known services.
#    Commands have {ip} resolved to actual IPs.
GET /agent/test-plans/{plan_id}/execution-context

# 2. For each host (in priority order: critical → low):

#    2a. OPTIONAL target check — evidence that you reached the host you meant to.
#        Worth it when the target could be ambiguous (a name behind a load
#        balancer, a reassigned address): dig -x {ip}, nc -w 3 {ip} {known_port},
#        your source IP. Record: POST .../entries/{entry_id}/sanity-check
#        Nothing is refused without one. A FAILED check is worth telling the
#        operator about before testing that host further.

#    2b. For each test in the entry:
#        SHOW the command to the user, always:
#          "Tool: nmap  Command: nmap --script smb-enum-shares -p 445 -oX smb.xml 10.0.1.5
#           Expected: List of shares with access levels."
#        Inside the bounds (target in the declared scope, output in the working
#        directory — see "The working directory and the scope are the bounds"):
#        run it, and say that you are.
#        Outside them: add "Shall I run this? [yes / modify / skip / abort]"
#        and WAIT for an explicit yes.
#        After execution, record:
#        POST /agent/test-plans/{plan_id}/entries/{entry_id}/test-results
#        {
#          "test_index": 0,
#          "status": "executed",
#          "command_run": "nmap --script smb-enum-shares -p 445 10.0.1.5",
#          "raw_output": "Host script results: ...",
#          "findings_summary": "Anonymous read access to ADMIN$ share.",
#          "severity": "critical",
#          "is_finding": true
#        }

#    2c. Complete the entry after EVERY proposed test has a terminal result row
#        (executed/skipped/failed/not_applicable). If some weren't run, either
#        record them (skipped/not_applicable) or pass no_tests_run_reason.
POST /agent/test-plans/{plan_id}/entries/{entry_id}/complete
{"findings_summary": "Host has critical SMB misconfiguration.", "overall_status": "completed"}

# 3. Check progress at any time:
GET /agent/test-plans/{plan_id}/execution-progress
```

### Resuming an interrupted session

If your host crashed or the agent stopped mid-execution, the work is **resumed**, not restarted. The operator clicks **Resume** on the interrupted agent session in BlueStick (Agent Sessions, or the session's own page); that rotates the session's API key and hands you a new instructions block carrying a `⟳ RESUMED SESSION` notice. The session and its open runs are unchanged — `GET /agent/identity` lists the run under `open_phases`.

When your instructions carry that notice:

- **Fetch `/execution-context` before doing anything else.** It reports each entry's `entry_status` and each test's `result_status`. Any entry already `completed`, and any test already `executed` or `skipped`, is **done** — do not re-run it.
- **Do not repeat a target check** a host's entry already recorded (`sanity_check_passed` on the context).
- Resume at the first host/entry with outstanding work and continue the normal flow.
- Every prior result is intact and must not be overwritten.

The session id is unchanged, so your results append to the same audit trail. To keep that trail readable for the human reviewer — and to make any future resume cleaner — post a real `findings_summary` on each entry `/complete` **as you finish each host**, not only at the end.

### Safety during execution

The [§ Safety rules](#safety-rules-mandatory) apply as everywhere; in execution they come down to:

1. **Every command shown (terminal layer).** Show each command before it runs. Inside the bounds — target in the declared scope, output in your working directory — you run it and say that you did; outside them it waits for `yes/modify/skip/abort`. For agents with shell access (Claude Code, Codex), the client's tool-use prompt is where the operator sees it. For agents without shell access (ChatGPT), the user runs commands themselves and pastes you the output.

2. **The right target (network layer).** A plan's hosts come from the inventory, but an address can be reassigned and a name can resolve somewhere new. Where that is a real risk, run a target check before testing and record it as evidence (optional since v2.433.0 — nothing is refused without one):
   - Report your own source IP, default gateway, and DNS server
   - Run `dig -x {ip}` and compare to the expected hostname
   - Banner-grab a known open port and compare to the service BlueStick recorded
   - This is single-port *verification* of already-recorded data (a `dig -x` + one `nc` banner-grab), **not** a port sweep or re-scan
   - If a check fails, stop and ask the user — do **not** keep testing a potentially wrong target

3. **Audit trail (API layer).** Every test attempt is recorded with timestamps and the command you actually ran, any target check you ran is logged beside it, and the execution run tracks overall progress. Results from interrupted or abandoned runs are preserved with the run's terminal status.

### Test result status values

| Status | Meaning |
|--------|---------|
| `pending` | Test exists but hasn't been attempted |
| `pending_approval` | You showed the command and are waiting for the operator's go-ahead |
| `executed` | Command was run and output recorded |
| `skipped` | User chose to skip this test |
| `failed` | Command failed to execute (network error, tool crash, etc.) |
| `not_applicable` | Test doesn't apply to this host on closer inspection |

`executed`, `skipped`, `failed`, and `not_applicable` are **terminal**. `pending` and `pending_approval` are **not** — a result row left in either state blocks entry completion (see below); resolve every recorded result to a terminal status before calling `/complete`.

### Entry completion gates

`POST /agent/test-plans/{id}/entries/{eid}/complete` enforces three gates and returns `400` if any fails:

1. **Per-test coverage.** For an entry with N proposed tests, **every** `test_index` 0..N-1 must have a result row — not just one. Record a terminal result for tests you don't run too (`skipped` / `failed` / `not_applicable`). Recording 1 of 3 and completing without a reason returns a 400 naming the missing indices.
2. **Terminal results.** No result row may be left `pending` / `pending_approval`.
3. **Empty entry.** An entry with zero proposed tests (or one you're closing before covering every test) must pass an explicit `no_tests_run_reason` in the complete payload (e.g. "host went offline before testing", "reclassified out of scope mid-run"). It is written to the audit log, so give a reason the operator would accept.

These are the only gates. There is **no sanity-check gate** (retired in v2.433.0): a result or a completion is never refused for lacking a target check, and `sanity_override_reason` / `override_reason` no longer exist (sent over REST they are ignored; the MCP tools no longer take them). The completion response and the entry's `results_data` carry `sanity_checks_passed` — the number of passing target checks you recorded — so a reader can see whether the target was verified.

### Target check methods (`POST .../sanity-check`)

| Method | What it checks |
|--------|---------------|
| `network_context` | Your source IP, default gateway, DNS server |
| `reverse_dns` | `dig -x {ip}` matches expected hostname |
| `banner_grab` | Service banner on a known-open port matches BlueStick data |
| `ping` | Host is reachable (optional — host may block ICMP) |

### Execution context response shape

`GET /execution-context` returns everything needed to work the plan:

- `plan` — plan id, title, status, entry count
- `session_id` — the active execution session
- `agent_name` — for attribution in findings
- `prompt_version` — the live prompt version; if it differs from your instructions block, re-fetch this guide
- `read_back` — on `POST /agent/execution-sessions/start` only (null on `/execution-context`): the per-host bounds to state before testing
- `hosts[]` — one per entry, sorted by priority (critical first):
  - `entry_id`, `host_id`, `ip_address`, `hostname`, `os_name`
  - `target_fqdn` — the named endpoint the entry targets, or null for the bare address. Commands carry `{fqdn}` resolved to it. When you record a result for such an entry, include `observed_ip` (the address the command actually reached) — a name behind a load balancer may resolve differently at run time, and the evidence must reference the real binding
  - `priority`, `test_phase`, `entry_status`
  - `sanity_check_passed` — null if no target check was recorded (it is optional), true/false after
  - `tests[]` — each proposed test with `{ip}` resolved in commands, plus `result_status` (null if not yet recorded)
  - `known_services[]` — open ports with service name, product, version. **Authoritative open-port/service set** (recon already discovered these): target tests at these ports and do not re-enumerate/re-scan. (Also what a target-check banner-grab compares against.)

### Raw output storage

`raw_output` in test results is capped to the configured `TEST_OUTPUT_MAX_BYTES` (default 100KB). Output exceeding the cap is truncated with a `--- OUTPUT TRUNCATED ---` marker. If you need to record verbose tool output (Nessus, nmap scripts), trim it to the most relevant sections before sending.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance" -->
## Workflow C — Populate Host Data (read a scope, scan, upload)

Recon runs were removed in v2.433.0. You read a scope, run your own tools
against it from your working directory, and upload the output to your session
as results land. No run to open, no phase to close.

### The flow

```
# 1. Pick a scope.
GET /agent/scopes                                    (MCP: assist_list_scopes)
#   → [{ id, name, subnets: [...] }]

# 2. Read what it covers. The subnet and domain lists page for large scopes;
#    the host/target files stream — redirect them to disk, never into context.
GET /agent/scopes/{scope_id}/subnets?offset=0&limit=500   (MCP: scope_list_subnets)
GET /agent/scopes/{scope_id}/domains?offset=0&limit=500   (MCP: scope_list_domains)
GET /agent/scopes/{scope_id}/hosts.ndjson            # every in-scope host + open ports
GET /agent/scopes/{scope_id}/live-hosts.txt          # one IP per line, an -iL target file
GET /agent/scopes/{scope_id}/web-targets.txt         # http/https URLs, a -l / -f target file
GET /agent/scopes/{scope_id}/named-targets.ndjson    # every in-scope NAME: current addresses + web evidence

# 3. Run your tools from the working directory, staying inside the scope.
#    Upload each output file as it finishes — do not hold results for one
#    upload at the end.
POST /agent/uploads   (multipart: file=@out.xml, tool_name=nmap, batch=<sweep label>)
#   → { job_id, status, batch_id }   409 duplicate_scan if the file is already in.
GET  /agent/uploads/{job_id}                         (MCP: get_upload_job)
#   → poll until status is completed / failed.
```

### Scale — bound the reads, stream the hosts

A scope can hold thousands of subnet CIDRs and tens of thousands of hosts.
Keep them out of your context window:

- **Subnets and domains** page: walk `offset` in `limit`-sized pages until the
  array comes back empty (`has_more: false`).
- **Hosts and target files stream.** Redirect them to a file and process locally:

```
curl -sS -H "X-API-Key: $KEY" "$URL/api/v1/agent/scopes/$SCOPE/hosts.ndjson"    -o scope-hosts.jsonl
curl -sS -H "X-API-Key: $KEY" "$URL/api/v1/agent/scopes/$SCOPE/live-hosts.txt"   -o scope-hosts.txt
curl -sS -H "X-API-Key: $KEY" "$URL/api/v1/agent/scopes/$SCOPE/web-targets.txt"  -o web-targets.txt
curl -sS -H "X-API-Key: $KEY" "$URL/api/v1/agent/scopes/$SCOPE/named-targets.ndjson" -o named-targets.jsonl
```

- **Name scope is its own file.** `hosts.ndjson`, `live-hosts.txt` and
  `web-targets.txt` are subnet scope (IP-only). For a domain-scoped engagement
  read `named-targets.ndjson`: one record per name a scope domain rule covers —
  `{name, name_id, scope_rule: {domain, include_subdomains, match:
  exact|subdomain}, addresses: [{ip_address, record_type, last_observed,
  host_id, in_subnet_scope}], unresolved, reason, web: [{interface_id, url,
  scheme, port, ip_address, at_current_address, source, status_code, title,
  observed_at}]}`. `addresses` is what the name CURRENTLY resolves to (the
  latest A/AAAA batch). Where `in_subnet_scope` is false, the name is
  authorised on that address and the address is not: test by name (Host header
  / SNI), never the whole address, and never the other names on it. Names only
  a certificate SAN or a shared address connect to are not listed; a declared
  domain nothing has observed is listed `unresolved` with its reason.

### Uploading

- Accepts any format the ingestion pipeline supports (nmap XML, masscan, gnmap,
  nessus, openvas, eyewitness, nikto, naabu, bloodhound, netexec, …).
- **Chunk large sweeps.** A `/16` nmap XML can exceed `MAX_FILE_SIZE` (1 GB by
  default). Split the scan and send each chunk with the **same `batch` label** —
  every chunk with that label joins one batch, shown on /scans as a single row.
- **A duplicate is not a failure.** An identical file already in the project
  answers `409 duplicate_scan` naming it; the data is in — mark the file done
  and move on. Never rename or alter a file to force a re-import.
- **A busy batch** answers `503` with `Retry-After`; nothing was stored — wait
  and re-POST the same file with the same `batch`.

The parsed hosts land in the project inventory and are correlated to scopes
downstream, so a sweep that discovers an adjacent host is kept, not rejected —
staying inside the scope is your discipline, not a server boundary.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## API Reference — `/agent/*`

All paths are relative to `/api/v1`. Include `X-API-Key: nm_agent_...` on every request.

### Common endpoints (all workflows)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/project` | Project metadata |
| GET | `/agent/dashboard` | Host/port/scan/vuln counts for the project |
| GET | `/agent/hosts` | List the project's hosts. **Bare array, paginated (default 500, max 5000), no `has_more`/`total` — you MUST page; see Host list filters below** |
| GET | `/agent/hosts/{id}` | Host detail with ports — 404 if the host is not in this project |
| GET | `/agent/scans` | List the project's scans. **Newest-first, default 100 / max 500, NO offset — scans past the newest 500 are not retrievable here ** Optional filters: `tool`, `created_after`, `sort_by` (`created_at`\|`filename`\|`tool_name`), `sort_order` (`asc`\|`desc`) |
| GET | `/agent/scopes` | List scopes |
| POST | `/agent/hosts/{id}/notes` | Create a note on a host — a project write |
| GET | `/agent/hosts/{id}/notes` | List notes for a host |
| POST | `/agent/hosts/{id}/follow` | Set review status (`{"status": "watching"}`) — a project write |
| PATCH | `/agent/hosts/{id}` | Correct operator-curated host attributes (`hostname` / `os_name`) after investigation — a project write. Only these two fields; scan-derived facts (ports/services/vulns) are never editable here |
| POST | `/agent/feedback` | **File structured feedback at the moment you hit friction** (a retry, a guess, a workaround, a re-read of this guide) — several short submissions per session; `feedback_recorded: false` on a phase completion means you have filed none yet. Over MCP the tool is `submit_feedback`. Your session is attributed from your key; `source` names the kind of work (`assist` / `reconnaissance` / `plan_generation` / `in_session_execution`) and the matching `test_plan_id` / `execution_session_id` is optional context. See the `## Feedback Requested` block at the end of the session prompt for the payload shape. |
| GET | `/agent/identity` | **Who am I** — `session_id`, `can_write_project_data`, `key_expires_at` / `renew_path` / `renewable_until`, and **`open_phases`**: the execution runs this session has open (`active_execution_session_ids`) and the plans it drafted (`drafted_plan_ids`). Read it first after a resume — it is the only way to find the runs a previous key left open. |
| POST | `/agent/session/renew` | Extend your key's deadline (same key; accepts an already-expired key while the session is under its lifetime cap) |
| POST | `/agent/session/end` | **End the session — the last call you make, only when the operator says they are finished.** Revokes your key; `409` while an execution run you opened is still active (complete those first). A run left paused (another session took its plan over) is marked `abandoned` when the session ends, its results kept. Over MCP: `end_session`. Optional `notes` |
| POST | `/agent/uploads` | **Submit scanner output here** — multipart upload, any supported tool format; no run needed. Form fields: `file`, optional `tool_name`, `command_run`, `batch` (see Upload batches & duplicates), and `skip_informational=true|false` (Nessus only, v2.341.0): drop severity-0 report items instead of storing a vulnerability row each — ports are still derived from them. Omit it to follow the project's setting; do not set it on your own initiative, it is the operator's choice. `409 duplicate_scan` = already ingested |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status — only jobs this session uploaded (404 otherwise). MCP `get_upload_job` |
| POST | `/agent/tool-suggestions` | Propose a tool for BlueStick's catalogue (201) — one you used or needed that `list_tools` lacks. Catalogue intake only: it grants or withholds nothing. The response's `already_catalogued` says whether the tool was already there. |

<!-- agents:end -->

<!-- agents:section tags="plan_generation,execution" -->

### Planning and execution endpoints (any session; results go to the execution run you open)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/test-plans` | **Register a plan** — create a draft (`{title}`, optional `description`, `filter_criteria`, `host_ids` or `q` for an exact host selection, and `agent_model`); 201 returns its `id` |
| GET | `/agent/test-plans` | List the project's plans. `?mine=true` narrows to the ones this session drafted; `?status=` filters by status |
| GET | `/agent/test-plans/{id}` | Get test plan detail |
| GET | `/agent/test-plans/{id}/context` | **Planning context** — candidate hosts + enrichment in one call |
| PATCH | `/agent/test-plans/{id}` | Update test plan metadata (description, title) |
| POST | `/agent/test-plans/{id}/entries` | Batch-add entries (up to 500) |
| PATCH | `/agent/test-plans/{id}/entries/{eid}` | Update an entry |
| GET | `/agent/test-plans/{id}/validate` | Check the plan for gaps (no entries, no description, short rationales) and its candidate-host coverage — advice, not a gate |
| POST | `/agent/execution-sessions/start` | **Open an execution run** on a `draft` or `in_progress` plan (`{plan_id, agent_model?}`); 201 returns the execution context + `read_back`. The first run moves a draft to `in_progress`. Reuses a run this session already has open; PAUSES another session's active run on the plan. 409 if the plan is completed/archived or has no entries, or if it is a draft still open in ANOTHER active agent session (starting a run freezes its tests — start it from that session, or once that session has ended) |
| GET | `/agent/test-plans/{id}/execution-context` | Execution context — hosts + tests + known services with `{ip}` resolved |
| POST | `/agent/test-plans/{id}/entries/{eid}/sanity-check` | Record an optional target check — evidence that you reached the intended host |
| POST | `/agent/test-plans/{id}/entries/{eid}/test-results` | Record one test's execution result |
| POST | `/agent/test-plans/{id}/entries/{eid}/complete` | Mark entry completed (aggregates results) |
| GET | `/agent/test-plans/{id}/execution-progress` | Live execution progress summary |
| POST | `/agent/execution-sessions/{session_id}/complete` | **Close the session** — `overall_status: "completed"` after the last entry, or `"failed"` for a session-level break. Calling it transitions the session out of ACTIVE so the runs list stops showing "still active". |

<!-- agents:end -->

<!-- agents:section tags="reconnaissance" -->

### Scope reads and uploads (any session; no run to open)

To get a scope's ranges, names or target files, read them by `scope_id` (from `GET /agent/scopes`); to put scanner output into the inventory, upload it and poll the job.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/scopes/{scope_id}/subnets` | Paginated subnet CIDR list for a scope (default 500, max 2000 per page). MCP `scope_list_subnets` |
| GET | `/agent/scopes/{scope_id}/domains` | Paginated in-scope domain list (`{domain, include_subdomains}`) — the names you may resolve/probe without asking. MCP `scope_list_domains` |
| GET | `/agent/scopes/{scope_id}/hosts.ndjson` | **Complete** in-scope per-host dataset, newline-delimited JSON. Streamed — redirect to a file (`-o scope-hosts.jsonl`) and query it with `jq`, never read it into context. curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/live-hosts.txt` | **Complete** in-scope IP list, one per line — an `-iL` target file. curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/web-targets.txt` | **Complete** http/https URL list, one per line — a `-l` / `-f` target file. curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/named-targets.ndjson` | **Complete** name-scope list, one JSON object per in-scope name: the matching scope rule, the addresses it currently resolves to (each flagged `in_subnet_scope`), web interfaces reached as the name, `unresolved` + `reason` when no address is known. A name never puts its address in scope. curl only; auditor role or above |
| POST | `/agent/uploads` | Upload scanner output for ingestion (multipart; curl). No run needed; batches are keyed per agent session |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status. MCP `get_upload_job` |

> **These reads are project-wide.** A key is bound to a project session: `/agent/hosts`, `/agent/dashboard` and `/agent/scans` return the whole project. The scope reads above bound to one scope's subnets (`named-targets.ndjson`: to its domain rules). Being able to read a host does not put it in scope.

<!-- agents:end -->

<!-- agents:section tags="shared" -->

> An agent reads a scope, runs its own tools, and uploads the output — no run to open (v2.433.0 removed recon runs). The same session registers plans and opens execution runs with the same key; nothing is rejected for being "the wrong workflow" (that was the pre-v2.337.0 model).

### Host list filters (`GET /agent/hosts`)

`state`, `ports` (comma-separated), `services` (comma-separated), `subnets` (CIDR), `has_critical_vulns`, `has_high_vulns`, `has_exploit_available` (hosts with ≥1 vuln flagged exploitable by the Nessus parser), `search`, `not_in_plan_id` (exclude hosts already in a plan), `limit`, `offset`.

Each host in the response includes `open_port_count` and `vuln_summary` (`{critical, high, medium, low}`) so you can prioritize without calling the detail endpoint.

> **`/agent/hosts` is a BARE ARRAY, paginated, with NO continuation signal.** `limit` defaults to 500 (hard max 5000); the response carries no `total`, `has_more`, or `next_cursor`. To cover a scope you MUST page: issue the request at `offset=0`, then keep incrementing `offset` by `limit` until a request returns **fewer than `limit`** rows (an empty array ends it). A 40,000-host scope queried once at the default 500 returns 1.25% of hosts **with no error and no warning**. For plan coverage prefer `GET /agent/test-plans/{id}/context` (which DOES report `has_more`/`next_cursor`); use `/agent/hosts` only for spot cross-checks.

### Rate limit

Default **240 requests/minute** per agent, enforced in FIXED 60-second windows, global across all Uvicorn workers (not per-process). A rejected call still counts toward the window. See the 429 row in **Error Handling** for backoff.

<!-- agents:end -->

<!-- agents:section tags="plan_generation" -->

### Planning context (`GET /agent/test-plans/{id}/context`)

Returns plan metadata, filter criteria, the selection policy, `agent_name` (use for attribution), and a project summary. Hosts with zero open ports are excluded by default; pass `include_zero_port=true` to include them.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | 500 | Max hosts per page (1-2000) |
| `after_host_id` | null | Cursor — only return hosts with `id > N`. Use the last host's id from the previous page. |
| `detail_level` | `full` | `brief` returns summary fields only (no ports array); `full` includes full port details per host. Use `brief` for candidate selection, `full` for hosts you'll create entries for. |
| `include_zero_port` | false | Include hosts with no open ports. |

Each candidate host includes `open_port_count`, `vuln_summary`, `top_vulnerabilities` (title + CVE for critical/high — **capped at the first 5; not the complete list**), `services`, `meets_policy` (boolean), and (when `detail_level=full`) full `ports` with product/version. `vuln_summary.critical`/`.high` carry the **full counts** — when they exceed the number of `top_vulnerabilities` entries, there are more not shown. Use `vuln_summary` for priority/coverage decisions and `GET /agent/hosts/{id}` for the complete vuln list.

The `summary` object includes `total_hosts`, `matching_filter`, `already_in_plan`, `candidates_reviewed`, `policy_match_count`, `has_more` (boolean — true if more pages available), `next_cursor` (int — use as `after_host_id` on the next call, or null if no more pages), and `detail_level` (echo of the requested level).

**Pagination pattern (required whenever there are more than `limit` candidates — `has_more: true`):**

```bash
# Page 1 — brief mode for fast candidate scanning
GET /agent/test-plans/{id}/context?detail_level=brief&limit=500

# If summary.has_more is true, page 2:
GET /agent/test-plans/{id}/context?detail_level=brief&limit=500&after_host_id={summary.next_cursor}

# After selecting candidates, fetch full detail for those you'll create entries for:
GET /agent/test-plans/{id}/context?detail_level=full&limit=50
# (with a filter that matches only your selected hosts — or just use /agent/hosts/{id})
```

> **Empty final page is normal.** `has_more` is `len(page) == limit`, so a final page of exactly `limit` hosts still sets `has_more: true` and a cursor — the next call then legitimately returns zero `candidate_hosts`. Treat an empty page as *done*, not an error.

**Resuming plan creation:** If a session is interrupted, start a new agent session with the same `plan_id`. The `not_in_plan_id` filter on `/context` automatically excludes hosts that already have entries, and `after_host_id` lets you skip past hosts you've already evaluated. Combined, this means a second agent can pick up exactly where the first left off.

### POST /entries response shape

The response is a JSON object `{"entries": [...]}`, **not** a bare array. Access results via `response["entries"]` or `response.get("entries", [])`.

### Validate coverage

`GET /validate` includes a `coverage` field split into two explicit buckets:

```json
{
  "ready": true,
  "coverage": {
    "entries_in_plan": 326,
    "policy_matching_remaining": 12,
    "non_policy_with_open_ports": 1262,
    "eligible_hosts_remaining": 1274,
    "coverage_pct": 20.4,
    "note": "Plan covers 326 hosts. 12 additional host(s) match the selection policy and are NOT in the plan..."
  }
}
```

**Read `policy_matching_remaining`, not `eligible_hosts_remaining`.** The two buckets mean:

- **`policy_matching_remaining`** — hosts that match the selection policy (critical/high vulns, or medium + high-value port) AND are **not** in the plan. A non-zero value means you missed scope. Page through `/context` with `after_host_id` to pick them up, or add a description note explaining why the exclusion is intentional.
- **`non_policy_with_open_ports`** — hosts with open ports that the policy correctly skipped. Non-zero here is **normal and expected** — it's the count of hosts the rubric intentionally excludes. Do **not** add entries for these just because the number is large.
- **`eligible_hosts_remaining`** — equals the sum of the two buckets above; prefer the split fields.

Coverage is informational — it does **not** block `ready`. But a non-zero `policy_matching_remaining` is worth acting on.

### Inferred service hints

`GET /context` responses include an `inferred_service_hints` field on each candidate host:

```json
{
  "candidate_hosts": [
    {
      "id": 42,
      "ip_address": "10.0.1.5",
      "ports": [
        { "port": 445, "protocol": "tcp", "service": null, "state": "open" }
      ],
      "inferred_service_hints": [
        { "port": 445, "protocol": "tcp", "inferred_service": "smb", "source": "port_number_heuristic" }
      ]
    }
  ]
}
```

The hint list is populated **only** when an open high-value port (SMB/RDP/MSSQL/MySQL/PostgreSQL/Oracle/Redis/VNC/MongoDB/NetBIOS) has a null or generic (`unknown`, `tcpwrapped`) service name. It lets you explain policy decisions without re-implementing the port→service mapping agent-side. If a port has a real service detection (e.g. `"nginx 1.18"`), the hint list does not include it — `ports[].service` is authoritative.

<!-- agents:end -->

---

<!-- agents:section tags="plan_generation" -->

## Proposed Test Format (Required)

Each item in `proposed_tests` **must** be a structured object, not a plain string. The analyst or agent executing the plan needs to know exactly what tool to run, what command to use, and what to look for. Target the host's **already-known open ports** (recon recorded them) — this is validation/exploitation, not a discovery or version re-scan.

```json
{
  "tool": "netexec",
  "description": "Validate anonymous/null-session SMB access on the already-open 445",
  "command": "netexec smb {ip} -u '' -p '' --shares",
  "expected_result": "Share list with READ/WRITE markers, or an explicit access-denied. Flag any anonymous READ/WRITE share.",
  "references": ["https://www.netexec.wiki/smb-protocol/enumerating-shares"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `tool` | Yes | Tool name (e.g., `nmap`, `crackmapexec`, `curl`, `smbclient`, `nikto`) |
| `description` | Yes | What this test checks and why |
| `command` | No | Exact command to run. Use `{ip}` as placeholder for the target IP, and `{fqdn}` for the entry's `target_fqdn` when set |
| `expected_result` | No | What to look for in the output. What constitutes a finding vs. a pass |
| `references` | No | URLs to tool docs, CVEs, or technique references |

**Named endpoints (optional, entry-level).** A host behind a load balancer or NAT carries many names; `hostname` on the host is only its display name. `GET /agent/hosts/{host_id}` returns `names` — every FQDN observed at that address. When a test is against a *name* (web tests need the Host header / SNI to reach the right vhost), set `target_fqdn` on the entry to one of those names and write commands with `{fqdn}`. A `target_fqdn` that is not in the host's `names` is rejected with 400 — the target-in-inventory guardrail applied to names. Leave it unset for tests against the bare address.

**Bad** (too vague — the analyst can't act on it): `"proposed_tests": ["SMB null session check", "Anonymous FTP test"]`. **Good** is the structured object shown above — a `tool`, an exact `{ip}` command, and an `expected_result` stating what counts as a finding vs. a pass.

Test entry enums (invalid values return 422):

- **priority**: `critical`, `high`, `medium`, `low`, `info`
- **test_phase**: `reconnaissance`, `enumeration`, `exploitation`, `post_exploitation`, `reporting`
- **status** (on updates): `proposed` (not tested yet), `in_progress`, `completed`, `rejected` (not tested — a terminal state). There is no `approved` state (retired in v2.433.0).

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Agent Attribution (Required)

All agent-created content (notes, test plan descriptions, test plan entry rationales) is recorded under the agent's owner — the human user who created the agent. To ensure analysts can distinguish agent work from direct human input, **every piece of content you create must include the agent attribution mark**.

### Attribution Mark

Prefix every note body, test plan description, and entry rationale with:

```
🤖 **Agent-generated** — {agent_name}
```

- `{agent_name}` is available in the `agent_name` field of `GET /agent/project` (all workflows) and of `GET /agent/test-plans/{id}/context` (plan workflow). Either is authoritative.
- The marker must be the **first line** of the body, followed by a blank line before the rest of the content.

This applies to:

- **Notes** created via `POST /agent/hosts/{id}/notes`
- **Test plan descriptions** set via `PATCH /agent/test-plans/{id}`
- **Entry rationale** fields set via `POST /agent/test-plans/{id}/entries`

### Example

The mark is the first line, then a blank line, then the content — e.g. a test-plan description:

```markdown
🤖 **Agent-generated** — recon-bot

First-pass penetration test plan covering hosts with critical or high-risk services identified during automated triage.
```

(The full note structure is in **Note Formatting Guidelines** below.)

> **Non-negotiable:** Content without the attribution mark may be mistaken for direct analyst input, creating confusion in audit trails and review workflows. This is a policy requirement, not a suggestion.

---

## Note Formatting Guidelines

When creating notes, follow this structure so human analysts can quickly parse your output:

```markdown
🤖 **Agent-generated** — {agent_name}

## AI Assessment — {brief summary}

**Risk Level:** Critical | High | Medium | Low | Informational
**Confidence:** High | Medium | Low
**Services:** {comma-separated list of key services}

### Observations
- {finding 1}
- {finding 2}

### Recommended Actions
1. {action 1}
2. {action 2}

### Context
{OS details, network position, related hosts, or other relevant context}
```

Use the `status` field meaningfully:

- `open` — Assessment complete, requires human action
- `in_progress` — Partially assessed, more data needed
- `resolved` — Issue addressed or determined to be non-issue

---

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 401 | API key expired, revoked, or invalid | Read the body. `recoverable: true` → POST to `renew_path` with the same key and retry the failed request; never re-run work you already have output for. `recoverable: false` → save your output to a file and ask the user to start a new session. |
| 403 (read-only / not a member) | Your key acts for its operator, and their role changed, or they left the project | Not retryable. Tell the user — only they can fix it. |
| 403 — about your operator | A key carries its operator's project role, re-checked on EVERY request. Writes need the operator to hold `analyst`; a read needs the role its page needs of a person: bulk exports (`*.ndjson`, target lists) and client reports need `auditor`; ingestion issues and uninterpreted lines need `analyst`; everything else any member. You also get 403 if the operator was deactivated or removed from the project mid-session. | Read `can_write_project_data` from `GET /agent/identity` before attempting writes. Do not retry: tell the operator what you were refused and why — only they (or a project admin) can change it. |
| 403 — other | The operation is not one an agent may perform (promoting/dismissing a finding, user or project administration) | Use the endpoints documented in this guide; report what you wanted in a note instead. |
| 404 | Resource not found | Verify the `plan_id`, `entry_id`, or `host_id`. The resource may have been deleted. |
| 404 — scope | You read a scope that is not in this project | Use a scope id from `/agent/scopes`. |
| 410 | The project is archived | Not retryable. Ending your session still works; tell the operator. |
| 409 — `detail.code: "duplicate_scan"` (upload) | This exact file is already in the project — a scan, still parsing, or staged awaiting its format review (`detail.scan_id` / `detail.job_id`) | Not a failure — the data is in. Mark the file done and continue. Never retry, rename, or alter the file to force a re-import. |
| 422 | Validation error | Invalid field values — usually a wrong enum (`priority`, `test_phase`, `status`). Check the values against the lists in this file. |
| 429 | Rate limited | Default 240 req/min in fixed 60 s windows. Wait for the current window to end (at most 60 s) and retry ONCE — rejected calls still count, so retrying in a loop keeps you locked out for the whole window. |
| 503 + `Retry-After` (upload) | The sweep's batch label was briefly busy — parallel chunks contended for it. Nothing was stored. | Wait the `Retry-After` seconds and re-POST the same file with the same `batch`. Not a duplicate, not a failure to report. |

---

## Tips

1. **Start with the planning context.** `GET /agent/test-plans/{plan_id}/context` gives you candidate hosts, services, and vulnerabilities in one call. Report what you find before adding entries.

2. **Use `detail_level=brief` first on large projects.** Skim candidates in brief mode, then fetch full detail only for the hosts you'll actually create entries for.

3. **Be specific in rationale.** Include the services, ports, and OS you observed. Generic text like "needs review" is not useful. Say *why* each host warrants testing.

4. **Use follow status to track your review.** `POST /agent/hosts/{id}/follow` with `{"status": "watching"}` marks hosts you've assessed so neither you nor the user revisits them unnecessarily.

5. **Markdown works in notes, descriptions, and rationale.** Use headers, lists, and bold text for readability — analysts read these in the UI.

6. **Report what you did.** After adding entries or completing execution, tell the user plainly: "Added 28 entries covering SMB hosts in 192.168.1.0/24 to plan #8. Want me to work it?"

7. **Don't hallucinate services.** Only report what the API data shows. If a host has port 22 open with `service_name: "ssh"`, say that. Don't infer services that aren't in the data.

8. **During execution, show every command and never touch a target outside the declared scope without the operator's explicit go-ahead.** The cost of running the wrong command against the wrong host is very high. Where a target could be ambiguous, record a target check before testing it.

<!-- agents:end -->

---

<!-- agents:section tags="assist" -->

## Inventory-assist phase (interactive query; optional narrow write)

Use this phase to *query the project* — answer ad-hoc questions, summarize state, and surface findings — via `/agent/assist/*` endpoints. It generates no target traffic by itself. This is a mode within the same unified project session that also scans, plans and executes when the operator asks.

**You act as the operator who started the session.**  Their project role decides what you may write, checked on every call — there is no separate per-session grant to look up. If a write returns 403, their role does not permit it, and retrying will not change that.

### Runs on any OS

Inventory assistance has **no host-tool requirements** — its "commands" are HTTPS API calls to `/agent/assist/*`, so Windows, macOS, and Linux operators are all first-class (recon/execution, by contrast, need a Linux/Windows scanner toolchain). Only the HTTP-client invocation differs:

- **bash / zsh** (Linux, macOS): `curl -sk -H 'X-API-Key: …' '<url>'`
- **Windows PowerShell:** use **`curl.exe`** — bare `curl` is an alias for `Invoke-WebRequest` and won't accept these flags — e.g. `curl.exe -sk -H "X-API-Key: …" "<url>"`; or native `Invoke-RestMethod -SkipCertificateCheck -Headers @{'X-API-Key'='…'} '<url>'`. For POST bodies, pass `-d (ConvertTo-Json $obj)` to `curl.exe` or `-Body ($obj | ConvertTo-Json)` to `Invoke-RestMethod` rather than bash single-quoted JSON.

### Hard contract

- **You have the operator's permissions, not more.** The key is bound to one project session; project writes require the operator's role to permit them. A 403 is a guardrail, not a route-around opportunity.
- **Inventory assistance itself creates no target traffic.** To scan, write a plan, or execute, do it with the same key under the safety rules and that section of this guide.
- **Project-scoped.** The session binds to one project (the one the operator picked at start-up).  You see all hosts in that project; you do not see other projects.  No cross-project access.
- **No target traffic.** You never scan, probe, or otherwise generate traffic to in-scope hosts.  All your data comes from BlueStick's already-ingested state.  If the operator asks you to scan, see "When to hand off" below.

### Endpoint surface

> **MCP transport (lower friction).** These reads and writes, plus scope-read, planning, and execution tools, are exposed over `/api/v1/mcp`. An MCP-capable host (VS Code Copilot, Claude Code, Codex) can call them natively instead of shelling `curl`; a unified-session key sees the complete catalogue. The endpoint enforces project scope, run state and the operator role. The bulk `report-context.ndjson` stream remains a download-to-file rather than a tool.

All under `/agent/assist/*`.  X-API-Key header on every call:

| Endpoint | Purpose |
|---|---|
| `GET  /agent/assist/context` | **Headline** project summary. Scope list capped at 50 (check `scopes_truncated`); `recent_scans` capped at 5. Read BEFORE answering — but take real counts from the `totals` block, not the truncated lists. Also the engagement dates (`project.start_date` / `end_date`) and `members` with their project roles ("who is on this engagement"). |
| `GET  /agent/assist/hosts` | List hosts. Discrete filters: `state`, `ports`, `services` (known names map to their standard port numbers — an unknown name is ignored, so the filter silently disappears; for a detected service name use `q=service:<name>`), `subnets`, `has_critical_vulns`, `has_high_vulns`, `search`, `limit`, `offset`. **`q` — the full boolean query DSL** (same engine as the human Hosts page): `ip:`, `hostname:` (alias `host:`), `state:`, `port:`, `os:` (OS name or OS family), `service:` (alias `svc:`), `version:` (alias `product:`; service product or version, e.g. `version:"OpenSSH 7"`) — **`port:`/`service:`/`version:` match OPEN ports only**; name another state after `@` on the value: `port:22@closed`, `service:ssh@filtered`, `port:22@unfiltered`, `port:22@open|filtered`, `port:22@any` (every state). A closed/filtered port's service name is nmap's guess from the port number, not evidence the service runs. `portstate:closed` alone is a separate "has some closed port" condition, not a qualifier. `path:` (alias `webpath:`; a path content discovery found, e.g. `path:/admin`), `subnet:` (alias `cidr:`), `scope:` (`subnet` = in a scope subnet, `name` = reached only through an in-scope name, `none` = neither), `org:` (alias `owner:`; the netblock's registered owner, RDAP), `certorg:`, `asn:`, `country:`, `tag:`, `label:`, `site:` (`site:none` = inside a scoped subnet that carries no site — Posture's "Unassigned"), `conclusion:` (what a finished review concluded: `no_issue` / `finding_created` / `needs_evidence` / `out_of_scope` / `duplicate` — `conclusion:needs_evidence` is every reviewed host whose question is still open), `cve:`, `vuln:`, `issue:` (exactly one scanner-observation issue by its key — `check:<id>`, `cve:<CVE>`, `title:<normalised title>` or `row:<id>`, e.g. `issue:"check:smb_signing_not_required"` or `issue:"cve:CVE-2021-44228"`; `vuln:` is a title substring), `kind:` (`misconfiguration` / `vulnerability` / `informational`), `check:` (one misconfiguration-catalog check whichever tool reported it — e.g. `check:smb_signing_not_required`, `check:smbv1_enabled`, `check:smb_null_session`, `check:vnc_no_auth`, `check:ftp_anonymous`, `check:tls_deprecated_protocol`, `check:tls_cert_expired`, `check:http_missing_hsts` — the `check_id` on a host's findings is the value to use), `exploitport:`, `header:`, `webtitle:`, `tech:`, `note:`, `scan:`, `firstseen:` / `changedsince:` / `vulnsince:` (time windows — quote the ISO value: `firstseen:"2026-09-19T20:00:00Z"` = hosts first observed since then; `changedsince:"<start>..<end>"` = hosts already known that gained a port or a scanner observation; `vulnsince:"critical@<start>"` = a critical observation recorded since, severity and time on the same row), `has:`, **`follow:`** (`watching` / `in_review` / `reviewed` / `none` / `in_review_any`), **`assigned:`** (alias `assignee:`) combined with `AND`/`OR`/`NOT` and parentheses. `has:` values: `eol`, `smb_unsigned`, `weak_auth`, `cert_issue`, `weak_tls`, `cleartext`, `critical`/`high`/`medium`/`low`, `exploit`, `critical_exploit` (a critical that is ITSELF exploitable — `has:critical AND has:exploit` also matches a critical beside an exploitable low), `web`, `open_ports`, `tested`, `planned`, `notes`, `stale_review`, `untouched` (nobody has touched it: no review or assignment, note, plan entry or finding), `local_admin` (a credential was local admin — NetExec "Pwn3d!"), `writable_share` (a share granted WRITE). `GET /agent/assist/vocabulary` returns the values this project uses after `tag:`, `label:`, `site:` and `assigned:` — use it instead of guessing (a guessed tag returns zero hosts, not an error). `assigned:me`/`follow:` resolve against the operator who started the session; `assigned:`/`assignee:` also take a **username** (case-insensitive) or numeric id. `q` ANDs with the discrete filters; a malformed `q` returns 400. **Bare array, paginated (default 500, max 5000), NO `has_more`/`total` — page with `offset` until a short page; never report a count from one page.** Rows carry `exploitable_count` and `critical_exploitable_count` (same-row, as the Hosts page's "critical · exploit"). `sort_by` takes the Hosts page's keys (`ip_address` default, `critical_vulns`, `high_vulns`, `exploitable_vulns`, `open_ports`, `note_count`, `discovery_count`, `hostname`, `last_seen`) with `sort_order=asc\|desc`. `GET /agent/assist/hosts/by-ip/{ip}` is the host detail by address. |
| `GET  /agent/assist/hosts/count` | **How many hosts match** — same filters and `q=` as the list; returns `{count, query}`. Use this for every counting question instead of paging. |
| `GET  /agent/assist/hosts/{host_id}` | One host with ALL its ports, in any state — filter on each port's `state` (can be large — prefer `open_port_count` from the list for triage), `os_family`, and up to 10 `web_interfaces` (`web_interfaces_total` / `web_interfaces_truncated`; the full list is `/hosts/{host_id}/web-interfaces`). Each port's `protocol` is the IP transport (`tcp`/`udp`); the application (smb/http/…) is `service_name`. `open_port_count` = distinct physical open ports. The host's `vuln_summary` is **severity counts only** — for the actual CVEs/evidence use the findings endpoint below. Both list and detail also carry `follow` = the session operator's review status on the host (watching/in_review/reviewed, or null), so you can check it before writing follow. Detail also carries what the host inspector shows: `names` observed at the address, OS/MAC/NetBIOS detail, `smb_signing`, `tags`, `assignees`, `scope_membership`, per-domain `assessment`, `weakness_flags` / `weakness_labels`, certificate facts (`cert_orgs`, `cert_status`), `attributions`, NSE script output per port and per host (bounded — check `*_truncated`), scan `conflicts` (where scans disagreed), `note_count` and `finding_count`. |
| `GET  /agent/assist/hosts/{host_id}/findings` | **Individual findings on a host** — the evidence `vuln_summary` only counts. Each carries `severity`, `cve_id`/`plugin_id`, `title`, `port_number`/`service_name` (null = host-level), `exploitable`, `cvss_score`, `source` (the scanner), `check_id` (the misconfiguration-catalog check, null for a scanner's own finding), `description`, `solution` (remediation), and `evidence` (scanner output; truncated). Filter `?severity=critical,high`. **Paginated with `total`/`has_more`** (default 200, max 1000) — page `offset` until `has_more` is false to report complete coverage. Use this for evidence-rich reporting on ONE host. |
| `GET  /agent/assist/report-context.ndjson` | **The report data source — use this to write a report.** Streams the COMPLETE per-host dossier for every matching host, one JSON object per line, **uncapped**: identity, ports (transport + service), findings (severity/CVE/plugin/port/evidence/remediation), notes, scan discoveries, canonical + execution findings, provenance, tags, and the operator's review state. Same discrete filters + `q` DSL as `/agent/assist/hosts`. This is the same correlated record the server-side report builds — populate your report template from it instead of stitching together per-host calls. **Redirect to a file and process it locally; NEVER read the stream whole into context** (`curl -sk -H "X-API-Key: $KEY" ".../agent/assist/report-context.ndjson" -o report-context.jsonl`). Safe on tens-of-thousands-of-host projects — the server hydrates one chunk at a time. |
| `GET  /agent/assist/hosts.ndjson` | **The complete matching host set** — same filters + `q` DSL as `/agent/assist/hosts`, but uncapped and streamed one JSON object per line. Use this instead of paging when the project is large: redirect to a file and query it locally (`curl -sk -H "X-API-Key: $KEY" ".../agent/assist/hosts.ndjson" -o hosts.jsonl`, then `jq`/`grep`/`wc -l`). Report counts from the file, never a truncated page. Never read the stream into context whole. |
| `GET  /agent/assist/scopes` | Scope CIDR lists **and declared domains** — **each capped at 100 per scope**. Each ScopeBrief carries `subnet_total` / `subnets_truncated` and `domain_total` / `domains_truncated`; when a `*_truncated` flag is true the list is only a sample, so tell the operator it's partial — full enumeration needs a recon session. `domains[]` entries are `{domain, include_subdomains}` (exact name vs the name and everything under it); `names_in_scope_total` is the deduplicated count of inventory names they cover. **Name scope is independent of subnet scope**: an in-scope name does not put the address it resolves to in scope, and an in-scope subnet does not put names in scope. |
| `GET  /agent/assist/names` | The named-asset inventory (FQDNs), paged (`limit` default 100, max 1000, `offset`). Each row: `in_scope` (a declared domain covers it), `current_ips` (derived from the latest A/AAAA observations — never stored; empty = unresolved), `current_ip_total`, `sources` (observation kinds: A, AAAA, IMPORT, HTTP, CERT, …). Filters: `q`, `in_scope`, `resolved`, `host_id` (names currently bound to that host's address), `kind`. **How to act:** `in_scope=true&resolved=false` is the queue — names the operator approved that no upload has ever resolved (chase with dnsx/amass output, or tell the operator to drop them from scope). A name whose address is shared with other names (`current_ip_total` on the host's other names, a load balancer / vhost) must be tested **by name**, not by IP — the bare address reaches a different site. |
| `GET  /agent/assist/scans` | Scan inventory, newest-first — **default 100, max 500**; `offset=` pages further back (a page shorter than `limit` is the last one). `tool=` narrows to one tool's scans (the Scans page's chips): the last two nmap scans are `tool=nmap&limit=2`. Each row carries `ingestion_job_id` — the import that produced it, the `job_id` `/assist/uninterpreted-lines` takes (a scan id is not a job id) — and `time_source`: `tool_run` / `tool_records` mean the start and end times are instants, returned in UTC with an offset; `tool_clock` means the scanner's own wall clock, zone unknown, returned WITHOUT an offset — never correlate it with a UTC event as if it were UTC. |
| `GET  /agent/assist/session` | Your own session metadata (purpose, started_at, the operator `assigned:me` refers to). |
| `GET  /agent/assist/vocabulary` | The values this project uses: tags, labels, sites, scope names, usernames (for `assigned:`), finding statuses and severities. |
| `GET  /agent/assist/findings` | **Triaged findings** (not raw scanner rows): filters `status`, `severity`, `source`, `host_id`, `unowned=true`, `owner` (username or `me`), `search`; default 50, max 500. The report's findings come from here. |
| `GET  /agent/assist/findings/{finding_id}` | One finding with its affected hosts (per-host endpoint status) and evidence; `report_text` (description, impact, recommendation, references, steps to reproduce, CVSS vector and score — what a report will say), `endpoint_status_counts`, and `status_history` (who changed the status, when, from→to, why). |
| `GET  /agent/assist/scanner-observations` · `/scanner-observations/hosts?issue_key=` | Scanner results grouped by ISSUE across the project (`host_count`, `judged_host_count`, the covering finding) — the Findings page's "Scanner observations" view; unjudged issues only unless `include_judged=true`. Then the hosts carrying one issue — `{items, total, has_more}`, paged with `limit`/`offset`. `sort=hosts` puts the most widespread first; a row with `judged_host_count > 0` is partly judged (listed until every host is). |
| `GET  /agent/assist/client-reports` · `/client-reports/{id}` · `/client-reports/{id}/files/{fmt}` | The Reports page (operator needs `auditor`): drafts and issued reports; one report with every finding as the report states it (`content_source`: `issued_snapshot` = what the client was given, `draft_live` = what a draft would say now); the rendered files. |
| `GET  /agent/assist/hosts/{host_id}/web-interfaces` | Every web interface on a host: URL, title, server, technologies, screenshot reference, and the certificate / TLS facts (`cert_not_after`, `cert_self_signed`, cert organisations, `tls_weak_protocol`, and as the web panel reads them `tls_version`, `cert_issuer`, `cert_subject_cn`, `cert_sans` + `cert_san_total`). Null = the tool did not report it, not "fine"; an expired certificate is `check:tls_cert_expired`. |
| `GET  /agent/assist/hosts/{host_id}/access` | NetExec / SMBMap results on the host (logins, shares, local admin) next to the raw tool line — which may contain credentials the tool found. |
| `GET  /agent/assist/hosts/{host_id}/testing` | What has been planned and executed against the host. |
| `GET  /agent/assist/hosts/{host_id}/notes` · `GET /agent/assist/notes` | Notes on one host · across the project, with their threads (`parent_id` / `thread_root_id`), type, status, assignee, due date, the `finding_id` a thread was promoted to, and attachment references. `/assist/notes` rows carry `target {kind, id, label}` (host, port, finding, scan, scope, test plan or project). |
| `GET  /agent/assist/workbench` | Your operator's Operations "My work": my queue, tasks, assigned notes, owned findings, team review, `since_last_visit`, follow-ups ("needs another look"), blockers. Reading it never marks anything seen; `*_unavailable: true` means not computed, not "nothing". |
| `GET  /agent/assist/workbench/investigate?tier=&limit=&offset=` | "Worth a look": untouched hosts with reasons, in stated tier order (1 exploitable critical … 5 scans disagree); `queue_total` / `tier_counts` are whole-queue. |
| `GET  /agent/assist/workbench/terrain?sort=address\|untouched\|critical_untouched&limit=` | Hosts per /24 (IPv6 /64): tested / planned / worked / untouched, and `critical_untouched`. |
| `GET  /agent/assist/evidence/gaps?domain=&segment=&limit=` | The Evidence page's gap list: eligible-but-unassessed hosts, their ports, and the step that closes the gap (`domain` = a key from `/assist/coverage`). Respect `scope_caution`. `segment` is `matrix.segments[].key` from `/assist/coverage` (a site id, a subnet key or `unmapped`) — NOT a CIDR; a wrong value answers 404 listing the accepted keys. |
| `GET  /agent/assist/scans/compare?a=&b=&limit=` | What changed between two scans: hosts new / gone / changed, ports newly open / closed / not observed (not observed is not remediation). |
| `GET  /agent/assist/coverage` · `/segments` · `/posture` · `/patterns` | Scope coverage; the Posture page's segments, headline and recurring-weakness patterns. |
| `GET  /agent/assist/ingestion-issues` | Imports that failed, were partial, or skipped records (operator needs `analyst`, as the Ingestion Results page does). |
| `GET  /agent/assist/uninterpreted-lines?job_id=` | Lines an import did not read, as redacted shapes (NetExec imports record them; operator needs `analyst`). A `job_id` that is not an import job of this project is a 404; an empty page for a real job means every line was read. |
| `GET  /agent/assist/attachments/{attachment_id}` · `/web-interfaces/{interface_id}/screenshot` | Evidence files (any member, as in the UI). Over MCP, `assist_get_image {attachment_id | interface_id}` shows one inline (image content, up to 2 MB); the `download_path` references are for saving files beside a report. |

**Write routes.**  Note these live under `/agent/hosts/…`, not `/agent/assist/…`:

| Endpoint | Purpose |
|---|---|
| `POST /agent/hosts/{host_id}/notes` | Add a note. Body `{"body": "...", "status": "open"}` (`open` \| `in_progress` \| `resolved`). An `@username` in an agent's note notifies nobody — ask the operator to mention someone. |
| `POST /agent/hosts/{host_id}/follow` | Set review status. Body `{"status": "in_review"}` (`watching` \| `in_review` \| `reviewed`). |
| `PATCH /agent/hosts/{host_id}` | Correct a host's `hostname` / `os_name` after investigation. Body `{"hostname": "...", "os_name": "..."}` — send only the field you're fixing; only these two are editable (setting `os_name` re-derives `os_family`). Use when your investigation established the real hostname/OS a scan mis-detected. |

**Whether you may write is your operator's project role, not a per-session grant.** `GET /agent/identity` returns `can_write_project_data` — check it once at start rather than discovering the answer from a 403. `true` means you can write anywhere in the project, `false` means nowhere in it. The role is re-checked on every request, so it can change mid-session if the operator's membership changes.

Project-wide is deliberately wider than what you should *touch*. Use `GET /agent/assist/hosts?q=assigned:me` to find your operator's own work, and stay inside it unless they point you elsewhere — writing on a colleague's host is permitted and still rude.

### Write discipline

Every note you create is stamped agent-authored and surfaces in the operator's UI *and in client-facing reports*, under their name. Treat that weight seriously:

1. **Announce, then write.** Tell the operator the note you intend to add and let them react. Never batch-write silently.
2. **Observations, not unsupported conclusions.** Write what the data shows and cite the host / port / finding it came from. Never assert a vulnerability you haven't seen evidence for in BlueStick's own data.
3. **Mark uncertainty in the note body itself.** A reader six weeks out cannot separate your inferences from your facts unless you say which is which.
4. **Never set `reviewed` on your own initiative.** "Reviewed" is a human judgement with client-reportable weight — ask the operator to confirm first. `in_review` is fine when they asked you to pick work up.
5. **A 403 is the guardrail working.** If a write is refused, your operator's project role is read-only, their membership changed, or their account was deactivated. Report what the message says; don't hunt for another route.
6. **Host attribute edits are corrections, not guesses.** Only `PATCH /agent/hosts/{id}` (`hostname` / `os_name`) when your investigation actually established the real value — cite what showed it (a banner, a cert CN, a service response) in a note alongside the edit. Don't overwrite a scan's OS on a hunch; a wrong "correction" is worse than the scan's uncertainty because it reads as settled.

### How to operate

1. **Fetch `/agent/assist/context`.**  This grounds you — but it's a HEADLINE summary: the scope list is capped at 50 (check `scopes_truncated`), and recent scans at 5. Read `totals` for real counts and use the dedicated list endpoints for full enumeration. Don't answer "how many scopes/scans/hosts does this project have" from the truncated lists. Anchor every response in something you actually read.
2. **Answer the operator's question** using the filter vocabulary above.  Examples:
   - "Which hosts have FTP open?" → `GET /agent/assist/hosts?ports=21` (or `services=ftp`, or `q=port:21`).
   - "Which hosts do I have in review?" → `GET /agent/assist/hosts?q=follow:in_review` (resolves to the session operator). "Assigned to me?" → `q=assigned:me`.
   - "Generate a test plan for the hosts assigned to me or in review by me" → `POST /agent/test-plans {"title": …, "q": "assigned:me OR follow:in_review"}` (the query is resolved to its hosts now, and the plan's description records it), then `/context` for exactly those hosts and `POST /agent/test-plans/{id}/entries`. The operator takes hosts into review from the **Worth a look** queue on Operations, so this is how their review queue becomes a plan; do not add hosts outside the query result.
   - "What's exposed to Log4Shell?" → `GET /agent/assist/hosts?q=cve:CVE-2021-44228 OR vuln:"log4j"`.
   - "Which SMB hosts have a working exploit *on 445*?" → `GET /agent/assist/hosts?q=exploitport:445`. Note `exploitport:445` (exploit ON that port, same finding) is stricter than `q=port:445 AND has:exploit` (445 open AND *any* exploit anywhere).
   - "What critical findings landed this week?" → `GET /agent/assist/hosts?has_critical_vulns=true` (or `q=has:critical`) + `GET /agent/assist/scans?limit=20` to correlate.
   - "Summarize scope X" → `GET /agent/assist/scopes` to confirm CIDR list + `GET /agent/assist/hosts?subnets=...` for the host count and posture.
   - "What's worth a look / what's mine / what changed since I was last here?" → `GET /agent/assist/workbench/investigate` · `GET /agent/assist/workbench` (`since_last_visit`).
   - "What changed between the last two scans?" → `GET /agent/assist/scans?limit=5` to pick them (`&tool=nmap` for the last two nmap scans), then `GET /agent/assist/scans/compare?a=…&b=…`.
   - "Which hosts have an exploitable critical, worst first?" → `GET /agent/assist/hosts?q=has:critical_exploit&sort_by=critical_vulns&sort_order=desc`. Not `has:critical AND has:exploit` — that also matches a critical beside an exploitable low.
   - "What is still unassessed in segment Y?" → `GET /agent/assist/coverage` (domain and segment keys), then `GET /agent/assist/evidence/gaps?domain=…&segment=…`.
   - "What are the most widespread issues not yet made findings?" → `GET /agent/assist/scanner-observations?sort=hosts` (most widespread first; `min_hosts=` narrows).
   - "What did we report to the client?" → `GET /agent/assist/client-reports`, then `/client-reports/{id}` (`content_source: issued_snapshot`).
   - "Who closed finding X, and why?" → `GET /agent/assist/findings/{id}` → `status_history`.
   - "Which names are we allowed to test but haven't found yet?" → `GET /agent/assist/names?in_scope=true&resolved=false` (the context's `names.in_scope_unresolved` is the count). "What sits behind 10.0.0.5?" → `GET /agent/assist/names?host_id=<id>` — several names on one address means test each **by name**.
3. **Cite what you read — and count with the count endpoint.** Every claim maps back to a specific endpoint + filter. "How many" is ONE call: `GET /agent/assist/hosts/count` (MCP `assist_count_hosts`) takes the same filters and `q=` as the list and returns `{count, query}`. Page the list, or take the `hosts.ndjson` download, only when you need the rows themselves — and then one 500-row page is **not** "500 hosts." Say "12 hosts (per `?ports=21`, from `/hosts/count`)," never a one-page count on a project that may have thousands of hosts.
4. **Flag uncertainty.**  If the data is ambiguous (e.g. the host has port 21 open but no service name), say so.  Don't infer.

### When to hand off

When your synthesis points to a follow-up that requires action, propose it, and do it only when the operator asks — with this same key, under the safety rules and that work's section of this guide (its read-back and exit criteria):

- **"You should scan these hosts more thoroughly."** → Read the scope (`GET /agent/scopes/{id}/subnets`), scan within it from your working directory, and upload the output (`POST /agent/uploads`).
- **"These hosts need a test plan."** → Register one (`POST /agent/test-plans {"title": …}`, MCP `create_test_plan`) and fill its entries from the candidates; when the operator says to work it, open an execution run on it (`start_execution`) — no approval step sits in between.
- **"Mark these as in review for me."** → With write access, do it for the hosts assigned to them (announce first). Without it, or for hosts assigned to someone else: "I can't change follow status for that target in this session. Use the `/hosts` page checkboxes, or I can hand you a filter URL to apply."

The operator drives every action; you assist their query.

### What you can NOT do

- Create notes or change follow status when `can_write_project_data` is false. Cannot assign hosts to anyone, ever.
- Access other projects, or list other operators' assist sessions.
- **Promote, dismiss or otherwise triage a finding.** There is no agent route for it — that judgement is the operator's (and theirs can be about one host or the whole issue). You can READ findings (`/agent/assist/findings`, `/agent/assist/findings/{id}`); if a scanner observation looks real or looks like a false positive, say so in a note on the host with your evidence.
- Archive or delete a test plan — that is the operator's, from the Test Plans page.

### Tone

You're a research partner.  Concise responses.  Lead with the answer, then show your work (endpoints called, filters applied).  Roll with mid-session pivots ("actually, just show me the up hosts") — assist is conversational by design.

<!-- agents:end -->
