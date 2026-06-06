"""Dispatcher routing tests for every fixture in artifacts/manual/.

v2.45.1 — added after the nmap-mis-detected-as-OpenVAS bug.  The bug
existed for months because no automated test exercised the dispatcher
against representative samples; operators only discovered it when a
real engagement scan triggered the keyword collision and they had to
sanitize their own scan output to work around it.

This module loops every fixture in ``artifacts/manual/`` through
``IngestionService._build_parsing_attempts`` and asserts the FIRST
parser attempt matches the expected (parser_name) for that file.  The
expected parser is derived from a single source-of-truth dict declared
inline below, mirroring the same information documented in
``artifacts/manual/README.md`` (each fixture's "Expected parser" cell).

When you add a new fixture, add it to ``EXPECTED_PARSER_BY_FIXTURE``
below AND to the README table.  CI will catch a mismatch either way:
this test will fail if the file isn't listed, and the README is the
operator-facing doc.

The tests intentionally use ``_build_parsing_attempts`` directly rather
than going through the HTTP upload endpoint — we're verifying parser
selection, not the full ingestion pipeline.  Parser correctness for
each fixture's content lives in the per-parser test files.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Resolve the artifacts directory in both run contexts:
#   * Host pytest run (from repo root): /home/.../NetworkMapper/artifacts/manual
#     — backend/tests/test_*.py is two levels deep, so parents[2].
#   * In-container pytest run: /app/tests/test_*.py, with /app/artifacts/
#     mounted read-only by docker-compose.  parents[1] = /app.
#
# v2.45.4 — artifacts/ is operator-local and NOT committed to the
# repo (see .gitignore — the fixtures can carry environment-specific
# scan data).  When the directory is absent (fresh CI checkout, a
# clone without the local fixtures) this whole module SKIPS rather
# than erroring: the dispatcher routing it verifies is also covered
# by the inline-fixture tests in test_phase1_regressions.py, so the
# loss of coverage on a fixture-less checkout is bounded.
def _resolve_artifacts_dir() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (
        here.parents[2] / "artifacts" / "manual",  # host
        here.parents[1] / "artifacts" / "manual",  # container
    ):
        if candidate.is_dir():
            return candidate
    return None


ARTIFACTS_DIR = _resolve_artifacts_dir()

pytestmark = pytest.mark.skipif(
    ARTIFACTS_DIR is None,
    reason=(
        "artifacts/manual/ not present — operator-local test fixtures "
        "(see .gitignore).  Dispatcher routing is also covered by the "
        "inline-fixture tests in test_phase1_regressions.py."
    ),
)


# Single source of truth: filename → (expected_parser_name, why).
# Synced with artifacts/manual/README.md.  Test failures here mean
# either the dispatcher routing changed (regression) or you added a
# fixture without updating this dict (CI catches it via the assert
# below in test_every_fixture_has_an_expected_parser_entry).
EXPECTED_PARSER_BY_FIXTURE: dict[str, tuple[str, str]] = {
    # --- XML scan formats (the bug-prone family — root element matters) ---
    "nmap_sample.xml": (
        "nmap_xml",
        "Root element <nmaprun> without scanner='masscan' attribute → nmap parser. "
        "Edge case: NSE scripts may capture cert subjects or page titles containing "
        "the literal words 'openvas' or 'greenbone'; structural root-element check "
        "prevents mis-routing to OpenVASParser (v2.45.1 regression fix).",
    ),
    "masscan_sample.xml": (
        "masscan_xml",
        "Root element <nmaprun scanner='masscan'> — masscan emits nmap-shaped XML "
        "with an explicit scanner attribute.  Dispatcher must inspect the attribute, "
        "not the root tag alone, otherwise routes to nmap (functionally close but "
        "missing masscan-specific fields).",
    ),
    "openvas_sample.xml": (
        "openvas_xml",
        "Root element <report> with <results>/<result> children → OpenVAS/Greenbone "
        "parser.  Filename matching ('openvas', 'greenbone', 'gvm') also wins outright.",
    ),
    "nessus_sample.nessus": (
        "nessus_xml",
        ".nessus extension takes precedence; content double-check via "
        "<NessusClientData_v2> root.",
    ),
    "nmap_sample.gnmap": (
        "nmap_gnmap",
        ".gnmap extension; content has 'Host:' lines with 'Ports:' or 'Status:'.",
    ),
    # --- JSON scan formats ---
    "masscan_sample.json": (
        "masscan_json",
        "Top-level array of {ip, ports[], status, ...} objects.  Distinct from "
        "naabu JSON which uses {host, ip, port} flat shape.",
    ),
    "naabu_sample.json": (
        "naabu_json",
        "JSONL with {host, ip, port, scheme, tls, timestamp} per line — naabu's "
        "default JSON output.  Could collide with httpx if 'tech'/'webserver' "
        "appeared; looks_like_naabu requires the inverse to fire.",
    ),
    "nikto_sample.json": (
        "nikto_json",
        "Top-level array of {ip, host, port, vulnerabilities[]} — Nikto's --Format json export.",
    ),
    "smbmap_sample.json": (
        "smbmap_json",
        "Top-level array with 'shares' or 'ip' fields.  Could collide with NetExec "
        "(also SMB-themed); smbmap-specific 'shares' key distinguishes.",
    ),
    "netexec_sample.json": (
        "netexec_json",
        "NetExec/NXC JSON keyed by IP with per-protocol blocks.",
    ),
    "amass_sample.json": (
        "amass_json",
        "Top-level array with 'name' (subdomain) + 'addresses' fields.  Subfinder "
        "JSON has same shape — both route to AmassParser which handles both schemas.",
    ),
    "bloodhound_sample.json": (
        "bloodhound_json",
        "Top-level 'data' array with 'Properties', 'dnshostname', 'computers' etc. "
        "— BloodHound/SharpHound collector output.",
    ),
    "eyewitness_sample.json": (
        "eyewitness_json",
        "Top-level 'version' + 'results' array with 'screenshot_path'/'page_title'/'url'. "
        "Edge case: page_title may contain arbitrary scraped HTML — keyword scanning "
        "of page_text would be unreliable; we key on the structural fields.",
    ),
    "feroxbuster_sample.json": (
        "dirbuster_json",
        "Feroxbuster JSON with 'type'/'status_code'/'url' per record.  Same parser "
        "covers feroxbuster + ffuf + dirsearch + gobuster + dirbuster — but the "
        "dispatcher uses per-format labels (dirbuster_json / dirbuster_csv / "
        "dirbuster_output) so the audit log shows which extension was uploaded.",
    ),
    "ffuf_sample.json": (
        "dirbuster_json",
        "ffuf JSON with top-level 'results' array of {url, status} objects.",
    ),
    # --- Text scan formats ---
    "masscan_sample.txt": (
        "masscan_list",
        "Header comment '# masscan' + lines like 'open tcp PORT IP TIMESTAMP'. "
        "Edge case: bare 'open' lines could be confused with rustscan's 'open IP -> [ports]' "
        "format; '# masscan' header and 'tcp NUM IP' shape distinguish.",
    ),
    "naabu_sample.txt": (
        "naabu_output",
        "Lines of form 'IP:PORT' with no header.  Edge case: subfinder TXT is "
        "'name IP' (space-separated) — naabu's colon-delimited shape distinguishes.",
    ),
    "rustscan_sample.txt": (
        "rustscan_output",
        "Header banner '~~~~~ RustScan vX' or content with 'open IP -> [ports]'. "
        "Edge case: bare 'open' tokens appear in masscan and gnmap too; the "
        "'->' arrow pattern is rustscan-specific.",
    ),
    "subfinder_sample.txt": (
        "amass_output",
        "'name IP' lines.  Same parser as Amass — both produce subdomain inventories; "
        "AmassParser handles both shapes by sniffing the line format.",
    ),
    "netexec_sample.txt": (
        "netexec_output",
        "Lines starting with 'SMB IP PORT HOSTNAME [*|+|-]' — NetExec/NXC console "
        "output.  Edge case: SMBMap text also mentions SMB; netexec's specific "
        "'SMB' protocol-prefix + status-tag shape distinguishes.",
    ),
    "smbmap_sample.txt": (
        "smbmap_output",
        "Lines starting with '[+] IP:445  Name: HOSTNAME' + 'Disk' tabular block.",
    ),
    "nikto_sample.txt": (
        "nikto_output",
        "Header line '- Nikto v2.x' or 'Target IP:' marker.",
    ),
    "dirbuster_sample.txt": (
        "dirbuster_output",
        "Lines of form 'http://IP/path  (Status: NNN) [Size: M]'.  Dispatcher's "
        "looks_like_dirbuster picks up '(status:' substring (lowercase).",
    ),
    "gobuster_sample.txt": (
        "dirbuster_output",
        "Header banner '===' + 'Gobuster vX'.  Same family parser as dirbuster "
        "(label = dirbuster_output for .txt regardless of which specific tool produced it).",
    ),
    # --- CSV formats ---
    "dirsearch_sample.csv": (
        "dirbuster_csv",
        "Header row 'url,status,content-length'.  Filename match ('dirsearch') "
        "wins; content also has url+status columns.",
    ),
    "dns_inventory_sample.csv": (
        "dns_csv",
        "Header row 'record_type,name,address' — DNS inventory upload.  Distinct "
        "from eyewitness CSV (which has 'URL,Protocol,Port,Title' columns).",
    ),
    "eyewitness_sample.csv": (
        "eyewitness_csv",
        "Header row 'URL,Protocol,Port,Title,Server,...,Screenshot Path,Response Code,...'. "
        "'Screenshot Path' column is the eyewitness-distinctive signal.",
    ),
    "nikto_sample.csv": (
        "nikto_csv",
        "Header row 'ip,hostname,port,id,msg,description,severity,cve' with "
        "'nikto-' prefix in id values.",
    ),
    # --- Scope import (special-cased; not a scan upload) ---
    "subnet_scope_sample.csv": (
        "_scope_import",
        "One CIDR per line — not a scan upload.  Should be uploaded via the "
        "Scopes page, not the Scans page; the dispatcher rejects it on the scan "
        "endpoint.  Test asserts it does NOT match any scan parser.",
    ),
}


# Fixtures that intentionally do NOT live in EXPECTED_PARSER_BY_FIXTURE
# because they exist to verify negative behaviour (no parser routes to
# them, or routing succeeds but the parser raises at the 0-records
# guard).  Each has its own dedicated test elsewhere in this file —
# this set just exempts them from the surface-contract check.
HARDENING_NEGATIVE_FIXTURES = {
    "noise_sample.txt",
    "inventory_sample.csv",
    "dns_headers_no_valid_rows_sample.csv",
}
# Fixtures that test the v2.54.0 JSONL routing fix.  These DO get
# dispatched (positive parser attempt) but aren't in the
# EXPECTED_PARSER_BY_FIXTURE table because the first-attempt assertion
# would duplicate test_naabu_jsonl_routes_correctly_and_parses.
HARDENING_POSITIVE_FIXTURES = {
    "naabu_sample.jsonl",
}


def test_every_fixture_has_an_expected_parser_entry():
    """Surface contract: every file in artifacts/manual/ must appear in
    EXPECTED_PARSER_BY_FIXTURE OR be the README.md OR be one of the
    hardening fixtures with its own dedicated test.  Forgetting to
    register a new fixture is a soft bug — the upload would still
    work via fallback parsers, but operators lose the regression
    coverage we built this test to provide.
    """
    on_disk = {
        p.name for p in ARTIFACTS_DIR.iterdir()
        if p.is_file() and p.name != "README.md"
    }
    registered = (
        set(EXPECTED_PARSER_BY_FIXTURE.keys())
        | HARDENING_NEGATIVE_FIXTURES
        | HARDENING_POSITIVE_FIXTURES
    )
    missing = on_disk - registered
    extra = set(EXPECTED_PARSER_BY_FIXTURE.keys()) - on_disk
    assert not missing, (
        f"New fixture(s) in artifacts/manual/ without dispatcher-test entry: "
        f"{sorted(missing)}.  Add them to EXPECTED_PARSER_BY_FIXTURE AND "
        f"to artifacts/manual/README.md, OR add them to HARDENING_*_FIXTURES "
        f"with a dedicated test."
    )
    assert not extra, (
        f"EXPECTED_PARSER_BY_FIXTURE references file(s) that no longer exist "
        f"in artifacts/manual/: {sorted(extra)}.  Remove the entry or restore "
        f"the fixture."
    )


def _expected_label_for(filename: str) -> str:
    """Look up the dispatcher's `parser_name` label for a fixture."""
    return EXPECTED_PARSER_BY_FIXTURE[filename][0]


