# BlueStick AI Agent Guide

**Prompt version:** 4.21.0

The number above is the `prompt_version` in your session prompt and on `GET /agent/assist/context`. If they differ, the deployment changed mid-session: fetch this guide again.

This is the reference for an AI agent working in a BlueStick project session: field shapes, request bodies, limits and endpoint detail. **The rules are in your session prompt** (over MCP: the server's opening instructions, which carry the same ones) — the safety rules, the read-back before you start, long-running commands, key expiry, the certificate, feedback and ending the session. They are not repeated here.

Your key is the API key the operator pasted to you. It reaches `/api/v1/agent/*` and the public reference surfaces this guide names (`/.well-known/networkmapper.json`, `/api/v1/agents-guide`, `/api/v1/references/*`, `/api/v1/mcp`). Do not log in, mint keys, create agents or call anything else: it answers 401 or 403. No key? Ask the operator: **Operations → Start Agent Session**.

**Parts of this guide.** `GET /api/v1/agents-guide?part=<part>` (MCP `read_agent_guide`) returns the shared sections plus one part: `reconnaissance` (scope reads, scanning, uploads), `testing` (host tests, evidence, proposals, the host query language), `assist` (the inventory reads, notes, proposals, the host query language) or `remediation` (remediation tracking). Leave `part` out for the whole guide; any other value is a 422. A part is a reading aid, not a kind of session and not a permission: one session does every kind of work, in whatever order the work needs.

---

<!-- agents:section tags="shared" -->

## Instance Identity (verify once) — what the server enforces

`GET /.well-known/networkmapper.json` (no key) returns `instance_id`, `name`, `version`, `purpose` and `safety_properties`. Your prompt says how to check `instance_id` against it. `safety_properties` separates what the server enforces from what it cannot:

Enforced by the server:
- `server_executes_commands: false` — BlueStick never runs a command; everything runs on the operator's machine.
- `agent_authority: "operator_project_role"` — your key may do exactly what your operator's project role allows, re-checked on every request.
- `agent_key_binding: "project_session"` — your key is bound to one project session and one operator.
- `agent_keys_time_limited` / `agent_keys_renewable` — your key expires (24 h by default) and you can renew it while the session is under its lifetime cap. Ending the session, not expiry, is what revokes it.
- `audit_trail_recorded: true` — every `/agent/*` request you make is recorded and shown to the operator. `audit_trail_retention_days` says for how long the installation keeps those rows (0 = kept).

Yours to uphold — the server cannot see your terminal:
- `command_approval: "operator_driven"` — you show every command; a target outside the declared scope, anything outside the working directory, or a change to the operator's machine waits for their explicit go-ahead.
- `command_approval_enforced_by: "agent_and_client_sandbox"` — holding to those bounds is your discipline plus your client's sandbox. The server contributes the record of what you report; it cannot stop a command.

## Authentication, keys and the session

Every request to `/api/v1/agent/*` carries the key: `X-API-Key: nm_agent_...` (`Authorization: Bearer nm_agent_...` is also accepted). There is no `project_id` in any URL: the key is bound to one project session and every read and write is scoped to that project.

- **401.** The fields are under `detail`: `detail.error` (`key_expired` · `operator_credentials_changed`), `detail.recoverable`, `detail.renew_path`, `detail.message`. What to do with each is in your prompt (§ If your key expires).
- **Renewal.** `POST /agent/session/renew` (MCP `session_renew`) extends the same key's deadline; it accepts an already-expired key while the session is under its lifetime cap. `GET /agent/identity` returns `key_expires_at`, `renew_path` and `renewable_until`.
- **The session's states.** A session ends on `POST /agent/session/end` (MCP `end_session`; optional `notes` of at most 2,000 characters, and `agent_model`), when the operator ends it, or when an hourly sweep finds its key expired and `renewable_until` passed (the session's start plus its maximum lifetime, 7 days by default). Until then the operator's Agent Sessions page lists it as **live** (the key is valid) or **resumable** (the key expired but can be renewed, or the operator can resume the session with a new key). Ending never loses work: tests, evidence, proposals and uploads are project data.
- **A resumed session.** The tests and evidence are where the session left them: `GET /agent/host-tests?agent_session_id={session_id}` and `GET /agent/evidence?agent_session_id={session_id}`. A test with evidence, or one that is `done` or `dismissed`, is finished — do not run it again.
- **Feedback.** `POST /agent/feedback` (MCP `submit_feedback`); the body is in your prompt, and no field is required. The answer is an acknowledgement, not your text: 201 `{id, status, agent_session_id, created_at, friction_notes_chars, api_critique_count, tool_suggestion_count}`.

### MCP — the same endpoints as tools

If your client speaks MCP, every tool call loops back into the endpoint this guide describes, with the same key and the same checks. Every tool is listed; the endpoint behind it decides on each call by your operator's project role, so a listed tool can still answer 403. Bulk data is not a tool: the NDJSON streams, the target files and the upload itself (`POST /agent/uploads`) stay curl, because they belong in a file on disk and not in your context. One MCP request is limited to 1 MiB. `GET /api/v1/references/mcp-tools` lists every tool the server exposes.

## Attribution — what the record says about you

BlueStick does not inspect the operator's machine and does not choose your tools or commands: which tools are installed and which command fits a host is yours to work out with the operator. What the server records is who did the work:

| Recorded | Source |
|---|---|
| **Client** (`generated_by_tool`) | Over MCP, the `initialize` handshake's `clientInfo` (name and version). A curl agent: the first call's `User-Agent`; a handshake name replaces it, never the other way round. You send nothing for this. |
| **Prompt version** | Set by the server when the session starts or is resumed. You send nothing for this. |
| **Model** (`generated_by_model`) | Your own report: the optional `agent_model` (e.g. `"claude-opus-5-5"`) on `POST /agent/host-tests`, `POST /agent/evidence`, each `POST /agent/proposals/*`, `POST /agent/remediation/apply`, `POST /agent/remediation/assign-from-report` and `POST /agent/session/end`. |

The session keeps the last model reported. A host test, an evidence record and a proposal each take a snapshot of the session's attribution (client, model, prompt version) when created; a record made without `agent_model` carries the session's last-reported model, or none. Several proposals for one field from different models stand side by side, and the model is what tells them apart.

Every authenticated `/agent/*` request, and an `/agent/` path that matched no route, is recorded and shown to the operator on the session's page ("API activity", filterable by host, target address and status code).

## Safety rules

The five rules are in your session prompt or, over MCP, the opening instructions. BlueStick cannot enforce them: commands run on the operator's machine and the server sees only what you report, so the client's sandbox is the boundary and the recorded audit trail is what a person reviews. A target outside the declared scope, anything outside the working directory, or a change to the machine waits for the operator's explicit go-ahead. Report accurately, including when you went outside the bounds and why.

**Reading the declared scope.** `GET /agent/scopes` (curl) returns every scope with all its subnets and domains. MCP `assist_list_scopes` is a different read, `GET /agent/assist/scopes`: the first 100 subnets and the first 100 domains of each scope. When its `subnets_truncated` or `domains_truncated` is true, page `scope_list_subnets` / `scope_list_domains` (`GET /agent/scopes/{scope_id}/subnets`, `/domains`) before you state the scope. If the project declares no scope, ask the operator what you may touch.

**A name in scope does not put its address in scope.** `portal.acme.com` in scope and resolving to `203.0.113.7` outside every CIDR: test the name (Host header / SNI); the address, its neighbours and the other names on it are not in scope. An in-scope subnet does not put names in scope either.

`GET /api/v1/references/tools` (MCP `list_tools`) is the team's tool catalogue; it is not a permission list.

## Say the rules back before you start

The rule is in your session prompt or, over MCP, the opening instructions. Two examples of the result — specific to the session, a few lines, not a recital.

Before scanning:

> I'm working scope **acme-dmz** (`10.10.0.0/24`, `10.10.4.0/24`; names: `portal.acme.com` exactly and anything under `*.lab.acme.com`), as **jsmith**. Everything runs from `./networkmapper-acme-42` and every output file lands there. I'll show you each command as I go. I'll ask before touching an address outside those two ranges or a name those domains don't cover, writing anywhere but that folder, or installing or changing anything on your machine. An address one of those names resolves to isn't in scope just because the name is.

Before proposing tests — no commands yet, so it is about data:

> I'm proposing tests in **acme-internal** for the 14 hosts you have in review, and only those. I'll read their ports and scanner observations and put the tests I'd run on each host's page; nothing runs until you tell me to run them, and then I'll show you each command from `./networkmapper-acme-internal-42`.

If the operator corrects you, the correction is binding: say what changed before you continue.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance,testing" -->

## Working directory & concurrent agents

One operator often runs two agents at once. BlueStick keeps them apart on the server (a key and an audit trail per session; uploads are serialised), but the operator's machine is shared: two agent processes see the same filesystem and process table.

**Before running any command, create a session-scoped working directory and `cd` into it:**

```
mkdir -p networkmapper-<project_slug>-<session_id>     # e.g. networkmapper-homenetwork-42
cd networkmapper-<project_slug>-<session_id>
```

`slug` is on `GET /agent/project` and `session_id` on `GET /agent/identity`. You choose the directory and your read-back states it.

- **Every command runs from it and every output file, target list and result directory lands in it** — never `/tmp`, a home directory, a system path or another session's folder. Two agents writing `targets.txt` into a shared directory silently overwrite each other.
- **Identify your processes by the directory or the PID, never by tool name.** The path is in each process's arguments, so `ps aux | grep networkmapper-homenetwork-42` matches only this session's processes. For a backgrounded command, capture its PID at launch (`<command> & echo $!`) and poll that PID.
- **Do not delete the directory when you finish.** The operator may want the raw output; cleanup is their call.
- **After a resume**, look here first for output a previous process left behind and never uploaded or recorded; upload or record it rather than running the command again.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance" -->

## Populate Host Data — scan and upload

You read a scope, run your own tools against it from your working directory, and upload each output file as it finishes.

### Scope reads

All paths are under `/api/v1`. The subnet and domain reads are also in the common endpoints.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/scopes` | Every scope: `[{id, name, description, subnets, subnet_total, domains: [{domain, include_subdomains}], domain_total, names_in_scope_total}]`. Uncapped; curl. |
| GET | `/agent/scopes/{scope_id}/subnets` | The scope's CIDRs, paged: `offset`, `limit` (default 500, max 2000). Answers `{total, limit, returned, has_more}` with the list; raise `offset` by `limit` while `has_more`. MCP `scope_list_subnets` |
| GET | `/agent/scopes/{scope_id}/domains` | The scope's declared domains (`{domain, include_subdomains}`: the exact name, or the name and everything under it), paged the same way. MCP `scope_list_domains` |
| GET | `/agent/scopes/{scope_id}/hosts.ndjson` | Every in-scope host with its open ports, one JSON object per line. Each open port carries `service`, `tunnel` (`"ssl"` = TLS-wrapped) and `method` (`"table"` = the scanner guessed the name from the port number, `"probed"` = it identified the service, null = the tool did not say). |
| GET | `/agent/scopes/{scope_id}/live-hosts.txt` | Every in-scope address, one per line. |
| GET | `/agent/scopes/{scope_id}/web-targets.txt` | Every http/https URL, one per line. A port is listed when its service was identified as HTTP (https when TLS-wrapped), or when nothing identified it and its number is a common web port. A TLS service that is not HTTP (imaps, ldaps) and a port identified as something else (ssh on 443) are not listed. IPv6 addresses are bracketed (`https://[2001:db8::1]:8443/`). |
| GET | `/agent/scopes/{scope_id}/named-targets.ndjson` | Every in-scope NAME, one JSON object per line (see Name scope). |

