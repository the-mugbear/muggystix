# Normalization: how a tool's output becomes BlueStick data

*For a developer who wants to ingest a new tool's output — or an agent's own
findings — and wants to know what the platform expects of it.*

BlueStick puts the output of many tools on one set of hosts and compares,
counts and filters it as one thing: "which hosts have a critical", "which
issues have a known exploit", "how many observations per source". That only
works if a value means the same thing whoever wrote it. This page is the
contract that makes it so. The per-tool field reference is
[PARSERS.md](PARSERS.md); this page is the *why* and the rules every parser
follows.

## The one rule

**A field on a scanner observation is the tool's statement. A statement the
tool did not make is never written.**

Every tool rates, scores and names things differently, and most tools say
nothing about some of them. The platform does not fill the silence with a
default that reads like a statement — a Nikto finding is not "low" because
Nikto rated nothing, an observation is not "no exploit" because Nikto never
says. The empty value is itself the information, and every reader knows what
it means.

| Field | A statement | No statement | Who reads it |
|---|---|---|---|
| `severity` | `critical` … `info` as the tool rated it (or as the weakness catalog rates a known check) | `unknown` — ranks below `info`, matches no `has:<severity>`, is not "informational" | severity filters, Posture, the queue tiers, the Scanner observations page |
| `cvss_score` + `cvss_vector` + `cvss_version` | the score on its scale; the version is derived from the vector by the writer (`cvss_service.cvss_version_of`), never read from the tool | `NULL` | the inspector, the Query page (`cvss`, `cvss_version`) |
| `cve_id` + `vulnerability_cves` | the first CVE (the issue's identity) and every CVE the tool named | none | `cve:` filter, the observations search, the Query page (`cve`, `cves`) |
| `exploitable` | `true` — exploit code reported (proof-of-concept or better); `false` — the tool can say and reported none | `NULL` — the tool never says | `has:exploit` / `exploitport:` (test `IS TRUE`), the Operations tiers, the Scanner observations page (`bool_or` over an issue's rows), the Query page (`isnull(exploitable)`) |

Identity is the same across tools: `vulnerabilities.issue_key` is the CVE
when there is one, else the normalised title, else the row itself
(`vuln_identity.py`); a check the weakness catalog knows is keyed by its
`check_id` whichever tool saw it (`misconfig_checks.py`). The Scanner
observations page groups by that key, which is why the Query page's examples
group `by issue` and count `dc(host)`.

## The declaration, and the guard that enforces it

`backend/app/services/source_statements.py` declares, per
`VulnerabilitySource`, which statements the tool's output can carry:
`severity`, `cvss`, `cve`, `exploitability`. It is read in three places:

- **The writers refuse an undeclared statement.** `parser_utils.upsert_vulnerability`
  and the Nessus path call `refuse_undeclared(...)` before every write; a
  parser that passes `exploitable=True` for a source declared unable to state
  it fails at the write with `UndeclaredStatement`. A catalog check's severity
  is the catalog's statement, not the tool's, and is not checked.
- **The Tool coverage page** (Reference → What BlueStick reads;
  `GET /api/v1/references/parser-coverage` → `statements`) shows the
  declaration beside each tool, so an analyst reading an empty column knows
  whether the tool never says or could have said and did not.
- **The tests** pin it: `tests/test_source_statements_and_normalization.py`
  (every source declared; no coverage claim a source cannot make; the guard;
  no parser writes a literal fallback severity; the CVSS version derivation;
  every CVE kept; the `cve:` / search / Query readers) and
  `tests/test_exploitable_tristate.py` (the three states end to end).

Readers follow the same rule from the other side: nothing tests `= false` to
mean "no exploit"; the inspector says "No exploit reported" only for `false`
and nothing for `NULL`; the list's kind calls an unrated issue a
vulnerability, not informational.

## Two ways in

### 1. No code: the BlueStick observation format

A tool BlueStick has no parser for, a script, or an agent working from its own
output writes one JSON object per observation (JSON lines; a JSON array works
too) and uploads it like any file — or, from an MCP client, sends the same
objects as JSON to `POST /agent/observations` (tool `record_observations`),
which hands them to the same parser through the upload path.

```json
{"schema": "bluestick-observation/1", "tool": "my-scanner",
 "host": "10.0.0.5", "hostname": "web01.example.com",
 "port": 443, "protocol": "tcp", "service": "https",
 "title": "Outdated TLS library", "severity": "high",
 "description": "…", "solution": "…", "references": ["https://…"],
 "cvss": {"score": 9.8, "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
 "cves": ["CVE-2024-0001", "CVE-2024-0002"], "exploitable": true,
 "plugin_id": "my-check-7", "evidence": "the output that showed it",
 "observed_at": "2026-11-06T10:00:00Z"}
```

The format is the rule made writable: every field is the uploader's
statement and an absent field is the explicit absence of one. `host` is an
address and is required (a name alone cannot be placed and is skipped);
`title` is required unless `check_id` names a weakness-catalog check. A line
that cannot be read — a severity word that is not one, a port out of range, a
non-boolean `exploitable`, an unknown `check_id` — is skipped and named in the
import's warning; the rest imports. Rows carry `source = external` and the
tool's name on the scan. The parser is
`backend/app/parsers/observations_parser.py`; the full field list is on the
Tool coverage page and in [UPLOAD_FORMATS.md](UPLOAD_FORMATS.md).

Reach for this first. A native parser is worth writing when the tool's own
format is what people have on disk and is widely used.

### 2. A native parser

The step-by-step is [PARSERS.md, Part 2](PARSERS.md#part-2--adding-a-new-parser)
(the parser contract, the shared toolkit, detection and registration, schema
discipline, testing). The normalization checklist that part assumes:

1. **Declare first.** Add the source to `VulnerabilitySource` (an
   `ALTER TYPE … ADD VALUE` revision) and its line to
   `source_statements.STATEMENTS`. Decide honestly: does the tool rate? score?
   name CVEs? say anything about exploits?
2. **Write only statements.** Severity the tool rated, else
   `VulnerabilitySeverity.UNKNOWN`; `exploitable` only when the tool says
   (never `False` for silence); CVSS score and vector as given, never a
   version (the writer derives it); every CVE through `cve_ids=`; a known
   misconfiguration through `record_misconfig` with its `check_id`, so it is
   the same issue as another tool's report of it.
3. **One writer.** `upsert_vulnerability` for an observation,
   `record_misconfig` for a catalog check, `persist_host_observation` for the
   host and its ports — never a `Vulnerability(...)` of your own.
4. **Register once.** A `FORMATS` row and its place in the detection order
   (`format_registry.py`), a structural sniffer in `content_detection.py`,
   an entry in `app/data/parser_coverage.json` (the Tool coverage page; its
   `stored_as` claims are checked against the declaration), rows in
   `UPLOAD_FORMATS.md` and `frontend/src/data/uploadFormats.ts`, a case in
   `tests/test_every_import_records_the_hosts_it_touches.py` (which puts the
   format under every per-format contract: hosts touched, a cut file, renamed
   hosts) and `tests/test_every_format_at_a_cut.py`.
5. **Let the guard tell you.** Run `tests/test_source_statements_and_normalization.py`
   and `tests/test_parser_coverage.py`; an undeclared statement or an
   over-claiming coverage entry fails there, not in production.

## Where the pieces are

| Piece | File |
|---|---|
| The declaration and the guard | `backend/app/services/source_statements.py` |
| The observation writer and re-observation rule | `backend/app/parsers/parser_utils.py` (`upsert_vulnerability`, `refresh_reobserved_vulnerability`, `record_cves`) |
| The Nessus path (its own service, same rule) | `backend/app/services/vulnerability_service.py` |
| CVSS version derivation | `backend/app/services/cvss_service.py` (`cvss_version_of`) |
| Issue identity | `backend/app/services/vuln_identity.py` |
| The weakness catalog | `backend/app/services/misconfig_checks.py` |
| The tool-agnostic format | `backend/app/parsers/observations_parser.py`; `POST /agent/observations` in `backend/app/api/v1/endpoints/agent_recon.py` |
| What each tool can state, served | `backend/app/services/parser_coverage.py`; the page `frontend/src/pages/ToolCoverage.tsx` |
| The readers | `host_query_predicates.py` (`has:exploit`, `cve:`), `scanner_observation_service.py` (the page), `query_datasets.py` (the Query page's `observations` columns) |
| The revisions that normalised stored rows | `a3f1c7e9d2b4` (exploit tri-state), `b7d2e4f8a1c3` (CVSS version), `c9e1a5b3d7f2` (every CVE), `d4f7b2c8e1a9` (the `external` source), `e5a8c3d1f7b4` (Nikto re-rated, empty ids cleared) |

Not decided yet: identity by *any* shared CVE (today the first CVE), which
would merge an issue two tools lead with different ids — a design change with
a recomputing migration, tracked as an owner question.