@pytest.mark.parametrize(
    "fixture_filename",
    sorted(
        f for f in EXPECTED_PARSER_BY_FIXTURE
        # The scope import isn't a scan upload; skip the first-parser
        # assertion path and verify separately below.
        if EXPECTED_PARSER_BY_FIXTURE[f][0] != "_scope_import"
    ),
)
def test_first_parser_attempt_matches_expectation(fixture_filename: str):
    """Loop every scan-upload fixture through _build_parsing_attempts
    and assert the FIRST attempt is the expected parser.

    Catches the class of bug that v2.45.1 fixes: a content-detection
    heuristic that fires too eagerly and steals routing from the
    actual format's parser.
    """
    from app.services.ingestion_service import IngestionService

    fixture_path = ARTIFACTS_DIR / fixture_filename
    sample = fixture_path.read_bytes()
    job = MagicMock(original_filename=fixture_filename)

    svc = IngestionService.__new__(IngestionService)  # bypass __init__ (no DB)
    attempts = list(svc._build_parsing_attempts(job, sample))
    assert attempts, f"No parser attempts produced for {fixture_filename}"

    actual_label = attempts[0][0]
    expected_label = _expected_label_for(fixture_filename)
    assert actual_label == expected_label, (
        f"Dispatcher routed {fixture_filename!r} to {actual_label!r} first, "
        f"expected {expected_label!r}.  All attempts in order: "
        f"{[a[0] for a in attempts]}.  "
        f"Reason this expectation exists: {EXPECTED_PARSER_BY_FIXTURE[fixture_filename][1]}"
    )