The four files are complete, streamed, curl only, and need your operator to be a project auditor or above. A scope that is not in this project is a 404.

```
curl -sS -H "X-API-Key: $KEY" "$URL/api/v1/agent/scopes/$SCOPE/hosts.ndjson" -o scope-hosts.jsonl
```

**Scale.** A scope can hold thousands of CIDRs and tens of thousands of hosts. Redirect every file to disk and process it locally; never read one into your context.

**These bound to one scope; the other reads are project-wide.** `/agent/hosts`, `/agent/dashboard` and `/agent/scans` return the whole project. Being able to read a host does not put it in scope.

### Name scope

`hosts.ndjson`, `live-hosts.txt` and `web-targets.txt` are subnet scope (addresses only). For a domain-scoped engagement read `named-targets.ndjson`: one record per name a scope domain rule covers —

`{name, name_id, scope_rule: {domain, include_subdomains, match: exact|subdomain}, addresses: [{ip_address, record_type, last_observed, host_id, in_subnet_scope}], unresolved, reason, web: [{interface_id, url, scheme, port, ip_address, at_current_address, source, status_code, title, observed_at}]}`

`addresses` is what the name CURRENTLY resolves to (the latest A/AAAA observations). Where `in_subnet_scope` is false, the name is authorised on that address and the address is not: test by name (Host header / SNI), never the whole address and never the other names on it. Names only a certificate SAN or a shared address connect to are not listed; a declared domain nothing has observed is listed `unresolved` with its `reason`.

### Uploading

```
POST /agent/uploads   (multipart: file=@out.xml, tool_name=<tool>, command_run=<the command>, batch=<sweep label>)
#   → 201 { job_id, filename, status, message, batch_id, batch }
GET  /agent/uploads/{job_id}                         (MCP: get_upload_job)
#   → poll until status is completed / failed. Only jobs this session uploaded (404 otherwise).
```

- **Formats.** Any format BlueStick parses; the list, and what each becomes, is `GET /api/v1/references/parser-coverage`.
- **Form fields.** `file`; optional `tool_name`, `command_run`, `batch` (at most 200 characters) and `skip_informational` (Nessus only: drop severity-0 report items instead of storing a row each; ports are still derived from them). Omit `skip_informational` to follow the project's setting — it is the operator's choice, not yours.
- **Chunk large sweeps.** A large scan's output can exceed `MAX_FILE_SIZE` (1 GB by default). Split it and send each chunk with the **same `batch` label**: every chunk with that label in this session joins one batch, shown on the Scans page as a single row.
- **A duplicate is not a failure.** An identical file already in the project (a scan, still parsing, or staged) answers `409` with `detail.code: "duplicate_scan"` naming it. The data is in: mark the file done and move on. Never rename or alter a file to force a re-import.
- **A busy batch** answers `503` with `Retry-After`; nothing was stored. Wait and re-POST the same file with the same `batch`.
- **A failed or cancelled import** leaves no scan and nothing that only it created (its new hosts, ports and observations are removed). It does NOT undo what it changed on hosts and ports that already existed (state, service, OS, hostname), the names (DNS) it added, or conflict history; a row a later scan re-observed is kept and belongs to that scan. Fix the file and upload again — the retry writes the same values.

The parsed hosts land in the project inventory and are correlated to scopes afterwards, so a sweep that discovers an adjacent host is kept, not rejected: staying inside the scope is your discipline, not a server boundary.

<!-- agents:end -->

---

<!-- agents:section tags="testing" -->

## Propose Tests on Hosts

A **host test** is one check on one host: the tool, what it establishes, the command, and why it is worth running. Each appears on its host's page (the **Tests** section) the moment you propose it. Proposing runs nothing, so it does not wait on anyone.

```bash
# 1. Read the hosts you were asked about. For a set, use the Hosts page's own query.
GET /agent/assist/hosts?q=<host query>            # e.g. has:critical, follow:mine OR assigned:me
GET /agent/assist/hosts/{host_id}                 # one host in full

# 2. Read the tests already there — do not propose a duplicate.
GET /agent/host-tests?host_id={host_id}                  # MCP: host_tests_list
GET /agent/host-tests?q=<host query>&active_only=true    # tests on every host a query matches

# 3. Propose. 1–200 tests per call; the whole batch is validated before anything is written.
POST /agent/host-tests                                   # MCP: host_tests_propose
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

The body above shows the shape; the tool and the command are yours to choose.

**Propose what the operator asked for and never widen it:** no tests on hosts outside their request, and a host outside the declared scope needs their go-ahead first. With no direction, choose an order yourself and say it is yours. Afterwards say what you proposed: the count, the hosts, the label.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `request_key` | Yes | Your own stable key for THIS test (at most 100 characters), unique in the project and distinct within the batch (`422` otherwise). Re-sending the same key with the same content returns the test already stored — a safe retry; the same key with different content is a `409`. |
| `host_id` | Yes | The host the test is for. A host that is not in this project is a `404` and nothing in the batch is written. |
| `tool` | Yes | The tool's name as invoked (at most 100 characters). |
| `description` | Yes | What this test establishes. |
| `rationale` | Yes | Why this host warrants it — the service, port, version or observation you read. "Needs review" is not a rationale. |
| `command` | No | The command. `{ip}` is the host's address; `{fqdn}` is `target_fqdn` when set. |
| `expected_result` | No | What to look for — what counts as a finding and what counts as a pass. |
| `references` | No | Up to 20 `http(s)` URLs of at most 2,048 characters. Anything else is a `422`. |
| `priority` | No | `critical` · `high` · `medium` (default) · `low` · `info`. |
| `label` | No | Groups the tests of one request (at most 255 characters). Give every test in a batch the same label so the operator can find the batch again: `testlabel:"…"` on the Hosts page. |
| `target_fqdn` | No | A NAME the test is aimed at (at most 253 characters). It must be a name observed at this host — one of `names` on the host's detail — or the call is a `422`. Leave it unset for a test against the bare address. |
| `vulnerability_id` | No | The scanner observation this test would CONFIRM or rule out: an `id` from `GET /agent/assist/hosts/{host_id}/vulnerabilities` (MCP `assist_get_host_vulnerabilities`; the host detail carries only severity counts). It must be an observation on this same host (`422` otherwise). The stored test carries `issue_key` / `issue_title`. |
| `assigned_to_id` | No | A project analyst or admin to assign it to (`422` for anyone else). Normally omit: the person is notified, so assign only when the operator asked for it. |

`description`, `rationale`, `command` and `expected_result` take at most 10,000 characters each. Unknown fields are refused (`422`), not ignored.

### What makes a test

**A test is a targeted check against something the inventory already shows** — a known open port, service or observation — not rediscovery; a sweep is scanning, and its output is uploaded. One test per check: three checks on one host are three tests, so each has its own status and its own evidence.

**Name the observation a test confirms.** When a test exists to confirm or rule out one scanner observation, pass that observation's `vulnerability_id`. The operator then sees the test on the weakness itself, and a result that shows the issue is promoted as that observation — joining the issue's finding if one exists — instead of becoming a separate finding. When the operator asks for a test "for this observation" the task names the id — use it. Leave it unset for a test about something no scanner reported.

**Credentials come only from the operator:** a credential-bearing test uses what they gave you, against the hosts they named — say so in `rationale`.

A test nobody can act on (`"description": "SMB check"`, no command) is not one: give the tool, the command and what counts as a finding.

## Run tests and record evidence

When the operator asks you to run tests — the ones on a host, under a label, or assigned to them — you work each from the working directory and record what came back as **evidence**. A test's result is never written onto the test: it is the evidence record that answers it, and evidence with a real outcome is what makes the host *tested*. When a result points somewhere new (another host, a follow-up), propose it; do not take it.

**A test's `command` is a sample.** `description` and `expected_result` are the test: run what fits the operator's machine, and put the command you actually ran in the evidence record's `command`.

```bash
# 1. Read the tests you were asked to run.
GET /agent/host-tests?host_id={host_id}&active_only=true      # MCP: host_tests_list
GET /agent/host-tests?label=<label>&active_only=true
GET /agent/host-tests?mine=true&active_only=true              # assigned to your operator
#    Each test carries `id`, `revision`, `command`, `host_ip`, `target_fqdn`, `evidence_count`
#    and `last_outcome`. A test with evidence may be done: read it (GET /agent/evidence?host_test_id=)
#    before running it again.

# 2. Mark the test in progress.                               # MCP: host_tests_update
PATCH /agent/host-tests/{test_id}
{"expected_revision": 1, "status": "in_progress"}
#    → the test, with its new `revision`. Use THAT revision on your next change.
#    409 = someone changed the test since you read it: read it again and decide; never retry blind.

# 3. Show the command (safety rule 1) and run it. Outside the bounds, ask and wait for an explicit yes.
#    Without shell access, the operator runs it and pastes you the output.

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
| `dismissed` | Decided not to run. Needs `dismissed_reason` (`422` without one). Use it when a test does not apply on closer inspection or the operator says to skip it. A test is never deleted. |

`PATCH /agent/host-tests/{test_id}` takes `expected_revision` (required) and any of `status`, `tester_summary` (at most 10,000 characters), `dismissed_reason` (at most 2,000; only with `status: "dismissed"`), `assigned_to_id`. Put what you *concluded* in `tester_summary`; put what the tool *printed* in the evidence record.

### A finding is a proposal

An evidence record with `outcome: "finding"` does not create a finding. When your evidence shows something the team should conclude, **propose** it — `POST /agent/proposals/finding` (MCP `propose_finding`) citing the record in `evidence_ids` — and a person accepts or rejects it (see Evidence and proposals).

### Named targets

When the test carries `target_fqdn`, run the command against the name (Host header / SNI) and pass `observed_ip` — the address the command actually reached. A name behind a load balancer may resolve differently at run time, and the record must reference the real binding: evidence with a real outcome and an `observed_ip` on a named test is what records that the name was tested at that address. `observed_ip` must be an IPv4 or IPv6 literal (`422` otherwise).

### The right target

An address can be reassigned and a name can resolve somewhere new. Where that is a real risk, check the target against what BlueStick recorded before testing, record what you saw as an `info` evidence record, and stop and ask the operator if it does not match. That is a check of recorded data, never a re-scan.

