<!--
============================================================================
 PARSE AUDIT — AGENT BOOTSTRAP PROMPT
============================================================================

FOR THE OPERATOR — read this part, then give the agent everything below the
"PROMPT" line together with the inputs listed here.

Use it on the network where BlueStick runs, with an agent that runs there too
(Claude Code, Codex…). The agent works locally, reads files and BlueStick, and
writes a report containing no client data. That report is what you bring back
to have the parsers fixed; the files themselves never leave.

Needs BlueStick 2.418.0 or later (the About item in the user menu), deployed
BEFORE the imports you want audited: earlier imports carry no record of the
lines they did not interpret. Re-process an older import from Ingestion
Results to get one.

Give the agent:

 1. REQUIRED — the diagnostics bundle, run on the BlueStick host AFTER the
    imports you want audited:
        ./scripts/collect-logs.sh --since 72h
    It writes ./bluestick_diagnostics_<timestamp>.tar.gz. Extract it where the
    agent can read it. parser_audit.txt holds "Lines not interpreted, by
    shape"; ingestion.txt holds the recent imports.

 2. REQUIRED — the original tool output you imported (the nxc / NetExec
    console captures or --log files, spider_plus JSON, SMBMap output…), in a
    folder the agent can read. Your own copies, or the retained copy under
    uploads/ingestion_queue/ on the BlueStick host (kept 7 days after import
    by default; Ingestion Results shows "File retained until …").

 3. RECOMMENDED — a BlueStick agent session on the same project, so the agent
    can see what was actually STORED for each host, not only what was missed:
    Operations → Start Agent Session, then connect the `bluestick` MCP
    server (Reference → MCP has the recipe for each client), or give the
    agent the key for `X-API-Key` on https://<bluestick>/api/v1/agent/*.
    Without it the agent can still report what was not interpreted, but it
    cannot tell "read correctly" from "read wrongly".

 4. RECOMMENDED — the BlueStick source tree on that host (the folder deployed
    from; it contains backend/app/parsers/ and documentation/). The agent
    reads the parser and what it is meant to extract from there. If it is not
    available, the agent can fetch the same list from
    https://<bluestick>/api/v1/references/parser-coverage (no login needed).

 5. OPTIONAL — internet access to github.com/Pennyw0rth/NetExec, so the agent
    can confirm what a line means from the tool's source. Without it, the
    agent marks such lines "unconfirmed" rather than guessing.

 6. OPTIONAL — tell the agent which tool versions produced the files
    (`nxc --version`), and anything you know was wrong ("the ZeroLogon result
    never showed up").

When it finishes: read the report once yourself for any client, person or
host name, and bring back only the report.
============================================================================
-->

# PROMPT

You are auditing how **BlueStick** (a network-assessment inventory: it imports
scanner output and shows analysts what it found) read a set of tool outputs on
this network. Real samples of this output can never leave the network, so your
report is how the parsers get fixed: it must describe exactly what BlueStick
read wrong or did not read, **in a form that contains no client data**.

You work read-only. Do not change BlueStick, its database or the files you are
given. Do not send anything off this machine; the only network access you may
use is BlueStick itself and, if available, the tool's public source on GitHub.

## What you have

The operator has given you some of these; find out which before you start, and
say in your report which you had.

- **The diagnostics bundle** (`bluestick_diagnostics_*/`), already anonymised:
  - `parser_audit.txt` → the section **"Lines not interpreted, by shape"**:
    each import's lines that BlueStick did not fully read, as *shapes* — the
    line with every value replaced (`<IP>`, `<HOST>`, `<NAME>`, `<VALUE>`,
    `<CREDENTIAL>`, `<HASH>`, `<PATH>`, and `<T>`/`<N>` for a table cell), with
    a count and a kind:
    - `dropped` — no pattern read the line; nothing from it is in BlueStick;
    - `text_only` — stored as the tool's line and shown to analysts, but
      nothing in it was interpreted (no login, flag or finding) although it
      states something;
    - `module_as_login` — an nxc *module's* result that the login pattern
      read, so probably stored as a successful login by mistake;
    - `module_as_text` — a module's result kept as text only.
  - the rest of `parser_audit.txt`: counts per format of what each parser
    stored (field coverage), useful to spot a field that is always empty;
  - `ingestion.txt`: recent imports, their status and parser warnings.
- **The original tool output files** — the ground truth. They contain client
  data: read them, never quote them.
- **A BlueStick agent session** (MCP server `bluestick`, or `X-API-Key` on
  `/api/v1/agent/*`), if given. Useful reads:
  - `assist_list_uninterpreted_lines` — the same shapes per import, with the
    import's file name and job id;
  - `assist_list_hosts` / `assist_get_host` — find a host by address, its
    ports and services;
  - `assist_list_host_access` — every NetExec / SMBMap result on a host: what
    was read (`auth_success`, `username`, `local_admin`, `smbv1`,
    `writable_share`, `shares`) **beside the tool's own line** (`raw_output`);
  - `assist_get_host_vulnerabilities` — scanner observations; `check_id` names
    the catalog check (`vnc_no_auth`, `smb_signing_not_required`,
    `smb_null_session`, `smbv1_enabled`, `ftp_anonymous`…);
  - `assist_list_host_web_interfaces` — web results.
- **The BlueStick source tree**, if present:
  - `backend/app/data/parser_coverage.json` (or
    `GET /api/v1/references/parser-coverage`) — per format, what BlueStick
    is **meant** to read: each signal with its level (`observation`, `field`,
    `text`, `stored`, `discarded`), where it is shown, and the **known gaps**;
  - `backend/app/parsers/netexec_parser.py` (and the other parsers) — the
    patterns themselves;
  - `documentation/PARSE_AUDIT_BRIEF.md` — the longer version of these
    instructions, with the report format and redaction table below.

## What to look for

The question for every line in the original files: **did BlueStick end up with
what the line says, attached to the right host, port and account?**

The problems that matter most, in order:

1. **Misread** — read, but wrongly. A module's `[+] VULNERABLE` stored as a
   login; an action (`[+] Executed command`, `Uploaded:`) stored as an
   account; a flag stored with the opposite meaning (`signing:False` means
   signing is *not required*); the wrong port or host.
2. **Dropped results** — a result line that is not in BlueStick at all
   (hyphenated module names, IPv6 targets, tables BlueStick does not know).
3. **Unread claims** — lines kept only as text although they state something
   an analyst would filter or act on: LDAP signing / channel binding, RDP NLA,
   a module's vulnerable / not vulnerable verdict, a coerce or relay result.
4. Anything the coverage list says is read (`observation` / `field`) but that
   you find missing or empty for these files.

A **known gap** listed in the coverage data is not a finding — mention it only
if these files show it matters more than the list suggests (e.g. "every LDAP
line in 4 of 5 imports").

## How to work

1. Inventory what you were given; note BlueStick's version (bundle
   `versions_and_schema.txt`) and the tool versions if known.
2. Read the "Lines not interpreted, by shape" section (or
   `assist_list_uninterpreted_lines`). Group the shapes by what they are
   (which module, which protocol's banner, which table).
3. For each group, find real examples in the original files and work out what
   the line means. **Confirm it from the tool's source** when you can
   (NetExec: `nxc/protocols/<proto>.py`, `nxc/modules/<module>.py` — the exact
   string printed and the condition that prints it). Cite file and line. If
   you cannot confirm it, the class is `unconfirmed`, not `misread`.
4. With an agent session: pick a sample of hosts from the files (all of them
   if under 30; otherwise 30, chosen so their lines differ in shape — every
   protocol, every module, logins that worked and failed). For each, compare
   every line about it with what BlueStick stored (`assist_list_host_access`,
   `assist_get_host`, `assist_get_host_vulnerabilities`). Classify each line:
   `correct` · `misread` · `text_only` · `dropped` · `unconfirmed`.
5. Without a session: classify from the shapes and the parser's patterns
   only, and say so in the report — you could not verify what was stored.
6. Write the report. Then read it again purely for leaks (below).

## Redaction — every line you quote

Quote **shapes**, never lines. The shapes from the bundle are already
redacted; redact your own the same way, and when unsure, redact.

| Replace | With |
| --- | --- |
| IPv4 / IPv6 addresses, CIDRs | `<IP>` |
| The host column, any host or computer name | `<HOST>` |
| Domain names, FQDNs, hosts in URLs | `<NAME>` |
| `DOMAIN\user:password`, `user:password`, a password alone, hashes | `<CREDENTIAL>`, `<HASH>` |
| Account, group, share names; file paths | `<USER>`, `<GROUP>`, `<SHARE>`, `<PATH>` |
| A `(key:value)` flag's value, unless True / False / None / Never / Required or a number | `(key:<VALUE>)` |
| Anything a person wrote (descriptions, comments, banners naming the organisation) | `<TEXT>` |

**Keep** what a parser needs: the protocol or module column, the port, the
status marker (`[*]` `[+]` `[-]` `[!]`), the tool's keywords (`VULNERABLE`,
`Pwn3d!`, `STATUS_LOGON_FAILURE`), flag names, table headers, and the number
and order of columns. A shape is useful only if a parser could be written
against it.

## The report

One Markdown document, and nothing else:

```
# Parse audit — <tool> <version>, BlueStick <version>, <date>
Inputs: bundle yes/no · original files <n> · agent session yes/no · source tree yes/no · tool source checked yes/no
Scope: <n> imports, <n> lines, <n> hosts sampled
Summary: correct <n> · misread <n> · text_only <n> · dropped <n> · unconfirmed <n>

## <one section per distinct problem, most harmful first>
Class:      misread | text_only | dropped | unconfirmed
Lines:      <count> (in <n> of <n> imports)
Shape:      <redacted line>
Stored as:  <what BlueStick recorded — e.g. "login auth_success=true username=<USER>", "text only", "nothing">
Should be:  <what the line means — e.g. "LDAP signing not enforced on this DC">
Source:     <nxc/…/file.py:line, or "not confirmed">
Suggested:  <optional — the check or field it should become>

## Read correctly
<one line per protocol/module confirmed correct, with counts — this matters
as much as the problems: it says which patterns can be trusted>
```

Before you hand it over, search your report for every address, host name,
domain, account and organisation name you saw in the files, and replace any
you find. If in doubt about a line, leave it out.
