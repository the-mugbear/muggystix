"""Integration tests for the /hosts boolean query DSL (``q=``) end-to-end.

Seeds a small project and drives ``GET /projects/{pid}/hosts/?q=...``,
asserting AND/OR/NOT semantics, evidence search, merge with discrete
params, and error handling — plus a regression sweep proving a ``q``
leaf returns the same host set as its equivalent legacy panel param
(the contract that lets both share ``host_query_predicates``).
"""
from __future__ import annotations

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _seed(db_session, project_id):
    """Four hosts with deliberately varied evidence:

    h1 10.0.0.1 web.example  Linux   up    ports 80+443, nginx web iface,
                                            note "misconfig", Log4Shell CVE, tag "prod"
    h2 10.0.0.2 app.example  Linux   up    port 80
    h3 10.0.0.3 db.example   Windows up    port 443
    h4 10.0.0.4 (no name)    Linux   down  no ports, no vuln (zero-vuln host)
    """
    scan = models.Scan(project_id=project_id, filename="fix.xml", scan_type="nmap")
    db_session.add(scan)
    db_session.flush()

    def host(ip, name, os_name, state):
        h = models.Host(project_id=project_id, ip_address=ip, hostname=name,
                        os_name=os_name, state=state)
        db_session.add(h)
        db_session.flush()
        return h

    h1 = host("10.0.0.1", "web.example", "Linux", "up")
    h2 = host("10.0.0.2", "app.example", "Linux", "up")
    h3 = host("10.0.0.3", "db.example", "Windows", "up")
    h4 = host("10.0.0.4", None, "Linux", "down")

    for h, port in [(h1, 80), (h1, 443), (h2, 80), (h3, 443)]:
        db_session.add(models.Port(host_id=h.id, port_number=port, protocol="tcp", state="open"))

    db_session.add(models.WebInterface(
        project_id=project_id, host_id=h1.id, scan_id=scan.id, port=443,
        url="https://10.0.0.1/", source="httpx",
        server_header="nginx/1.18.0", title="Welcome",
        technologies=["Nginx"],
    ))
    db_session.add(models.HostNote(host_id=h1.id, body="needs review: TLS misconfig here"))
    db_session.add(Vulnerability(
        host_id=h1.id, scan_id=scan.id, cve_id="CVE-2021-44228", plugin_id="155999",
        title="Apache Log4Shell RCE", severity=VulnerabilitySeverity.CRITICAL,
        source=VulnerabilitySource.NESSUS,
    ))

    tag = models.HostTag(project_id=project_id, name="prod")
    db_session.add(tag)
    db_session.flush()
    db_session.add(models.HostTagAssignment(host_id=h1.id, tag_id=tag.id))

    db_session.flush()
    return {"scan": scan, "tag": tag,
            "h1": h1.id, "h2": h2.id, "h3": h3.id, "h4": h4.id}


def _ips(resp):
    assert resp.status_code == 200, resp.text
    return {item["ip_address"] for item in resp.json()["items"]}


def _q(client, project_id, q, **params):
    return client.get(f"/api/v1/projects/{project_id}/hosts/", params={"q": q, **params})


# ---------------------------------------------------------------------------
# Boolean semantics
# ---------------------------------------------------------------------------

def test_repeated_field_is_and_both_ports(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "port:80 port:443")) == {"10.0.0.1"}


def test_comma_within_field_is_or_either_port(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "port:80,443")) == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}


def test_or_across_fields(client, db_session, test_project):
    _seed(db_session, test_project.id)
    # port:443 (h1,h3) OR state:down (h4)
    assert _ips(_q(client, test_project.id, "port:443 OR state:down")) == {"10.0.0.1", "10.0.0.3", "10.0.0.4"}


def test_not_includes_zero_vuln_hosts(client, db_session, test_project):
    _seed(db_session, test_project.id)
    # Only h1 has a CVE containing "2021"; NOT must include the zero-vuln h4.
    assert _ips(_q(client, test_project.id, "NOT cve:2021")) == {"10.0.0.2", "10.0.0.3", "10.0.0.4"}


def test_grouping_precedence(client, db_session, test_project):
    _seed(db_session, test_project.id)
    # (os:windows OR has:web) AND port:443  →  h3(win,443) + h1(web,443)
    assert _ips(_q(client, test_project.id, "(os:windows OR has:web) AND port:443")) == {"10.0.0.1", "10.0.0.3"}


# ---------------------------------------------------------------------------
# Evidence search
# ---------------------------------------------------------------------------

def test_evidence_cve(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "cve:44228")) == {"10.0.0.1"}


def test_evidence_vuln_title(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, 'vuln:log4shell')) == {"10.0.0.1"}


def test_evidence_header(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "header:nginx")) == {"10.0.0.1"}


def test_evidence_note_quoted(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, 'note:"TLS misconfig"')) == {"10.0.0.1"}


def test_has_family(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "has:web")) == {"10.0.0.1"}
    assert _ips(_q(client, test_project.id, "has:critical")) == {"10.0.0.1"}
    assert _ips(_q(client, test_project.id, "has:notes")) == {"10.0.0.1"}


def test_tag_by_name(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _ips(_q(client, test_project.id, "tag:prod")) == {"10.0.0.1"}
    # Case-insensitive.
    assert _ips(_q(client, test_project.id, "tag:PROD")) == {"10.0.0.1"}


# ---------------------------------------------------------------------------
# Merge with discrete params + errors
# ---------------------------------------------------------------------------

def test_q_ands_with_discrete_param(client, db_session, test_project):
    _seed(db_session, test_project.id)
    # q=os:linux (h1,h2,h4) AND state=up param (h1,h2,h3) → {h1,h2}
    assert _ips(_q(client, test_project.id, "os:linux", state="up")) == {"10.0.0.1", "10.0.0.2"}


def test_malformed_q_is_400_not_500(client, db_session, test_project):
    _seed(db_session, test_project.id)
    r = _q(client, test_project.id, "port:")
    assert r.status_code == 400, r.text
    assert "position" in r.json()


def test_short_trgm_value_rejected(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _q(client, test_project.id, "cve:ab").status_code == 400


# ---------------------------------------------------------------------------
# Regression sweep: a q leaf == its legacy panel param (shared predicates)
# ---------------------------------------------------------------------------

def test_regression_q_matches_legacy_param(client, db_session, test_project):
    seeded = _seed(db_session, test_project.id)
    pid = seeded["tag"].id
    cases = [
        ("port:80", {"ports": "80"}),
        ("os:linux", {"os_filter": "linux"}),
        ("state:up", {"state": "up"}),
        (f"tag:prod", {"tags": str(pid)}),
        ("has:web", {"has_web_interface": "true"}),
        ("has:critical", {"has_critical_vulns": "true"}),
    ]
    for q, legacy in cases:
        via_q = _ips(client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q}))
        via_legacy = _ips(client.get(f"/api/v1/projects/{test_project.id}/hosts/", params=legacy))
        assert via_q == via_legacy, f"{q!r} ({via_q}) != legacy {legacy} ({via_legacy})"
