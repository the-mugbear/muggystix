"""Every agent list a caller can page answers ONE envelope and takes the same
two arguments (native MCP run 2026-10-10: "paging has several dialects" —
``skip`` beside ``offset``, lists with no ``has_more``, no echo of ``limit``).

The envelope is ``{items, total, limit, offset, returned, has_more}``
(``agent_schemas.AgentPage``, built by ``agent_schemas.agent_page``); the
arguments are ``limit`` and ``offset``.  ``PAGED`` is the inventory.  A new
agent read that takes a paging argument fails
``test_every_agent_route_with_a_paging_argument_is_accounted_for`` until it is
added here — as a paged list, or in ``NOT_PAGED`` with its reason.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.agent_schemas import PAGE_KEYS, agent_page
from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding, FindingHost
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource

API = "/api/v1"

#: Every agent list a caller can page: its route (under /api/v1) and the MCP
#: tool that reaches it.
PAGED = {
    "/agent/assist/hosts": "assist_list_hosts",
    "/agent/assist/hosts/{host_id}/vulnerabilities": "assist_get_host_vulnerabilities",
    "/agent/assist/hosts/{host_id}/web-interfaces": "assist_list_host_web_interfaces",
    "/agent/assist/hosts/{host_id}/access": "assist_list_host_access",
    "/agent/assist/hosts/{host_id}/notes": "assist_get_host_notes",
    "/agent/assist/notes": "assist_list_recent_notes",
    "/agent/assist/findings": "assist_list_findings",
    "/agent/assist/names": "assist_list_names",
    "/agent/assist/query-history": "assist_query_history",
    "/agent/assist/scans": "assist_list_scans",
    "/agent/assist/scans/{scan_id}/hosts": "assist_list_scan_hosts",
    "/agent/assist/scans/{scan_id}/ports": "assist_list_scan_ports",
    "/agent/assist/segments": "assist_list_segments",
    "/agent/assist/scanner-observations": "assist_list_scanner_observations",
    "/agent/assist/scanner-observations/hosts": "assist_list_observation_hosts",
    "/agent/assist/uninterpreted-lines": "assist_list_uninterpreted_lines",
    "/agent/assist/workbench/investigate": "assist_list_worth_a_look",
    "/agent/assist/workbench/findings": "assist_list_my_findings",
    "/agent/evidence": "list_evidence",
    "/agent/proposals": "list_proposals",
    "/agent/host-tests": "host_tests_list",
    "/agent/remediation": "remediation_list",
    "/agent/remediation/hosts/{host_id}/events": "remediation_timeline",
    "/agent/scope/subnets": "scope_list_subnets",
    "/agent/scope/domains": "scope_list_domains",
}

#: Agent reads that take ``limit`` (or ``offset``) and are NOT the envelope,
#: each with why.  Nothing here takes ``skip``.
NOT_PAGED = {
    "/agent/assist/client-reports/{report_id}":
        "one report; `limit` / `offset` page its `findings` member (`findings_total`, "
        "`findings_has_more`) inside an answer that is otherwise whole on every page",
    "/agent/assist/evidence/gaps":
        "the Evidence page's own answer, key for key; its service cuts at `limit` and cannot skip",
    "/agent/assist/ingestion-issues":
        "a summary of counts with a capped `issues` list beside `total_issues`",
    "/agent/assist/scans/compare":
        "several lists in one answer, each capped at `limit`; `counts` is exact",
    "/agent/assist/workbench/terrain":
        "a map of address blocks cut at `limit` (`blocks_total`, `limited`)",
    # The pre-assist reads: no MCP tool, kept as they are for existing curl
    # clients until production shows nobody calls them.
    "/agent/hosts": "pre-assist read, a bare array; superseded by /agent/assist/hosts",
    "/agent/scans": "pre-assist read, a bare array; superseded by /agent/assist/scans",
}

_PAGING_ARGUMENTS = {"limit", "offset", "skip"}


def _openapi():
    from app.main import app
    return app.openapi()


def _query_arguments(operation) -> set:
    return {p["name"] for p in operation.get("parameters", ()) if p.get("in") == "query"}


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count,total,limit,offset,more", [
    (0, 0, 50, 0, False), (2, 5, 2, 0, True), (2, 5, 2, 2, True), (1, 5, 2, 4, False),
    (0, 5, 2, 9, False), (5, 5, 5, 0, False),
])
def test_the_envelope_is_built_in_one_place(count, total, limit, offset, more):
    page = agent_page(list(range(count)), total=total, limit=limit, offset=offset)
    assert tuple(page) == PAGE_KEYS
    assert page == {
        "items": list(range(count)), "total": total, "limit": limit, "offset": offset,
        "returned": count, "has_more": more,
    }


# ---------------------------------------------------------------------------
# The surface, read from the routes
# ---------------------------------------------------------------------------

def test_every_agent_route_with_a_paging_argument_is_accounted_for():
    """A new agent list is paged like the others, or says why it is not."""
    unaccounted = []
    for path, operations in _openapi()["paths"].items():
        if not path.startswith(f"{API}/agent/") or "get" not in operations:
            continue
        if _query_arguments(operations["get"]) & _PAGING_ARGUMENTS:
            short = path[len(API):]
            if short not in PAGED and short not in NOT_PAGED:
                unaccounted.append(short)
    assert not unaccounted, (
        f"{sorted(unaccounted)} take a paging argument: add each to PAGED (and answer "
        "agent_schemas.agent_page) or to NOT_PAGED with the reason"
    )
    stale = [p for p in (*PAGED, *NOT_PAGED) if f"{API}{p}" not in _openapi()["paths"]]
    assert not stale, f"{stale} are not routes any more"


@pytest.mark.parametrize("path", sorted(PAGED))
def test_a_paged_route_takes_limit_and_offset_and_declares_the_envelope(path):
    operation = _openapi()["paths"][f"{API}{path}"]["get"]
    arguments = _query_arguments(operation)
    assert {"limit", "offset"} <= arguments, f"{path} takes {sorted(arguments)}"
    assert "skip" not in arguments, f"{path}: the agents' argument is `offset`"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in schema:   # the two router factories answer a service's dict
        declared = _openapi()["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]["properties"]
        assert set(PAGE_KEYS) <= set(declared), f"{path} declares {sorted(declared)}"


def test_no_agent_route_takes_skip():
    takes_skip = [
        path for path, operations in _openapi()["paths"].items()
        if path.startswith(f"{API}/agent/")
        and any("skip" in _query_arguments(op) for op in operations.values() if isinstance(op, dict))
    ]
    assert not takes_skip, f"{takes_skip}: the agents' argument is `offset`, on every route"


@pytest.mark.parametrize("path,tool", sorted(PAGED.items()))
def test_the_tool_of_a_paged_route_offers_limit_and_offset_only(path, tool):
    from app.api.v1.endpoints.mcp_tools import TOOLS

    spec = TOOLS[tool]
    assert spec["path"] == f"{API}{path}"
    offered = set(spec["input_schema"]["properties"])
    assert {"limit", "offset"} <= offered and "skip" not in offered, sorted(offered)


# ---------------------------------------------------------------------------
# The surface, called
# ---------------------------------------------------------------------------

ROWS = 5


@pytest.fixture
def estate(client, db_session, test_project, test_user):
    """ROWS of everything a paged route lists, in one project, and the key of
    a session whose operator is ``test_user`` (a global admin)."""
    from app.db.models_remediation import RemediationPolicy

    pid = test_project.id
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30,
                                     days_medium=90, days_low=120, due_soon_days=7))
    scope = models.Scope(project_id=pid)
    scan = models.Scan(project_id=pid, filename="paging.xml", tool_name="nmap")
    db_session.add_all([scope, scan])
    db_session.flush()
    for i in range(ROWS):
        db_session.add(models.Subnet(scope_id=scope.id, cidr=f"10.80.{i}.0/24"))
        db_session.add(models.ScopeDomain(scope_id=scope.id, domain=f"d{i}.example.test", include_subdomains=False))
        db_session.add(models.DNSName(project_id=pid, fqdn=f"n{i}.example.test"))
        db_session.add(models.Scan(project_id=pid, filename=f"more-{i}.xml", tool_name="nmap"))
        db_session.add(models.IngestionJob(
            project_id=pid, filename=f"nxc-{i}.txt", original_filename=f"nxc-{i}.txt", storage_path="/x",
            status="completed", tool_name="netexec", final_file_type="netexec_txt",
            uninterpreted_lines={"total": 1, "distinct": 1, "shapes": [
                {"kind": "text_only", "shape": "RDP <IP> 3389 <HOST>", "count": 1}]},
        ))

    # ROWS untouched hosts, each carrying the SAME exploitable critical: the
    # untouched queue, one scanner issue on ROWS hosts, the scan's hosts.
    for i in range(ROWS):
        host = models.Host(project_id=pid, ip_address=f"10.80.{i}.5", state="up")
        db_session.add(host)
        db_session.flush()
        db_session.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan="up", discovered_at=datetime.now(timezone.utc),
        ))
        # …and a port the scan recorded on each (the scan's ports read).
        recorded = models.Port(host_id=host.id, port_number=22, protocol="tcp", state="open")
        db_session.add(recorded)
        db_session.flush()
        db_session.add(models.PortScanHistory(port_id=recorded.id, scan_id=scan.id, state_at_scan="open"))
        db_session.add(Vulnerability(
            title="Shared weakness", severity=VulnerabilitySeverity.CRITICAL,
            source=VulnerabilitySource.MANUAL, host_id=host.id, scan_id=scan.id, exploitable=True,
        ))

    # One worked host carrying ROWS of everything that hangs off a host.
    worked = models.Host(project_id=pid, ip_address="10.80.9.9", state="up")
    db_session.add(worked)
    db_session.flush()
    endpoints = []
    for i in range(ROWS):
        db_session.add(Vulnerability(
            title=f"Issue {i}", severity=VulnerabilitySeverity.HIGH,
            source=VulnerabilitySource.MANUAL, host_id=worked.id, scan_id=scan.id,
        ))
        db_session.add(models.Annotation(host_id=worked.id, user_id=test_user.id, body=f"note {i}"))
        db_session.add(models.QueryHistory(user_id=test_user.id, project_id=pid, kind="query", q=f"hosts | head {i + 1}"))
        db_session.add(models.WebInterface(
            project_id=pid, host_id=worked.id, scan_id=scan.id, url=f"http://10.80.9.9:80{i}/", source="httpx",
        ))
        db_session.add(NetexecResult(scan_id=scan.id, host_id=worked.id, protocol="smb", port=4450 + i))
        finding = Finding(
            project_id=pid, title=f"Finding {i}", severity="high", status="confirmed",
            source="manual", owner_id=test_user.id,
        )
        db_session.add(finding)
        db_session.flush()
        link = FindingHost(finding_id=finding.id, host_id=worked.id, host_status="open")
        db_session.add(link)
        db_session.flush()
        endpoints.append((finding.id, link.id))
    db_session.commit()

    started = client.post(f"/api/v1/projects/{pid}/assist/start", json={})
    assert started.status_code == 201, started.text
    headers = {"X-API-Key": started.json()["api_key"]}

    # What only an agent's own write makes.
    tests = client.post(f"{API}/agent/host-tests", headers=headers, json={"tests": [
        {"request_key": f"paging-{i}", "host_id": worked.id, "tool": "curl",
         "description": f"check {i}", "rationale": "paging fixture"}
        for i in range(ROWS)
    ]})
    assert tests.status_code == 201, tests.text
    for i in range(ROWS):
        evidence = client.post(f"{API}/agent/evidence", headers=headers, json={
            "host_id": worked.id, "tool": "curl", "outcome": "info", "summary": f"ran {i}",
        })
        assert evidence.status_code == 201, evidence.text
        finding_id, finding_host_id = endpoints[i]
        proposal = client.post(f"{API}/agent/proposals/endpoint-status", headers=headers, json={
            "finding_id": finding_id, "finding_host_id": finding_host_id, "host_status": "retest",
        })
        assert proposal.status_code == 201, proposal.text
        note = client.post(f"{API}/agent/remediation/events", headers=headers, json={
            "host_id": worked.id, "body": f"timeline {i}",
        })
        assert note.status_code == 201, note.text

    issues = client.get(f"{API}/agent/assist/scanner-observations", headers=headers).json()["items"]
    shared = next(i for i in issues if i["host_count"] == ROWS)
    return {
        "headers": headers,
        "path": {"host_id": worked.id, "scan_id": scan.id},
        "query": {"/agent/assist/scanner-observations/hosts": {"issue_key": shared["issue_key"]}},
    }


def _read(client, estate, path, **paging):
    response = client.get(
        f"{API}{path.format(**estate['path'])}", headers=estate["headers"],
        params={**estate["query"].get(path, {}), **paging},
    )
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _identity(row):
    """What tells two rows apart: an id where the row has one, else the row."""
    if isinstance(row, dict):
        for key in ("id", "finding_host_id", "finding_id", "host_id", "job_id", "issue_key", "cidr", "domain"):
            if row.get(key) is not None:
                return (key, row[key])
    return repr(row)


@pytest.mark.parametrize("path", sorted(PAGED))
def test_a_paged_route_answers_the_envelope_and_pages_to_its_total(client, estate, path):
    whole = _read(client, estate, path)
    assert set(PAGE_KEYS) <= set(whole), f"{path} answers {sorted(whole)}"
    assert whole["offset"] == 0 and whole["returned"] == len(whole["items"])
    assert whole["has_more"] == (whole["returned"] < whole["total"])
    # The fixture exceeds a page of two on every route, so a list cut at its
    # page and a list that is whole cannot be told apart by accident.
    assert whole["total"] >= ROWS, f"{path} lists {whole['total']} rows; the fixture is meant to give it {ROWS}"

    seen, offset = [], 0
    while True:
        page = _read(client, estate, path, limit=2, offset=offset)
        assert (page["limit"], page["offset"], page["total"]) == (2, offset, whole["total"]), path
        assert page["returned"] == len(page["items"]) <= 2
        assert page["has_more"] == (offset + page["returned"] < page["total"]), path
        seen += [_identity(row) for row in page["items"]]
        if not page["has_more"]:
            break
        offset += page["returned"]
        assert offset <= whole["total"], f"{path} never stops paging"
    assert len(seen) == len(set(seen)) == whole["total"], f"{path}: paged {len(seen)} of {whole['total']}"

    past = _read(client, estate, path, limit=2, offset=whole["total"] + 10)
    assert (past["items"], past["returned"], past["has_more"], past["total"]) == ([], 0, False, whole["total"])


def test_one_tier_of_the_untouched_queue_pages_to_that_tiers_total(client, estate):
    """`total` is the list THIS call pages: with `tier`, that tier's count."""
    path = "/agent/assist/workbench/investigate"
    whole = _read(client, estate, path)
    tier = whole["items"][0]["tier"]
    one = _read(client, estate, path, tier=tier, limit=2)
    assert one["total"] == one["tier_counts"][tier - 1] and one["queue_total"] == whole["queue_total"]
    assert one["has_more"] == (one["returned"] < one["total"])


# ---------------------------------------------------------------------------
# The argument's old names
# ---------------------------------------------------------------------------

def _call(client, headers, name, arguments):
    return client.post("/api/v1/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).json()


@pytest.mark.parametrize("tool,arguments", [
    ("assist_list_scan_hosts", {"scan_id": 1, "skip": 2}),
    ("assist_list_scanner_observations", {"skip": 2}),
    ("assist_list_hosts", {"skip": 2}),
])
def test_skip_is_an_unknown_argument_over_mcp(client, test_project, tool, arguments):
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    refused = _call(client, {"X-API-Key": started.json()["api_key"]}, tool, arguments)
    assert refused.get("error", {}).get("code") == -32602, refused
    assert "skip" in str(refused)


def test_the_page_routes_two_doors_share_still_answer_their_own_shape(client, test_project):
    """The envelope is the AGENTS' door: the people's routes of the two shared
    router factories answer what they did (the frontend reads them)."""
    tests = client.get(f"/api/v1/projects/{test_project.id}/host-tests").json()
    assert tests == {"items": [], "total": 0, "has_more": False}
