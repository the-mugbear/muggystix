# BlueStick AI Agent Guide

**Prompt version:** 4.8.5 · **Verified against:** backend 2.453.5 (2026-10-02)

> **Version & compatibility (read this).** The number that matters is the **Prompt version** above — stamped live from the running deployment when this guide is fetched, and identical to the `prompt_version` in your instructions block (echoed on every `/context` response). If the two **match**, your prompt and this guide are the same contract — proceed; if they **differ**, the deployment changed mid-session, so **re-fetch this guide and prefer it**. Ignore the "Verified against backend X" stamp for compatibility — it's a different numbering scheme and won't equal the Prompt version.

You are an AI assistant (Claude Code, Codex, ChatGPT, etc.) assigned to a workflow in BlueStick. This file is the entire surface you are authorized to use. Follow it literally — the surrounding scaffolding (your operator's project role checked on every call, the project's declared scope, the audit trail) depends on you behaving as described.

**The operator drives; you do the work.** You act as the operator who started your session, with their project permissions. You scan, upload, propose tests on hosts, run them and record what you found — in whatever order the work needs. A test belongs to its host, and the tools you use are between you and the operator. What you owe the operator is in [§ Safety rules](#safety-rules-mandatory): show every command, propose rather than act unasked, stay in the declared scope, keep to the working directory, and record everything.

Everything below is reachable through one auth mechanism: the API key the user pasted to you. Do not attempt to log in, reach admin surfaces, mint new keys, create new agents, or touch endpoints outside `/api/v1/agent/*`. The only other surfaces that are yours are the public reference ones this guide sends you to — `/.well-known/networkmapper.json`, `/api/v1/agents-guide`, `/api/v1/references/*`, and `/api/v1/mcp` (the same agent endpoints as MCP tools, authenticated by the same key). Everything else is not available to you and will return 401/403.

One exception, and it matters: you **can** extend your own key's deadline via `POST /api/v1/agent/session/renew`. That is renewal, not rotation — same key, later expiry — and it is how you survive a long-running scan outliving your credential. See **If your key expires** below.

Your key carries the permissions of the operator who started your session, re-checked on every call. If you get a 403 saying the key is read-only or the operator has left the project, that is not a bug and retrying will not help — tell the user.

> **Context optimization:** a unified project session receives this full guide. You may request `?workflow=testing|reconnaissance|assist` for a focused reference, but the key is not restricted to that kind of work.

---

<!-- agents:section tags="shared" -->

## Instance Identity (verify once before acting)

BlueStick publishes its identity at an unauthenticated well-known URI so you can verify that the host you're being asked to curl is the same instance that generated your prompt.

```
curl -s https://<host>/.well-known/networkmapper.json
```

The response contains `instance_id`, `name`, `version`, `purpose`, and a `safety_properties` block. Your instructions block includes the `instance_id` the prompt was generated with — cross-check the two values match **once** at the start of your session. If they match, the session is trusted for the duration of your API key; you do not need to re-check on every request. If they do not match, stop and alert the user — the prompt may have been tampered with, copied from a different instance, or served by an unrelated host.

You can also read `safety_properties`. It states the model BlueStick operates under and is explicit about which parts the server enforces and which are yours:

Enforced by the server:
- `server_executes_commands: false` — BlueStick is a coordinator. It never runs a command; everything runs on the operator's machine.
- `agent_authority: "operator_project_role"` — your key may do exactly what your operator's project role allows, re-checked on every request.
- `agent_key_binding: "project_session"` — your key is bound to one project session and one operator.
- `agent_keys_time_limited` / `agent_keys_renewable` — your key expires (24 h by default); you can renew it yourself while the session is under its lifetime cap. Ending the session, not expiry, is what revokes it.
- `audit_trail_persistent: true` — every `/agent/*` request you make is recorded and shown to the operator.

Yours to uphold — the server cannot see your terminal:
- `command_approval: "operator_driven"` — the operator drives: you show every command, and a target outside the declared scope, anything outside the working directory, or a change to the operator's machine waits for their explicit go-ahead (see [§ Safety rules](#safety-rules-mandatory)).
- `command_approval_enforced_by: "agent_and_client_sandbox"` — holding to those bounds is your discipline plus your client's sandbox. The server contributes the record of what you report and the read-back of your bounds; it cannot stop a command.

## Quick Start

The user will give you an **API key** and an **instructions block** copied from the BlueStick UI. If you don't have one, ask the user to start an agent session from the BlueStick UI — **Operations → Start Agent Session** — and paste you what it produces. Pages about one object (a scope, a host, a host selection) open the same dialog with a one-line task to hand you, such as `Propose tests in BlueStick for these hosts only (host ids): 12, 14.`; the key is the same kind either way: one session for the project. There is nothing to open first — you call what the work needs (`POST /agent/uploads`, `POST /agent/host-tests`, `POST /agent/evidence`).

### Authentication

Every request to `/api/v1/agent/*` carries your API key in a header:

```
X-API-Key: nm_agent_abc123...
```

No login, no password, no `project_id` in the URL — the key is bound to exactly one project session, and every read and write auto-scopes to that project. A 403 means your operator's project role does not permit that action.

> Both `X-API-Key: nm_agent_...` and `Authorization: Bearer nm_agent_...` are accepted. Prefer `X-API-Key`.

If your key stops working, read the 401 body before doing anything else. It tells you which case you are in:

* `recoverable: true` — your key expired but the session is still open. `POST` to the `renew_path` in that body **with the same key**, then **retry the exact request that failed**. Do not re-run a scan or command whose output you are already holding, and do not ask the user for a new key.
* `recoverable: false` — the session ended or passed its maximum lifetime. Save anything you are holding to a file in your working directory, then ask the user to start a new session.

A 403 is different: it means your key is valid but not allowed to do that. Do not retry it.

### Ending the session (MANDATORY last step)

Your session does not end on its own: it ends when you call `POST /api/v1/agent/session/end` (MCP `end_session`), the operator ends it from Agent Sessions, or it lapses: an hourly sweep ends a session only once its key has expired **and** its renewal deadline (`renewable_until` — the session's start plus its maximum lifetime, 7 days by default) has passed. Until one of those happens the operator's Agent Sessions page lists it as **live** (its key is valid) or **resumable** (the key expired but can still be renewed, or the operator can resume the session with a new key) — never as ended. Ending never loses work — the tests you proposed and the evidence you recorded are project data, and another session or a person carries them on.

End it only when the operator tells you they are finished. Finishing a task is not that: report what you did and wait for the next instruction. A session opened with no task yet is waiting for one, not done — after the setup steps, say you are ready and wait. Ending early revokes your key, and the operator's next question fails with a `401` they cannot recover from without starting a new session.

When the operator says they are finished:

1. If you have filed no feedback in this session yet, file it now (`POST /agent/feedback` / `submit_feedback`). Feedback belongs at the moment of friction (see below), so by this point there is usually nothing left to add.
2. `POST /agent/session/end` with a line of `notes` (and `agent_model` — see [§ Attribution](#attribution--what-the-record-says-about-you)). It revokes your key; nothing you call afterwards authenticates, so it is the last call.

### Feedback — file it when the friction happens

**The trigger is an event, not the end of the session.** The moment you retry a call, guess at a field or a route (a `404` on a path you expected to exist counts — its body says so), work around a tool, or go back to this guide to make something work, file a short critique right then — `POST /agent/feedback` (MCP `submit_feedback`), one line per item, naming the endpoint or tool, what you expected, what happened, and the exact error or missing field. Several small submissions during a session are the norm. Most sessions never reach a tidy ending — the terminal closes, the operator moves on — so feedback saved for the end is feedback that is never filed.

If you filed nothing along the way, file before you end the session — the session end is the last resort, not the plan. Payload shape: `## Feedback Requested` block at the end of your session prompt.

### Long-running commands — never block a single tool call on one

Your client has its own tool timeout. A scan that outlives it ends *your process* while the scan keeps running: the output is orphaned, and the operator is left with a session that looks alive but has no agent behind it. Run anything that may take more than a minute or two in the background from the working directory, capture its PID at launch (`nmap … & echo $!`), and poll **that PID** — see *Working directory & concurrent agents*. Upload each output file as it finishes rather than holding everything for one upload at the end; an upload that already landed is answered `409 duplicate_scan`, which is safe.

If the operator resumes a session a previous agent process died in, your prompt carries a `⟳ RESUMED SESSION` notice: the previous key is revoked, and the working directory may hold output that never got uploaded. Look there first.

### MCP (optional — same endpoints, native tools)

If your client speaks MCP, the operator can hand you the same session as a set of **tools** instead of curl recipes; the session-start dialog emits the config for VS Code, Claude Code and Codex. Every tool call loops back into the endpoints described in this guide with the same key and the same checks, so nothing here changes — you just stop shelling out for the interactive calls.

Two things to know:

- **You see the whole catalogue.** One session does every kind of work, so `tools/list` is not filtered (v2.337.0). A tool being listed does not mean a call will succeed: the endpoint behind it decides on every call — by your operator's project role. `agent_identity` tells you what you may write and when your key expires.
- **Bulk data is not a tool.** The NDJSON streams, target-file downloads and the upload itself (`POST /agent/uploads`) stay curl on purpose — they belong in a file on disk, not in your context. `GET /api/v1/references/mcp-tools` lists everything the server exposes.

### HTTPS and the certificate

BlueStick's certificate is issued by the operator's local root CA, so use `curl -s` — never `-k`, which accepts any server claiming this address. If curl reports a certificate error, tell the operator: the root is not trusted on this machine yet (`ca/local-ca.sh trust-help`), or the deployment is still self-signed; add `-k` only if they say to. In PowerShell use `curl.exe` (bare `curl` is an alias for `Invoke-WebRequest`). If your execution environment blocks the connection, ask the user to run the command for you.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Attribution — what the record says about you

BlueStick does not inspect the operator's machine and does not choose your tools or commands: which tools are installed and which command fits this host is yours to work out with the operator. What the server records is who did the work, from three sources:

| Recorded | Source |
|---|---|
| **Client** (the harness — `generated_by_tool`) | Over MCP, the `initialize` handshake's `clientInfo` (name and version, e.g. `claude-code 2.1.0`), when the handshake carries your key. A curl agent: the first call's `User-Agent` (e.g. `curl/8.5.0`); a handshake name replaces it, never the other way round. You send nothing for this. |
| **Prompt version** | Set by the server when the session starts or is resumed — the version of the instructions you were given. You send nothing for this. |
| **Model** (`generated_by_model`) | Your own report, the one thing no protocol carries. Pass the optional `agent_model` (e.g. `"claude-opus-5-5"`) on `POST /agent/host-tests` (MCP `host_tests_propose`), `POST /agent/evidence` (`record_evidence`), each `POST /agent/proposals/*` (`propose_*`) and `POST /agent/session/end` (`end_session`). |

The session keeps the last model reported; a host test, an evidence record and a proposal each take a snapshot of the session's attribution (client, model, prompt version) when they are created. **Pass `agent_model` on those calls** — a record made without it carries the session's last-reported model, or none. Several proposals for one field from different models stand side by side so the operator can compare them; the model is what tells them apart.

### A test describes intent — you translate when you run it

A host test's `command` is a sample; treat the `description` and `expected_result` as authoritative and pick the command that fits the operator's machine. Two examples of the same intent, two valid translations:

| Intent | Kali Linux | Windows + RemoteSigned, no Python, no WSL |
|---|---|---|
| Enumerate SMB sessions on 10.0.0.5 | `enum4linux -S 10.0.0.5` | `powershell -Command "Get-SmbSession -CimSession 10.0.0.5"` |
| DNS reverse lookup for 10.0.0.5 | `dig -x 10.0.0.5` | `nslookup 10.0.0.5` |
| List listening ports on the local host | `ss -tlnp` | `powershell -Command "Get-NetTCPConnection -State Listen \| Format-Table"` |

When you record the result, put the *actual command you ran* in the evidence record's `command` so a reviewer can set the test's intent beside what happened on this operator's machine. The audit trail is then full: BlueStick has the inbound API calls, the session's attribution, and the command as run on every evidence record.

### Evidence and proposals — what you did, and what you think it means

**Evidence (direct).** `POST /agent/evidence` (MCP `record_evidence`) records a command you ran against a host and what came back: `host_id`, `tool`, the `command` verbatim, `outcome` (`finding` · `no_finding` · `inconclusive` · `failed` · `info`), a one-line `summary`, and the `raw_output` (up to 5 MB, kept with the record; lists carry a 2,000-character preview). Optional `finding_id` / `finding_host_id` when it bears on a finding, `observed_ip`, `executed_at`, and `host_test_id` + `request_key` when it answers a proposed test (see [Workflow B](#workflow-b--run-tests-and-record-evidence); re-sending a `request_key` with the same content returns the record already stored). Evidence needs no test: record anything you ran. A record is never changed afterwards — it is the audit trail. `GET /agent/evidence` (`list_evidence`; filters `host_id`, `finding_id`, `host_test_id`, `agent_session_id`) lists them; the full output is `GET /agent/evidence/{id}/raw` (curl — up to 5 MB of text, too much for a tool result).

**Proposals (a person decides).** A change to what the team has CONCLUDED or what the CLIENT REPORT says is never made directly. You propose it; the operator or a colleague accepts (and may edit) or rejects it, and on accept it runs as them:

| Proposal | Route (MCP tool) | Body |
|---|---|---|
| Report text for a finding | `POST /agent/proposals/finding-text` (`propose_finding_text`) | `finding_id`, `fields` {`description` · `impact` · `recommendation` · `references` · `steps_to_reproduce` · `cvss_vector`: text} — one proposal per field. **Each value is the section's complete new text, report-ready** (see below) |
| A new finding | `POST /agent/proposals/finding` (`propose_finding`) | `title`, `severity`, `host_ids`, optional `status` (`open` · `confirmed`), `report_text` |
| Promote or dismiss a scanner observation | `POST /agent/proposals/observation` (`propose_observation`) | `vulnerability_id`, `action` (`promote` · `dismiss`), optional `scope` (`host` · `issue`), `severity`, `summary` |
| An endpoint's status | `POST /agent/proposals/endpoint-status` (`propose_endpoint_status`) | `finding_id`, `finding_host_id`, `host_status` (`open` · `remediated` · `retest` · `false_positive`) |

**Report text is a rewrite, not a review.** Accepting a `finding_text` proposal replaces the whole section with your value, word for word, and it goes into the client report. So each value is the section's complete new text, written for the client: finished prose (or the steps, or the references), keeping what was right in the current text. It is never a critique ("the description should mention…"), a list of suggestions, a diff, or a note to the author. What you changed, and why, belongs in `rationale`, which the reviewer reads beside the proposal. Propose only the sections you would change; a section that is fine gets no proposal. Asked to "review" a finding, this is still the shape: rewrite what needs it, then explain in `rationale`.

**Saying "I don't have enough to write this" is the right answer, not a failure.** Write a section only from what BlueStick or your own recorded evidence shows: the finding, its hosts, its evidence records, the scanner observations and the notes. When that is too thin for a section (impact with no evidence of what the system holds or who reaches it, a recommendation with no product or version to name, steps to reproduce with no recorded command), **do not propose that section**, and never fill the gap with a plausible guess, a generic sentence, or a placeholder ("TBD", "[needs confirmation]", "information not available"): a section's text goes into the client report word for word, and a confident wrong sentence there is worse than an empty one. Instead, say what is missing and what would let you write it: in `rationale` on the sections you do propose ("Impact not proposed: no evidence of what data the portal holds; a record of an authenticated session would show it"), and to your operator in the session — when you can write nothing, tell them that and ask for the context. The same holds for a new finding's `report_text` (`propose_finding`): leave out a section you cannot support.

**Images in report text.** A section may place one of the finding's own images with an ordinary Markdown image whose target is `evidence:<id>`: `![The relayed session](evidence:57)`. The report prints that image there, as a numbered figure (the text in the brackets is its caption for that place; left empty, the image's own caption prints). `GET /agent/assist/findings/{id}` lists the finding's images under `images`: `id`, `caption`, `in_report`, `printable` and `placed_in` (the fields that place it now). When you rewrite a section:

- **Keep the references it already holds**, unless the image should no longer be in that section. A reference you drop is not an error: the image goes back to the finding's Evidence block.
- **Reference only ids from that finding's `images`** with `printable: true`. Any other id (another finding's image, a number you made up) is refused with a 422 that names it. A new finding (`propose_finding`) has no images yet, so its text cannot place any.
- **You cannot tick an image "In report" or caption it**; those are the decisions of the person who attached it. A proposal that places an image with `in_report: false` is accepted only after a person ticks it (the accept is refused with the reason until then), so say in `rationale` that it needs ticking. Any other image (a web address, a file path) prints as its alt text only.

The finding's author and owner are notified that an AI proposed changes (once per session, not per proposal) — also for a promote / dismiss proposal on an observation that already evidences their finding. A proposal that is on no finding yet (a new finding, or an observation nobody has promoted) has no author or owner, so the project's admins are notified instead. The Proposals page shows each person the proposals on their own findings (project admins, everyone's). Every proposal takes an optional `rationale` (what the reviewer should know) and `evidence_ids` (records from this project that support it) — cite them. Proposing changes nothing, so it never waits; `GET /agent/proposals` (`list_proposals`, `mine=true` for this session's) shows what was decided: `pending`, `accepted` (`result_finding_id` for a new finding), `rejected` (with the reviewer's `decision_note` — what to change; follow it in a new proposal), or `superseded` (another proposal for the same field was accepted first). Everything else you write — notes, review status, hostname/OS corrections, uploads, host tests, evidence, feedback — is direct and carries your session.

### What the user sees

Every `/api/v1/agent/*` request is recorded and surfaced to the operator under "API activity" on your agent session's page, filterable by host, target IP, and status code. Don't try to obscure activity by routing around the API — you'd only be visible-but-suspicious instead of visible-and-correct. Operate transparently.

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

Do not paste the rules verbatim. A recital is something you can produce without having read anything, and it gives the operator nothing to check. Restating means resolving the rules against *this* session — this directory, these CIDRs, this project — which is the part that can be wrong. Call `agent_identity` and `assist_list_scopes` (or `GET /agent/identity` and `GET /agent/scopes`) for the specifics rather than guessing at them. Before scanning a scope, read its CIDRs and names (`GET /agent/scopes/{scope_id}/subnets` and `/domains`, MCP `scope_list_subnets` / `scope_list_domains`) and state them.

A scanning read-back:

> I'm working scope **acme-dmz** (`10.10.0.0/24`, `10.10.4.0/24`; names: `portal.acme.com` exactly and anything under `*.lab.acme.com`). Everything runs from `./networkmapper-acme-dmz` and every output file lands there. I'll show you each command as I go. I'll ask before touching an address outside those two ranges or a name those domains don't cover, writing anywhere but that folder, or installing or changing anything on your machine. An address one of those names resolves to isn't in scope just because the name is.

A read-back for proposing tests — no commands yet, so it's about data:

> I'm proposing tests in **acme-internal** for the 14 hosts you have in review, and only those. I'll read their ports and findings and put the tests I'd run on each host's page; nothing runs until you tell me to run them, and then I'll show you each command from `./networkmapper-acme-testing-<session>`.

Why this is a rule and not a nicety: **BlueStick cannot enforce the bounds.** Commands run on the operator's machine and the server sees only what you report. The read-back is the one moment a human sees your *understanding* of the bounds rather than your output — a wrong scope costs a sentence to fix there and a great deal more after the scan. It also makes your own words the record: an agent that said it would write to one directory and then wrote elsewhere has visibly contradicted itself, which an operator notices.

If the operator corrects you, take the correction as binding and say what changed before continuing.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance,testing" -->

## Working directory & concurrent agents (MANDATORY)

One operator often runs **two agentic workflows at once** (e.g. one agent scanning a scope while another runs tests on hosts). BlueStick isolates those server-side (per-session key + audit trail; the ingestion worker serializes uploads safely), but the **operator's machine is shared** — two agent processes see the same filesystem and process table, and nothing isolates them there unless you do.

**Before running any tool, create a session-scoped working directory
and `cd` into it:**

```
mkdir -p networkmapper-<project_slug>-<session_id>
   # e.g. networkmapper-homenetwork-42
cd networkmapper-<project_slug>-<session_id>
```

Build the name yourself: the project slug from `GET /agent/project` and your `session_id` (from `GET /agent/identity`). Your prompt does not name the directory for you — you choose it, and your read-back states it. The qualified path self-documents which project a folder belongs to and survives a Nuclear-Clean reset (session ids restart at 1) without colliding with leftover folders.

Run **every** tool from inside this directory; every output file, target list, and result directory (`nmap.xml`, `httpx.jsonl`, `targets.txt`, `eyewitness-results/`, …) lives here.

Why this matters: two agents both writing `targets.txt`/`nmap.xml` into a shared cwd silently overwrite each other, and `pgrep nmap` can't tell your scan from another agent's. Because every command runs from `networkmapper-<project>-42/`, that path is in each process's argv — `ps aux | grep networkmapper-homenetwork-42` matches **only your session's** processes. Never identify a process by tool name alone when other agents may be running. **For a backgrounded scan, capture its PID at launch** (`nmap … & echo $!`) and poll *that PID*, not the name.

Do not delete the directory when you finish — the operator may want the raw tool output. Cleanup is their call.

### The working directory and the scope are the bounds

That directory is not just for tidiness — with the declared scope, it is where "go ahead" stops (safety rules 3 and 4):

1. **The output lands here.** Every file a command writes goes into this working directory. `-oX nmap.xml`, not `-oX /tmp/nmap.xml`; no writes to a home directory, a system path, or another session's folder.
2. **The target is in the declared scope.** An address inside the scope's subnets, or a name a declared domain covers — exactly, or as a subdomain of an `include_subdomains` entry (`GET /agent/scopes/{id}/domains`, or a `names[]` entry on a host you read). Resolving or probing any other name, or touching an address outside the ranges, waits for the operator's explicit go-ahead. And the address a name resolves to is **not** thereby in scope: `portal.acme.com` in scope + resolves to `203.0.113.7` outside every CIDR = you may probe the name; you may not sweep the address's neighbours or treat `203.0.113.7` as subnet-in-scope.

**Outside those bounds, ask first.** Reading or writing outside this directory, installing software, changing settings or credentials, a target outside the scope — present it, explain why you need it, and wait for an explicit yes.

Inside them you still show every command (rule 1) and still work on what the operator asked for (rule 2): being in bounds makes a command permissible, not requested.

<!-- agents:end -->

---

<!-- agents:section tags="testing" -->

## Workflow A — Propose Tests on Hosts

A **host test** is one check on one host: the tool, what it establishes, the exact command, and why it is worth running. You propose tests on the hosts the operator names; each appears on its host's page (the **Tests** section) the moment you propose it. There is nothing to register or open first. Proposing runs nothing — you run a test only when the operator asks (Workflow B).

```bash
# 1. Read the hosts you were asked about: ports, services, vulnerabilities,
#    names. For a set of hosts use the same query the Hosts page takes.
GET /agent/assist/hosts?q=<host query>            # e.g. has:critical, follow:mine OR assigned:me
GET /agent/assist/hosts/{host_id}                 # one host in full

# 2. Read the tests already there — do not propose a duplicate.
GET /agent/host-tests?host_id={host_id}           # MCP: host_tests_list
GET /agent/host-tests?q=<host query>&active_only=true   # tests on every host a query matches

# 3. Propose. Up to 200 tests per call; the whole batch is validated before
#    anything is written.                           (MCP: host_tests_propose)
POST /agent/host-tests
{"agent_model": "claude-opus-5-5",
 "tests": [
   {"request_key": "smb-null-10.0.1.5",
    "host_id": 412,
    "tool": "netexec",
    "description": "Validate anonymous/null-session SMB access on the already-open 445",
    "command": "netexec smb {ip} -u '' -p '' --shares",
    "expected_result": "Share list with READ/WRITE markers, or an explicit access-denied. Flag any anonymous READ/WRITE share.",
    "rationale": "445/tcp open, SMB signing not required (scanner observation).",
    "priority": "high",
    "label": "SMB review 2026-10-01"}
 ]}
#    → 201 {"items": [ {id, host_id, status: "proposed", revision: 1, ...} ]}
```

**What you propose is what the operator asked for** — a service, a finding to verify, a host list they chose, a low-severity check a ranking would skip. You never widen it: hosts outside their request get no tests, and a host outside the declared scope needs their go-ahead before you propose anything that would touch it. When they gave no direction, a sensible default is critical- and high-severity observations first, then hosts exposing several services or a high-value port (SMB, RDP, databases) — say that this is your ordering, not theirs.

**Control flow:** read (steps 1–2), tell the operator briefly what you found ("36 hosts match, 12 with critical vulnerabilities; 9 already carry tests"), propose (step 3), then report what you proposed ("28 tests on 14 hosts, 9 critical, label *SMB review 2026-10-01*") and ask whether to run any. Do not wait between steps 1 and 3 — proposing changes nothing on the network.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `request_key` | Yes | Your own stable key for THIS test (≤100 chars), unique in the project. Re-sending the same key with the same content returns the test already stored — a safe retry; the same key with different content is a `409`. |
| `host_id` | Yes | The host the test is for. A host that is not in this project is a `404` and nothing in the batch is written. |
| `tool` | Yes | Tool name as invoked (`nmap`, `netexec`, `curl`, `testssl.sh`, …). |
| `description` | Yes | What this test establishes. |
| `rationale` | Yes | Why this host warrants it — the service, port, version or observation you read. "Needs review" is not a rationale. |
| `command` | No | The exact command. `{ip}` is the host's address; `{fqdn}` is `target_fqdn` when set. Write output into the working directory. |
| `expected_result` | No | What to look for — what counts as a finding and what counts as a pass. |
| `references` | No | Up to 20 `http(s)` URLs (tool docs, CVEs, technique references). Anything else is a `422`. |
| `priority` | No | `critical` · `high` · `medium` (default) · `low` · `info`. |
| `label` | No | Groups the tests of one request (≤255 chars). Give every test in a batch the same label so the operator can find the batch again — `testlabel:"…"` on the Hosts page. |
| `target_fqdn` | No | A NAME the test is aimed at (web tests need the Host header / SNI to reach the right vhost). It must be a name observed at this host — one of `names` on `GET /agent/hosts/{host_id}` — or the call is a `422`. Leave it unset for a test against the bare address. |
| `vulnerability_id` | No | The scanner observation this test would CONFIRM or rule out — an `id` from the host's scanner rows (`GET /agent/assist/hosts/{host_id}/vulnerabilities`, MCP `assist_get_host_vulnerabilities`; the host detail carries only severity counts). It must be an observation on this same host (`422` otherwise). The test is then shown on that weakness on the host's page, and a result that shows the issue is promoted as that observation — joining the issue's finding if one exists — instead of becoming a separate finding. Set it whenever the test is about a specific observation; leave it unset for a test about something no scanner reported. The stored test carries `issue_key` / `issue_title`. |
| `assigned_to_id` | No | A project analyst or admin to assign it to. Normally omit — the operator assigns. The person is notified (one notification for a batch on a host; nothing when it is your own operator), so assign only when the operator asked for it. |

Unknown fields are refused (`422`), not ignored.

### What to propose

> **Build on what is already scanned.** Each host's open ports, services and versions are in the inventory. A test is **targeted validation or exploitation against a KNOWN open port**, not rediscovery. Do **not** propose discovery or service-version scans (`nmap -sn`, `nmap -sV`, `--top-ports …`, `-p-`) as tests — that is scanning (Workflow C), and here it is wasted work and IDS noise. An `nmap --script` test means a **specific named NSE script** against the known port (`--script ssl-enum-ciphers -p 443`), never a port or version sweep.

| What the host shows | priority | Tools (aimed at the known port) |
|-----------|----------|-------------------|
| Critical vulnerability (confirmed RCE / SQLi / auth bypass) | `critical` | `nuclei -t cves/<cve-id>`, a named `nmap --script <vuln>`, `curl`, exploit-specific tools |
| High vulnerability (TLS weakness, known CVE without a confirmed exploit) | `high` | `testssl.sh`, `nuclei`, `nikto`, a named `nmap --script` |
| Web services (HTTP/HTTPS) | `high` | `whatweb`, `gobuster` / `ffuf` (content discovery on the known web port), `nuclei -t exposures/`, `nikto` |
| SMB / file shares (445, 139) | `medium` | `netexec smb`, `smbclient`, `enum4linux` |
| Remote access (SSH, RDP, VNC) | `medium` | `netexec ssh/winrm/rdp`, service clients |
| Databases (MySQL, MSSQL, PostgreSQL, …) | `medium` | `netexec mssql`, service-specific clients |
| Several services, no vulnerabilities | `low` | Targeted default-credential / configuration checks on the *identified* products (`netexec`, `nuclei -t default-logins/`); propose nothing if there is nothing to validate |

One test per check — three checks on one host are three tests, not one test with three commands — so each can be started, finished or dismissed on its own and its evidence is its own.

**Name the observation a test confirms.** When a test exists to confirm or rule out one scanner observation (a Nessus plugin, a nuclei match, a misconfiguration check), pass that observation's `vulnerability_id`. The operator then sees the test on the weakness itself, and its result settles that weakness rather than starting a parallel finding. When the operator asks for a test "for this observation" the task names the id — use it.

**Tool notes:**
- **`nuclei`** — template-based scanner for CVE validation, exposed panels, default configurations and fingerprinting. `-t cves/<cve-id>` to validate a known vulnerability, `-t exposures/` on web surfaces. Prefer `-rl 50` in shared environments.
- **`testssl.sh`** — TLS weakness validation (cipher strength, protocol downgrades, certificate chain, HSTS). Non-intrusive.
- **`netexec`** — SMB / WinRM / RDP / MSSQL / SSH enumeration: null-session checks (`--shares -u '' -p ''`), default-credential sweeps (sanctioned wordlists only). **Credentials come from the operator:** a credential-bearing test uses only what they gave you, against the hosts they named — say so in the `rationale`.

**Bad** (nobody can act on it): `"description": "SMB check"` with no command. **Good**: the structured test above — a `tool`, an exact `{ip}` command, and an `expected_result` saying what counts as a finding.

<!-- agents:end -->

---

<!-- agents:section tags="testing" -->

## Workflow B — Run Tests and Record Evidence

When the operator asks you to run tests — the ones on a host, the ones under a label, the ones assigned to them — you work each test from the working directory, showing every command, and record what came back as **evidence**. A test's result is never written onto the test: it is the evidence record that answers it, and evidence with a real outcome is what makes the host *tested*.

> **Key principle:** the operator drives; the operator's terminal is the executor. Show each command, run it (if you have shell access) or ask the user to run it, then record the result verbatim. When a result points somewhere new (another host, a follow-up exploit), propose it (Workflow A) rather than taking it.

```bash
# 1. Read the tests you were asked to run.
GET /agent/host-tests?host_id={host_id}&active_only=true      # MCP: host_tests_list
GET /agent/host-tests?label=<label>&active_only=true
GET /agent/host-tests?mine=true&active_only=true              # assigned to your operator
#    Each test carries its `id`, `revision`, `command`, `host_ip`, `target_fqdn`
#    and `evidence_count`. A test with evidence already may be done — read it
#    (step 4's list call) before running it again.

# 2. Mark the test in progress.                               # MCP: host_tests_update
PATCH /agent/host-tests/{test_id}
{"expected_revision": 1, "status": "in_progress"}
#    → the test, with its new `revision`. Use THAT revision on your next change.
#    409 = someone changed the test since you read it: read it again
#    (GET /agent/host-tests/{test_id}) and decide; never retry blind.

# 3. SHOW the command, always:
#      "Tool: netexec  Command: netexec smb 10.0.1.5 -u '' -p '' --shares
#       Expected: share list, or access denied."
#    Inside the bounds (target in the declared scope, output in the working
#    directory): run it, and say that you are.
#    Outside them: add "Shall I run this? [yes / modify / skip / abort]" and
#    WAIT for an explicit yes.

# 4. Record what came back — the command AS RUN, the outcome, the output.
POST /agent/evidence                                          # MCP: record_evidence
{"host_id": 412, "host_test_id": 87, "request_key": "smb-null-10.0.1.5-run1",
 "tool": "netexec",
 "command": "netexec smb 10.0.1.5 -u '' -p '' --shares",
 "outcome": "finding",
 "summary": "Anonymous READ on the ADMIN$ share.",
 "raw_output": "SMB  10.0.1.5  445  FS01  [+] ...",
 "observed_ip": "10.0.1.5",
 "executed_at": "2026-10-01T09:12:00Z",
 "agent_model": "claude-opus-5-5"}
GET /agent/evidence?host_test_id=87                           # MCP: list_evidence

# 5. Close the test.
PATCH /agent/host-tests/{test_id}
{"expected_revision": 2, "status": "done"}
```

### Outcomes

| `outcome` | Meaning | Makes the host *tested*? |
|--------|---------|---|
| `finding` | The command ran and demonstrated an issue | Yes |
| `no_finding` | It ran and the issue was not there | Yes |
| `inconclusive` | It ran; the output does not settle it | Yes |
| `failed` | It could not run (network error, tool crash, refused) | No |
| `info` | Context you are recording, not a test | No |

Record a `failed` attempt too — it is part of the record — and say why in the `summary`.

### Test statuses

| Status | Meaning |
|--------|---------|
| `proposed` | Nobody has started it |
| `in_progress` | Being worked |
| `done` | Finished. Needs either an evidence record for the test or a `tester_summary` explaining why none exists — a test nobody ran is not silently "done" (`422` otherwise) |
| `dismissed` | Decided not to run. Needs `dismissed_reason` (`422` without one). Use it when a test does not apply on closer inspection or the operator says to skip it |

`PATCH /agent/host-tests/{test_id}` takes `expected_revision` (required) and any of `status`, `tester_summary`, `dismissed_reason` (only with `status: "dismissed"`), `assigned_to_id`. Put what you *concluded* in `tester_summary`; put what the tool *printed* in the evidence record.

### A finding is a proposal

An evidence record with `outcome: "finding"` does not create a finding. When your evidence shows something the team should conclude, **propose** it — `POST /agent/proposals/finding` (MCP `propose_finding`) citing the record in `evidence_ids` — and a person accepts or rejects it (see [§ Evidence and proposals](#evidence-and-proposals--what-you-did-and-what-you-think-it-means)).

### Named targets

When the test carries `target_fqdn`, run the command against the name (Host header / SNI) and pass `observed_ip` — the address the command actually reached. A name behind a load balancer may resolve differently at run time, and the record must reference the real binding: evidence with a real outcome and an `observed_ip` on a named test is what records that the name was tested at that address. `observed_ip` must be an IPv4 or IPv6 literal (`422` otherwise).

### Resuming an interrupted session

If your host crashed or the agent stopped mid-work, the session is **resumed**, not restarted. The operator clicks **Resume** on the session in BlueStick (Agent Sessions, or the session's own page); that rotates the session's API key and hands you a new instructions block carrying a `⟳ RESUMED SESSION` notice. Nothing else changed: the tests the session proposed and the evidence it recorded are where it left them.

When your instructions carry that notice:

- **Read the record before doing anything else:** `GET /agent/host-tests?agent_session_id={session_id}` (the tests this session proposed) and `GET /agent/evidence?agent_session_id={session_id}` (what it recorded). A test with evidence, or already `done` or `dismissed`, is finished — do not run it again.
- **Look in the working directory** for output a previous process left behind and never recorded; record it rather than re-running the command.
- Continue at the first test still `proposed` or `in_progress`.

### Safety while running tests

The [§ Safety rules](#safety-rules-mandatory) apply as everywhere; here they come down to:

1. **Every command shown.** Inside the bounds — target in the declared scope, output in your working directory — you run it and say that you did; outside them it waits for `yes/modify/skip/abort`. For agents without shell access, the user runs the command and pastes you the output.
2. **The right target.** A host's address can be reassigned and a name can resolve somewhere new. Where that is a real risk, verify before testing — `dig -x {ip}` against the expected hostname, one banner-grab on a known-open port against the service BlueStick recorded — and record what you saw as an `info` evidence record. That is single-port *verification* of recorded data, never a re-scan. If it does not match, stop and ask the operator; do not keep testing a possibly wrong target.
3. **The record.** Every attempt is an evidence record with the command you actually ran and when; records are never changed afterwards.

### Raw output

`raw_output` holds up to 5 MB and is kept with the record (lists carry a 2,000-character preview; the whole of it is `GET /agent/evidence/{id}/raw`, curl). Over that the call is a `413` — trim to the relevant part, or upload the file with `POST /agent/uploads` if it is a supported scanner format. Over MCP the whole request is limited to 1 MiB, so `record_evidence` carries less than that: send larger output with curl to `POST /agent/evidence` (same fields).

**Screenshots and other images.** An evidence record holds text only, and you cannot attach an image anywhere: images are added by a person, on a comment of the finding they illustrate. When a picture shows what text cannot (a logged-in page, a rendered response, a GUI), save it in your working directory under a name that says what it is (`finding-12-admin-login-10.0.1.5.png`), name the file in the evidence record's `summary` (or in your note on the host), and when you report back give the operator the list — each file, what it shows, and the finding or host it belongs to — and ask them to attach it there. Never say an image was uploaded.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance" -->
## Workflow C — Populate Host Data (read a scope, scan, upload)

You read a scope, run your own tools against it from your working directory,
and upload the output to your session as results land. Nothing to open and
nothing to close.

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
#   A failed or cancelled import leaves no scan and nothing that only it
#   created (its new hosts, ports and observations are removed). It does NOT
#   undo what it changed on hosts and ports that already existed (state,
#   service, OS, hostname), the names (DNS) it added, or conflict history;
#   a row a later scan re-observed is kept and belongs to that scan. Fix the
#   file and upload again — the retry writes the same values.
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
| POST | `/agent/hosts/{id}/follow` | Set review status (`{"status": "in_review"}`) — a project write |
| PATCH | `/agent/hosts/{id}` | Correct operator-curated host attributes (`hostname` / `os_name`) after investigation — a project write. Only these two fields; scan-derived facts (ports/services/vulns) are never editable here |
| POST | `/agent/feedback` | **File structured feedback at the moment you hit friction** (a retry, a guess, a workaround, a re-read of this guide) — several short submissions per session. Over MCP the tool is `submit_feedback`. Your session is attributed from your key; `source` names the kind of work: `assist` (queries and notes), `reconnaissance` (scope reads, scanning, uploads) or `testing` (proposing host tests, recording evidence). One submission is about one kind of work. |
| GET | `/agent/identity` | **Who am I** — `session_id`, the project, the operator and their role, `can_write_project_data`, `key_expires_at` / `renew_path` / `renewable_until`. A session has nothing "open": after a resume, what it did before is in `GET /agent/host-tests?agent_session_id=` and `GET /agent/evidence?agent_session_id=`. |
| POST | `/agent/session/renew` | Extend your key's deadline (same key; accepts an already-expired key while the session is under its lifetime cap) |
| POST | `/agent/session/end` | **End the session — the last call you make, only when the operator says they are finished.** Revokes your key. Never refused over unfinished work: the tests you proposed and the evidence you recorded stay, for another session or a person to carry on. Over MCP: `end_session`. Optional `notes`, `agent_model` |
| POST | `/agent/uploads` | **Submit scanner output here** — multipart upload, any supported tool format; nothing to open first. Form fields: `file`, optional `tool_name`, `command_run`, `batch` (see Upload batches & duplicates), and `skip_informational=true|false` (Nessus only, v2.341.0): drop severity-0 report items instead of storing a vulnerability row each — ports are still derived from them. Omit it to follow the project's setting; do not set it on your own initiative, it is the operator's choice. `409 duplicate_scan` = already ingested |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status — only jobs this session uploaded (404 otherwise). MCP `get_upload_job` |
| POST | `/agent/evidence` | Record a command you ran against a host and what came back — direct, never changed (see Evidence and proposals). Pass `host_test_id` + `request_key` when it answers a host test. MCP `record_evidence` |
| GET | `/agent/evidence` · `/agent/evidence/{id}/raw` | Evidence records (newest first; `host_id`, `finding_id`, `host_test_id`, `agent_session_id` filters) · one record's full raw output. MCP `list_evidence` |
| POST | `/agent/proposals/finding-text` · `/finding` · `/observation` · `/endpoint-status` | Propose a change a person decides (see Evidence and proposals). MCP `propose_finding_text` · `propose_finding` · `propose_observation` · `propose_endpoint_status` |
| GET | `/agent/proposals` | Proposals and their decisions (`status`, `kind`, `finding_id`, `mine`). MCP `list_proposals` |
| POST | `/agent/tool-suggestions` | Propose a tool for BlueStick's catalogue (201) — one you used or needed that `list_tools` lacks. Catalogue intake only: it grants or withholds nothing. The response's `already_catalogued` says whether the tool was already there. |

<!-- agents:end -->

<!-- agents:section tags="testing" -->

### Host test endpoints (any session; nothing to open first)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/host-tests` | **Propose tests** — a batch of 1–200 (`{tests: [...], agent_model?}`); 201 returns the stored tests. All-or-nothing: an unknown host (404), an invalid field (422) or a reused `request_key` with different content (409) writes nothing. MCP `host_tests_propose` |
| GET | `/agent/host-tests` | List tests: `host_id`, `status`, `label`, `assigned_to_id`, `agent_session_id`, `mine` (assigned to your operator), `active_only` (proposed + in progress), `q` (a Hosts query — tests on the hosts it matches), `limit` (≤200), `offset`. Returns `{items, total, has_more}`; each item carries `revision` and `evidence_count`. MCP `host_tests_list` |
| GET | `/agent/host-tests/{test_id}` | One test — 404 if it is not in this project. MCP `host_tests_get` |
| PATCH | `/agent/host-tests/{test_id}` | Change `status`, `tester_summary`, `dismissed_reason` or `assigned_to_id`, with the `expected_revision` you read; 409 when the test changed since. MCP `host_tests_update` |

The result of a test is an evidence record — `POST /agent/evidence` with `host_test_id` (Common endpoints, above).

<!-- agents:end -->

<!-- agents:section tags="reconnaissance" -->

### Scope reads and uploads (any session; nothing to open first)

To get a scope's ranges, names or target files, read them by `scope_id` (from `GET /agent/scopes`); to put scanner output into the inventory, upload it and poll the job.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/scopes/{scope_id}/subnets` | Paginated subnet CIDR list for a scope (default 500, max 2000 per page). MCP `scope_list_subnets` |
| GET | `/agent/scopes/{scope_id}/domains` | Paginated in-scope domain list (`{domain, include_subdomains}`) — the names you may resolve/probe without asking. MCP `scope_list_domains` |
| GET | `/agent/scopes/{scope_id}/hosts.ndjson` | **Complete** in-scope per-host dataset, newline-delimited JSON; each open port carries `service`, `tunnel` ("ssl" = TLS-wrapped) and `method` ("table" = the scanner guessed the name from the port number, "probed" = it identified the service, null = the tool did not say). Streamed — redirect to a file (`-o scope-hosts.jsonl`) and query it with `jq`, never read it into context. curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/live-hosts.txt` | **Complete** in-scope IP list, one per line — an `-iL` target file. curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/web-targets.txt` | **Complete** http/https URL list, one per line — a `-l` / `-f` target file. A port is listed when its service was identified as HTTP (https when TLS-wrapped), or when nothing identified it and its number is a common web port; a TLS service that is not HTTP (imaps, ldaps) and a port identified as something else (ssh on 443) are not listed. IPv6 addresses are bracketed (`https://[2001:db8::1]:8443/`). curl only; auditor role or above |
| GET | `/agent/scopes/{scope_id}/named-targets.ndjson` | **Complete** name-scope list, one JSON object per in-scope name: the matching scope rule, the addresses it currently resolves to (each flagged `in_subnet_scope`), web interfaces reached as the name, `unresolved` + `reason` when no address is known. A name never puts its address in scope. curl only; auditor role or above |
| POST | `/agent/uploads` | Upload scanner output for ingestion (multipart; curl). Batches are keyed per agent session |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status. MCP `get_upload_job` |

> **These reads are project-wide.** A key is bound to a project session: `/agent/hosts`, `/agent/dashboard` and `/agent/scans` return the whole project. The scope reads above bound to one scope's subnets (`named-targets.ndjson`: to its domain rules). Being able to read a host does not put it in scope.

<!-- agents:end -->

<!-- agents:section tags="shared" -->

> An agent reads a scope, runs its own tools, and uploads the output — nothing to open. The same session proposes host tests and records evidence with the same key.

### Host list filters (`GET /agent/hosts`)

`state`, `ports` (comma-separated), `services` (comma-separated), `subnets` (CIDR), `has_critical_vulns`, `has_high_vulns`, `has_exploit_available` (hosts with ≥1 vuln flagged exploitable by the Nessus parser), `search`, `limit`, `offset`.

Each host in the response includes `open_port_count` and `vuln_summary` (`{critical, high, medium, low}`) so you can prioritize without calling the detail endpoint.

> **`/agent/hosts` is a BARE ARRAY, paginated, with NO continuation signal.** `limit` defaults to 500 (hard max 5000); the response carries no `total`, `has_more`, or `next_cursor`. To cover a scope you MUST page: issue the request at `offset=0`, then keep incrementing `offset` by `limit` until a request returns **fewer than `limit`** rows (an empty array ends it). A 40,000-host scope queried once at the default 500 returns 1.25% of hosts **with no error and no warning**. For a filtered list with a `total`, prefer `GET /agent/assist/hosts` (and `/agent/assist/hosts/count`); use `/agent/hosts` only for spot cross-checks.

### Rate limit

Default **240 requests/minute** per agent, enforced in FIXED 60-second windows, global across all Uvicorn workers (not per-process). A rejected call still counts toward the window. See the 429 row in **Error Handling** for backoff.

<!-- agents:end -->

<!-- agents:section tags="shared" -->

## Agent Attribution (Required)

A note you write is recorded under your operator — the human who started your session. So that analysts can tell agent work from direct human input at a glance, **every note body you create must start with the agent attribution mark**:

```
🤖 **Agent-generated** — {agent_name}
```

- `{agent_name}` is the `agent_name` field of `GET /agent/project` (or `GET /agent/identity`).
- The mark is the **first line** of the body, followed by a blank line before the rest.

This applies to **notes** (`POST /agent/hosts/{id}/notes`). Host tests, evidence records and proposals need no mark: each is stored as agent work — with your session, client and model — and the pages label them so. Do not prefix a test's `description` or `rationale`, an evidence `summary`, or proposed report text (which goes into the client report word for word).

> **Non-negotiable:** a note without the mark may be mistaken for direct analyst input, confusing audit trails and review. This is a policy requirement, not a suggestion.

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

A note has no status: the body is `{"body": "..."}` and nothing else. A check you ran is evidence (`POST /agent/evidence`); a check still to run is a host test (`POST /agent/host-tests`).

---

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 401 | API key expired, revoked, or invalid | Read the body. `recoverable: true` → POST to `renew_path` with the same key and retry the failed request; never re-run work you already have output for. `recoverable: false` → save your output to a file and ask the user to start a new session. |
| 403 (read-only / not a member) | Your key acts for its operator, and their role changed, or they left the project | Not retryable. Tell the user — only they can fix it. |
| 403 — about your operator | A key carries its operator's project role, re-checked on EVERY request. Writes need the operator to hold `analyst`; a read needs the role its page needs of a person: bulk exports (`*.ndjson`, target lists) and client reports need `auditor`; ingestion issues and uninterpreted lines need `analyst`; everything else any member. You also get 403 if the operator was deactivated or removed from the project mid-session. | Read `can_write_project_data` from `GET /agent/identity` before attempting writes. Do not retry: tell the operator what you were refused and why — only they (or a project admin) can change it. |
| 403 — other | The operation is not one an agent may perform (promoting/dismissing a finding, user or project administration) | Use the endpoints documented in this guide: a triage decision is a proposal (`POST /agent/proposals/*`) a person accepts; for anything else, tell the operator what you wanted. |
| 404 — `detail` begins "No agent endpoint at" | The path is not an endpoint at all (the body carries a `hint`, `guide` and `feedback`). | Do not guess another URL: read the guide or `tools/list`. If you expected that route to exist, file it with `submit_feedback` — the path and what you were trying to do. |
| 404 | Resource not found | Verify the `host_id`, `test_id`, `finding_id` or `scope_id`. It may have been deleted, or it belongs to another project. A list filtered by a `host_id` that is not in the project (`/agent/host-tests`, `/agent/evidence`, `/agent/assist/findings`) is a 404 too — not an empty list. |
| 404 — scope | You read a scope that is not in this project | Use a scope id from `/agent/scopes`. |
| 410 | The project is archived | Not retryable. Ending your session still works; tell the operator. |
| 409 — `detail.code: "duplicate_scan"` (upload) | This exact file is already in the project — a scan, still parsing, or staged awaiting its format review (`detail.scan_id` / `detail.job_id`) | Not a failure — the data is in. Mark the file done and continue. Never retry, rename, or alter the file to force a re-import. |
| 409 — host test | `PATCH /agent/host-tests/{id}`: the test changed since the `revision` you sent. `POST /agent/host-tests` or `/agent/evidence`: the `request_key` was already used for different content. | Stale revision: read the test again and decide whether your change still applies — never retry blind. Reused key: use a new `request_key` for a new test or record; re-send the same content only to retry the same one. |
| 413 (evidence) | `raw_output` is over 5 MB — or, over MCP, the whole request is over 1 MiB (send it with curl to `POST /agent/evidence`) | Trim to the relevant part, or upload the file with `POST /agent/uploads` if it is a supported scanner format. |
| 422 | Validation error | An invalid or unknown field — a wrong enum (`priority`, `status`, `outcome`), a `done` test with no evidence and no `tester_summary`, a dismissal with no reason, a `target_fqdn` not observed at the host. The body names the field; check it against this file. |
| 429 | Rate limited | Default 240 req/min in fixed 60 s windows. Wait for the current window to end (at most 60 s) and retry ONCE — rejected calls still count, so retrying in a loop keeps you locked out for the whole window. |
| 503 — `detail` says a database query "ran longer than the server allows" | A read needed a query that ran past the server's statement limit (30 s by default) and was stopped. Nothing was written; it is not an outage. | Narrow the filter (a tighter `q`, a smaller page, one segment or host) and retry once. Do not report the result as empty or zero — say the read could not be computed. For a whole-project read use the streamed downloads (`hosts.ndjson`, `report-context.ndjson`), which are exempt from the limit. |
| 503 + `Retry-After` (upload) | The sweep's batch label was briefly busy — parallel chunks contended for it. Nothing was stored. | Wait the `Retry-After` seconds and re-POST the same file with the same `batch`. Not a duplicate, not a failure to report. |

---

## Tips

1. **Read before you write.** `GET /agent/assist/hosts?q=…` (with a `total`) and `GET /agent/host-tests?…` tell you what is there and what is already proposed. Report what you found before proposing tests.

2. **Be specific in rationale.** Include the service, port and version or observation you read. "Needs review" is not useful. Say *why* this host warrants this test.

3. **Use follow status to track your review.** `POST /agent/hosts/{id}/follow` with `{"status": "in_review"}` takes a host into the operator's review queue when they ask you to work on it; `none` clears it. Never mark a host `reviewed` on your own initiative — that is the operator's conclusion.

4. **Markdown works in notes.** Use headers, lists, and bold text for readability — analysts read these in the UI.

5. **Report what you did.** After proposing tests or running them, tell the user plainly: "Proposed 28 tests on the 14 SMB hosts in 192.168.1.0/24, label *SMB review*. Want me to run them?"

6. **Don't hallucinate services.** Only report what the API data shows. If a host has port 22 open with `service_name: "ssh"`, say that. Don't infer services that aren't in the data.

7. **When running tests, show every command and never touch a target outside the declared scope without the operator's explicit go-ahead.** The cost of running the wrong command against the wrong host is very high. Where a target could be ambiguous, verify it and record what you saw before testing it.

<!-- agents:end -->

---

<!-- agents:section tags="assist" -->

## Inventory assist (interactive query; optional narrow write)

Use these reads to *query the project* — answer ad-hoc questions, summarize state, and surface findings — via `/agent/assist/*` endpoints. It generates no target traffic by itself. It is the same project session that also scans, proposes tests and runs them when the operator asks.

**You act as the operator who started the session.**  Their project role decides what you may write, checked on every call — there is no separate per-session grant to look up. If a write returns 403, their role does not permit it, and retrying will not change that.

### Runs on any OS

Inventory assistance has **no host-tool requirements** — its "commands" are HTTPS API calls to `/agent/assist/*`, so Windows, macOS, and Linux operators are all first-class (recon/execution, by contrast, need a Linux/Windows scanner toolchain). Only the HTTP-client invocation differs:

- **bash / zsh** (Linux, macOS): `curl -s -H 'X-API-Key: …' '<url>'`
- **Windows PowerShell:** use **`curl.exe`** — bare `curl` is an alias for `Invoke-WebRequest` and won't accept these flags — e.g. `curl.exe -s -H "X-API-Key: …" "<url>"`; or native `Invoke-RestMethod -Headers @{'X-API-Key'='…'} '<url>'`.
- **No `-k` (or `-SkipCertificateCheck`).** BlueStick's certificate is issued by the operator's local root CA; skipping the check accepts any server claiming the address. A certificate error means the root is not trusted on this machine yet (`ca/local-ca.sh trust-help`) or the deployment is still self-signed: tell the operator, and add `-k` only if they say to. For POST bodies, pass `-d (ConvertTo-Json $obj)` to `curl.exe` or `-Body ($obj | ConvertTo-Json)` to `Invoke-RestMethod` rather than bash single-quoted JSON.

### Hard contract

- **You have the operator's permissions, not more.** The key is bound to one project session; project writes require the operator's role to permit them. A 403 is a guardrail, not a route-around opportunity.
- **Inventory assistance itself creates no target traffic.** To scan, propose tests or run them, do it with the same key under the safety rules and that section of this guide.
- **Project-scoped.** The session binds to one project (the one the operator picked at start-up).  You see all hosts in that project; you do not see other projects.  No cross-project access.
- **No target traffic.** You never scan, probe, or otherwise generate traffic to in-scope hosts.  All your data comes from BlueStick's already-ingested state.  If the operator asks you to scan, see "When to hand off" below.

### Endpoint surface

> **MCP transport (lower friction).** These reads and writes, plus the scope-read, host-test, evidence and proposal tools, are exposed over `/api/v1/mcp`. An MCP-capable host (VS Code Copilot, Claude Code, Codex) can call them natively instead of shelling `curl`; a unified-session key sees the complete catalogue. The endpoint enforces project scope and the operator role. The bulk `report-context.ndjson` stream remains a download-to-file rather than a tool.

All under `/agent/assist/*`.  X-API-Key header on every call:

| Endpoint | Purpose |
|---|---|
| `GET  /agent/assist/context` | **Headline** project summary. `default_host_view` (`name`, `filters`) is the view the Hosts page opens on for everyone when a project admin set one — your unfiltered counts are the whole project, so say which set you counted when the operator asks about "the hosts I see". Scope list capped at 50 (check `scopes_truncated`); `recent_scans` capped at 5. Read BEFORE answering — but take real counts from the `totals` block, not the truncated lists. Also the engagement dates (`project.start_date` / `end_date`) and `members` with their project roles ("who is on this engagement"). |
| `GET  /agent/assist/hosts` | List hosts. Discrete filters: `state`, `ports` (port numbers, a host matches with any of them OPEN; no ranges or names — those are a 422), `services` (the service name the scanner identified on an OPEN port, on ANY port number — exactly the Hosts page's filter and `q=service:<name>`; a port found open with no service name, e.g. by masscan, does not match, so ask `ports=` for "the standard ports"), `subnets`, `has_critical_vulns`, `has_high_vulns`, `search`, `limit`, `offset`. **`q` — the full boolean query DSL** (same engine as the human Hosts page): `ip:`, `hostname:` (alias `host:`), `state:`, `port:`, `os:` (OS name or OS family), `service:` (alias `svc:`), `version:` (alias `product:`; service product or version, e.g. `version:"OpenSSH 7"`) — **`port:`/`service:`/`version:` match OPEN ports only**; name another state after `@` on the value: `port:22@closed`, `service:ssh@filtered`, `port:22@unfiltered`, `port:22@open|filtered`, `port:22@any` (every state). A closed/filtered port's service name is nmap's guess from the port number, not evidence the service runs. `portstate:closed` alone is a separate "has some closed port" condition, not a qualifier. `path:` (alias `webpath:`; a path content discovery found, e.g. `path:/admin`), `subnet:` (alias `cidr:`), `scope:` (`subnet` = in a scope subnet, `name` = reached only through an in-scope name, `none` = neither), `vulnscan:` (`credentialed` / `uncredentialed` / `unstated` — whether the vulnerability scan of an ASSESSED host authenticated; a host nobody assessed matches none of them), `org:` (alias `owner:`; the netblock's registered owner, RDAP), `certorg:`, `asn:`, `country:`, `tag:`, `label:`, `site:` (`site:none` = inside a scoped subnet that carries no site — Posture's "Unassigned"), `conclusion:` (what a finished review concluded: `no_issue` / `finding_created` / `needs_evidence` / `out_of_scope` / `duplicate` — `conclusion:needs_evidence` is every reviewed host whose question is still open), `cve:`, `vuln:`, `issue:` (exactly one scanner-observation issue by its key — `check:<id>`, `cve:<CVE>`, `title:<normalised title>` or `row:<id>`, e.g. `issue:"check:smb_signing_not_required"` or `issue:"cve:CVE-2021-44228"`; `vuln:` is a title substring), `kind:` (`misconfiguration` / `vulnerability` / `informational`), `check:` (one misconfiguration-catalog check whichever tool reported it — e.g. `check:smb_signing_not_required`, `check:smbv1_enabled`, `check:smb_null_session`, `check:vnc_no_auth`, `check:ftp_anonymous`, `check:tls_deprecated_protocol`, `check:tls_cert_expired`, `check:http_missing_hsts`, `check:dns_zone_transfer_allowed` (a name server that handed a zone over to dnsx) — the `check_id` on a host's findings is the value to use), `exploitport:`, `header:`, `webtitle:`, `tech:`, `note:`, `scan:`, `firstseen:` / `changedsince:` / `vulnsince:` (time windows — quote the ISO value: `firstseen:"2026-09-19T20:00:00Z"` = hosts first observed since then; `changedsince:"<start>..<end>"` = hosts already known that gained a port or a scanner observation; `vulnsince:"critical@<start>"` = a critical observation recorded since, severity and time on the same row), `has:`, **`follow:`** (`watching` / `in_review` / `reviewed` / `none` / `in_review_any` — any teammate's review counts — `mine`: your operator has it In Review, exactly their "Hosts I am reviewing"; and `revisit`: a finished review of your operator's that is not done — they concluded `needs_evidence`, or the host gained an open port or a critical/high observation after THEIR review — exactly Operations' "Changed since review"), **`assigned:`** (alias `assignee:`) combined with `AND`/`OR`/`NOT` and parentheses. `has:` values: `eol`, `smb_unsigned`, `weak_auth`, `cert_issue`, `weak_tls`, `cleartext`, `critical`/`high`/`medium`/`low`, `exploit`, `critical_exploit` (a critical that is ITSELF exploitable — `has:critical AND has:exploit` also matches a critical beside an exploitable low), `web`, `open_ports`, `tested`, `planned`, `notes`, `stale_review`, `changed_since_review` (reviewed, then an open port first seen or a critical/high scanner observation recorded AFTER the review — ANY teammate's review; with `OR conclusion:needs_evidence` it is the team-wide list, and `follow:revisit` is the operator's own, which is what Operations' "Changed since review" shows), `untouched` (nobody has touched it: no review or assignment, note, host test that was not dismissed, evidence record or finding), `local_admin` (a credential was local admin — NetExec "Pwn3d!"), `writable_share` (a share granted WRITE). `GET /agent/assist/vocabulary` returns the values this project uses after `tag:`, `label:`, `site:` and `assigned:` — use it instead of guessing (a guessed tag returns zero hosts, not an error). `assigned:me`/`follow:` resolve against the operator who started the session; `assigned:`/`assignee:` also take a **username** (case-insensitive) or numeric id. `q` ANDs with the discrete filters; a malformed `q` returns 400. **Returns `{items, total, has_more, limit, offset}`** (paginated: default 500, max 5000). **`total` is every matching host — quote it, never the length of `items`**; raise `offset` by `limit` while `has_more`. Rows carry `exploitable_count` and `critical_exploitable_count` (same-row, as the Hosts page's "critical · exploit"). `sort_by` takes the Hosts page's keys (`ip_address` default, `critical_vulns`, `high_vulns`, `exploitable_vulns`, `open_ports`, `note_count`, `discovery_count`, `hostname`, `last_seen`) with `sort_order=asc\|desc`. `GET /agent/assist/hosts/by-ip/{ip}` is the host detail by address. |
| `GET  /agent/assist/hosts/count` | **How many hosts match** — same filters and `q=` as the list; returns `{count, query}`. Use this for every counting question instead of paging. |
| `GET  /agent/assist/hosts/{host_id}` | One host with ALL its ports, in any state — filter on each port's `state` (can be large — prefer `open_port_count` from the list for triage), `os_family`, and up to 10 `web_interfaces` (`web_interfaces_total` / `web_interfaces_truncated`; the full list is `/hosts/{host_id}/web-interfaces`). Each port's `protocol` is the IP transport (`tcp`/`udp`); the application (smb/http/…) is `service_name`. `open_port_count` = distinct physical open ports. The host's `vuln_summary` is **severity counts only** — for the actual CVEs/evidence use the findings endpoint below. Both list and detail also carry `follow` = the session operator's review status on the host (watching/in_review/reviewed, or null), so you can check it before writing follow. Detail also carries what the host inspector shows: `names` observed at the address, OS/MAC/NetBIOS detail, `smb_signing`, `tags`, `assignees`, `scope_membership`, per-domain `assessment` (its `vuln_scan_credentialed` says whether a vulnerability scan logged in to the host: `yes` / `no` / `not_stated`, null when not assessed — `no` and `not_stated` are still assessed; say which it was when you report a host as clean), `weakness_flags` / `weakness_labels`, certificate facts (`cert_orgs`, `cert_status`), `attributions`, NSE script output per port and per host (bounded — check `*_truncated`), scan `conflicts` (where scans disagreed), `note_count` and `finding_count`. |
| `GET  /agent/assist/hosts/{host_id}/vulnerabilities` | **The scanner rows on a host** (raw observations, NOT project findings) — the evidence `vuln_summary` only counts. Returns `{host_id, items, total, has_more, limit, offset}` (before v2.453.4 the path ended `/findings` and the rows were keyed `findings`). Each row also says which project finding covers its issue: `finding_id` / `finding_status` (the issue's), `finding_on_this_host` (false = the finding covers other hosts only — the row is still unjudged here) and `finding_endpoint_status` (this host's own state on it). Each carries `severity`, `cve_id`/`plugin_id`, `title`, `port_number`/`service_name` (null = host-level), `exploitable`, `cvss_score`, `source` (the scanner), `check_id` (the misconfiguration-catalog check, null for a scanner's own finding), `description`, `solution` (remediation), and `evidence` (scanner output; truncated). Filter `?severity=critical,high`. **Paginated with `total`/`has_more`** (default 200, max 1000) — page `offset` until `has_more` is false to report complete coverage. Use this for evidence-rich reporting on ONE host. |
| `GET  /agent/assist/report-context.ndjson` | **The report data source — use this to write a report.** Streams the COMPLETE per-host dossier for every matching host, one JSON object per line, **uncapped**: identity, ports (transport + service), findings (severity/CVE/plugin/port/evidence/remediation), notes, scan discoveries, canonical findings, test findings (`execution_findings[]`: evidence records whose outcome is `finding`) and tester summaries, provenance, tags, and the operator's review state. Same discrete filters + `q` DSL as `/agent/assist/hosts`. This is the same correlated record the server-side report builds — populate your report template from it instead of stitching together per-host calls. **Redirect to a file and process it locally; NEVER read the stream whole into context** (`curl -s -H "X-API-Key: $KEY" ".../agent/assist/report-context.ndjson" -o report-context.jsonl`). Safe on tens-of-thousands-of-host projects — the server hydrates one chunk at a time. |
| `GET  /agent/assist/hosts.ndjson` | **The complete matching host set** — same filters + `q` DSL as `/agent/assist/hosts`, but uncapped and streamed one JSON object per line. Use this instead of paging when the project is large: redirect to a file and query it locally (`curl -s -H "X-API-Key: $KEY" ".../agent/assist/hosts.ndjson" -o hosts.jsonl`, then `jq`/`grep`/`wc -l`). Report counts from the file, never a truncated page. Never read the stream into context whole. |
| `GET  /agent/assist/scopes` | Scope CIDR lists **and declared domains** — **each capped at 100 per scope**. Each ScopeBrief carries `subnet_total` / `subnets_truncated` and `domain_total` / `domains_truncated`; when a `*_truncated` flag is true the list is only a sample, so tell the operator it's partial — the full lists are `GET /agent/scopes/{scope_id}/subnets` and `/domains` (MCP `scope_list_subnets` / `scope_list_domains`), paged, with this same key. `domains[]` entries are `{domain, include_subdomains}` (exact name vs the name and everything under it); `names_in_scope_total` is the deduplicated count of inventory names they cover. **Name scope is independent of subnet scope**: an in-scope name does not put the address it resolves to in scope, and an in-scope subnet does not put names in scope. |
| `GET  /agent/assist/names` | The named-asset inventory (FQDNs), paged (`limit` default 100, max 1000, `offset`). Each row: `in_scope` (a declared domain covers it), `current_ips` (derived from the latest A/AAAA observations — never stored; empty = unresolved), `current_ip_total`, `sources` (observation kinds: A, AAAA, IMPORT, HTTP, CERT, …). Filters: `q`, `in_scope`, `resolved`, `host_id` (names currently bound to that host's address), `kind`. **How to act:** `in_scope=true&resolved=false` is the queue — names the operator approved that no upload has ever resolved (chase with dnsx/amass output, or tell the operator to drop them from scope). A name whose address is shared with other names (`current_ip_total` on the host's other names, a load balancer / vhost) must be tested **by name**, not by IP — the bare address reaches a different site. |
| `GET  /agent/assist/scans` | Scan inventory, newest-first — **default 100, max 500**; `offset=` pages further back. The answer is `{items, total, has_more, limit, offset}`: `total` is the count for the filter, so "how many nmap scans?" is one call with `limit=1`. `tool=` narrows to one tool's scans (the Scans page's chips): the last two nmap scans are `tool=nmap&limit=2`. Each row carries `ingestion_job_id` — the import that produced it, the `job_id` `/assist/uninterpreted-lines` takes (a scan id is not a job id) — and `time_source`: `tool_run` / `tool_records` mean the start and end times are instants, returned in UTC with an offset; `tool_clock` means the scanner's own wall clock, zone unknown, returned WITHOUT an offset — never correlate it with a UTC event as if it were UTC. An nmap scan's rows also carry `scan_info`: per scan type / protocol, the port list the scan was asked to probe (`services`, e.g. `1-1000`) — a port outside it was not looked at, which is not the same as closed. Empty for a tool that does not report it. |
| `GET  /agent/assist/session` | Your own session metadata (purpose, started_at, the operator `assigned:me` refers to). |
| `GET  /agent/assist/vocabulary` | The values this project uses: tags, labels, sites, scope names, usernames (for `assigned:`), finding statuses and severities. |
| `GET  /agent/assist/findings` | **Triaged findings** (not raw scanner rows): filters `status` (a status, or the group `active` / `resolved`; `all` or omitted for every one — any other value is a `422`, never an empty result), `severity`, `source`, `host_id` (a host not in this project is a `404`), `unowned=true`, `owner` (username or `me`), `search`; default 50, max 500. The report's findings come from here. |
| `GET  /agent/assist/findings/{finding_id}` | One finding with its affected hosts (per-host endpoint status, and each row's `finding_host_id` — the id `propose_endpoint_status` and `record_evidence` take) and evidence; `report_text` (description, impact, recommendation, references, steps to reproduce, CVSS vector and score — what a report will say), `endpoint_status_counts`, and `status_history` (who changed the status, when, from→to, why). `images` is the finding's images as the report sees them: `id`, `caption` (what the report prints under it; null → the file name), `filename`, `in_report` (ticked for the report), `printable` (PNG / JPEG / GIF), `placed_in` (the report-text fields whose Markdown places it with `![caption](evidence:<id>)`; empty for a ticked image means it prints under Evidence) and `download_path`; each note attachment also carries its `caption`. `scanner_evidence` lists at most 100 of the scanner rows that evidence the finding; `scanner_evidence_total` is how many there are and `scanner_evidence_truncated` says the list was cut — quote the total, never the length of the list. |
| `GET  /agent/assist/scanner-observations` · `/scanner-observations/hosts?issue_key=` | Scanner results grouped by ISSUE across the project (`host_count`, `judged_host_count`, the covering finding) — the Findings page's "Scanner observations" view; unjudged issues only unless `include_judged=true`. Then the hosts carrying one issue — `{items, total, has_more}`, paged with `limit`/`offset`. `sort=hosts` puts the most widespread first; a row with `judged_host_count > 0` is partly judged (listed until every host is). |
| `GET  /agent/assist/client-reports` · `/client-reports/{id}` · `/client-reports/{id}/files/{fmt}` · `/client-reports/{id}/scope.csv` | The Reports page (operator needs `auditor`): drafts and issued reports; one report with every finding as the report states it (`content_source`: `issued_snapshot` = what the client was given, `draft_live` = what a draft would say now); the rendered files; the complete scope as CSV (curl it to a file). A report over its template's scope cutoff does not list the scope. Its `scope.external` is true, and `summary.scope_external.file` names this file and its SHA-256, which the report prints. **How a finding was confirmed:** each finding's `confirmations` are the test results the report prints for it (`tool`, `host`, `command`, `summary`, an `output` excerpt with `output_truncated`, `date`, `by`, `by_agent`) — at most 10, with `confirmations_omitted` counting the rest. `by` is the name the report prints: the person who recorded the result, or — for a result an agent recorded — the OPERATOR of that agent's session, in full; the report says nothing about the session or the agent (`by_agent` is in the data for counting only). They are only what THIS report prints: the list is empty when the report's template does not print them, or shows that finding without its details (an addendum's already-reported finding). `summary.evidence_records` is how many the report prints, `summary.evidence_records_not_printed` how many it holds back for that reason, and `summary.agent_evidence_records` how many of the printed ones an agent recorded. **Images:** each finding's `images` is every image ticked "In report" (`attachment_id`, `caption`, `placed_in` — the sections whose text places it), with `printed` (does this report's template print it) and `printed_in` (in which of those sections; a printed image with `printed_in: []` is in the trailing evidence block); `evidence` is the images no section places. `summary.images_printed` (inside a section), `images_trailing` (the trailing block) and `images_not_printed` add up to `summary.images`; `images_not_printed_reasons` counts why (`finding_not_detailed`, `section_not_printed`, `no_evidence_block`) and `template_images` is what the template declares it prints (`{fields, trailing}`). All of these are `null` when the printing could not be measured and absent from a report issued before they existed — then say nothing about where an image prints. **An addendum** says why each finding is in it: `change` is `new`, `new_hosts` (the added endpoints are `new_affected`) or `severity_changed` — a finding already reported whose severity differs from the baseline report's, with `previous_severity` / `previous_severity_label` holding what the client was told; `delta` counts them (`new_findings`, `findings_with_new_endpoints`, `findings_with_changed_severity`, `withdrawn`). Only severity is compared, never title or status. |
| `GET  /agent/assist/hosts/{host_id}/web-interfaces` | Every web interface on a host: URL, title, server, technologies, screenshot reference, and the certificate / TLS facts (`cert_not_after`, `cert_self_signed`, cert organisations, `tls_weak_protocol`, and as the web panel reads them `tls_version`, `cert_issuer`, `cert_subject_cn`, `cert_sans` + `cert_san_total`). Null = the tool did not report it, not "fine"; an expired certificate is `check:tls_cert_expired`. |
| `GET  /agent/assist/hosts/{host_id}/access` | NetExec / SMBMap results on the host (logins, shares, local admin) next to the raw tool line — which may contain credentials the tool found. |
| `GET  /agent/host-tests?host_id={host_id}` · `GET /agent/evidence?host_id={host_id}` | What has been proposed for the host (each test with its status and `evidence_count`) and every command recorded against it. The host detail's `assessment` says whether it counts as tested. |
| `GET  /agent/assist/hosts/{host_id}/notes` · `GET /agent/assist/notes` | Notes on one host · across the project, with their threads (`parent_id` / `thread_root_id`), type, whether it is pinned, the `finding_id` an older thread was promoted to, and attachment references. `/assist/notes` rows carry `target {kind, id, label}` (host, port, finding, scan, scope or project). |
| `GET  /agent/assist/workbench` | Your operator's Operations page: `my_work` (what is waiting on them, by kind — say the kinds apart, as the page does, never as one sum: `findings_to_decide` (under investigation, or a proposal waits for their decision), `findings_to_write` (only required report text is missing; the two add up to `findings_needing_me`), `tests_assigned`, then what they hold: `hosts_in_review` and `tests_on_hosts_in_review` (tests on those hosts, NOT assigned to them); `to_claim` beside it; `total` is still returned and is those parts added up), `my_queue`, `my_tasks` (`group_counts` counts each test once), `my_findings` (findings they own that NEED them — each row's `needs` says why: under investigation, required report text missing, a proposal to decide; a confirmed, written-up finding is not listed), `since_last_visit`, `followups` ("Changed since review": YOUR OPERATOR'S own finished reviews that are not done — the host changed after their review, or they concluded `needs_evidence`; never a teammate's review; one row per host, `total` hosts, the same hosts as `q=follow:revisit`), blockers. It carries no project-wide measures (removed v2.451.0 — Operations is the operator's own page; project status is Posture's) and no team roster (`team_review` was removed in v2.451.1 — what the team is already reviewing is `GET /agent/assist/hosts?q=follow:in_review`, any teammate's). Reading it never marks anything seen; `*_unavailable: true` means not computed, not "nothing" and not 0. **The lists are previews** (hosts 10, tests 10 per group, findings / follow-ups 15) — the page itself shows one full list at a time, as tabs (Findings · Hosts · Tests · Changed since review · Pick up, v2.452.0) — so quote the counts, never a list's length. For a WHOLE list: hosts in review `GET /agent/assist/hosts?q=follow:mine`; changed since review `?q=follow:revisit`; tests assigned `GET /agent/host-tests?mine=true&active_only=true`; tests on hosts in review `GET /agent/host-tests?q=follow:mine&active_only=true` (every test to do on those hosts; `group_counts.in_review` leaves out the ones assigned to the operator, counted under `assigned`); the untouched queue is the next row. Findings that need the operator have no whole-list read beyond this preview and its count (`GET /agent/assist/findings` filtered by owner lists every finding they own). Each `my_tasks` row carries its `tool`. |
| `GET  /agent/assist/workbench/investigate?tier=&limit=&offset=` | "Untouched, with a reason" (the page called it "Worth a look" until the 2026-10-02 redesign; the MCP tool keeps its name, `assist_list_worth_a_look`): untouched hosts with reasons, in stated tier order (1 exploitable critical … 5 scans disagree); `queue_total` / `tier_counts` are whole-queue. |
| `GET  /agent/assist/workbench/terrain?sort=address\|untouched\|critical_untouched&limit=` | Hosts per /24 (IPv6 /64): tested / planned / worked / untouched, and `critical_untouched` — Posture's "Where the team has been". |
| `GET  /agent/assist/evidence/gaps?domain=&segment=&limit=` | The Evidence page's gap list: eligible-but-unassessed hosts, their ports, and the step that closes the gap (`domain` = a key from `/assist/coverage`). Respect `scope_caution`. `segment` is `matrix.segments[].key` from `/assist/coverage` (a site id, a subnet key or `unmapped`) — NOT a CIDR; a wrong value answers 404 listing the accepted keys. |
| `GET  /agent/assist/scans/compare?a=&b=&limit=` | What changed between two scans: hosts new / gone / changed, ports newly open / closed / not observed (not observed is not remediation). |
| `GET  /agent/assist/scans/{scan_id}/hosts?state=&search=&skip=&limit=` | The hosts ONE scan observed, as it observed them (the scan page's "As scanned" table; MCP `assist_list_scan_hosts`): state and hostname at scan, `host_created`, the ports it saw (at most 50 listed per host; `observed_port_count` / `open_port_count` are exact), and `credentialed` — whether the scan authenticated to the host: `true` / `false` when the scanner said so (Nessus), `null` when it did not say. **`null` is "not stated", never "no"**: do not report an nmap or an old Nessus import as unauthenticated. Read `total` / `has_more`; page with `skip`. |
| `GET  /agent/assist/coverage` · `/segments` · `/posture` · `/patterns` | Scope coverage; the Posture page's segments, headline and recurring-weakness patterns. `/coverage`'s `vuln_assessment` domain carries `credentialed` = `{credentialed, not_credentialed, credentials_not_stated}`: of the assessed hosts, how many a scanner logged in to (they add up to the assessed count). A clean result from a scan that did not authenticate is weaker evidence — quote the split whenever you say how much was assessed for vulnerabilities; list each with `q=vulnscan:credentialed` / `uncredentialed` / `unstated`. |
| `GET  /agent/assist/ingestion-issues` | Imports that failed, were partial, or skipped records (operator needs `analyst`, as the Ingestion Results page does). Counted as that page counts them: `failed` (an import went wrong), `expired` (a staged upload nobody started within 24 hours — never imported, nothing failed), `discarded`, `degraded`, and `needs_attention` (failed or partial, not dismissed, not replaced by a later import — the number Operations calls blocked imports). |
| `GET  /agent/assist/uninterpreted-lines?job_id=` | Lines an import did not read, as redacted shapes (NetExec imports record them; operator needs `analyst`). A `job_id` that is not an import job of this project is a 404; an empty page for a real job means every line was read. |
| `GET  /agent/assist/attachments/{attachment_id}` · `/web-interfaces/{interface_id}/screenshot` | Evidence files (any member, as in the UI). Over MCP, `assist_get_image {attachment_id | interface_id}` shows one inline (image content, up to 2 MB); the `download_path` references are for saving files beside a report. |

**Write routes.**  Note these live under `/agent/hosts/…`, not `/agent/assist/…`:

| Endpoint | Purpose |
|---|---|
| `POST /agent/hosts/{host_id}/notes` | Add a note. Body `{"body": "..."}`. A note is discussion for the team — context, a question, a handoff — and has no status: a check you ran is evidence (`POST /agent/evidence`), a check to run is a host test. An `@username` in an agent's note notifies nobody — ask the operator to mention someone. |
| `POST /agent/hosts/{host_id}/follow` | Set review status. Body `{"status": "in_review"}` (`in_review` \| `reviewed`; `none` clears it). |
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
2. **Say which question you answered when the words have more than one.** Each of these is a different, correct number:
   - "Hosts with ports 80 and 443" — `ports=80,443` (and `q=port:80,443`) is EITHER open; BOTH open is `q=port:80 port:443`.
   - "SMB hosts" — `services=` matches the scanner's own name for the service, and scanners call SMB `microsoft-ds`: ask `ports=445` for "SMB port open", `q=service:microsoft-ds` for "identified as SMB". `services=smb` matches almost nothing.
   - "Web servers" — `services=http` (identified as HTTP), `q=has:web` (a fingerprinted web interface), the Evidence page's web-eligible hosts, and the URLs in `web-targets.txt` are four different counts.
   - "How many criticals?" — critical FINDINGS (`/assist/findings?severity=critical`), critical scanner ISSUES (`/assist/scanner-observations`), critical scanner ROWS and the HOSTS carrying one (`/assist/posture` → `scanner_observations.by_severity` / `hosts_by_severity`). "Exploitable criticals" is `q=has:critical_exploit` (one observation that is both), not `has:critical AND has:exploit`.
   - "Open findings" — `status=open` is the one status; `status=active` (open, confirmed, retest) is what the Findings page opens on.
   - "Hosts in review" — `q=follow:mine` (your operator's) or `q=follow:in_review` (anyone's).
   - "Planned hosts" — `q=has:planned` counts every host with a proposed or in-progress test; the terrain's `planned` column is exclusive (a tested host is counted as tested, not also planned), so it is smaller.
   - "Names in scope" — `/assist/names?in_scope=true` lists names seen in the inventory; `named-targets.ndjson` also carries the declared scope domains themselves.
   - "How many hosts?" — the project's, those in scoped subnets (`q=scope:subnet`), or those up (`state=up`).
   - `search=` is a substring of IP, hostname and OS name only; the Hosts page's search box is `q=<word>` (which also matches the OS family).
3. **Answer the operator's question** using the filter vocabulary above.  Examples:
   - "Which hosts have FTP open?" → `GET /agent/assist/hosts?ports=21` (= `q=port:21`: port 21 open, whatever answers there). `services=ftp` (= `q=service:ftp`) is a different question — FTP identified on any port — and it is the one the Hosts page's service filter answers. Say which you asked. **"How many?"** is `GET /agent/assist/hosts/count` with the same filter, or the list's `total`.
   - "Which hosts do I have in review?" → `GET /agent/assist/hosts?q=follow:mine` (the session operator's own In Review; `follow:in_review` is ANY teammate's). "Assigned to me?" → `q=assigned:me`.
   - "Propose tests for the hosts assigned to me or in review by me" → `GET /agent/assist/hosts?q=assigned:me OR follow:mine` for exactly those hosts, `GET /agent/host-tests?q=assigned:me OR follow:mine` for what they already carry, then `POST /agent/host-tests` (MCP `host_tests_propose`) with one `label` for the batch. The operator takes hosts into review from Operations' **Pick up** tab (the "Untouched, with a reason" queue), so this is how their review queue becomes tests; do not propose for hosts outside the query result.
   - "What's exposed to Log4Shell?" → `GET /agent/assist/hosts?q=cve:CVE-2021-44228 OR vuln:"log4j"`.
   - "Which SMB hosts have a working exploit *on 445*?" → `GET /agent/assist/hosts?q=exploitport:445`. Note `exploitport:445` (exploit ON that port, same finding) is stricter than `q=port:445 AND has:exploit` (445 open AND *any* exploit anywhere).
   - "What critical findings landed this week?" → `GET /agent/assist/hosts?has_critical_vulns=true` (or `q=has:critical`) + `GET /agent/assist/scans?limit=20` to correlate.
   - "Summarize scope X" → `GET /agent/assist/scopes` to confirm CIDR list + `GET /agent/assist/hosts?subnets=...` for the host count and posture.
   - "What should we look at next / what's mine / what changed since I was last here?" → `GET /agent/assist/workbench/investigate` · `GET /agent/assist/workbench` (`my_work`, `since_last_visit`).
   - "Where does the engagement stand?" → the `total` of `GET /agent/assist/hosts?q=has:tested` (hosts tested), `?q=has:untouched has:critical` (untouched hosts carrying a critical observation) and `?q=has:changed_since_review OR conclusion:needs_evidence` (ANY teammate's reviewed hosts that are not done) — pass `limit=1` when only the count is wanted; `GET /agent/assist/workbench/terrain` gives the first two per address block, and `GET /agent/assist/posture` the assessment. The workbench does not answer this: it is your operator's own queue.
   - "What is the team already reviewing?" → `GET /agent/assist/hosts?q=follow:in_review` (ANY teammate's In Review hosts; `total` is the count — your operator's own are `q=follow:mine`). The workbench carries no team roster.
   - "Which hosts I reviewed have changed?" → `GET /agent/assist/workbench` → `followups` (`total`), or `GET /agent/assist/hosts?q=follow:revisit` for the whole list.
   - "What changed between the last two scans?" → `GET /agent/assist/scans?limit=5` to pick them (`&tool=nmap` for the last two nmap scans), then `GET /agent/assist/scans/compare?a=…&b=…`.
   - "Which hosts have an exploitable critical, worst first?" → `GET /agent/assist/hosts?q=has:critical_exploit&sort_by=critical_vulns&sort_order=desc`. Not `has:critical AND has:exploit` — that also matches a critical beside an exploitable low.
   - "What is still unassessed in segment Y?" → `GET /agent/assist/coverage` (domain and segment keys), then `GET /agent/assist/evidence/gaps?domain=…&segment=…`.
   - "What are the most widespread issues not yet made findings?" → `GET /agent/assist/scanner-observations?sort=hosts` (most widespread first; `min_hosts=` narrows).
   - "What did we report to the client?" → `GET /agent/assist/client-reports`, then `/client-reports/{id}` (`content_source: issued_snapshot`).
   - "Who closed finding X, and why?" → `GET /agent/assist/findings/{id}` → `status_history`.
   - "Which names are we allowed to test but haven't found yet?" → `GET /agent/assist/names?in_scope=true&resolved=false` (the context's `names.in_scope_unresolved` is the count). "What sits behind 10.0.0.5?" → `GET /agent/assist/names?host_id=<id>` — several names on one address means test each **by name**.
4. **Cite what you read — and count with the count endpoint.** Every claim maps back to a specific endpoint + filter. "How many" is ONE call: `GET /agent/assist/hosts/count` (MCP `assist_count_hosts`) takes the same filters and `q=` as the list and returns `{count, query}`. Page the list, or take the `hosts.ndjson` download, only when you need the rows themselves — and then one 500-row page is **not** "500 hosts." Say "12 hosts (per `?ports=21`, from `/hosts/count`)," never a one-page count on a project that may have thousands of hosts.
5. **Flag uncertainty.**  If the data is ambiguous (e.g. the host has port 21 open but no service name), say so.  Don't infer.

### When to hand off

When your synthesis points to a follow-up that requires action, propose it, and do it only when the operator asks — with this same key, under the safety rules and that work's section of this guide (its read-back and exit criteria):

- **"You should scan these hosts more thoroughly."** → Read the scope (`GET /agent/scopes/{id}/subnets`), scan within it from your working directory, and upload the output (`POST /agent/uploads`).
- **"These hosts need testing."** → Propose tests on them (`POST /agent/host-tests`, MCP `host_tests_propose` — Workflow A); when the operator says to run them, work each test and record its evidence (Workflow B) — no approval step sits in between.
- **"Mark these as in review for me."** → With write access, do it for the hosts assigned to them (announce first). Without it, or for hosts assigned to someone else: "I can't change follow status for that target in this session. Use the `/hosts` page checkboxes, or I can hand you a filter URL to apply."

The operator drives every action; you assist their query.

### What you can NOT do

- Create notes or change follow status when `can_write_project_data` is false. Cannot assign hosts to anyone, ever.
- Access other projects, or list other operators' agent sessions.
- **Promote, dismiss or otherwise triage a finding directly.** That judgement is a person's: PROPOSE it (`POST /agent/proposals/observation`, `/agent/proposals/endpoint-status`, `/agent/proposals/finding`) with your evidence records, and the operator or a colleague accepts or rejects it. The same holds for a finding's report text (`/agent/proposals/finding-text`).
- Delete a host test. A test that should not be run is **dismissed** with a reason (`PATCH /agent/host-tests/{id}`), and stays on the record.

### Tone

You're a research partner.  Concise responses.  Lead with the answer, then show your work (endpoints called, filters applied).  Roll with mid-session pivots ("actually, just show me the up hosts") — assist is conversational by design.

<!-- agents:end -->
