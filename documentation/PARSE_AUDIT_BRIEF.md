# Parse audit brief — for an agent on the assessed network

Hand this file to an agent (Claude Code, Codex…) running **on the network where
BlueStick and the tool output live**. It checks whether BlueStick read an
import correctly and returns a report that contains **no client data**, so the
report can leave the network and become parser fixes and test fixtures.

Nothing in this audit sends data anywhere. The agent reads files and
BlueStick; the operator carries the finished report out.

## What the agent needs

1. **The original tool output** that was imported: the operator's copy, or the
   file BlueStick retained (kept for `INGESTION_RETAIN_FILES_DAYS`, 7 by
   default, after the import — Ingestion Results says until when).
2. **A BlueStick agent session on the same project** (Operations → start an
   agent session; MCP server `bluestick`, or `X-API-Key` against
   `/api/v1/agent/*`). Read-only use is enough; the audit writes nothing.
3. **What BlueStick is meant to read** from the format: `GET
   /api/v1/references/parser-coverage` (the "What BlueStick reads" page) —
   each signal with its level: `observation`, `field`, `text` (kept as the
   tool's text, not interpreted), `stored`, `discarded`, and the known gaps.
   A known gap is not a finding; report only what differs from this page.

## Steps

1. **Start from what BlueStick already knows it missed.**
   `assist_list_uninterpreted_lines` (or `GET /agent/assist/uninterpreted-lines`)
   lists each import's lines that no pattern read (`dropped`), that were kept
   only as text (`text_only`), or that came from an nxc module (`module_as_login`
   — probably misread — and `module_as_text`), as redacted shapes with counts.
2. **Check what WAS read.** For a sample of hosts in the file (every host if
   there are fewer than 30; otherwise 30, choosing hosts whose lines differ in
   shape), compare each tool line with what BlueStick stored:
   - NetExec / SMBMap: `assist_list_host_access` — `auth_success`, `username`,
     `local_admin`, `smbv1`, `writable_share`, `shares` beside `raw_output`.
   - Ports and services: `assist_get_host` → `ports`.
   - Weaknesses: `assist_get_host_vulnerabilities` (scanner observations, with
     `check_id` for catalog checks such as `vnc_no_auth`, `smb_signing_not_required`).
   - Web tools: `assist_list_host_web_interfaces`.
3. **Classify every line you examined** as one of:
   - `correct` — read as the tool meant it;
   - `misread` — read, but wrongly (a module result stored as a login, a flag
     stored with the opposite meaning, the wrong port or account);
   - `text_only` — kept as the tool's line, nothing interpreted, although it
     states something an analyst would filter on (a signing / NLA / channel
     binding flag, a module's VULNERABLE);
   - `dropped` — not in BlueStick at all.
4. **Check your own reading before calling something `misread`.** The tool's
   source is the authority, not your expectation: for NetExec, the protocol or
   module file in `github.com/Pennyw0rth/NetExec` (`nxc/protocols/*.py`,
   `nxc/modules/*.py`) — the exact string it prints and what it means. Cite
   the file and the line. If you cannot confirm it, report the line as
   `unconfirmed`, not `misread`.

## What to return

One Markdown report, and **only** this — no raw lines, no host names, no
addresses, no accounts:

```
# Parse audit — <tool> <version if known>, <date>
Imports checked: <n> files, <n> lines, <n> hosts sampled.
Summary: correct <n> · misread <n> · text_only <n> · dropped <n> · unconfirmed <n>

## <one section per distinct problem>
Class: misread | text_only | dropped | unconfirmed
Lines affected: <count> (in <n> of <n> imports)
Shape:     <the line, redacted — see rules below>
Stored as: <what BlueStick recorded, e.g. "auth_success=true username=<USER>" or "nothing">
Should be: <what the line means, e.g. "LDAP signing not enforced">
Source:    <nxc file:line that prints it, or "not confirmed">
```

### Redaction rules (apply to every line you quote)

The shapes BlueStick returns in step 1 are already redacted; redact your own
the same way, and when unsure, redact.

| Replace | With |
| --- | --- |
| IPv4 / IPv6 addresses, CIDRs | `<IP>` |
| The host column and any host or computer name | `<HOST>` |
| Domain names, FQDNs, URLs' hosts | `<NAME>` |
| `DOMAIN\user:password`, `user:password`, a password alone, hashes | `<CREDENTIAL>`, `<HASH>` |
| Account, group and share names; file paths | `<USER>`, `<GROUP>`, `<SHARE>`, `<PATH>` |
| The value of a `(key:value)` flag, unless it is True / False / None / Never / Required or a number | `(key:<VALUE>)` |
| Free text a person wrote (descriptions, comments, banners naming the organisation) | `<TEXT>` |

**Keep** the tool's own words and structure: the protocol or module column, the
port, the status marker (`[*]`, `[+]`, `[-]`, `[!]`), keywords (`VULNERABLE`,
`Pwn3d!`, `STATUS_LOGON_FAILURE`), flag names, column headers, and the number
and order of columns. A shape is useful only if a parser could be written
against it:

```
LDAP <IP> 389 <HOST> [*] <TEXT> (name:<VALUE>) (domain:<VALUE>) (signing:None) (channel binding:Never)
ZEROLOGON <IP> 445 <HOST> [+] VULNERABLE
SMB <IP> 445 <HOST> -Username- -Last PW Set- -BadPW- -Description-
```

Before handing the report over, read it once more for anything that
identifies the client, its people or its hosts, and replace it.