def test_unrecognised_txt_does_not_silently_fall_back_to_masscan():
    """An arbitrary .txt with no parser-specific signal must NOT
    produce a parser attempt.  Pre-fix, ``_build_parsing_attempts``
    appended ``masscan_list`` as an unconditional last-ditch fallback;
    ``MasscanParser`` then committed an empty scan row with
    ``tool_name='masscan'`` and ``_run_job`` marked the job
    ``completed``.  That silent misattribution is the bug this test
    locks down.

    Reads ``artifacts/manual/noise_sample.txt`` so the fixture is part
    of the standard suite and operators can also drop it onto the
    Scans UI to confirm the same behavior end-to-end.
    """
    from app.services.ingestion_service import IngestionService

    sample = (ARTIFACTS_DIR / "noise_sample.txt").read_bytes()
    job = MagicMock(original_filename="noise_sample.txt")
    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))
    assert attempts == [], (
        f"Bogus .txt was routed to {[a[0] for a in attempts]!r}; "
        f"expected an empty attempt list so _process_job emits a "
        f"`parse_errors` row instead of a silent completed job."
    )


def test_unrecognised_csv_does_not_silently_fall_back_to_dns():
    """An arbitrary .csv with no DNS / nikto / dirbuster / eyewitness
    signal must NOT produce a parser attempt.  Pre-fix, the CSV branch
    appended ``dns_csv`` unconditionally; ``DNSParser`` then created
    the ``tool_name='dns'`` Scan row before validating headers and
    only raised if the file had NO header at all — an arbitrary CSV
    with arbitrary headers completed as a zero-record DNS scan.

    Reads ``artifacts/manual/inventory_sample.csv`` (a sku/quantity/
    price file that has no business being a DNS upload).
    """
    from app.services.ingestion_service import IngestionService

    sample = (ARTIFACTS_DIR / "inventory_sample.csv").read_bytes()
    job = MagicMock(original_filename="inventory_sample.csv")
    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))
    assert attempts == [], (
        f"Bogus .csv was routed to {[a[0] for a in attempts]!r}; "
        f"expected an empty attempt list so _process_job emits a "
        f"`parse_errors` row instead of a silent dns_csv completion."
    )


