# AGENTS.md — BlueStick AI Agent Guide

**Version:** 2.79.0 | **Updated:** 2026-05-29

You are an AI assistant (Claude Code, Codex, ChatGPT, etc.) assigned to a workflow in BlueStick. This file is the entire surface you are authorized to use. Follow it literally — the surrounding scaffolding (human approval, per-session key scope, audit trail) depends on you behaving as described.

Everything below is reachable through one auth mechanism: the API key the user pasted to you. Do not attempt to log in, reach admin surfaces, rotate keys, create new agents, or touch endpoints outside `/api/v1/agent/*`. They are not available to you and calling them will return 401/403.

> **Context optimization:** this file supports `?workflow=plan_generation|execution|reconnaissance|assist` on the `/api/v1/agents-guide` endpoint to return only the sections relevant to your workflow. The prompt you were given already includes the right URL. A workflow slice is roughly a third of the full file (the assist slice is smaller because the assist surface is intentionally small).

---

<!-- agents:section tags="shared" -->

## Instance Identity (verify once before acting)

BlueStick publishes its identity at an unauthenticated well-known URI so you can verify that the host you're being asked to curl is the same instance that generated your prompt.

```
curl -sk https://<host>/.well-known/networkmapper.json
```

The response contains `instance_id`, `name`, `version`, `purpose`, and a `safety_properties` block. Your instructions block includes the `instance_id` the prompt was generated with — cross-check the two values match **once** at the start of your session. If they match, the session is trusted for the duration of your API key; you do not need to re-check on every request. If they do not match, stop and alert the user — the prompt may have been tampered with, copied from a different instance, or served by an unrelated host.

You can also read `safety_properties` to confirm the architectural guarantees you operate under:

- `all_commands_require_user_approval`: true — BlueStick is a coordinator, not an executor. Every command you run goes to the user's terminal for approval.
- `no_autonomous_execution`: true — you cannot run anything without human-in-the-loop.
- `audit_trail_persistent`: true — every action you take is recorded.
- `agent_keys_time_limited`: true — your API key expires in 24h.
- `agent_keys_scope_bound`: true — your key is locked to exactly one plan or one scope. Cross-scope access is rejected at the auth layer.

## Quick Start

The user will give you an **API key** and an **instructions block** copied from the BlueStick UI. If you don't have one, ask the user to go to **Scopes → Start Agentic Recon** (to populate host data for a scope), **Test Plans → Generate with AI** (to populate a new plan), or click **Execute with AI** on an approved plan (to run one), then paste you whatever the UI produces.

### Authentication

Every request to `/api/v1/agent/*` carries your API key in a header:

```
X-API-Key: nm_agent_abc123...
```

No login, no password, no `project_id` in the URL — the key is pre-scoped to exactly one project *and* one test plan. All reads and writes auto-scope to that project. Attempting to use the key against a different plan's endpoints returns 403.

> Both `X-API-Key: nm_agent_...` and `Authorization: Bearer nm_agent_...` are accepted. Prefer `X-API-Key`.

### Your key stopped working — what to do

- **401 "Agent API key expired" or "Invalid agent API key"** — your key was revoked or its 24-hour TTL elapsed. Ask the user to re-run **Generate with AI** or **Execute with AI** for the same plan and paste you the new key. Do **not** try to refresh the key yourself; per-plan keys cannot be rotated from the agent side.
- **403 "scoped to a different test plan"** — you used your key against another plan's endpoint. Check the `plan_id` in your URL matches the one from your instructions block. If you genuinely need to work on a different plan, ask the user to generate a new key for it.

### HTTPS / self-signed certs

The API uses HTTPS with a self-signed certificate. All `curl` commands require `-sk` (silent + insecure) to skip verification. If your execution environment blocks localhost/HTTPS, request approval once for the first `curl` call and continue automatically after approval. If that isn't possible, ask the user to run the commands for you or to provide an alternate reachable URL.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## Environment probe (MANDATORY first step)

> **Applies to recon, execution, and assist — NOT plan generation.** This step needs a session to POST the probe to (`/agent/{recon/sessions,execution-sessions,assist/sessions}/{session_id}/environment`). Plan-generation has no such session and no probe endpoint: a plan describes test *intent*, and the *executing* agent probes when it runs the plan. If you are a plan-generation agent, skip this section.

Before you propose, scan, or run anything else, **probe the operator's environment and report it back to BlueStick.** Plans describe test *intent*; you translate intent into commands at execution time — and that translation depends on what is actually available on this user's host. Two operators on the same project will have different environments (Windows + RemoteSigned vs Kali Linux), and the right command for one is the wrong command for the other.

### What to probe

Run a short capability check appropriate to the shell you're talking to and capture this fixed shape:

| Field | What to capture |
|---|---|
| `os_family` | `windows`, `linux`, `darwin`, `bsd`, or `other` |
| `os_release` | Distribution + version when known (`Ubuntu 22.04`, `Kali rolling`, `Windows 11 23H2`) |
| `arch` | `x86_64`, `arm64`, … |
| `shell` | `pwsh`, `powershell`, `bash`, `zsh`, `cmd` |
| `powershell_version` | `$PSVersionTable.PSVersion` if PowerShell is available |
| `powershell_execution_policy` | `Get-ExecutionPolicy` result (`Restricted` / `AllSigned` / `RemoteSigned` / `Unrestricted` / `Bypass`) |
| `python` | path to a usable Python — or the literal string `microsoft-store-stub` if `python` resolves to the Win10/11 Store stub (unusable) |
| `python_version` | `python --version` output, null when not present |
| `wsl_available` | Windows only: did `wsl --status` succeed? |
| `tools_available` | Map of tool name → boolean (`{"nmap": true, "masscan": false, ...}`). Cover at least the tools in [§ Tool inventory](#tool-inventory). |
| `tools_status` | Optional but recommended after preflight: **list of `{name, status, issue}` dicts**, one per tool, mirroring `preflight.sh --json`'s `tools[]` output. `status` is `"ok"` / `"warn"` / `"missing"` / `"info"`. Re-post the env with this populated so `/recon/context` can adapt `recommended_sequence` (drop tools that are absent, swap to fallbacks, surface `manual_action_required` when there's no usable option). See [§ Environment preflight script](#environment-preflight-script-v2133-shell-agnostic-guidance-v2411). |
| `notes` | Free text — AV product detected, sandbox/VM indicators, network egress restrictions, anything a reviewer should see. ≤2000 chars. |

> **Shape gotcha for `tools_status`.** Send it as a **list** (`[{"name": "nmap", "status": "ok"}, ...]`), not a dict keyed by tool name. Agents naturally reshape into a dict for client-side lookup convenience — the server tolerates both shapes since v2.44.4, but the canonical list form is documented and forward-compatible. Example: `"tools_status": [{"name": "curl", "status": "ok", "path": "/usr/bin/curl", "issue": ""}, {"name": "httpx", "status": "warn", "issue": "Python httpx CLI shadows ProjectDiscovery httpx"}, {"name": "eyewitness", "status": "missing"}]`.

### How to report it

For execution sessions (plan-scoped key):

```bash
curl -sk -X POST https://<host>/api/v1/agent/execution-sessions/{session_id}/environment \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"os_family":"linux","os_release":"Kali rolling", ...}'
```

For recon sessions (scope-bound key):

```bash
curl -sk -X POST https://<host>/api/v1/agent/recon/sessions/{session_id}/environment \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"os_family":"linux","os_release":"Kali rolling", ...}'
```

The examples above are bash/zsh. **On Windows PowerShell**, the bare `curl` is an alias for `Invoke-WebRequest` and will not accept these flags — use **`curl.exe`** (and double-quote, since single-quoted JSON isn't a PowerShell idiom):

```powershell
curl.exe -sk -X POST "https://<host>/api/v1/agent/execution-sessions/{session_id}/environment" `
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" `
  -d (@{ os_family = "windows"; os_release = "Windows 11 23H2"; shell = "pwsh" } | ConvertTo-Json -Compress)
```

…or native `Invoke-RestMethod -SkipCertificateCheck -Method Post -Headers @{ 'X-API-Key' = $KEY } -ContentType 'application/json' -Body (... | ConvertTo-Json)`. The endpoint + body shape are identical to the bash form.

Both endpoints echo back the persisted record with `probed_at`, `probed_by_user_id`, and `probed_from_ip` for the audit trail. The same data is then echoed on subsequent `/execution-context` and `/recon/context` responses, so once you've probed you don't have to re-send it — just read `environment` from the next context call.

### Command-flavour preference order (use the environment to pick)

When the environment tells you what's available, prefer in this order:

1. **Inline `powershell -Command "..."` one-liners** on Windows. The unsigned-script gate (`RemoteSigned` / `AllSigned`) fires on `.ps1` *files*, not on inline commands — so `Get-NetTCPConnection`, `Resolve-DnsName`, and `Test-NetConnection` work even under restricted policies. This is the most under-used capability in constrained Windows environments.
2. **Built-in OS binaries** — `nslookup`, `tracert`, `netstat`, `arp`, `nbtstat`, `net`, `nltest`, `ipconfig`, `whoami /all`, `qwinsta`, `systeminfo` on Windows; `dig`, `ip`, `ss`, `traceroute`, `ping` on POSIX. These bypass execution policy entirely.
3. **Python** — only when the probe shows a real Python (not the Microsoft Store stub). `python --version` produced a real version string and `python` is not `microsoft-store-stub`.
4. **WSL** — when `wsl_available: true`, you can fall back to the full Linux toolbox transparently.
5. **`.ps1` script on disk** — last resort. If you must use one, include the elevated `-ExecutionPolicy Bypass -File ...` invocation explicitly in the proposed command so the user is approving the bypass on the record, not the script silently.

### Plans describe intent — you translate at execution time

A plan entry's `proposed_tests` may include a sample command, but treat the description and `expected_evidence` as authoritative. Two examples of the same intent, two valid translations:

| Intent | Kali Linux (recon from Kali, executing from Kali) | Windows + RemoteSigned, no Python, no WSL |
|---|---|---|
| Enumerate SMB sessions on 10.0.0.5 | `enum4linux -S 10.0.0.5` | `powershell -Command "Get-SmbSession -CimSession 10.0.0.5"` |
| DNS reverse lookup for 10.0.0.5 | `dig -x 10.0.0.5` | `nslookup 10.0.0.5` |
| List listening ports on the local host | `ss -tlnp` | `powershell -Command "Get-NetTCPConnection -State Listen \| Format-Table"` |

When you record the test result, put the *actual command you ran* in `command_run` so a reviewer can correlate the plan's intent with what happened on this operator's machine. The audit trail is then full: BlueStick has the inbound API calls, the probe identifying the environment, and the agent-reported `command_run` per test.

### What the user sees

Every request you make to `/api/v1/agent/*` is recorded by BlueStick and surfaced to the operator: plan-scoped calls on the Test Plan Detail page, recon-scoped calls on the Recon Session detail page, both under "Agent API activity". The user can filter by host, by target IP, by status code — they can verify directly that you queried the hosts you said you would. Do not try to obscure activity by routing around the API; you would only be visible-but-suspicious instead of visible-and-correct. Operate transparently.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance,execution" -->

## Working directory & concurrent agents (MANDATORY)

One operator often runs **two agentic workflows at once** — e.g. a recon
run and an execution run, or two recon runs, possibly on different
models. BlueStick keeps those *server-side* isolated (each session
has its own API key, its own audit trail; the ingestion worker
serializes uploads safely). But the **operator's machine is shared** —
two agent processes see the same filesystem and the same process table,
and nothing isolates them there unless you do.

**Before running any tool, create a session-scoped working directory
and `cd` into it:**

```
mkdir -p networkmapper-<project_slug>-<workflow>-<session_id>
   # e.g. networkmapper-homenetwork-recon-42
cd networkmapper-<project_slug>-<workflow>-<session_id>
```

Your prompt's `mkdir` line already fills in the project slug, workflow,
and session id — copy it verbatim. The qualified path **self-documents
which project a folder belongs to** when an operator works two projects
out of the same parent directory (`networkmapper-homenetwork-recon-1`
vs `networkmapper-clienta-recon-1`), and survives a Nuclear-Clean reset
(where session ids restart at 1) without colliding with leftover
folders.

Run **every** tool from inside this directory; every output file,
target list, and result directory (`nmap.xml`, `httpx.jsonl`,
`targets.txt`, `eyewitness-results/`, …) lives here.

Why this is mandatory:

- **No file collisions.** Two agents both writing `targets.txt` or
  `nmap.xml` into a shared cwd silently overwrite each other — you would
  upload a file the *other* agent produced. (The scan-naming convention
  governs *upload* filenames; it does not stop two agents clobbering
  *working* files on disk. The directory does.)
- **Your processes become identifiable.** If you launch a long scan and
  later check on it, `pgrep nmap` / `ps | grep nmap` shows **every**
  agent's nmap — you cannot tell yours apart by tool name. But because
  every command runs from `networkmapper-<project>-recon-42/`, that
  path is in the process's argv:
  `ps aux | grep networkmapper-homenetwork-recon-42` matches **only
  your session's** processes. Never identify a process by tool name
  alone when other agents may be running.
- **For a backgrounded scan, capture its PID** at launch
  (`nmap … & echo $!`) and poll *that PID* — do not re-discover it by
  name later.

Do not delete the directory when you finish — the operator may want the
raw tool output. Cleanup is their call.

<!-- agents:end -->

---

<!-- agents:section tags="plan_generation" -->

## Workflow A — Build a Test Plan (from `/generate`)

This is the flow when the user clicks **Test Plans → Generate with AI**. Your job is to populate a draft plan with structured test entries and submit it for human review.

```bash
# 1. Review candidate hosts (services, vulnerabilities, port data).
#    /context is PAGINATED — at most `limit` hosts per call (default 500).
#    Page until you've seen every candidate; has_more: true means
#    "fetch the next page", NOT "the rest are off-limits".
GET /agent/test-plans/{plan_id}/context
#    → while summary.has_more is true:
GET /agent/test-plans/{plan_id}/context?after_host_id={summary.next_cursor}

# 2. Set the plan description (required before submission)
PATCH /agent/test-plans/{plan_id}
{"description": "🤖 **Agent-generated** — {agent_name}\n\nScope, methodology, and prioritization summary..."}

# 3. Add structured test entries for candidate hosts (≤500 per call —
#    POST in multiple batches if you selected more than 500 hosts)
POST /agent/test-plans/{plan_id}/entries
{"entries": [{"host_id": ..., "priority": "...", ...}, ...]}

# 4. Validate the plan (dry-run — check for warnings)
GET /agent/test-plans/{plan_id}/validate

# 5. Submit the plan for human review
POST /agent/test-plans/{plan_id}/submit
```

**Control flow:** After step 1, report a brief summary to the user (e.g. "Found 36 actionable hosts, 12 with critical vulnerabilities"), then **continue automatically** through steps 2-5 without waiting for user input. Only stop if the validate step returns warnings you cannot resolve. After step 5, confirm submission (e.g. "Plan submitted with 28 entries — status: proposed").

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

**Tool notes** (added after agent feedback — v2.9.8):
- **`nuclei`** — template-based scanner for CVE validation, exposed panels, default configs, and tech fingerprinting. Use `-t cves/<cve-id>` for confirmed-vuln validation, `-t exposures/` on web surfaces, `-t technologies/` for breadth. Honors rate limits; prefer `-rl 50` in shared environments.
- **`testssl.sh`** — TLS weakness validation (cipher strength, protocol downgrades, cert chain, HSTS). Use whenever the context surfaces TLS findings or the port is 443/8443/993/465/etc. Non-intrusive; safe to run without approval-per-command escalation.
- **`netexec`** (formerly crackmapexec) — modern successor for SMB/WinRM/RDP/MSSQL/SSH enumeration. Supports null-session checks (`--shares -u '' -p ''`), default-credential sweeps (restrict to sanctioned wordlists only), and remote command execution for sanctioned testing. **Requires explicit user approval for any credential-bearing run.**

<!-- agents:end -->

---

<!-- agents:section tags="execution" -->

## Workflow B — Execute an Approved Plan (from `/execute`)

This is the flow when the user clicks **Execute with AI** on a plan in `approved` or `in_progress` status. You drive execution of the individual tests with **mandatory per-test human approval** and **per-host target verification**.

> **Key principle:** You are a coordinator, not an autonomous executor. The user's terminal is the executor. You propose each command, wait for explicit approval, run it (if you have shell access) or ask the user to run it, then record the result.

### Execution flow

```bash
# 1. Fetch execution context — hosts, tests, known services.
#    Commands have {ip} resolved to actual IPs.
GET /agent/test-plans/{plan_id}/execution-context

# 2. For each host (in priority order: critical → low):

#    2a. SANITY CHECK — verify target before any tests.
#        Run: dig -x {ip}, nc -w 3 {ip} {known_port}, report source IP.
#        Record: POST /agent/test-plans/{plan_id}/entries/{entry_id}/sanity-check
#        If the check fails → STOP and ask the user for guidance.

#    2b. For each test in the entry:
#        PRESENT the command to the user:
#          "Tool: nmap  Command: nmap --script smb-enum-shares -p 445 10.0.1.5
#           Expected: List of shares with access levels.
#           Shall I run this? [yes / modify / skip / abort]"
#        WAIT for user response. Do NOT proceed without approval.
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

If your host crashed or the agent stopped mid-execution, the work is **resumed**, not restarted. The operator clicks **Resume** on the interrupted session in BlueStick; that mints a fresh API key and hands you a new instructions block carrying a `⟳ RESUMED SESSION` notice.

When your instructions carry that notice:

- **Fetch `/execution-context` before doing anything else.** It reports each entry's `entry_status` and each test's `result_status`. Any entry already `completed`, and any test already `executed` or `skipped`, is **done** — do not re-run it.
- **Do not re-sanity-check** a host whose entry already recorded a passing sanity check.
- Resume at the first host/entry with outstanding work and continue the normal flow.
- The environment probe (step 1, below) is still required — your new key has no probe yet — but every prior result is intact and must not be overwritten.

The session id is unchanged, so your results append to the same audit trail. To keep that trail readable for the human reviewer — and to make any future resume cleaner — post a real `findings_summary` on each entry `/complete` **as you finish each host**, not only at the end.

### Safety protocol

Three safety layers — do not skip any of them:

1. **Per-test human approval (terminal layer).** Present each command to the user and wait for `yes/modify/skip/abort` before executing. For agents with shell access (Claude Code, Codex), this is the built-in tool-use approval gate. For agents without shell access (ChatGPT), the user runs commands themselves and pastes you the output.

2. **Per-host sanity check (network layer).** Before testing any host, verify the target:
   - Report your own source IP, default gateway, and DNS server
   - Run `dig -x {ip}` and compare to the expected hostname
   - Banner-grab a known open port and compare to the service BlueStick recorded
   - This is single-port *verification* of already-recorded data (a `dig -x` + one `nc` banner-grab), **not** a port sweep or re-scan — keep it scoped to the one known port; recon already enumerated the host
   - If any check fails, stop and ask the user — do **not** proceed against a potentially wrong target

3. **Audit trail (API layer).** Every test attempt is recorded with timestamps, the sanity check is logged before any tests run, and the execution session tracks overall progress. Results from interrupted or abandoned sessions are preserved with the session's terminal status.

### Test result status values

| Status | Meaning |
|--------|---------|
| `pending` | Test exists but hasn't been attempted |
| `pending_approval` | You proposed the command, waiting for user response |
| `executed` | Command was run and output recorded |
| `skipped` | User chose to skip this test |
| `failed` | Command failed to execute (network error, tool crash, etc.) |
| `not_applicable` | Test doesn't apply to this host on closer inspection |

`executed`, `skipped`, `failed`, and `not_applicable` are **terminal**. `pending` and `pending_approval` are **not** — a result row left in either state blocks entry completion (see below); resolve every recorded result to a terminal status before calling `/complete`.

### Entry completion gates

`POST /agent/test-plans/{id}/entries/{eid}/complete` enforces three gates and returns `400` if any fails:

1. **Per-test coverage.** For an entry with N proposed tests, **every** `test_index` 0..N-1 must have a result row — not just one. Record a terminal result for tests you don't run too (`skipped` / `failed` / `not_applicable`). Recording 1 of 3 and completing without a reason returns a 400 naming the missing indices.
2. **Terminal results.** No result row may be left `pending` / `pending_approval`.
3. **Empty entry.** An entry with zero proposed tests (or one you're closing before covering every test) must pass an explicit `no_tests_run_reason` in the complete payload (e.g. "host went offline before testing", "reclassified out of scope mid-run"). It's captured next to `override_reason` in the audit row.

The separate **sanity-check gate** still applies: completion also needs a passing `HostSanityCheck` for the entry, or an `override_reason`.

### Sanity check methods

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
- `environment` — your last posted environment probe (`os_family`, `shell`, …), or `null` until you POST one; echoed back so you don't re-send it
- `agent_name` — for attribution in findings
- `hosts[]` — one per entry, sorted by priority (critical first):
  - `entry_id`, `host_id`, `ip_address`, `hostname`, `os_name`
  - `priority`, `test_phase`, `entry_status`
  - `sanity_check_passed` — null if not yet checked, true/false after
  - `tests[]` — each proposed test with `{ip}` resolved in commands, plus `result_status` (null if not yet recorded)
  - `known_services[]` — open ports with service name, product, version. **Authoritative open-port/service set** (recon already discovered these): target tests at these ports and do not re-enumerate/re-scan. (Also used for the sanity-check banner-grab comparison.)

### Raw output storage

`raw_output` in test results is capped to the configured `TEST_OUTPUT_MAX_BYTES` (default 100KB). Output exceeding the cap is truncated with a `--- OUTPUT TRUNCATED ---` marker. If you need to record verbose tool output (Nessus, nmap scripts), trim it to the most relevant sections before sending.

<!-- agents:end -->

---

<!-- agents:section tags="reconnaissance" -->

## Workflow C — Populate Host Data via Reconnaissance (from `/recon/start`)

This is the flow when the user clicks **Scopes → Start Agentic Recon**. Your job is **completely different** from plan generation:

**You are populating BlueStick's host database.** You run scanner tools locally (nmap, masscan, etc.), submit the raw output to BlueStick for parsing, iterate until the scope is well-characterized, and then complete. A human reviews the populated data and (as a separate step) decides what to test.

**You do NOT create test plan entries in this workflow.** Your API key is scope-bound, not plan-bound — every call to `/agent/test-plans/*` returns 403. Test plan generation is Workflow A, which runs *after* recon populates the database.

### Key differences from Workflow A

| | Workflow A (plan generation) | **Workflow C (reconnaissance)** |
|---|---|---|
| **Goal** | Create a prioritized list of tests to run | **Populate host/port/service data in the DB** |
| **Key binding** | `test_plan_id` (plan-scoped) | `scope_id` (scope-scoped) |
| **Writes to** | `test_plans`, `test_plan_entries` | `hosts`, `ports`, `scans` (via ingestion pipeline) |
| **Endpoint root** | `/agent/test-plans/*` | **`/agent/recon/*`** |
| **What you submit** | JSON test entries | **Raw scanner output files (nmap XML, masscan, etc.)** |
| **How you know when you're done** | Plan meets coverage | You've characterized the scope |

### Resuming an interrupted recon session

If your host crashed mid-recon, the operator clicks **Resume** on the recon session — this re-issues the **same** session with a fresh API key rather than starting a parallel one (which would fragment the rolling host/scan counts). Your instructions then carry a `⟳ RESUMED RECON SESSION` notice.

When you see it:

- **Call `GET /agent/recon/summary` and `GET /agent/recon/context` before scanning.** They report the rolling host/port counts and the already-known hosts.
- Continue coverage from where the prior pass stopped rather than re-scanning subnets already characterized. Re-uploading a duplicate scan is harmless — ingestion dedupes it — but it wastes a pass.
- The environment probe (step 1) is still required for the new key.

### Recon flow

```bash
# 1. Orient yourself — get the scope's CIDRs, size analysis, recommended sequence, tool catalog
GET /agent/recon/context
# → { recon_session_id, scope_id, scope_cidrs, scope_size, recommended_sequence,
#     known_host_summary, tool_catalog, session_status, started_at }

# 2. Pick a tool from the catalog (or adapt), propose command, get user approval, run locally
#    IMPORTANT: use machine-readable output flags so BlueStick can parse the file.
#      - nmap    → -oX <file.xml>     (XML output)
#      - masscan → -oX or -oJ
#      - naabu   → -json -o <file.json>
#      - nuclei  → -je <file.json>
#      - eyewitness → --web -f targets.txt -d <dir>

# 3. Upload the output file
POST /agent/recon/upload  (multipart/form-data)
  file=<output file>
  tool_name=<e.g. "nmap">          # optional, for audit trail
  command_run=<the exact command>  # optional, for audit trail
# → { job_id, filename, status: "queued", message, recon_session_id }

# 4. Poll the parse job
GET /agent/recon/jobs/{job_id}
# → { status: "queued"|"processing"|"completed"|"failed", scan_id, tool_name, last_error,
#     queue_age_s, parse_s, ... }
#   queue_age_s = seconds the upload waited before the worker picked it up
#   parse_s     = seconds spent parsing (created->started, started->completed)
#   Both are null until the matching transition happens — use them to tell a
#   backed-up queue from a genuinely slow parse before you keep polling.

# 5. Check the rolling summary — decide what to do next
GET /agent/recon/summary
# → see "Summary response shape" below for the exact field names

# 6. Iterate — repeat 2–5 until the scope is well-characterized.

# --- Scan-naming convention (IMPORTANT) ---
# The uploaded file's NAME becomes the scan's display name.  If you
# split a scope into multiple scans of the same tool — e.g. one nmap
# run per /24 of a large scope, or a discovery pass then a service
# pass — give each output file a DISTINCT, metadata-bearing name.
# Uploading three files all called `nmap.xml` produces three scans
# named "nmap.xml" that are impossible to tell apart in the UI.
#
# Recommended pattern: <tool>_<phase>_<target-or-range>_<UTC-stamp>.<ext>
#   nmap_discovery_192.168.7.0-24_20260518T2230Z.xml
#   nmap_services_live-hosts_20260518T2240Z.xml
#   nmap_discovery_10.0.0.0-20_part2_20260518T2255Z.xml
# Keep it filesystem-safe (no spaces or colons).  `command_run`
# already captures the exact invocation for the audit trail — the
# filename is for the human scanning the scans list, so make it
# self-describing at a glance.

# 7. Close the session  — MANDATORY, and the LAST thing you do
POST /agent/recon/complete
Content-Type: application/json

{"notes": "Short summary of what you ran and what you found."}
# → ReconSummary with final frozen counts
#
# The recon session stays `active` in the operator's UI until this
# call returns 200.  Uploading scans and submitting feedback do NOT
# complete it — they are unrelated calls.  A run that scanned,
# uploaded, and submitted feedback but skipped /recon/complete looks
# unfinished to the operator forever (until someone hits Abandon).
# Final-action order: finish all uploads -> submit feedback -> POST
# /agent/recon/complete LAST.  Confirm the 200 before you stop.
```

### Summary response shape

`GET /agent/recon/summary` and the `POST /agent/recon/complete` response share the same envelope. Field names matter — agents have shipped wrong-field bugs (`ports` instead of `open_ports`) when reading this by trial and error.

```json
{
  "recon_session_id": 2,
  "scope_id": 1,
  "status": "active",                    // "active" | "completed" | "failed" | "abandoned"
  "uploads_submitted": 2,
  "scans_ingested": 2,
  "hosts_discovered": 19,
  "ports_discovered": 26,
  "started_at": "2026-05-18T22:14:26Z",
  "completed_at": null,
  "hosts": [
    {
      "host_id": 18,
      "ip_address": "192.168.7.200",
      "hostname": "pi.hole",
      "open_port_count": 4,
      "services": ["domain", "ssh", "webdav"],   // deduped service names — quick scannability
      "open_ports": [                            // full port records — authoritative
        {"port": 22, "protocol": "tcp", "state": "open", "service": "ssh",
         "product": "OpenSSH", "version": "9.2p1 Debian 2+deb12u9"},
        {"port": 53, "protocol": "tcp", "state": "open", "service": "domain",
         "product": "dnsmasq", "version": "pi-hole-v2.92test13"}
      ]
    }
  ],
  "web_targets": [                               // pre-computed for follow-on web tooling
    {"host_id": 18, "ip_address": "192.168.7.200", "hostname": "pi.hole",
     "port": 80, "protocol": "http", "url": "http://192.168.7.200/"}
  ],
  "live_hosts_file_content": "192.168.7.200\n..."  // every host found SO FAR, newline-joined
}
```

Per-host fields use `open_ports` (full objects) and `services` (deduped names) — *not* `ports`. `web_targets` is pre-filtered to ports detected as HTTP/HTTPS so an agent can drive httpx/eyewitness/nikto from a single array without re-scanning the open_ports list. `live_hosts_file_content` is a ready-to-redirect target file of every host discovered so far this session (newline-joined IPs, trailing newline) — write it straight to `session-hosts.txt` and feed the next stage with `-iL` instead of rebuilding the list from `hosts[]`. Empty string until the first host lands.

> **`hosts[]` and `web_targets[]` are UNBOUNDED — no pagination, no truncation flag.** Every in-scope discovered host is returned in one response with full per-host port detail; a `/20` sweep can return tens of thousands of host objects. The payload is *complete* (good for coverage) but can blow your context window if you read or echo it whole. Don't dump it — iterate incrementally, and use the scalar counters (`scans_ingested`, `hosts_discovered`, `ports_discovered`) for progress reporting.

### Scope-size awareness

`GET /agent/recon/context` now computes scale signals server-side so you don't have to do IP math to pick the right discovery tool.

```json
"scope_size": {
  "total_addresses": 65536,
  "largest_subnet_size": 65536,
  "cidr_count": 1,
  "size_bucket": "large",            // "small" | "medium" | "large"
  "recommended_discovery": "masscan", // "nmap_sn" | "rustscan_nmap" | "masscan"
  "estimated_durations": {
    "nmap_sn": "~2–6 hours",
    "masscan": "~1–3 min",
    "rustscan_nmap": "~5–15 min"
  }
}
```

Thresholds:

| Bucket | Addresses | Default tool | Why |
|---|---|---|---|
| `small` | ≤ 256 (~/24) | `nmap -sn` | Direct sweep finishes in minutes; thorough. |
| `medium` | 257–4096 (/20–/24) | `rustscan` → `nmap` | Rustscan finds hits in seconds; nmap service-probes the hits only. |
| `large` | > 4096 (/20+) | `masscan` | Only tool that finishes a /16 in minutes, not hours. |

**Pick by `recommended_discovery` — don't default to `nmap -sn` on anything bigger than a /24.** The catalog's discovery section is reordered to match: the recommended tool is entry #1 for this scope's size.

#### Very large scopes — the subnet list may be truncated

A scope can contain **thousands** of subnet CIDRs. To keep the prompt and `/recon/context` response inside your context window:

- The **recon prompt inlines at most ~25 CIDRs**. Past that you'll see `… and N more` plus a pointer to the paginated endpoint.
- **`/recon/context` caps `scope_cidrs` at 100.** Two new fields tell you when: `scope_cidrs_total` (the true count) and `subnets_truncated` (boolean). If `subnets_truncated` is true, `scope_cidrs` is only a sample — do **not** treat it as the whole scope.
- The **authoritative full list** comes from `GET /agent/recon/subnets?offset=0&limit=500`. Walk `offset` in `limit`-sized pages until the response's `subnets` array is empty (`has_more: false`). Ordered by subnet id, so paging is stable.

**Plan for it.** On a large multi-thousand-subnet scope you cannot hold every CIDR in context, run one scan, and be done. Work in **batches**: page a chunk of CIDRs, scan that chunk, upload (with a distinct metadata-bearing filename — see the Scan-naming convention above), poll, then page the next chunk. Report progress to the user between batches. `scope_size.total_addresses` tells you the magnitude up front — if it's in the millions, tell the user the estimated wall-clock (e.g. "~6h at default masscan rate") and ask whether to (a) proceed comprehensively, (b) sample a representative subnet first, or (c) narrow the scope before continuing. Wait for an explicit choice before burning hours of scan time.

#### Chunk your scans and uploads — don't produce one monolithic file

Batching isn't only a context-window concern; it matters just as much for **scanning and uploading**. Do **not** point one nmap/masscan run at an entire multi-thousand-host scope and then upload one giant result file. Split the work:

- **Chunk size** — aim for roughly **256–1024 addresses (about a /22–/24), or ~25–50 CIDRs from the paginated subnet list, per scan+upload**. Tune down for slow `-sV -sC` service scans, up for fast masscan discovery sweeps. When unsure, smaller is safer.
- **One scan → one upload → poll → next chunk.** Don't queue ten scans and upload them all at the end.

Why chunking beats a monolithic scan+upload:

1. **File size** — a `/16` nmap XML can be hundreds of MB. The upload proxy caps request bodies at 500 MB; a single monolithic scan can exceed that outright and fail to upload at all.
2. **Failure isolation** — if one giant upload fails to parse, you lose *everything*. If one chunk of ten fails, the other nine are already ingested and safe.
3. **Incremental visibility** — each chunked upload bumps the host/port counts in `GET /agent/recon/summary`, so the operator watches real progress instead of staring at a session that looks frozen for hours.
4. **Serial ingestion worker** — uploads are parsed one job at a time. A single huge file monopolizes the worker; smaller chunks interleave and keep the queue moving.

Pair every chunk's upload with the **Scan-naming convention** above so the operator can tell the chunks apart in the Scans list (`nmap_discovery_10.0.0.0-22_20260518T2230Z.xml`, `…_10.0.4.0-22_…`, …).

### Recommended sequence

The context response returns `recommended_sequence[]` — a 3-step starter plan stitched from the catalog for this specific scope. Each step has `{step, phase, command, estimated_duration, note, output_file, upload_after}`.

**v2.13.2 — comprehensive-by-default.** The plan always leads with fresh discovery, even when prior hosts exist in the scope. Pre-v2.13.2 behavior suggested skipping discovery when prior data existed, which silently narrowed scope and missed any new or changed hosts since the previous recon. An agent running feedback-session #5 did exactly that and under-scanned the /16. The fix: default to comprehensive, and surface the narrowing option explicitly so the user owns the speed/coverage trade-off. When prior data exists, `recommended_sequence[0]` is a **warning**, not a suggestion, and a separate `known_hosts_probe` field carries the ready-to-use narrow-path command.

Example for a large scope:

```json
[
  { "step": 1, "phase": "discovery", "command": "masscan --rate=5000 -p22,80,443,445,3389,8080,8443 -oX masscan-sweep.xml 10.0.0.0/16", "estimated_duration": "~1–3 min", "upload_after": true },
  { "step": 2, "phase": "service_probe", "command": "nmap -sV -sC -T3 --top-ports 1000 -iL live-hosts.txt -oX nmap-services.xml", "estimated_duration": "~5–30 min", "upload_after": true },
  { "step": 3, "phase": "web", "command": "httpx -l web_targets.txt -sc -title -server -tech-detect -favicon -tls-probe -cdn -json -o httpx.jsonl", "estimated_duration": "~2–10 min", "upload_after": true },
  { "step": 4, "phase": "web_screenshot", "command": "eyewitness --web -f web_targets.txt -d eyewitness-results --no-prompt", "estimated_duration": "~5–20 min", "upload_after": true, "optional": true }
]
```

**Step 4 — `web_screenshot` (optional, v2.45.5).** httpx (`-json`) captures status / title / server / tech / TLS / favicon but **no screenshots** — its JSONL has no image data, and the catalog command has no `-ss` flag. The two-stage design is: **httpx culls dead targets fast, then eyewitness screenshots the survivors.** Step 4 is that screenshot pass — marked `"optional": true`. Run it only when the operator wants visual triage, and point it at the **httpx-confirmed live URLs**, not the raw `web_targets` list (eyewitness spins a headless browser per target — minutes-per-host, so don't aim it at a huge dead surface). The upload populates `WebInterface.screenshot_path`, which then renders as a thumbnail in the host detail UI. If eyewitness isn't installed, `httpx -ss` is the alternative — but ask the operator before adding screenshot capture to step 3. When the env probe shows httpx itself is unavailable, the httpx→eyewitness swap turns step 3 into eyewitness (which screenshots anyway) and step 4 is dropped automatically.

If `known_host_summary.hosts_with_open_ports > 0`, a `step: 0` warning is prepended that **flags the narrowing option without recommending it**. The pre-built command for the narrow path is on the context response as `known_hosts_probe` (see below). Always present each step's command + duration to the user during approval so they know the wall-clock cost before saying yes.

**v2.39.0 — environment-aware substitutions** (v2.41.1 hardening). When the env probe (Step 0) includes a `tools_status` field — or just `tools_available` — and the default tool for a step is `missing` or `warn` on this host, the server returns either a swapped step inline or, if the fallback tool is *also* unavailable, an explicit `manual_action_required` placeholder.  Working swaps carry two extra fields:

```json
{
  "step": 3, "phase": "web",
  "tool": "eyewitness",
  "command": "eyewitness --web -f web_targets.txt -d eyewitness-results",
  "original_tool": "httpx",
  "swap_reason": "preflight warn: ProjectDiscovery httpx not on PATH (Python CLI shadows it)",
  "note": "Adapted from httpx to eyewitness because ...",
  ...
}
```

When *both* the default tool and its fallback are missing/broken, the server emits a blocked placeholder instead of a swap that wouldn't run (v2.41.1, review C-RR-3):

```json
{
  "step": 1, "phase": "manual_action_required",
  "tool": null,
  "command": null,
  "original_tool": "masscan",
  "fallback_tool": "rustscan",
  "blocked_reason": "neither_available",
  "note": "BLOCKED: neither masscan (...) nor the documented fallback rustscan (...) is usable in this environment.  Install one before continuing — see the `install_hints` block on the catalog entry for either tool, or ask the user to install via their package manager.  Do NOT improvise an alternate tool without per-command approval.",
  "upload_after": false
}
```

When the masscan→rustscan swap fires, the server also collapses the downstream standalone service-probe step (the one that would have used `live-hosts.txt`, which rustscan doesn't produce) into a synthesized web-fingerprint step matching the canonical `recommended_discovery=rustscan_nmap` shape (v2.41.1, review C-RR-2). The synthesized step carries `"synthesized_after": "masscan_to_rustscan_swap"` so the UI / agent can surface that derivation.

Current swaps:

| Default tool | Swap to | Triggered when |
|---|---|---|
| `httpx` (web) | `eyewitness` | `tools_status[httpx].status` is `warn`/`missing`, or `tools_available.httpx === false` |
| `masscan` (discovery on large scopes) | `rustscan` | same, for masscan — covers both missing-binary and no-raw-socket-privilege cases |

When you call `/agent/recon/context` *before* posting the env probe, no adaptation happens (no signal to adapt on); subsequent calls after the probe lands return the adapted sequence. Surface `swap_reason` to the user during plan approval so they see which steps deviated from the canonical plan and why. If you see `manual_action_required`, **stop and report to the user** rather than improvising — the placeholder exists precisely so the agent doesn't try an alternate tool without explicit approval.

### Known-hosts probe helper

When the scope has prior hosts with open ports, `/agent/recon/context` returns:

```json
"known_hosts_probe": {
  "live_hosts": ["10.0.0.5", "10.0.0.12", ...],
  "live_hosts_file_content": "10.0.0.5\n10.0.0.12\n...",
  "command": "nmap -sV -sC -T3 --top-ports 1000 -iL live-hosts.txt -oX nmap-services-known.xml",
  "note": "N host(s) with open ports are already known in this scope.  If the user asks you to narrow..."
}
```

Use this only when the user explicitly chooses to narrow during plan approval. Write `live_hosts_file_content` straight to `live-hosts.txt` and run `command` — no need to query `/agent/hosts` and build the target list yourself. Default is still comprehensive; narrowing is a speed/coverage trade-off the user owns.

### Web target helper

`/agent/recon/summary` and `/agent/recon/complete` now include:

```json
"web_targets": [
  {"host_id": 42, "ip_address": "10.0.0.5", "hostname": "www.corp", "port": 80, "protocol": "http", "url": "http://10.0.0.5/"},
  {"host_id": 42, "ip_address": "10.0.0.5", "hostname": "www.corp", "port": 443, "protocol": "https", "url": "https://10.0.0.5/"},
  ...
]
```

Derived from `hosts[].open_ports` using the standard HTTP/HTTPS port map (80/8080/8000/81 → http, 443/8443/4443 → https) plus explicit service-name hits for non-standard ports (e.g., https on 10443 that nmap version-probed). Feed directly to httpx / eyewitness / nikto without a second round trip.

Each `hosts[].open_ports[]` entry carries `{port, protocol, state, service, product, version}` — enough to build any follow-up target list without cross-referencing `/agent/hosts` or parsing the uploaded XML locally.

The same response also carries `live_hosts_file_content` — a newline-joined file of every host discovered so far this session (trailing newline, IP-sorted). For the staged service-probe pass below, write it straight to `session-hosts.txt` and run `nmap -sV -iL session-hosts.txt` rather than rebuilding the list from `hosts[]`. It's empty until the first host lands, and (unlike `known_hosts_probe.live_hosts_file_content`, which is *prior* recon) it reflects THIS session's running discoveries.

**Staged discovery is mandatory.** Never run `nmap -sV` on a full CIDR — it probes dead addresses for hours. Always: fast sweep → live-host list → service probe on hits only. `--top-ports 1000` covers >95% of real services; use `-p-` only as a targeted escalation on high-value hosts flagged during triage.

### Tool catalog

`GET /agent/recon/context` returns `tool_catalog[]`, a list of `{phase, tool, command, rationale, intrusive, output_format, estimated_duration?, best_for?, preflight?, requires_privileges?, alternatives?, install_hints?}` entries parameterized against the scope's CIDRs and reordered so the fastest discovery tool for this scope size leads.

**v2.13.2 `install_hints`** — when a preflight fails with "command not found" or the wrong binary on PATH (the httpx collision is the classic case), the entry's `install_hints` dict carries provider-specific install paths: `apt`, `brew`, `cargo`, `go`, `binary` (prebuilt download URL), `docker`. Pick whichever matches the agent's environment. For httpx specifically, `binary` is usually the right pick in sandboxed environments since Go isn't typically available. For masscan, there's also a `privilege_fix` hint (`sudo setcap cap_net_raw=eip $(which masscan)`) for granting the capability once without needing interactive sudo on every run.

### Environment preflight script

For a one-shot check of the entire recon-tool surface, instead of running per-entry `preflight` commands, use the aggregated preflight script.

**In bash-capable environments** (Linux, macOS, WSL, bash on Cygwin) — pipe directly:

```bash
# human-readable report
curl -sk https://<nm-host>/api/v1/references/preflight-script | bash --

# JSON output for parsing inside the agent
curl -sk https://<nm-host>/api/v1/references/preflight-script | bash -s -- --json

# exit non-zero if any essential tool is missing (CI / agent gate)
curl -sk https://<nm-host>/api/v1/references/preflight-script | bash -s -- --strict
```

**In PowerShell-only environments** (Windows without WSL) — the script is bash; you cannot pipe it to `bash` because there is no `bash`. Fetch and inspect, then produce the equivalent `tools_status` payload by hand:

```powershell
curl.exe -sk https://<nm-host>/api/v1/references/preflight-script -o preflight.sh
# Inspect preflight.sh to see the tool list it checks for (nmap, masscan, rustscan,
# httpx, whatweb, eyewitness, nikto, subfinder, amass, naabu, netexec, smbmap, nuclei, ...).
# For each tool produce one tools_status[] entry of the same shape the script's
# --json mode would have emitted:
#   {"name": "nmap", "status": "ok" | "warn" | "missing", "issue": "..."}
# Use `Get-Command <tool>` to check presence; mark missing tools accordingly.
```

A native PowerShell sibling script is a tracked follow-up; until then, the above is the supported PowerShell flow. Either way, the resulting `tools_status` payload is re-POSTed to `POST /agent/recon/sessions/{id}/environment` so the server can adapt `recommended_sequence`.

Also available at `scripts/preflight.sh` in the BlueStick repository for manual invocation.

The script:

- Reports presence + version for 20+ tools (nmap, masscan, rustscan, httpx, eyewitness, nikto, subfinder, amass, naabu, netexec, smbmap, nuclei, bloodhound-py, gvm-tools, plus support tools curl/jq/xmllint/python3/dig and optional runtimes go/cargo/pipx/docker).
- Detects the httpx Python-CLI collision automatically (reports a `WARN` with fix instructions rather than a false `OK`).
- Checks whether masscan can actually run — both sudo-non-interactive and `cap_net_raw=eip` capability. Reports `WARN` with the `setcap` fix if neither is available.
- For every missing / warned tool, prints installation hints that point **only** at official upstream sources — GitHub project repos, vendor pages, or distribution packages. No third-party Docker images, no random forks.
- JSON output matches the `install_hints` shape used elsewhere in the agent API.

Run this once at session start. Much faster than running 20+ individual preflight commands.

**Always run `preflight` before using a tool for the first time in a session.** It catches the common environment issues that would otherwise produce a failed scan + wasted upload: wrong binary resolution, missing privileges, tool not installed. Two real examples from agent feedback:

- **`httpx` binary collision** — in Python-heavy environments the Python `httpx` CLI shadows ProjectDiscovery's httpx on PATH and uses incompatible flags. The preflight (`httpx -version | grep -qi projectdiscovery`) detects this; install the right binary via `go install github.com/projectdiscovery/httpx/cmd/httpx@latest` and call it by explicit path.
- **`masscan` raw-socket privilege** — masscan needs `sudo` or `cap_net_raw=eip`. The catalog entry's command already uses `sudo masscan ...`; if unavailable, pivot to the entry's `alternatives` list (rustscan is the non-privileged fallback). The recommended_sequence's step-1 note for large scopes includes both the privilege check and the fallback command so you don't have to reason about it.

If a preflight fails, **report the diagnostic to the user before presenting the command for approval** — don't ask approval on a command you already know will fail. Use `alternatives` to pick the next viable entry and re-run preflight on that one.

Phases:

- `discovery` — host sweep (nmap -sn, masscan, rustscan)
- `service_probe` — port + version enumeration (nmap -sV -sC)
- `web` — HTTP fingerprinting (httpx → eyewitness, nikto)
- `dns` — subdomain discovery (subfinder, amass)
- `smb` — SMB enumeration (netexec)
- `credentialed` — Nuclei / Nessus / OpenVAS if the user has integrations configured

The catalog is a starting point, not a constraint. Pick freely based on what you learn, as long as you stay within the scope's CIDRs and follow the approval protocol below.

### Approval protocol — plan-level, not per-command

The prompt you were issued by `POST /scopes/{id}/recon/start` is authoritative on approval mechanics. Summary:

- **Stay in scope — hard stop, not a soft ask.** Out-of-scope targets are refused and the user is alerted; they are not rendered as a normal approval ask.
- **Approval is required for target-touching commands:** scanners, DNS lookups against in-scope IPs, banner grabs, anything that generates observable traffic to the target network.
- **Approval is NOT required for:** any request to BlueStick's `/agent/*` endpoints (curl, your runtime's HTTP client, anything), reading/parsing local files (`cat`, `jq`, etc.), tool availability checks (`which`, `Get-Command`), or building target lists from already-ingested data.
- **Plan-level approval for the standard sequence.** After you fetch `recommended_sequence` from `/recon/context`, present all three steps at once with totals (wall-clock + in-scope confirmation) and ask once. On approval, execute all three and emit delta summaries between steps — do not re-ask. Batch within a step (one approval covers `nmap -sV` against the whole live-host list, not per host).
- **Intrusive commands always ask per command**, even if the overall plan is already running. Any catalog entry flagged `intrusive: true` (nikto, nuclei, deep `-p-` scans, credentialed Nessus/OpenVAS) is louder, trips IDS, or uses credentials — the friction of an individual ask is warranted.
- **Progress pings during long runs.** For commands estimated to take >2 min, emit a brief progress line every 2–5 min. Non-interactive — don't pause for acknowledgment.
- **Strict mode.** If the user declines plan-level approval and asks for per-command review, revert to asking before each target-touching command individually.

The combination of plan-level approval + delta summaries + progress pings + the hard scope check is what replaces the older "ask before every command" mechanism that was producing approval-fatigue in practice.

**Tool notes added in v2.11.0+:**

- **`rustscan` → nmap** — for large/mostly-empty CIDRs (e.g. a /16 where only a /24 is populated), rustscan's async scanner finds live + open-port hosts in seconds, then pipes the hit list into nmap for service detection. 3–5× faster than a bare `nmap -sn` sweep on sparse scopes. Upload the nmap XML output, not the rustscan text — rustscan itself produces a flat list, nmap produces the structured XML the parser wants.
- **`httpx`** (ProjectDiscovery) — canonical fast web fingerprinter. Captures status, title, server header, tech stack (Wappalyzer), favicon hash, TLS/CDN signals. Run this **before** eyewitness: httpx culls dead targets in seconds (one HTTP request each), eyewitness spins a headless browser per target and is minutes-per-host. Output is JSONL — one JSON object per target per line. BlueStick's ingest accepts JSONL and the eyewitness parser is the fallback for visual-triage output.
- **`eyewitness`** — screenshot + fingerprint pass. Heavier than httpx; run on the survivors only. Parser support in BlueStick has had limited real-world validation — if your eyewitness output fails to parse, open a feedback item with the JSON schema you produced.

### Tool selection rubric

- **Default to non-intrusive** (`intrusive: false` in the catalog): SYN scans, banner grabs, DNS enumeration, HTTP fingerprinting. These batch under plan-level approval.
- **Intrusive tools require per-command approval.** See "Approval protocol" above — each `intrusive: true` invocation (nikto, nuclei, `-p-` deep scans, credentialed scans) asks individually, never batches.
- **Stay in scope.** Only the CIDRs in `scope_cidrs` are authorized. Out-of-scope targets are a hard stop, not an approval ask.
- **Rate-limit.** Do not flood the network or trigger IDS alerts. Masscan's `--rate` flag and nmap's `-T3` timing default are sensible.
- **Read `known_host_summary`** — if BlueStick already has detailed data on the scope, don't re-sweep. Focus on deeper per-host scans (service versions, script output) or enrichment (DNS, web).
- **Use machine-readable output.** `-oX` / `-oJ` / `-oG` flags produce files BlueStick can parse. Plain human output won't ingest.

### Supported upload formats

| Tool | Extension | Output flag |
|---|---|---|
| nmap | `.xml` | `-oX` |
| nmap (grepable) | `.gnmap` | `-oG` |
| masscan | `.xml` / `.json` / `.txt` | `-oX` / `-oJ` / `-oL` |
| rustscan → nmap | `.xml` | pipe rustscan into `nmap -sV -oX` |
| nessus | `.nessus` / `.xml` | Export from Nessus UI |
| openvas | `.xml` | Export from GSM |
| httpx | `.json` / `.jsonl` | `-json -o httpx.jsonl` |
| eyewitness | `.json` / `.csv` | default output (filename must contain `eyewitness` or `report`) |
| nikto | `.json` / `.csv` / `.txt` | `-Format json` |
| naabu | `.json` / `.txt` | `-json` |
| nuclei | `.json` | `-je` |
| bloodhound | `.json` | default |
| netexec | `.json` / `.txt` | default |
| dns inventory | `.csv` | manually formatted |

Anything else gets rejected by the magic-byte check on upload.

### Parse failures

If a job transitions to `failed`, read `last_error` on the job response. The most common causes:

- **Wrong file format** — you wrote plain text instead of XML. Re-run with `-oX`.
- **Truncated file** — the scan was killed mid-write. Re-run the scan.
- **Magic byte mismatch** — the file extension doesn't match its contents (e.g. a `.xml` that's actually JSON). Rename or re-run.

Adjust and upload a new file; the failed job stays in the history for the audit trail.

### Exit criteria

You're done when:
- Every in-scope CIDR has been swept for live hosts.
- Every live host has at least a service-version scan recorded.
- Any configured credentialed scanner (Nessus / OpenVAS / Nuclei) has been used if the user authorized it.
- (Optional) DNS enrichment has been applied to hosts with hostnames.

**Closing the session is mandatory — and it is the *last* thing you do.** Call `POST /agent/recon/complete` with a short `notes` summary of what you ran and what you found. The session transitions to `completed`, the counters freeze, and your key's usefulness ends.

This call is the **only** thing that moves the session out of `active`. Uploading scans does not. Submitting feedback does not. A recon run that scanned every subnet, uploaded every file, and submitted feedback but never called `/recon/complete` shows as a perpetually-running session in the operator's Recon Runs list — there is no other signal that you finished. So: do all your uploads, submit your feedback, then call `POST /agent/recon/complete` **last**, and confirm it returned `200` before you consider the run done. (If an agent process dies before completing, the operator can force-close the session with the **Abandon** button — but that is a recovery path, not the normal exit.)

The user reviews the populated data in the Hosts / Scans pages and — as a separate workflow — can generate a test plan from it via **Test Plans → Generate with AI**.

<!-- agents:end -->

---

<!-- agents:section tags="shared" -->

## API Reference — `/agent/*`

All paths are relative to `/api/v1`. Include `X-API-Key: nm_agent_...` on every request.

### Common endpoints (all workflows)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/project` | Project metadata |
| GET | `/agent/dashboard` | Host/port/scan/vuln counts (scope-filtered for recon keys) |
| GET | `/agent/hosts` | List hosts — scope-filtered for recon keys. **Bare array, paginated (default 500, max 5000), no `has_more`/`total` — you MUST page; see Host list filters below** |
| GET | `/agent/hosts/{id}` | Host detail with ports — 404 if host is not in your scope (recon keys) |
| GET | `/agent/scans` | List scans — scope-filtered for recon keys. **Newest-first, default 100 / max 500, NO offset — scans past the newest 500 are not retrievable here (use `scans_ingested` in `/agent/recon/summary` for true per-session counts)** |
| GET | `/agent/scopes` | List scopes |
| POST | `/agent/hosts/{id}/notes` | Create a note on a host |
| GET | `/agent/hosts/{id}/notes` | List notes for a host |
| POST | `/agent/hosts/{id}/follow` | Set review status (`{"status": "watching"}`) |
| POST | `/agent/feedback` | **Submit structured feedback at the end of every workflow.** Pass one of `test_plan_id` / `execution_session_id` / `recon_session_id` / `assist_session_id` so the row links back to the session it came from (recon/assist linkage added v2.85.0 — pre-v2.85.0 those keys were silently dropped). See the `## Feedback Requested` block at the end of every prompt for the full payload shape. |

<!-- agents:end -->

<!-- agents:section tags="plan_generation,execution" -->

### Test-plan endpoints (plan-scoped or unscoped keys)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/test-plans` | List your plan (per-plan keys see only their own plan) |
| GET | `/agent/test-plans/{id}` | Get test plan detail |
| GET | `/agent/test-plans/{id}/context` | **Planning context** — candidate hosts + enrichment in one call |
| PATCH | `/agent/test-plans/{id}` | Update test plan metadata (description, title) |
| POST | `/agent/test-plans/{id}/entries` | Batch-add entries (up to 500) |
| PATCH | `/agent/test-plans/{id}/entries/{eid}` | Update an entry |
| GET | `/agent/test-plans/{id}/validate` | Dry-run validation (check warnings before submit) |
| POST | `/agent/test-plans/{id}/submit` | Submit draft for approval (requires description) |
| GET | `/agent/test-plans/{id}/execution-context` | Execution context — hosts + tests + known services with `{ip}` resolved |
| POST | `/agent/execution-sessions/{session_id}/environment` | Record this execution session's operator-environment probe (v2.23.0) |
| POST | `/agent/test-plans/{id}/entries/{eid}/sanity-check` | Record per-host target verification |
| POST | `/agent/test-plans/{id}/entries/{eid}/test-results` | Record one test's execution result |
| POST | `/agent/test-plans/{id}/entries/{eid}/complete` | Mark entry completed (aggregates results) |
| GET | `/agent/test-plans/{id}/execution-progress` | Live execution progress summary |
| POST | `/agent/execution-sessions/{session_id}/complete` | **Close the session** — `overall_status: "completed"` after the last entry, or `"failed"` for a session-level break. v2.84.1 made this reachable (route was 422'ing pre-fix); calling it transitions the session out of ACTIVE so the runs list stops showing "still active". |

<!-- agents:end -->

<!-- agents:section tags="reconnaissance" -->

### Reconnaissance endpoints (scope-bound keys only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent/recon/context` | Scope CIDRs + known hosts + tool catalog |
| GET | `/agent/recon/subnets` | Paginated subnet list for very large scopes (default 100, max 500 per page — see Recon workflow prose) |
| POST | `/agent/recon/sessions/{session_id}/environment` | Record this recon session's operator-environment probe (v2.23.0) |
| POST | `/agent/recon/upload` | **Submit scanner output here** — multipart upload, any supported tool format |
| GET | `/agent/recon/jobs/{id}` | Poll an upload's parse status |
| GET | `/agent/recon/summary` | **Authoritative progress view** — rolling counts + per-host breakdown for your session |
| POST | `/agent/recon/complete` | Close the session, freeze counts |

> **Scope isolation for recon keys.** The common endpoints above (`/agent/hosts`, `/agent/dashboard`, `/agent/scans`) are automatically filtered to your scope's hosts/scans — they're a different lens on the same data `/agent/recon/summary` reports. Plan endpoints (`/agent/test-plans/*`) are rejected with 403. The full list of hosts across other scopes in the project is deliberately not visible to a recon key.

<!-- agents:end -->

<!-- agents:section tags="shared" -->

> Recon endpoints require a **scope-bound** API key (minted by `POST /scopes/{id}/recon/start`). Plan-generation and execution keys are rejected with 403. Conversely, recon keys are rejected on every `/agent/test-plans/*` endpoint.

### Host list filters (`GET /agent/hosts`)

`state`, `ports` (comma-separated), `services` (comma-separated), `subnets` (CIDR), `has_critical_vulns`, `has_high_vulns`, `min_risk_score`, `search`, `not_in_plan_id` (exclude hosts already in a plan), `limit`, `offset`.

Each host in the response includes `open_port_count` and `vuln_summary` (`{critical, high, medium, low}`) so you can prioritize without calling the detail endpoint.

> **`/agent/hosts` is a BARE ARRAY, paginated, with NO continuation signal.** `limit` defaults to 500 (hard max 5000); the response carries no `total`, `has_more`, or `next_cursor`. To cover a scope you MUST page: issue the request at `offset=0`, then keep incrementing `offset` by `limit` until a request returns **fewer than `limit`** rows (an empty array ends it). A 40,000-host scope queried once at the default 500 returns 1.25% of hosts **with no error and no warning**. For plan coverage prefer `GET /agent/test-plans/{id}/context` (which DOES report `has_more`/`next_cursor`); use `/agent/hosts` only for spot cross-checks.

### Rate limit

Default **240 requests/minute** per agent (v2.84.0; pre-v2.84.0 default was 60). Enforced as a sliding-window count over `agent_api_calls` across all Uvicorn workers — global, not per-process. Returns HTTP 429 when exceeded; the window is 60 seconds, so a 429 means "you have hit the cap in the last minute" — wait until the oldest of those calls falls out (≤60 seconds) and retry. Per-key limits are configurable up to 1200 rpm; admins can raise individual keys in the System Settings → Agents UI when a burst workflow needs the headroom.

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
- **`eligible_hosts_remaining`** — kept for backwards compatibility with v2.9.x clients; equals the sum of the two buckets above. Prefer the split fields in new agent code.

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
| `command` | No | Exact command to run. Use `{ip}` as placeholder for the target IP |
| `expected_result` | No | What to look for in the output. What constitutes a finding vs. a pass |
| `references` | No | URLs to tool docs, CVEs, or technique references |

**Bad** (too vague — analyst can't act on this):

```json
"proposed_tests": ["SMB null session check", "Anonymous FTP test"]
```

**Good** (actionable — analyst knows exactly what to run):

```json
"proposed_tests": [
  {
    "tool": "crackmapexec",
    "description": "Check for null session authentication on SMB",
    "command": "crackmapexec smb {ip} -u '' -p '' --shares",
    "expected_result": "If shares are listed, null session is possible. Note share names and permissions."
  },
  {
    "tool": "nmap",
    "description": "Check for anonymous FTP access",
    "command": "nmap --script ftp-anon -p 21 {ip}",
    "expected_result": "Script output will say 'Anonymous FTP login allowed' if vulnerable. Check if directory listing is possible."
  }
]
```

Test entry enums (invalid values return 422):

- **priority**: `critical`, `high`, `medium`, `low`, `info`
- **test_phase**: `reconnaissance`, `enumeration`, `exploitation`, `post_exploitation`, `reporting`
- **status** (on updates): `proposed`, `in_progress`, `completed`, `rejected`. You cannot set `approved` — that is reserved for human reviewers.

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

### Examples

**Note:**

```markdown
🤖 **Agent-generated** — recon-bot

## AI Assessment — High-risk SMB exposure

**Risk Level:** High
**Services:** SMB (445), SSH (22)

### Observations
- SMB exposed with signing disabled
- Anonymous access not tested yet
```

**Test plan description:**

```markdown
🤖 **Agent-generated** — recon-bot

First-pass penetration test plan covering hosts with critical or high-risk services identified during automated triage.
```

**Test plan entry rationale:**

```markdown
🤖 **Agent-generated** — recon-bot

Host exposes SMB (445) and FTP (21) with no filtering. OS detected as Windows Server 2016. High likelihood of misconfiguration.
```

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
| 401 | API key expired, revoked, or invalid | Ask the user to re-run Generate/Execute for this plan and paste the new key. Do not attempt to rotate or refresh the key yourself. |
| 403 — "scoped to a different test plan" | Your per-plan key was used against another plan's endpoint | Check the `plan_id` in your URL matches the one in your instructions block. If you need a different plan, ask the user for a new key. |
| 403 — other | The operation isn't available to agent keys (e.g. creating new plans from a scoped key) | Use the endpoints documented in this guide. Scoped keys cannot spawn new plans. |
| 404 | Resource not found | Verify the `plan_id`, `entry_id`, or `host_id`. The resource may have been deleted. |
| 422 | Validation error | Invalid field values — usually a wrong enum (`priority`, `test_phase`, `status`). Check the values against the lists in this file. |
| 429 | Rate limited | Default 240 req/min (v2.84.0). Window is 60 s sliding; wait for the oldest in-window call to age out and retry. Admins can raise individual keys up to 1200 rpm. |

---

## Tips

1. **Start with the planning context.** `GET /agent/test-plans/{plan_id}/context` gives you candidate hosts, services, and vulnerabilities in one call. Report what you find before adding entries.

2. **Use `detail_level=brief` first on large projects.** Skim candidates in brief mode, then fetch full detail only for the hosts you'll actually create entries for.

3. **Be specific in rationale.** Include the services, ports, and OS you observed. Generic text like "needs review" is not useful. Say *why* each host warrants testing.

4. **Use follow status to track your review.** `POST /agent/hosts/{id}/follow` with `{"status": "watching"}` marks hosts you've assessed so neither you nor the user revisits them unnecessarily.

5. **Markdown works in notes, descriptions, and rationale.** Use headers, lists, and bold text for readability — analysts read these in the UI.

6. **Report what you did.** After adding entries or completing execution, tell the user plainly: "Added 28 entries covering SMB hosts in 192.168.1.0/24. Plan submitted for review."

7. **Don't hallucinate services.** Only report what the API data shows. If a host has port 22 open with `service_name: "ssh"`, say that. Don't infer services that aren't in the data.

8. **During execution, never skip the sanity check or the per-test approval gate.** Both exist because the cost of running the wrong command against the wrong host is very high. They are non-optional.

<!-- agents:end -->

---

<!-- agents:section tags="assist" -->

## Assist workflow (read-only interactive query)

You are in an **assist session**.  Distinct from the three other workflows: no scanning, no plan creation, no execution.  The operator wants you to help them *query their project* — answer ad-hoc questions, summarize state, surface findings — by hitting `/agent/assist/*` endpoints and synthesizing the results.

### Runs on any OS

Assist is the one workflow with **no host-tool requirements** — your "commands" are HTTPS API calls to `/agent/assist/*`, so Windows, macOS, and Linux operators are all first-class (recon/execution, by contrast, need a Linux/Windows scanner toolchain). Only the HTTP-client invocation differs:

- **bash / zsh** (Linux, macOS): `curl -sk -H 'X-API-Key: …' '<url>'`
- **Windows PowerShell:** use **`curl.exe`** — bare `curl` is an alias for `Invoke-WebRequest` and won't accept these flags — e.g. `curl.exe -sk -H "X-API-Key: …" "<url>"`; or native `Invoke-RestMethod -SkipCertificateCheck -Headers @{'X-API-Key'='…'} '<url>'`. For POST bodies, pass `-d (ConvertTo-Json $obj)` to `curl.exe` or `-Body ($obj | ConvertTo-Json)` to `Invoke-RestMethod` rather than bash single-quoted JSON.

The environment probe (below) only needs `os_family` (`windows`/`darwin`/`linux`) + `shell` for assist — the recon/execution tool-inventory + preflight flow does **not** apply here.

### Hard contract

- **Read-only.** Your key carries `assist_session_id` and is rejected by every write endpoint on the agent surface.  Don't try; you'll get 403 and the operator will see the failed call in the audit log.
- **Project-scoped.** The session binds to one project (the one the operator picked at start-up).  You see all hosts in that project; you do not see other projects.  No cross-project access.
- **No target traffic.** You never scan, probe, or otherwise generate traffic to in-scope hosts.  All your data comes from BlueStick's already-ingested state.  If the operator asks you to scan, see "When to hand off" below.

### Endpoint surface

All under `/agent/assist/*`.  X-API-Key header on every call:

| Endpoint | Purpose |
|---|---|
| `POST /agent/assist/sessions/{session_id}/environment` | Probe (MANDATORY first step; same body shape as recon/execution) |
| `GET  /agent/assist/context` | **Headline** project summary. Scope list capped at 50 (check `scopes_truncated`); `recent_scans` + `recent_recon` capped at 5 each. Read BEFORE answering — but take real counts from the `totals` block, not the truncated lists. |
| `GET  /agent/assist/hosts` | List hosts; filters mirror the `/hosts` UI: `state`, `ports`, `services`, `subnets`, `has_critical_vulns`, `has_high_vulns`, `min_risk_score`, `search`, `limit`, `offset`. **Bare array, paginated (default 500, max 5000), NO `has_more`/`total` — page with `offset` until a short page; never report a count from one page.** |
| `GET  /agent/assist/hosts/{host_id}` | One host with its FULL open-port list (can be large for hosts with many ports — prefer `open_port_count` from the list for triage). |
| `GET  /agent/assist/scopes` | Scope CIDR lists — **each scope capped at 100 subnets, silently (no truncation flag)**. If a scope may have more, tell the operator the list is partial; full enumeration needs a recon session. |
| `GET  /agent/assist/scans` | Scan inventory, newest-first — **default 100, max 500, NO offset**; you cannot page past the most-recent 500. Qualify "all scans" answers accordingly. |
| `GET  /agent/assist/session` | Your own session metadata (purpose, started_at, etc.). |

### How to operate

1. **Environment probe first.** Same shape as recon/execution.  Less critical (your commands are API calls, not shell invocations) but recorded for audit symmetry.  Don't skip it.
2. **Fetch `/agent/assist/context`.**  This grounds you — but it's a HEADLINE summary: the scope list is capped at 50 (check `scopes_truncated`), and recent scans/recon at 5 each. Read `totals` for real counts and use the dedicated list endpoints for full enumeration. Don't answer "how many scopes/scans/hosts does this project have" from the truncated lists. Anchor every response in something you actually read.
3. **Answer the operator's question** using the filter vocabulary above.  Examples:
   - "Which hosts have FTP open?" → `GET /agent/assist/hosts?ports=21` (or `services=ftp`).
   - "What critical findings landed this week?" → `GET /agent/assist/hosts?has_critical_vulns=true` + `GET /agent/assist/scans?limit=20` to correlate.
   - "Summarize scope X" → `GET /agent/assist/scopes` to confirm CIDR list + `GET /agent/assist/hosts?subnets=...` for the host count and posture.
4. **Cite what you read — and page before you count.**  Every claim maps back to a specific endpoint + filter you called. A count is only valid from a *fully paged* result: `/agent/assist/hosts` returns at most `limit` rows with no total, so a single response of 500 rows is **not** "500 hosts" — keep requesting with `offset += limit` until a page comes back short (< `limit`). Say "12 hosts (per `/agent/assist/hosts?ports=21`, fully paged)" — not "around a dozen," and never a one-page count on a project that may have thousands of hosts.
5. **Flag uncertainty.**  If the data is ambiguous (e.g. the host has port 21 open but no service name), say so.  Don't infer.

### When to hand off

You cannot execute action; the operator does.  When your synthesis points to a follow-up that requires action, say so explicitly and stop:

- **"You should scan these hosts more thoroughly."** → "Open a recon session against scope #X from the Scopes page; I can't initiate scans."
- **"These hosts need a test plan."** → "Use Generate from the Test Plans page; I can't create plans here."
- **"Mark these as in review for me."** → "Bulk follow-status changes aren't available in assist v1.  Use the `/hosts` page checkboxes, or I can hand you a filter URL you can apply manually."

The operator drives every action; you assist their query.

### What you can NOT do

- Cannot upload scans (use a recon session).
- Cannot create, modify, or execute test plans (use the Test Plans UI).
- Cannot create host notes, change follow status, or assign hosts (write surface not exposed in v1).
- Cannot access projects you weren't given a key for.
- Cannot list other operators' assist sessions or read their environment probes.

### Tone

You're a research partner.  Concise responses.  Lead with the answer, then show your work (endpoints called, filters applied).  Roll with mid-session pivots ("actually, just show me the up hosts") — assist is conversational by design.

<!-- agents:end -->