### Raw output and images

`raw_output` holds up to 5 MB and is kept with the record; lists carry a 2,000-character preview (`raw_output_preview`, `raw_output_truncated_in_preview`), and the whole of it is `GET /agent/evidence/{id}/raw` (curl). Over 5 MB the call is a `413`: trim to the relevant part, or upload the file with `POST /agent/uploads` if BlueStick parses its format. Over MCP the whole request is limited to 1 MiB, so send larger output with curl to `POST /agent/evidence` (same fields).

**Screenshots and other images.** An evidence record holds text only, and you cannot attach an image anywhere: images are added by a person, on a comment of the finding they illustrate. When a picture shows what text cannot (a logged-in page, a rendered response, a GUI), save it in your working directory under a name that says what it is (`finding-12-admin-login-10.0.1.5.png`), name the file in the evidence record's `summary` (or in your note on the host), and when you report back give the operator the list — each file, what it shows, and the finding or host it belongs to — and ask them to attach it there. Never say an image was uploaded.

<!-- agents:end -->

---

<!-- agents:section tags="testing,assist" -->

## Evidence and proposals

### Evidence — what you ran (direct)

`POST /agent/evidence` (MCP `record_evidence`) records a command you ran against a host and what came back. It needs no test: record anything you ran. A record is never changed afterwards — it is the audit trail.

| Field | Required | Description |
|-------|----------|-------------|
| `host_id` | Yes | The host it was run against. |
| `tool` | Yes | The tool or method (at most 100 characters). |
| `outcome` | Yes | `finding` · `no_finding` · `inconclusive` · `failed` · `info`. |
| `summary` | Yes | What it showed, in a sentence or two (at most 10,000 characters). |
| `command` | No | The command as actually run, verbatim. |
| `raw_output` | No | The tool's output, up to 5 MB (`413` over that). |
| `host_test_id` + `request_key` | No | When it answers a proposed test. `request_key` is then required (`422` without it); re-sending it with the same content returns the record already stored, with different content is a `409`. The test must be on the same host (`422`). |
| `finding_id` / `finding_host_id` | No | The finding, and the finding's endpoint, it bears on. |
| `observed_ip` | No | The address actually reached (an IPv4 or IPv6 literal). |
| `executed_at` | No | When it ran. |
| `agent_model` | No | The model you are running as. |

`GET /agent/evidence` (MCP `list_evidence`) lists records newest first: filters `host_id`, `finding_id`, `host_test_id`, `agent_session_id`; `limit` (default 50, max 500), `offset`; answers `{items, total, has_more}`.

### Proposals — what you think it means (a person decides)

A change to what the team has CONCLUDED or what the CLIENT REPORT says is never made directly. You propose it; the operator or a colleague accepts (and may edit) or rejects it, and on accept it runs as them. Proposing changes nothing, so it never waits. Everything else you write — notes, review status, hostname/OS corrections, uploads, host tests, evidence, feedback, remediation records — is direct and carries your session.

| Proposal | Route (MCP tool) | Body |
|---|---|---|
| Report text for a finding | `POST /agent/proposals/finding-text` (`propose_finding_text`) | `finding_id`, `fields` {`description` · `impact` · `recommendation` · `references` · `steps_to_reproduce` · `cvss_vector`: text} — one proposal per field; an unknown field is a `422` |
| A new finding | `POST /agent/proposals/finding` (`propose_finding`) | `title` (at most 500 characters), `severity` (`critical` · `high` · `medium` · `low` · `info`), `host_ids` (1–1000), optional `status` (`open` (default) · `confirmed`), `report_text` {the same fields, without `cvss_vector`} |
| Promote or dismiss a scanner observation | `POST /agent/proposals/observation` (`propose_observation`) | `vulnerability_id`, `action` (`promote` · `dismiss`), optional `scope` (`host` — the default for dismiss · `issue` — every host carrying it, the default for promote), `severity`, `summary` (at most 2,000 characters) |
| An endpoint's status | `POST /agent/proposals/endpoint-status` (`propose_endpoint_status`) | `finding_id`, `finding_host_id`, `host_status` (`open` · `remediated` · `retest` · `false_positive`) |

Every proposal also takes `rationale` (what the reviewer should know, at most 10,000 characters), `evidence_ids` (at most 100 records from this project that support it — cite them) and `agent_model`.

**What a create answers (201).** Finding text: `{proposals: [{id, field, status, value_chars, base_value_chars, …}]}` — the lengths, not the text you sent or the text it would replace. A new finding: the row, with `payload.report_text_chars` `{field: length}` in place of the text. An observation or endpoint-status proposal: the row.

**What was decided.** `GET /agent/proposals` (MCP `list_proposals`): `status` (`pending` · `accepted` · `rejected` · `superseded`), `kind` (`finding_text` · `finding_create` · `observation_promote` · `observation_dismiss` · `endpoint_status`), `finding_id`, `mine=true` (this session's), `limit` (default 50, max 500), `offset`; answers `{items, total, has_more}` with the texts. An accepted new finding carries `result_finding_id`; a rejected proposal carries the reviewer's `decision_note` — what to change; follow it in a new proposal; `superseded` means another proposal for the same field was accepted first. The finding's author and owner (or, for a proposal on no finding yet, the project's admins) are notified that an AI proposed changes.

### Report text

**Report text is a rewrite, not a review.** Accepting a `finding_text` proposal replaces the whole section with your value, word for word, and it goes into the client report. So each value is the section's complete new text, written for the client: finished prose (or the steps, or the references), keeping what was right in the current text. It is never a critique ("the description should mention…"), a list of suggestions, a diff, or a note to the author. What you changed, and why, belongs in `rationale`, which the reviewer reads beside the proposal. Propose only the sections you would change. Asked to "review" a finding, this is still the shape: rewrite what needs it, then explain in `rationale`.

**Write each section the way this installation asks.** `GET /agent/assist/findings/{id}` (MCP `assist_get_finding`) carries `writing_guidance`: `general` (applies to every section) and `sections` — instructions for `description`, `impact`, `recommendation`, `steps_to_reproduce` and `references`. For a finding that does not exist yet the same block is `GET /agent/assist/writing-guidance` (MCP `assist_get_writing_guidance`). Read it before you write and write to it: length, structure, tone, what a section must name. It decides style and content only. It never overrides the rules here — a complete rewrite, nothing the data does not support, no BlueStick record named — and where the two seem to disagree, the rules here hold and you say so in `rationale`.

**The reader has never seen BlueStick.** Report text is read by the client, in a document, with nothing to click. So it never names a BlueStick record by its number or id ("Finding #277", "evidence record 57", "host #12", "proposal #9"), never mentions BlueStick, a scanner observation, a proposal or your session, and never says how the text was produced ("based on the recorded evidence…", "as the analyst noted…"). Name another finding by its **title**, a system by its **address or hostname**, and say what was observed rather than which record holds it. Record ids belong in `rationale`. The server refuses a section that names a record: `422` with the phrase quoted (text inside a code span or a fenced block — a command, its output — is not checked). The same holds for a new finding's `report_text`.

**Saying "I don't have enough to write this" is the right answer, not a failure.** Write a section only from what BlueStick or your own recorded evidence shows: the finding, its hosts, its evidence records, the scanner observations and the notes. When that is too thin for a section (impact with no evidence of what the system holds or who reaches it, a recommendation with no product or version to name, steps to reproduce with no recorded command), **do not propose that section**, and never fill the gap with a plausible guess, a generic sentence, or a placeholder ("TBD", "[needs confirmation]", "information not available"): a confident wrong sentence in the client report is worse than an empty section. Instead, say what is missing and what would let you write it: in `rationale` on the sections you do propose ("Impact not proposed: no evidence of what data the portal holds; a record of an authenticated session would show it"), and to your operator — when you can write nothing, tell them that and ask for the context. The same holds for a new finding's `report_text`: leave out a section you cannot support.

### Images in report text

A section may place one of the finding's own images with a Markdown image whose target is `evidence:<id>`: `![The relayed session](evidence:57)`. The report prints that image there, as a numbered figure (the text in the brackets is its caption for that place; left empty, the image's own caption prints). `GET /agent/assist/findings/{id}` lists the finding's images under `images`: `id`, `caption`, `in_report`, `printable` and `placed_in` (the fields that place it now). When you rewrite a section:

- **Keep the references it already holds**, unless the image should no longer be in that section. A reference you drop is not an error: the image goes back to the finding's Evidence block.
- **Reference only ids from that finding's `images`** with `printable: true`. Any other id (another finding's image, a number you made up) is refused with a `422` that names it. A new finding has no images yet, so its text cannot place any.
- **You cannot tick an image "In report" or caption it**; those are the decisions of the person who attached it. A proposal that places an image with `in_report: false` is accepted only after a person ticks it (the accept is refused with the reason until then), so say in `rationale` that it needs ticking. Any other image (a web address, a file path) prints as its alt text only.

<!-- agents:end -->

---

<!-- agents:section tags="assist" -->

## Inventory assist (interactive query)

These reads answer questions about the project — ad-hoc questions, summaries, report material — under `/agent/assist/*`. They send nothing to targets: everything comes from what BlueStick already holds. All paths are under `/api/v1`; over MCP each is a tool (named in brackets). The host query language (`q=`) and the discrete host filters have their own section.

In PowerShell use `curl.exe` (bare `curl` is an alias of `Invoke-WebRequest`), or `Invoke-RestMethod -Headers @{'X-API-Key'='…'} '<url>'`; for a POST body pass `-d (ConvertTo-Json $obj)` or `-Body ($obj | ConvertTo-Json)` rather than bash single-quoted JSON.

### Project, hosts and scans

- **`GET /agent/assist/context`** (`assist_get_context`) — the headline summary; read it first. Take real counts from its `totals` block: the scope list is capped at 50 (check `scopes_truncated`) and `recent_scans` at 5, so never answer "how many scopes / scans / hosts" from those lists. `default_host_view` (`name`, `filters`) is the view the Hosts page opens on for everyone when a project admin set one — your unfiltered counts are the whole project, so say which set you counted when the operator asks about "the hosts I see". Also the engagement dates (`project.start_date` / `end_date`), `members` with their project roles, and `names.in_scope_unresolved`.

### Every inventory read

All under `/agent/assist/*`. (This table and "Answering questions" below are carried over word for word from the previous guide; they have not been tightened yet.)