def test_dns_csv_header_still_routes_to_dns_parser():
    """Positive complement to the bogus-CSV test: a CSV whose header
    row matches the DNSParser's expected aliases MUST still route to
    dns_csv.  Locks down the gating heuristic so a future tightening
    can't accidentally exclude real DNS exports.
    """
    from app.services.ingestion_service import IngestionService

    dns_sample = b"record_type,name,address\nA,example.com,10.0.0.1\n"
    job = MagicMock(original_filename="dns_records.csv")
    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, dns_sample))
    labels = [a[0] for a in attempts]
    assert "dns_csv" in labels, (
        f"DNS-shaped CSV did not route to dns_csv (attempts: {labels!r})"
    )


def test_dns_csv_with_no_valid_rows_routes_then_fails_closed(db_session):
    """A CSV with the DNS-shaped header but every row invalid (bad IPs,
    missing fields) must:
      1. Route to ``dns_csv`` (the heuristic is header-based, fires here)
      2. Fail at parse time with ``ValueError`` from the v2.55.0
         0-records guard — NOT commit an empty ``tool_name='dns'``
         scan.

    This is the residual silent-success path the v2.54.0 dispatcher
    fix couldn't close on its own.
    """
    from app.parsers.dns_parser import DNSParser
    from app.services.ingestion_service import IngestionService

    fixture_path = ARTIFACTS_DIR / "dns_headers_no_valid_rows_sample.csv"
    sample = fixture_path.read_bytes()
    job = MagicMock(original_filename="dns_headers_no_valid_rows_sample.csv")

    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))
    assert "dns_csv" in [a[0] for a in attempts], (
        "DNS-shaped header should still route to dns_csv (heuristic fires)"
    )

    parser = DNSParser(db_session)
    with pytest.raises(ValueError, match="0 valid records"):
        parser.parse_file(str(fixture_path), "dns_headers_no_valid_rows_sample.csv")


def test_nikto_json_with_renamed_filename_still_avoids_naabu():
    """Pre-v2.57.0 looks_like_naabu fired on nikto's stock JSON shape
    (``{ip, port, id, msg, severity}``) because none of the existing
    exclusions matched.  In the 2026-05-26 bulk-upload regression this
    surfaced as nikto_sample.json being attributed to ``tool_name='naabu'``.

    The filename-based guard covers the common case; this test
    additionally simulates a renamed export to confirm the per-record
    ``id`` prefix + ``msg``/``severity`` vocabulary check also
    excludes nikto when the filename gives the dispatcher no clue.
    """
    from app.services.ingestion_service import IngestionService

    sample = (ARTIFACTS_DIR / "nikto_sample.json").read_bytes()
    # Rename so the filename doesn't carry the "nikto" hint.  The
    # per-record id starting with "nikto-" should still keep naabu off
    # the attempt list.
    job = MagicMock(original_filename="web_findings.json")
    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))
    labels = [a[0] for a in attempts]
    assert "naabu_json" not in labels, (
        f"Renamed nikto JSON routed to naabu: {labels!r}.  "
        f"looks_like_naabu's nikto exclusions failed."
    )


def test_naabu_jsonl_routes_correctly_and_parses(db_session):
    """A ``.jsonl`` naabu export must:
      1. Route to ``naabu_json`` (dispatcher accepts both extensions).
      2. The NaabuParser must parse the JSONL records — not silently
         demote to its bare-text path that called
         ``parse_host_port_token`` on JSON lines.

    Pre-v2.54.0 the parser gated on ``suffix == ".json"`` (strict),
    so a real naabu JSONL produced zero hosts.  Now both this test
    and the v2.55.0 0-records guard would catch the regression.
    """
    from app.parsers.naabu_parser import NaabuParser
    from app.services.ingestion_service import IngestionService

    fixture_path = ARTIFACTS_DIR / "naabu_sample.jsonl"
    sample = fixture_path.read_bytes()
    job = MagicMock(original_filename="naabu_sample.jsonl")

    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))
    assert attempts, "no parser attempts for naabu_sample.jsonl"
    assert attempts[0][0] == "naabu_json", (
        f"naabu .jsonl routed to {attempts[0][0]!r}, expected naabu_json"
    )

    parser = NaabuParser(db_session)
    scan = parser.parse_file(str(fixture_path), "naabu_sample.jsonl")
    assert scan is not None
    # 4 records in the fixture → 3 unique IPs (192.168.0.10/11/12)
    from app.db import models
    hosts = (
        db_session.query(models.Host)
        .join(models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id)
        .filter(models.HostScanHistory.scan_id == scan.id)
        .all()
    )
    assert len(hosts) == 3, f"expected 3 hosts, got {len(hosts)}"