| Endpoint | Purpose |
|---|---|
| `GET  /agent/assist/context` | **Headline** project summary. `default_host_view` (`name`, `filters`) is the view the Hosts page opens on for everyone when a project admin set one — your unfiltered counts are the whole project, so say which set you counted when the operator asks about "the hosts I see". Scope list capped at 50 (check `scopes_truncated`); `recent_scans` capped at 5. Read BEFORE answering — but take real counts from the `totals` block, not the truncated lists. Also the engagement dates (`project.start_date` / `end_date`) and `members` with their project roles ("who is on this engagement"). |
| `GET  /agent/assist/hosts` | List hosts. Discrete filters: `state`, `ports` (port numbers, a host matches with any of them OPEN; no ranges or names — those are a 422), `services` (the service name the scanner identified on an OPEN port, on ANY port number — exactly the Hosts page's filter and `q=service:<name>`; a port found open with no service name, e.g. by masscan, does not match, so ask `ports=` for "the standard ports"), `subnets`, `has_critical_vulns`, `has_high_vulns`, `search`, `limit`, `offset`. These are the Hosts page's own filters: `search` is its search box; `has_critical_vulns` with `has_high_vulns` is critical OR high; `ports` with `services` must be met by ONE open port; an unknown `state`, a `subnets` value that is not a network or an address, or a list that names nothing is a 422 naming it — see "Host list filters" in the API reference. **`q` — the full boolean query DSL** (same engine as the human Hosts page): `ip:`, `hostname:` (alias `host:`), `state:`, `port:`, `os:` (OS name or OS family), `service:` (alias `svc:`), `version:` (alias `product:`; service product or version, e.g. `version:"OpenSSH 7"`) — **`port:`/`service:`/`version:` match OPEN ports only**; name another state after `@` on the value: `port:22@closed`, `service:ssh@filtered`, `port:22@unfiltered`, `port:22@open|filtered`, `port:22@any` (every state). A closed/filtered port's service name is nmap's guess from the port number, not evidence the service runs. `portstate:closed` alone is a separate "has some closed port" condition, not a qualifier. `path:` (alias `webpath:`; a path content discovery found, e.g. `path:/admin`), `subnet:` (alias `cidr:`), `scope:` (`subnet` = in a scope subnet, `name` = reached only through an in-scope name, `none` = neither), `vulnscan:` (`credentialed` / `uncredentialed` / `unstated` — whether the vulnerability scan of an ASSESSED host authenticated; a host nobody assessed matches none of them), `gap:` (`port_discovery` / `service_detection` / `os_detection` / `vuln_assessment` / `web_tls` / `auth_smb_ad` / `validation` — that kind of evidence applies to the host and nothing imported so far provides it: the hosts of that Evidence gap), `org:` (alias `owner:`; the netblock's registered owner, RDAP), `certorg:`, `asn:`, `country:`, `tag:`, `label:`, `site:` (`site:none` = inside a scoped subnet that carries no site — Posture's "Unassigned"), `conclusion:` (what a finished review concluded: `no_issue` / `finding_created` / `needs_evidence` / `out_of_scope` / `duplicate` — `conclusion:needs_evidence` is every reviewed host whose question is still open), `cve:`, `vuln:`, `issue:` (exactly one scanner-observation issue by its key — `check:<id>`, `cve:<CVE>`, `title:<normalised title>` or `row:<id>`, e.g. `issue:"check:smb_signing_not_required"` or `issue:"cve:CVE-2021-44228"`; `vuln:` is a title substring), `kind:` (`misconfiguration` / `vulnerability` / `informational`), `check:` (one misconfiguration-catalog check whichever tool reported it — e.g. `check:smb_signing_not_required`, `check:smbv1_enabled`, `check:smb_null_session`, `check:vnc_no_auth`, `check:ftp_anonymous`, `check:tls_deprecated_protocol`, `check:tls_cert_expired`, `check:http_missing_hsts`, `check:dns_zone_transfer_allowed` (a name server that handed a zone over to dnsx) — the `check_id` on a host's scanner observations is the value to use), `exploitport:`, `header:`, `webtitle:`, `tech:`, `note:`, `scan:`, `firstseen:` / `changedsince:` / `vulnsince:` (time windows — quote the ISO value: `firstseen:"2026-09-19T20:00:00Z"` = hosts first observed since then; `changedsince:"<start>..<end>"` = hosts already known that gained a port or a scanner observation; `vulnsince:"critical@<start>"` = a critical observation recorded since, severity and time on the same row), `has:`, **`follow:`** (`watching` / `in_review` / `reviewed` / `none` / `in_review_any` — any teammate's review counts — `mine`: your operator has it In Review, exactly their "Hosts I am reviewing"; and `revisit`: a finished review of your operator's that is not done — they concluded `needs_evidence`, or the host gained an open port or a critical/high observation after THEIR review — exactly Operations' "Changed since review"), **`assigned:`** (alias `assignee:`) combined with `AND`/`OR`/`NOT` and parentheses. `has:` values: `eol`, `smb_unsigned`, `weak_auth`, `cert_issue`, `weak_tls`, `cleartext`, `critical`/`high`/`medium`/`low`, `exploit`, `critical_exploit` (a critical that is ITSELF exploitable — `has:critical AND has:exploit` also matches a critical beside an exploitable low), `web`, `open_ports`, `tested`, `planned`, `notes`, `changed_since_review` (reviewed, then an open port first seen or a critical/high scanner observation recorded AFTER the review — ANY teammate's review; with `OR conclusion:needs_evidence` it is the team-wide list, and `follow:revisit` is the operator's own, which is what Operations' "Changed since review" shows), `untouched` (nobody has touched it: no review or assignment, note, host test that was not dismissed, evidence record or finding), `local_admin` (a credential was local admin — NetExec "Pwn3d!"), `writable_share` (a share granted WRITE). `GET /agent/assist/vocabulary` returns the values this project uses after `tag:`, `label:`, `site:` and `assigned:` — use it instead of guessing (a guessed tag returns zero hosts, not an error). `assigned:me`/`follow:` resolve against the operator who started the session; `assigned:`/`assignee:` also take a **username** (case-insensitive) or numeric id. `q` ANDs with the discrete filters; a malformed `q` returns 400. **Returns `{items, total, has_more, limit, offset}`** (paginated: default 500, max 5000). **`total` is every matching host — quote it, never the length of `items`**; raise `offset` by `limit` while `has_more`. Rows carry `exploitable_count` and `critical_exploitable_count` (same-row, as the Hosts page's "critical · exploit"). `sort_by` takes the Hosts page's keys (`ip_address` default, `critical_vulns`, `high_vulns`, `exploitable_vulns`, `open_ports`, `note_count`, `discovery_count`, `hostname`, `last_seen`) with `sort_order=asc\|desc`. `GET /agent/assist/hosts/by-ip/{ip}` is the host detail by address. |
| `GET  /agent/assist/hosts/count` | **How many hosts match** — same filters and `q=` as the list; returns `{count, query}`. Use this for every counting question instead of paging. |
| `GET  /agent/assist/hosts/{host_id}` | One host with ALL its ports, in any state — filter on each port's `state` (can be large — prefer `open_port_count` from the list for triage), `os_family`, and up to 10 `web_interfaces` (`web_interfaces_total` / `web_interfaces_truncated`; the full list is `/hosts/{host_id}/web-interfaces`). Each port's `protocol` is the IP transport (`tcp`/`udp`); the application (smb/http/…) is `service_name`. `open_port_count` = distinct physical open ports. The host's `vuln_summary` is **severity counts only** — for the actual CVEs/evidence use the findings endpoint below. Both list and detail also carry `follow` = the session operator's review status on the host (watching/in_review/reviewed, or null), so you can check it before writing follow. Detail also carries what the host inspector shows: `names` observed at the address, OS/MAC/NetBIOS detail, `smb_signing`, `tags`, `assignees`, `scope_membership`, per-domain `assessment` (its `vuln_scan_credentialed` says whether a vulnerability scan logged in to the host: `yes` / `no` / `not_stated`, null when not assessed — `no` and `not_stated` are still assessed; say which it was when you report a host as clean), `weakness_flags` / `weakness_labels`, certificate facts (`cert_orgs`, `cert_status`), `attributions`, NSE script output per port and per host (bounded — check `*_truncated`), scan `conflicts` (where scans disagreed), `note_count` and `finding_count`. |
| `GET  /agent/assist/hosts/{host_id}/vulnerabilities` | **The scanner rows on a host** (raw observations, NOT project findings) — the evidence `vuln_summary` only counts. Returns `{host_id, items, total, has_more, limit, offset}` (before v2.453.4 the path ended `/findings` and the rows were keyed `findings`). Each row also says which project finding covers its issue: `finding_id` / `finding_status` (the issue's), `finding_on_this_host` (false = the finding covers other hosts only — the row is still unjudged here) and `finding_endpoint_status` (this host's own state on it). Each carries `severity`, `cve_id`/`plugin_id`, `title`, `port_number`/`service_name` (null = host-level), `exploitable`, `cvss_score`, `source` (the scanner), `check_id` (the misconfiguration-catalog check, null for a scanner's own finding), `description`, `solution` (remediation), and `evidence` (scanner output; truncated). Filter `?severity=critical,high`; to read one issue's rows only, `cve=<CVE id>`, `plugin_id=<id>` or `search=<title text>` (`total` is then the narrowed count). **Paginated with `total`/`has_more`** (default 200, max 1000) — page `offset` until `has_more` is false to report complete coverage. Use this for evidence-rich reporting on ONE host. |
| `GET  /agent/assist/report-context.ndjson` | **The report data source — use this to write a report.** Streams the COMPLETE per-host dossier for every matching host, one JSON object per line, **uncapped**. Each line's top-level keys: `host_id`, `identity`, `scope`, `timeline`, `os`, `ports`, `host_scripts`, `vulnerabilities` (every scanner row on the host: `id`, severity, CVE, `plugin_id`, port, `plugin_output`, `solution`), `vulnerability_summary`, `untriaged_vulnerabilities` (the scanner rows no finding covers on this host), `canonical_findings` (the project's findings this host is on — there is no key named `findings`), `execution_findings[]` (evidence records whose outcome is `finding`), `tester_summaries`, `dossier_summary`, `analyst_context` (notes, the operator's review state), `confidence`. **Joining a finding to its scanner rows:** each `canonical_findings[]` entry carries `vulnerability_ids` — the `vulnerabilities[].id` values on THIS host that the finding covers. Its `vuln_id` is the single row the finding was first promoted from, usually on another host, so it rarely matches a row of the host you are reading. Same discrete filters + `q` DSL as `/agent/assist/hosts`. This is the same correlated record the server-side report builds — populate your report template from it instead of stitching together per-host calls. **Redirect to a file and process it locally; NEVER read the stream whole into context** (`curl -sS -H "X-API-Key: $KEY" ".../agent/assist/report-context.ndjson" -o report-context.jsonl`). Safe on tens-of-thousands-of-host projects — the server hydrates one chunk at a time. |
| `GET  /agent/assist/hosts.ndjson` | **The complete matching host set** — same filters + `q` DSL as `/agent/assist/hosts`, but uncapped and streamed one JSON object per line. Use this instead of paging when the project is large: redirect to a file and query it locally (`curl -sS -H "X-API-Key: $KEY" ".../agent/assist/hosts.ndjson" -o hosts.jsonl`, then `jq`/`grep`/`wc -l`). Report counts from the file, never a truncated page. Never read the stream into context whole. |
| `GET  /agent/assist/scopes` | Scope CIDR lists **and declared domains** — **each capped at 100 per scope**. Each ScopeBrief carries `subnet_total` / `subnets_truncated` and `domain_total` / `domains_truncated`; when a `*_truncated` flag is true the list is only a sample, so tell the operator it's partial — the full lists are `GET /agent/scopes/{scope_id}/subnets` and `/domains` (MCP `scope_list_subnets` / `scope_list_domains`), paged, with this same key. `domains[]` entries are `{domain, include_subdomains}` (exact name vs the name and everything under it); `names_in_scope_total` is the deduplicated count of inventory names they cover. **Name scope is independent of subnet scope**: an in-scope name does not put the address it resolves to in scope, and an in-scope subnet does not put names in scope. |
| `GET  /agent/assist/names` | The named-asset inventory (FQDNs), paged (`limit` default 100, max 1000, `offset`). Each row: `in_scope` (a declared domain covers it), `current_ips` (derived from the latest A/AAAA observations — never stored; empty = unresolved), `current_ip_total`, `sources` (observation kinds: A, AAAA, IMPORT, HTTP, CERT, …). Filters: `q`, `in_scope`, `resolved`, `host_id` (names currently bound to that host's address), `kind`. **How to act:** `in_scope=true&resolved=false` is the queue — names the operator approved that no upload has ever resolved (tell the operator: they resolve them, or drop them from scope). A name whose address is shared with other names (`current_ip_total` on the host's other names, a load balancer / vhost) must be tested **by name**, not by IP — the bare address reaches a different site. |
| `GET  /agent/assist/scans` | Scan inventory, newest-first — **default 100, max 500**; `offset=` pages further back. The answer is `{items, total, has_more, limit, offset}`: `total` is the count for the filter, so "how many nmap scans?" is one call with `limit=1`. `tool=` narrows to one tool's scans (the Scans page's chips): the last two nmap scans are `tool=nmap&limit=2`. Each row carries `ingestion_job_id` — the import that produced it, the `job_id` `/assist/uninterpreted-lines` takes (a scan id is not a job id) — and `time_source`: `tool_run` / `tool_records` mean the start and end times are instants, returned in UTC with an offset; `tool_clock` means the scanner's own wall clock, zone unknown, returned WITHOUT an offset — never correlate it with a UTC event as if it were UTC. An nmap scan's rows also carry `scan_info`: per scan type / protocol, the port list the scan was asked to probe (`services`, e.g. `1-1000`) — a port outside it was not looked at, which is not the same as closed. Empty for a tool that does not report it. |
| `GET  /agent/assist/session` | Your own session metadata (purpose, started_at, the operator `assigned:me` refers to). |
| `GET  /agent/assist/writing-guidance` | v2.470.0. How this installation wants a finding's report text written: `general` and `sections` {field: instructions} — the block a finding read carries as `writing_guidance`. Read it before proposing a NEW finding with `report_text`. |
| `GET  /agent/assist/vocabulary` | The values this project uses: tags, labels, sites, scope names, usernames (for `assigned:`), finding statuses and severities. |
| `GET  /agent/assist/findings` | **Triaged findings** (not raw scanner rows): filters `status` (a status, or the group `active` / `resolved`; `all` or omitted for every one — any other value is a `422`, never an empty result), `severity`, `source` (an unknown value of either is a `422` too), `host_id` (a host not in this project is a `404`), `unowned=true`, `owner` (username or `me`), `search`; default 50, max 500. The report's findings come from here. |
| `GET  /agent/assist/findings/{finding_id}` | One finding with its affected hosts (per-host endpoint status, and each row's `finding_host_id` — the id `propose_endpoint_status` and `record_evidence` take) and evidence; `report_text` (description, impact, recommendation, references, steps to reproduce, CVSS vector and score — what a report will say), `endpoint_status_counts`, and `status_history` (who changed the status, when, from→to, why). `writing_guidance` (v2.469.0) is how this installation wants report text written: `general` and `sections` {field: instructions} — read it before proposing report text. `images` is the finding's images as the report sees them: `id`, `caption` (what the report prints under it; null → the file name), `filename`, `in_report` (ticked for the report), `printable` (PNG / JPEG / GIF), `placed_in` (the report-text fields whose Markdown places it with `![caption](evidence:<id>)`; empty for a ticked image means it prints under Evidence) and `download_path`; each note attachment also carries its `caption`. `scanner_evidence` lists at most 100 of the scanner rows that evidence the finding; `scanner_evidence_total` is how many there are and `scanner_evidence_truncated` says the list was cut — quote the total, never the length of the list. |
| `GET  /agent/assist/scanner-observations` · `/scanner-observations/hosts?issue_key=` | Scanner results grouped by ISSUE across the project (`host_count`, `judged_host_count`, the covering finding) — the Findings page's "Scanner observations" view; unjudged issues only unless `include_judged=true`. The answer is `{items, total, has_more, limit, offset}`: `limit` defaults to 50 (max 200), so page with `offset` until `has_more` is false before saying "all". Filters: `severity` (one of `critical` · `high` · `medium` · `low` · `info`), `kind`, `search`, `min_hosts`, `exploitable=true` (issues a scanner reports an exploit for; each row says `exploitable` — a report, not proof of exploitation). Then the hosts carrying one issue — `{items, total, has_more}`, paged with `limit`/`offset`; each host row carries `tests_to_do` / `tests_recorded` for the tests that name this issue there. `sort=hosts` puts the most widespread first; a row with `judged_host_count > 0` is partly judged (listed until every host is). |
| `GET  /agent/assist/client-reports` · `/client-reports/{id}` · `/client-reports/{id}/files/{fmt}` · `/client-reports/{id}/scope.csv` | The Reports page (operator needs `auditor`): drafts and issued reports; one report with every finding as the report states it (`content_source`: `issued_snapshot` = what the client was given, `draft_live` = what a draft would say now); the rendered files; the complete scope as CSV (curl it to a file). A report over its template's scope cutoff does not list the scope. Its `scope.external` is true, and `summary.scope_external.file` names this file and its SHA-256, which the report prints. **How a finding was confirmed:** each finding's `confirmations` are the test results the report prints for it (`tool`, `host`, `command`, `summary`, an `output` excerpt with `output_truncated`, `date`, `by`, `by_agent`) — at most 10, with `confirmations_omitted` counting the rest. `by` is the name the report prints: the person who recorded the result, or — for a result an agent recorded — the OPERATOR of that agent's session, in full; the report says nothing about the session or the agent (`by_agent` is in the data for counting only). They are only what THIS report prints: the list is empty when the report's template does not print them, or shows that finding without its details (an addendum's already-reported finding). `summary.evidence_records` is how many the report prints, `summary.evidence_records_not_printed` how many it holds back for that reason, and `summary.agent_evidence_records` how many of the printed ones an agent recorded. `summary.internal_references` lists the findings whose written text names a BlueStick record the reader cannot look up (`fields[].field`, `fields[].phrases`) — tell the operator, and propose the rewritten section. **Images:** each finding's `images` is every image ticked "In report" (`attachment_id`, `caption`, `placed_in` — the sections whose text places it), with `printed` (does this report's template print it) and `printed_in` (in which of those sections; a printed image with `printed_in: []` is in the trailing evidence block); `evidence` is the images no section places. `summary.images_printed` (inside a section), `images_trailing` (the trailing block) and `images_not_printed` add up to `summary.images`; `images_not_printed_reasons` counts why (`finding_not_detailed`, `section_not_printed`, `no_evidence_block`) and `template_images` is what the template declares it prints (`{fields, trailing}`). All of these are `null` when the printing could not be measured and absent from a report issued before they existed — then say nothing about where an image prints. **An addendum** says why each finding is in it: `change` is `new`, `new_hosts` (the added endpoints are `new_affected`) or `severity_changed` — a finding already reported whose severity differs from the baseline report's, with `previous_severity` / `previous_severity_label` holding what the client was told; `delta` counts them (`new_findings`, `findings_with_new_endpoints`, `findings_with_changed_severity`, `withdrawn`). Only severity is compared, never title or status. |
| `GET  /agent/assist/hosts/{host_id}/web-interfaces` | Every web interface on a host: URL, title, server, technologies, screenshot reference, and the certificate / TLS facts (`cert_not_after`, `cert_self_signed`, cert organisations, `tls_weak_protocol`, and as the web panel reads them `tls_version`, `cert_issuer`, `cert_subject_cn`, `cert_sans` + `cert_san_total`). Null = the tool did not report it, not "fine"; an expired certificate is `check:tls_cert_expired`. |
| `GET  /agent/assist/hosts/{host_id}/access` | NetExec / SMBMap results on the host (logins, shares, local admin) next to the raw tool line — which may contain credentials the tool found. |
| `GET  /agent/host-tests?host_id={host_id}` · `GET /agent/evidence?host_id={host_id}` | What has been proposed for the host (each test with its status and `evidence_count`) and every command recorded against it. The host detail's `assessment` says whether it counts as tested. |
| `GET  /agent/assist/hosts/{host_id}/notes` · `GET /agent/assist/notes` | Notes on one host · across the project, with their threads (`parent_id` / `thread_root_id`), type, whether it is pinned, the `finding_id` an older thread was promoted to, and attachment references. `/assist/notes` rows carry `target {kind, id, label}` (host, port, finding, scan, scope or project). |
| `GET  /agent/assist/workbench` | Your operator's Operations page: `my_work` (what is waiting on them, by kind — say the kinds apart, as the page does, never as one sum: `findings_to_decide` (under investigation, or a proposal waits for their decision), `findings_to_write` (only required report text is missing; the two add up to `findings_needing_me`), `tests_assigned`, then what they hold: `hosts_in_review` and `tests_on_hosts_in_review` (tests on those hosts, NOT assigned to them); `to_claim` beside it; `total` is still returned and is those parts added up), `my_queue`, `my_tasks` (`group_counts` counts each test once), `my_findings` (findings they own that NEED them — each row's `needs` says why: under investigation, required report text missing, a proposal to decide; a confirmed, written-up finding is not listed), `since_last_visit`, `followups` ("Changed since review": YOUR OPERATOR'S own finished reviews that are not done — the host changed after their review, or they concluded `needs_evidence`; never a teammate's review; one row per host, `total` hosts, the same hosts as `q=follow:revisit`), blockers. It carries no project-wide measures (removed v2.451.0 — Operations is the operator's own page; project status is Posture's) and no team roster (`team_review` was removed in v2.451.1 — what the team is already reviewing is `GET /agent/assist/hosts?q=follow:in_review`, any teammate's). Reading it never marks anything seen; `*_unavailable: true` means not computed, not "nothing" and not 0. **The lists are previews** (hosts 10, tests 10 per group, findings / follow-ups 15) — the page itself shows one full list at a time, as tabs (Findings · Hosts · Tests · Changed since review · Pick up, v2.452.0) — so quote the counts, never a list's length. For a WHOLE list: hosts in review `GET /agent/assist/hosts?q=follow:mine`; changed since review `?q=follow:revisit`; tests assigned `GET /agent/host-tests?mine=true&active_only=true`; tests on hosts in review `GET /agent/host-tests?q=follow:mine&active_only=true` (every test to do on those hosts; `group_counts.in_review` leaves out the ones assigned to the operator, counted under `assigned`); the untouched queue is the next row. Findings that need the operator, whole: `GET /agent/assist/workbench/findings` (its own row below; `GET /agent/assist/findings` filtered by owner lists every finding they own, needing them or not). Each `my_tasks` row carries its `tool`. |
| `GET  /agent/assist/workbench/investigate?tier=&limit=&offset=` | "Untouched, with a reason" (the page called it "Worth a look" until the 2026-10-02 redesign; the MCP tool keeps its name, `assist_list_worth_a_look`): untouched hosts with reasons, in stated tier order (1 exploitable critical … 5 scans disagree); `queue_total` / `tier_counts` are whole-queue. |
| `GET  /agent/assist/workbench/findings?need=decide\|write&limit=&offset=` | The findings your operator owns that NEED them — the Operations "Findings" tab, the whole list (MCP `assist_list_my_findings`; the workbench carries its first 15 rows). Each row's `needs` says why (under investigation, required report text missing, a proposal to decide); severity first. `need=decide` is under investigation or a proposal waiting, `need=write` only report text missing — they never overlap and add up to the list; any other value is a 422. `total` is the size of the list this call pages (use it for "how many"), with `limit`, `offset`, `has_more`; `total_open` and `need_counts {decide, write}` describe the whole list whatever `need` says. Personal to the operator; any member's agent may read it, as the page. |
| `GET  /agent/assist/workbench/terrain?sort=address\|untouched\|critical_untouched&limit=` | Hosts per /24 (IPv6 /64): tested / planned / worked / untouched, and `critical_untouched` — Posture's "Where the team has been". |
| `GET  /agent/assist/evidence/gaps?domain=&segment=&limit=` | The Evidence page's gap list: eligible-but-unassessed hosts, their ports, and the step that closes the gap (`domain` = a key from `/assist/coverage`). Respect `scope_caution`. `segment` is `matrix.segments[].key` from `/assist/coverage` (a site id, a subnet key or `unmapped`) — NOT a CIDR; a wrong value answers 404 listing the accepted keys. |
| `GET  /agent/assist/scans/compare?a=&b=&limit=` | What changed between two scans: hosts new / gone / changed, ports newly open / closed / not observed (not observed is not remediation). |
| `GET  /agent/assist/scans/{scan_id}/hosts?state=&search=&skip=&limit=` | The hosts ONE scan observed, as it observed them (the scan page's "As scanned" table; MCP `assist_list_scan_hosts`): state and hostname at scan, `host_created`, the ports it saw (at most 50 listed per host; `observed_port_count` / `open_port_count` are exact), and `credentialed` — whether the scan authenticated to the host: `true` / `false` when the scanner said so (Nessus), `null` when it did not say. **`null` is "not stated", never "no"**: do not report an nmap or an old Nessus import as unauthenticated. Read `total` / `has_more`; page with `skip`. |
| `GET  /agent/assist/coverage` · `/segments` · `/posture` · `/patterns` | Scope coverage; the Posture page's segments, headline and recurring-weakness patterns. `/coverage`'s `vuln_assessment` domain carries `credentialed` = `{credentialed, not_credentialed, credentials_not_stated}`: of the assessed hosts, how many a scanner logged in to (they add up to the assessed count). A clean result from a scan that did not authenticate is weaker evidence — quote the split whenever you say how much was assessed for vulnerabilities; list each with `q=vulnscan:credentialed` / `uncredentialed` / `unstated`. |
| `GET  /agent/assist/ingestion-issues` | Imports that failed, were partial, or skipped records (operator needs `analyst`, as the Ingestion Results page does). Counted as that page counts them: `failed` (an import went wrong), `expired` (a staged upload nobody started within 24 hours — never imported, nothing failed), `discarded`, `degraded`, and `needs_attention` (failed or partial, not dismissed, not replaced by a later import — the number Operations calls blocked imports). |
| `GET  /agent/assist/uninterpreted-lines?job_id=` | Lines an import did not read, as redacted shapes (NetExec imports record them; operator needs `analyst`). A `job_id` that is not an import job of this project is a 404; an empty page for a real job means every line was read. |
| `GET  /agent/assist/attachments/{attachment_id}` · `/web-interfaces/{interface_id}/screenshot` | Evidence files (any member, as in the UI). Over MCP, `assist_get_image {attachment_id | interface_id}` shows one inline (image content, up to 2 MB); the `download_path` references are for saving files beside a report. |

### Answering questions

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
   - `search=` is the Hosts page's search box: a substring of the address, host name, OS name or family, or — on any of the host's ports — a port number, a service name or a product. For one of them only, use the field (`q=hostname:web`, `q=os:windows`).
   - "Critical or high?" — `has_critical_vulns=true&has_high_vulns=true` is a critical OR a high observation (the page's chips). "Both on one host" is `q=has:critical has:high`.
   - `NOT` counts a host the scans say nothing about: `q=NOT os:windows` includes hosts with no OS recorded. Say so when it matters ("not identified as Windows" is not "identified as something else").
3. **Answer the operator's question** using the filter vocabulary above.  Examples:
   - "Which hosts have FTP open?" → `GET /agent/assist/hosts?ports=21` (= `q=port:21`: port 21 open, whatever answers there). `services=ftp` (= `q=service:ftp`) is a different question — FTP identified on any port — and it is the one the Hosts page's service filter answers. Say which you asked. **"How many?"** is `GET /agent/assist/hosts/count` with the same filter, or the list's `total`.
   - "Which hosts do I have in review?" → `GET /agent/assist/hosts?q=follow:mine` (the session operator's own In Review; `follow:in_review` is ANY teammate's). "Assigned to me?" → `q=assigned:me`.
   - "Propose tests for the hosts assigned to me or in review by me" → `GET /agent/assist/hosts?q=assigned:me OR follow:mine` for exactly those hosts, `GET /agent/host-tests?q=assigned:me OR follow:mine` for what they already carry, then `POST /agent/host-tests` (MCP `host_tests_propose`) with one `label` for the batch. The operator takes hosts into review from Operations' **Pick up** tab (the "Untouched, with a reason" queue), so this is how their review queue becomes tests; do not propose for hosts outside the query result.
   - "What's exposed to Log4Shell?" → `GET /agent/assist/hosts?q=cve:CVE-2021-44228 OR vuln:"log4j"`.
   - "Which SMB hosts have a working exploit *on 445*?" → `GET /agent/assist/hosts?q=exploitport:445`. Note `exploitport:445` (exploit ON that port, same scanner observation) is stricter than `q=port:445 AND has:exploit` (445 open AND *any* exploit anywhere).
   - "What critical scanner observations landed this week?" → `GET /agent/assist/hosts?has_critical_vulns=true` (or `q=has:critical`) + `GET /agent/assist/scans?limit=20` to correlate.
   - "Summarize scope X" → `GET /agent/assist/scopes` to confirm CIDR list + `GET /agent/assist/hosts?subnets=...` for the host count and posture.
   - "What should we look at next / what's mine / what changed since I was last here?" → `GET /agent/assist/workbench/investigate` · `GET /agent/assist/workbench` (`my_work`, `since_last_visit`) · "which findings are waiting on me?" → `GET /agent/assist/workbench/findings` (the whole list; `need=decide` / `need=write` for one kind).
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

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Notes, review status and host corrections

These three writes live under `/agent/hosts/…`, not `/agent/assist/…`. Whether you may write is your operator's project role: `GET /agent/identity` returns `can_write_project_data` — read it once rather than discovering the answer from a 403.

| Endpoint | Purpose |
|---|---|
| `POST /agent/hosts/{host_id}/notes` | Add a note (201; MCP `assist_add_note`). Body `{"body": "..."}` and nothing else. A note is Markdown: lead with what you observed and where (host, port, record), and say what is uncertain. It is discussion for the team — context, a question, a handoff — and has no status: a check you ran is evidence, a check to run is a host test. An `@username` in an agent's note notifies nobody — ask the operator to mention someone. |
| `POST /agent/hosts/{host_id}/follow` | Set your operator's review status (204; MCP `assist_set_follow`). Body `{"status": "in_review"}`: `in_review` · `reviewed` · `none` (clears). Any other value is a 422 naming these three. |
| `PATCH /agent/hosts/{host_id}` | Correct a host's `hostname` / `os_name` (MCP `assist_patch_host`). Send only the field you are fixing; only these two are editable (setting `os_name` re-derives `os_family`). Scan-derived facts (ports, services, observations) are never editable here. |

Every note you create is stored as agent work under your operator's name; the server records that, so add no mark of your own. Write discipline:

1. **Announce, then write.** Tell the operator the note you intend to add and let them react. Never batch-write silently.
2. **Observations, not unsupported conclusions.** Write what the data shows and cite the host, port or record it came from. Never assert something you have not seen evidence for in BlueStick's own data.
3. **Mark uncertainty in the note body itself.** A reader six weeks out cannot separate your inferences from your facts unless you say which is which.
4. **Never set `reviewed` on your own initiative.** It is the operator's conclusion — ask them to confirm first. `in_review` is fine when they asked you to pick work up. The host list and detail carry `follow`, your operator's current status on the host: read it before writing.
5. **A host correction is a correction, not a guess.** Change `hostname` / `os_name` only when your investigation established the real value, and cite what showed it in a note alongside the edit. A wrong correction is worse than the scan's uncertainty, because it reads as settled.
6. **Project-wide is wider than what you should touch.** Stay inside your operator's own work (`q=assigned:me`, `q=follow:mine`) unless they point you elsewhere. You cannot assign a host to anyone.

<!-- agents:end -->

---

<!-- agents:section tags="remediation" -->

## Remediation tracking

Reads need your operator to be a project auditor; writes, a project admin. Writes are direct and attributed, not proposals.

Who was told about a finding **on a host**, and where the fix stands: a contact (`contact_email`, `contact_name`), a `team` (the group that owns the fix — free text, at most 100 characters), `notified_on`, a `status` of `open` / `closed` / `deferred`, and `closed_on`. One row is one finding on one host — the same finding can have a different contact and status on each host. This is the client's progress as a project admin recorded it. It is **not** the assessor's conclusion (`finding_status` and `endpoint_status` on the same row): neither moves the other, and you never change one because of the other.

**Remediation tracking is the installation's choice.** Where it is off, every endpoint below answers `404` "Remediation tracking is not enabled on this installation." That is an answer, not an error to work around: tell the operator (a global administrator turns it on in System settings) and do not retry.

**Two statuses, two facts — one word each.** `status: closed` on a remediation row means the contact **reported it fixed**: say "reported fixed", never "closed" and never "remediated". `endpoint_status: remediated` is the assessment's own conclusion: say "remediated". The stored values are unchanged, so calls keep sending `closed`. Where the two disagree, the row says so in `verification`, which the server derives — never work it out yourself:

| `verification` | Meaning |
|----------------|---------|
| `reported_fixed_not_retested` | The record is `closed` and the endpoint is not `remediated` (and not a false positive): the contact says fixed, the team has not concluded so. |
| `remediated_record_open` | The endpoint is `remediated` and a record exists that is `open` or `deferred`: the team concluded fixed, the record still reads as work. An endpoint nobody ever tracked is not one. |
| `null` | The two agree, or have nothing to say to each other. |

Report a gap to the operator; do not close it for them — setting `status: closed` because an endpoint is remediated, or proposing an endpoint status because a contact reported a fix, is the operator's decision.

**Deadlines.** Each open finding on a host has a deadline: the day it was assigned (`notified_on`) plus the installation's days for the finding's severity. The server derives it — **never compute or set a deadline yourself**; report the `due_on`, `days_left` and `state` the server returns. "Today" is the installation's calendar day in its own time zone, returned as `as_of` on the list and the follow-up read: use `as_of`, not your machine's date, when you say what is overdue or default a date, and never send a date after it (`followed_up_on` in the future is a `422`). A row's `state` is one of:

| `state` | Meaning |
|---------|---------|
| `overdue` | Open, and the deadline has passed (`days_left` is negative). |
| `due_soon` | Open, and the deadline is today or inside the installation's "due soon" window. |
| `on_track` | Open, with a deadline further away. |
| `not_assigned` | Open, with no assigned date: no clock is running, so it is never overdue. |
| `no_deadline` | Open, and the installation sets no deadline for this severity. |
| `deferred` | Recorded as deferred: the clock is stopped. |
| `closed` | The contact reported it fixed; `due_on` is the deadline that applied then and `closed_days_late` how many days after it (0 = on time, null = it had none). |

### The list — `GET /agent/remediation` (MCP `remediation_list`)

Filters:
- `status`; `state` (repeat it for several); `severity`; `host_id`; `finding_id`
- `contact` (part of an address or name); `unassigned` (only rows with no contact); `team` (exactly this team; case does not matter)
- `overdue_band` (`1-7` / `8-30` / `31-90` / `90+` — only overdue rows that many days past their deadline)
- `no_follow_up_days` (1–365 — only overdue and due-soon rows nobody recorded a follow-up for in that many days, or ever)
- `verification` (`reported_fixed_not_retested` / `remediated_record_open`)
- `flag` (`deferral_review_due` / `deadline_overridden`)
- `q` (2–200 characters — part of the finding's title, the host's address or its name; every count follows it)
- `group` (`host` (default) / `finding` / `contact` / `due` / `team` — the order; `due` puts the longest overdue first); `limit` (default 50, max 200); `offset`

The answer: `{items, total, has_more, as_of}` plus counts over the whole selection —
- `state_counts`, `status_counts`: before the `state` / `status` filter.
- `verification_counts`: before the `verification`, `state` and `status` filters.
- `flag_counts`: before the `flag`, `verification`, `state` and `status` filters.
- `severity_counts`: `{severity: {overdue, due_soon}}`, before the `severity` filter.
- `overdue_ages`: the overdue rows by days past the deadline (`1-7`, `8-30`, `31-90`, `90+`).
- `not_followed_up`: the overdue and due-soon rows with no follow-up recorded in `not_followed_up_days` days (the `no_follow_up_days` you sent, else the installation's "due soon" window).

Each item carries the `finding_host_id` a write takes, and `team`, `state`, `due_on`, `days_left`, `closed_days_late`, `last_follow_up_on`, `endpoint_status`, `verification`, `policy_due_on`, `due_override_on`, `deadline_source` (`policy` / `override` / null), `deferred_review_on` and `deferral_review_due`.

`GET /agent/remediation/export` takes the same filters without `limit` / `offset` and answers the same rows in one answer, without the counts: `{items, total, as_of}`, at most 20,000 rows (curl it to a file).

### The other routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/remediation/contacts` | The contacts in use, the one with the most overdue first: `total`, `open`, `overdue`, `due_soon`, `on_track`, `deferred`, `closed` counts and `last_follow_up_on` (the last day anyone recorded following up about a row still at risk). MCP `remediation_contacts` |
| GET | `/agent/remediation/teams` | The teams that own fixes, the one with the most overdue first: `team`, the same counts, and how many `contacts` each has. The last entry, `team: null`, is the rows with a contact and no team. MCP `remediation_teams` |
| GET | `/agent/remediation/trend` | Whether the backlog is shrinking. `days` (7–730, default 90). `daily`: the counts by deadline state recorded each day (`day`, `overdue`, `due_soon`, `on_track`, `not_assigned`, `deferred`, `closed`). `closed_by_month`: rows the contact reported fixed per month over the last twelve calendar months (this one included) as `on_time`, `late` or `no_deadline`. MCP `remediation_trend` |
| GET | `/agent/remediation/follow-up` | `contact_email` (the exact address, from the contacts read), `upcoming_days` (0–365, default 0: also the on-track rows due within that many days; `upcoming` counts them). That contact's overdue and due-soon rows (`items`, at most 500, the longest overdue first) and `text`, a plain-text message listing them for the operator to send. `total` is every such row of the contact and `not_listed` how many of them `items` and `text` leave out — when it is not 0, tell the operator the message does not name them all. The server sends nothing. MCP `remediation_follow_up` |
| POST | `/agent/remediation/follow-up` | Record that the operator followed up: `{contact_email, followed_up_on?, note?, finding_host_ids?, upcoming_days?}`. One timeline entry per row and `last_follow_up_on` on each; a row already followed up that day is left alone. Without `finding_host_ids` it records EVERY overdue and due-soon row of the contact — including any the read did not list (`not_listed`). MCP `remediation_record_follow_up` |
| POST | `/agent/remediation/contact-report` | Queue ONE contact's remediation list as a document (202): `{contact_email, format?}` (`contact-docx` (default) or `contact-html`). When the document is ready it writes an entry on the timeline of each host it lists, so call it only when the operator asks for the document. No MCP tool. |
| GET | `/agent/remediation/contact-report/{job_id}` · `…/download` | Poll until `ready` is true, then download the file with curl (409 while it is not ready, 410 once it has expired). `status` is `queued`, then `processing`, then `completed` or `failed`; `error` says why a failed one failed. `images_withheld` (once ready) is how many evidence images were left out because the finding is also on systems that are not this contact's — say so to the operator when it is not 0. |
| GET | `/agent/remediation/hosts/{host_id}/events` | The host's timeline (`finding_host_id`, `limit` of at most 200, `offset`): field changes (`field`, `from`, `to`), recorded follow-ups (`kind: follow_up`) and notes, each with `occurred_at` and `recorded_at`. A change with `field: "finding"` records that a finding which had a remediation record was removed from this host or deleted: `from` is what the record held. MCP `remediation_timeline` |
| POST | `/agent/remediation/apply` | Set fields on up to 500 findings on hosts: `{rows: [...], dry_run?, overwrite?, agent_model?}`. A row with `finding_id` alone on a finding that has more than 500 hosts is refused (the dry run too) with a message saying to name the hosts — send `finding_id` with `host_id`, or `finding_host_id`, at most 500 a call. MCP `remediation_apply` |
| POST | `/agent/remediation/assign-from-report` | Start the clock from an ISSUED client report: `{report_id, assigned_on?, dry_run?, agent_model?}`. Every finding on a host that report listed (its frozen content) that is still in the list, has no assigned date and is open gets the assigned date — `assigned_on`, or the day the report was issued. Nothing else is touched. Returns `{report_id, assigned_on, assigned, already_assigned, not_open, not_in_list, dry_run}`. A draft or replaced report is a `409`; a date after `as_of` a `422`. MCP `remediation_assign_from_report` |
| POST | `/agent/remediation/events` | Add a note to a host's timeline: `{host_id, body, finding_host_id?, occurred_at?, request_key?}`. 201, or 200 when the `request_key` was already stored. MCP `remediation_add_note` |
| PATCH / DELETE | `/agent/remediation/events/{event_id}` | Edit or remove a note your operator wrote. A recorded change or follow-up cannot be edited (409). |

These cover this project only; the cross-project deadlines page has no agent read.

### Working with it

**A deadline set by hand, and a deferral.** `due_on` is the deadline in force. A project admin may have set it by hand for one row (`due_override_on`; `deadline_source: "override"`) — then it is no longer the policy's date (`policy_due_on`), and the row has a clock even with no assigned date. Send `due_override_on` in `apply` only when the operator's source gives that date for that row, always with a note saying why (a `422` otherwise); `null` goes back to the policy's date, with a note too. `status: deferred` is a decision with a date and a reason: the row must carry `deferred_review_on` (the day to look at it again, `as_of` or later) and a note — ask the operator for both rather than inventing them; a row you cannot give them to is reported, not deferred. `deferral_review_due` on a row means that day has come (or the row was deferred before review dates existed): list them with `flag=deferral_review_due` and tell the operator.

**Starting the clock from a report.** When the operator says "the report went out, start the deadlines", use `assign-from-report` with the report's id (MCP `assist_list_client_reports`) — `dry_run: true` first, show the four numbers, then the real call. Do not build the same thing from `apply` rows: the route works from what the report froze, not from today's findings.

**Following up with a contact.** Read who needs it (the contacts read), then that contact's message (the follow-up read) and show the operator the `text` — they send it, by their own mail or chat; you send nothing. **Record the follow-up only after the operator says the message was sent**, never when the text was merely produced. With `upcoming_days` the read also lists what is coming; send the same `upcoming_days` when you record.

**Answering "where do we stand?"** Use the counts the list returns rather than paging and counting yourself. To name the rows behind a number, send its filter — `state` + `severity`, `overdue_band`, or `no_follow_up_days` — and read `total`. For "which team is behind?" read the teams; set a row's team with `team` in `apply` (an explicit `null` clears it).

**Reading the trend.** `daily` holds only the days the server recorded: **a day that is absent was not recorded — it is not zero**, and nothing exists before the installation began tracking. Say when the history starts (the first `day`) and never fill a gap or describe a period you have no rows for. `closed_by_month` comes from the closed rows themselves, so it can reach back further than `daily`; `no_deadline` rows were closed with no deadline to judge them by — they are neither on time nor late.

**Filling it in from a file the operator holds** (a spreadsheet, a CSV). Nothing is uploaded — you read the file where it is and send ids:

1. Read the rows that exist (the list, raising `offset` while `has_more`, or the export) and the findings they belong to.
2. Match each source row to **exactly one** finding, and to the host it names. A row you cannot match with confidence is **not written**: list those rows for the operator and say why. Never pick the closest title.
3. Build one `rows` entry per source row: `finding_host_id`, or `finding_id` with `host_id`. `finding_id` alone means **every host of that finding** — use it only when the source really gives one contact for all of them. Send only the fields the source has (`contact_email`, `contact_name`, `team`, `notified_on`, `status`, `closed_on`, `due_override_on`, `deferred_review_on`); a field you leave out is untouched, an explicit `null` clears it.
4. Call with `"dry_run": true` and show the operator the `summary` (targets, changed, unchanged, conflicts) before anything is written.
5. A **conflict** is a field someone already set to a different value. It is left alone. Send `"overwrite": true` only when the operator, having seen the conflicts, tells you to replace them.
6. Send the same call without `dry_run`. A source comment becomes a `notes` entry (at most 20 per row) — give each a `request_key` that is stable for that source row (so a re-run does not add it twice) and `occurred_at` when the source dates it.

The whole call is planned before anything is written: one row that names nothing in this project, two rows that name the same finding on the same host, a `closed_on` on a row whose status is not (and is not becoming) `closed`, or one `request_key` carrying two different notes refuses all of it (422, with the row numbers) — the dry run refuses the same way. A `request_key` identifies ONE note about ONE finding on a host: never reuse a key for another finding or other text.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## API Reference — `/agent/*`

All paths are relative to `/api/v1`.

### Common endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/identity` | Who am I: `session_id`, `project_id`, `project_name`, `agent_name`, the `operator` and their `project_role`, `can_write_project_data`, `key_expires_at`, `renew_path`, `renewable_until`. MCP `agent_identity` |
| POST | `/agent/session/renew` | Extend your key's deadline (same key). MCP `session_renew` |
| POST | `/agent/session/end` | End the session — the last call. Optional `notes`, `agent_model`. MCP `end_session` |
| POST | `/agent/feedback` | File feedback (201, an acknowledgement). MCP `submit_feedback` |
| GET | `/agent/project` | Project metadata: `id`, `name`, `slug`, `description`, `status`, the engagement's start and end dates, `agent_name` |
| GET | `/agent/dashboard` | `host_count`, `up_host_count`, `open_port_count`, `scan_count`, `last_scan_at` |
| GET | `/agent/hosts` | The project's hosts as a bare array (see Host list filters, below) |
| GET | `/agent/hosts/{id}` | One host with its ports and the `names` observed at it — 404 if it is not in this project |
| GET | `/agent/hosts/{id}/notes` | Notes on a host |
| GET | `/agent/scans` | The project's scans, newest first: `limit` (default 100, max 500) and NO offset, so scans past the newest 500 are not retrievable here (`GET /agent/assist/scans` pages). Filters: `tool`, `created_after` (an ISO-8601 date or timestamp; anything else is a `422` naming it), `sort_by` (`created_at` · `filename` · `tool_name`), `sort_order` (`asc` · `desc`) |
| GET | `/agent/scopes` | Every scope with all its subnets and domains (uncapped; curl). MCP `assist_list_scopes` is the capped `GET /agent/assist/scopes` |
| GET | `/agent/scopes/{scope_id}/subnets` | A scope's CIDRs, paged (`offset`, `limit` default 500, max 2000; `has_more`). MCP `scope_list_subnets` |
| GET | `/agent/scopes/{scope_id}/domains` | A scope's declared domains, paged the same way. MCP `scope_list_domains` |
| POST | `/agent/uploads` | Submit scanner output (multipart; curl). `409` with `detail.code: "duplicate_scan"` = already in |
| GET | `/agent/uploads/{job_id}` | An upload's parse status — only jobs this session uploaded. MCP `get_upload_job` |
| POST | `/agent/evidence` | Record a command you ran and what came back. MCP `record_evidence` |
| GET | `/agent/evidence` · `/agent/evidence/{id}/raw` | Evidence records · one record's full raw output (curl). MCP `list_evidence` |
| POST | `/agent/proposals/finding-text` · `/finding` · `/observation` · `/endpoint-status` | Propose a change a person decides. MCP `propose_finding_text` · `propose_finding` · `propose_observation` · `propose_endpoint_status` |
| GET | `/agent/proposals` | Proposals and their decisions. MCP `list_proposals` |
| POST | `/agent/tool-suggestions` | Suggest a tool for the team's catalogue (201): `name`, `rationale`, optional `category`, `description`. Grants nothing. MCP `suggest_tool` |

The three writes on a host (`POST /agent/hosts/{id}/notes`, `POST /agent/hosts/{id}/follow`, `PATCH /agent/hosts/{id}`) are in Notes, review status and host corrections.

### Host list filters (`GET /agent/hosts`)

`state`, `ports`, `services`, `subnets`, `has_critical_vulns`, `has_high_vulns`, `has_exploit_available`, `search`, `limit`, `offset`. These are the Hosts page's own filters — the same code answers the page, this route and `GET /agent/assist/hosts` — so each word means what the operator sees it mean:

| Filter | Meaning |
|--------|---------|
| `search` | The page's search box: a substring of the address, host name, OS name or family, or — on any of the host's ports — a port number, a service name or a product. |
| `has_critical_vulns` + `has_high_vulns` | Sent together: a critical **OR** a high scanner observation, as the page's severity chips. |
| `ports` with `services` | ONE open port must meet both (`ports=8443&services=http` is "http on 8443"), not "one port from the list and, anywhere, that service". |
| `ports`, `services` alone | `ports`: any of the numbers open; no ranges, no names. `services`: the service the scanner identified on an open port, on any port number. A comma list is ANY of them. |
| `state` | `up`, `down` or `unknown`. |
| `subnets` | CIDR blocks or single addresses; a host inside any of them. |

**A value that cannot be understood is a `422` that names it — never an empty answer and never the whole project.** That covers a port that is not a number, an unknown `state`, a `subnets` entry that is not a network or an address, and a list that names nothing (`ports=`, `services=,`). Correct the value and ask again; do not report a count from a call that was refused.

Each host carries `open_port_count` and `vuln_summary` (`{critical, high, medium, low}`).

**`/agent/hosts` is a bare array with no continuation signal.** `limit` defaults to 500 (max 5000) and the answer carries no `total` or `has_more`: raise `offset` by `limit` until a call returns fewer than `limit` rows. One call at the default on a 40,000-host project returns 1.25% of it with no warning. For a list with a `total`, use `GET /agent/assist/hosts` and `/agent/assist/hosts/count`.

<!-- agents:end -->

<!-- agents:section tags="testing" -->

### Host test endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/host-tests` | Propose tests — a batch of 1–200 (`{tests: [...], agent_model?}`); 201 returns the stored tests. All-or-nothing: an unknown host (404), an invalid field (422) or a reused `request_key` with different content (409) writes nothing. MCP `host_tests_propose` |
| GET | `/agent/host-tests` | List tests: `host_id`, `status`, `label`, `assigned_to_id`, `agent_session_id`, `mine` (assigned to your operator), `active_only` (proposed + in progress), `q` (a Hosts query — tests on the hosts it matches), `limit` (default 50, max 200), `offset`. Returns `{items, total, has_more}`; each item carries `revision` and `evidence_count`. MCP `host_tests_list` |
| GET | `/agent/host-tests/{test_id}` | One test — 404 if it is not in this project. MCP `host_tests_get` |
| PATCH | `/agent/host-tests/{test_id}` | Change `status`, `tester_summary`, `dismissed_reason` or `assigned_to_id`, with the `expected_revision` you read; 409 when the test changed since. MCP `host_tests_update` |

<!-- agents:end -->

<!-- agents:section tags="shared" -->

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 400 | A malformed `q` (the body gives `detail` and the `position` of the problem), a username that does not exist (`owner`, `author`) | Correct the value and send it again. |
| 401 | The key expired, was revoked, or is invalid. The body's fields are under `detail` (`error`, `recoverable`, `renew_path`, `message`) | Your prompt, § If your key expires. `detail.error: "operator_credentials_changed"` is never recoverable: the operator's password changed after the key was issued; what you already recorded is kept. |
| 403 | Your key carries its operator's project role, re-checked on EVERY request. Writes need `analyst`; remediation writes `admin`. A read needs what its page needs of a person: bulk exports (`*.ndjson`, target files), client reports and remediation reads need `auditor`; ingestion issues and uninterpreted lines need `analyst`; everything else any member. Also when the operator was deactivated or left the project | Do not retry and do not look for another route: tell the operator what was refused and why — only they (or a project admin) can change it. |
| 404 — `detail` begins "No agent endpoint at" | The path is not an endpoint (the body carries `hint`, `guide` and `feedback`) | Do not guess another URL: read this guide or, over MCP, `tools/list`. If you expected the route to exist, file it as feedback — the path and what you were trying to do. |
| 404 | The resource is not in this project (a host, test, finding, scope, upload job of another session) or was deleted. A list filtered by a `host_id` that is not in the project is a 404 too — not an empty list | Check the id. |
| 404 — remediation | Remediation tracking is not enabled on this installation | An answer, not an error: tell the operator. |
| 409 — `detail.code: "duplicate_scan"` (upload) | This exact file is already in the project — a scan, still parsing, or staged (`detail.scan_id` / `detail.job_id`) | Not a failure: the data is in. Never rename or alter the file to force a re-import. |
| 409 — test or evidence | `PATCH /agent/host-tests/{id}`: the test changed since the `revision` you sent. A create: the `request_key` was already used for different content | Stale revision: read the test again and decide whether your change still applies — never retry blind. Reused key: a new key for a new test or record; re-send the same content only to retry the same one. |
| 410 | The project is archived | Not retryable. Ending your session still works; tell the operator. |
| 413 (evidence) | `raw_output` is over 5 MB — or, over MCP, the whole request is over 1 MiB (send it with curl to `POST /agent/evidence`) | Trim to the relevant part, or upload the file if BlueStick parses its format. |
| 422 | An invalid or unknown field: a wrong enum (`priority`, `status`, `outcome`), a `done` test with no evidence and no `tester_summary`, a dismissal with no reason, a `target_fqdn` not observed at the host, report text that names a BlueStick record | The body names the field; check it against this guide. |
| 422 — a filter | A filter value the server could not understand: a port that is not a number, an unknown host `state`, a `subnets` entry that is not a network or an address, a list that names nothing, a `created_after` that is not a date, an unknown `part` of the guide. The body names the value | Correct the value and ask again. Never answer the operator from a refused call, and never drop the filter to get a result — that answers a different question. |
| 429 | Rate limited: 240 requests a minute per agent by default, in fixed 60-second windows shared by every server process. A rejected call still counts | Wait for the window to end (at most 60 s) and retry ONCE. |
| 503 — `detail` says a database query "ran longer than the server allows" | A read ran past the server's statement limit (30 s by default) and was stopped. Nothing was written; it is not an outage | Narrow the filter (a tighter `q`, a smaller page, one segment or host) and retry once. Do not report the result as empty or zero — say the read could not be computed. For a whole-project read use the streamed downloads, which are exempt from the limit. |
| 503 + `Retry-After` (upload) | The sweep's batch label was briefly busy. Nothing was stored | Wait the `Retry-After` seconds and re-POST the same file with the same `batch`. |

<!-- agents:end -->