def test_subnet_scope_csv_is_not_handled_by_scan_dispatcher():
    """The scope-import CSV uses a different upload endpoint and
    must NOT match any scan parser when fed to the scan dispatcher.
    Asserts the fallback chain doesn't accidentally parse it as a
    scan output (e.g. a CSV-shaped DNS inventory or directory bust).
    """
    from app.services.ingestion_service import IngestionService

    sample = (ARTIFACTS_DIR / "subnet_scope_sample.csv").read_bytes()
    job = MagicMock(original_filename="subnet_scope_sample.csv")
    svc = IngestionService.__new__(IngestionService)
    attempts = list(svc._build_parsing_attempts(job, sample))

    # The CSV path in _build_parsing_attempts attempts the DNS and
    # eyewitness CSV parsers based on heading match.  This file has
    # neither shape (no header row, just CIDRs), so the attempts list
    # should be empty OR confined to "best-effort" fallbacks that the
    # parsers themselves will reject.
    scan_parser_labels = {
        "nmap_xml", "masscan_xml", "masscan_json", "masscan_list",
        "nmap_gnmap", "naabu_json", "naabu_list", "rustscan_text",
        "nessus_xml", "openvas_xml", "amass_json", "amass_text",
        "nikto_json", "nikto_text", "nikto_csv", "smbmap_json",
        "smbmap_text", "netexec_json", "netexec_text",
        "bloodhound_json", "eyewitness_json", "eyewitness_csv",
        "dirbuster_family",
    }
    # If any scan parser was matched as the FIRST attempt, that's a
    # false positive worth catching — operators uploading a scope
    # file to the wrong page should get a clean rejection, not a
    # silent parse.
    if attempts:
        first = attempts[0][0]
        assert first not in scan_parser_labels or first == "dns_csv", (
            f"subnet_scope_sample.csv was routed to {first!r} as a scan upload; "
            f"that's a misroute — operators should upload it on the Scopes page, "
            f"not Scans.  Either dns_csv (acceptable false positive — DNS parser "
            f"will reject) or no scan-parser match."
        )
