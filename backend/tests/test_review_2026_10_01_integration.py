"""Regression tests for review 2026-10-01 — backend integration follow-ups.

1   ``testlabel:`` in the Hosts query raised NameError (HTTP 500)
4   a report job says who requested it
5   streamed exports are exempt from the API statement timeout
6   agent host filters confine their port subquery to the project; an issue's
    sibling rows are looked up by ``cve_id`` without wrapping the column
7   the agent's client-report read carries the page's new fields
8   ``ReportTemplate`` exposes ``evidence_records``
10  a ``row:``-keyed observation promoted twice at once makes one finding; an
    observation proposal on an observation that has a finding tells its
    people; ``POST /agent/evidence`` serializes before it commits

(2 — the oversize-evidence status — is in ``test_agent_proposals.py`` and
``test_review_2026_10_01_findings.py``; 3 — the ``host_id`` preview — in
``test_finding_loading.py``.)
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text

from app.db import models
from app.db.models import ReportJob, Scope, Subnet
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost, FindingVulnerability
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.db.session import _NO_TIMEOUT
from app.services import agent_evidence_service
from app.services import host_query_predicates as P
from app.services import report_template_service as templates
from app.services.client_report_service import ClientReportService
from app.services.finding_service import FindingService
from app.services.vuln_identity import issue_key_for
# The report tests' template folder (autouse here too: ``pentest`` prints how
# findings were confirmed, ``brief`` does not) and their seeding helpers.
from tests.test_report_review_2026_10_01 import (  # noqa: F401
    _create, _finding, _issue, _record, template_dir,
)
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _agent(client, project):
    r = client.post(f"{_base(project)}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}, r.json()["agent_session_id"]


def _member(db, project, user_id, username, role=ProjectRole.ANALYST):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def _scan(db, project):
    scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    return scan


# --- 1: testlabel: -------------------------------------------------------------

def test_the_testlabel_query_lists_the_hosts_with_that_label(client, db_session, test_project):
    a, b, c = (_host(db_session, test_project, f"10.81.0.{i}") for i in (1, 2, 3))

    def propose(host, label):
        r = client.post(f"{_base(test_project)}/host-tests", json={"tests": [{
            "request_key": str(uuid.uuid4()), "host_id": host.id, "tool": "curl",
            "description": "Check headers", "rationale": "Asked", "label": label,
        }]})
        assert r.status_code == 201, r.text

    propose(a, "web sweep")
    propose(b, "smb sweep")

    def ips(q):
        r = client.get(f"{_base(test_project)}/hosts/", params={"q": q})
        assert r.status_code == 200, r.text
        return {h["ip_address"] for h in r.json()["items"]}

    assert ips('testlabel:"web sweep"') == {"10.81.0.1"}
    assert ips('testlabel:"web sweep","smb sweep"') == {"10.81.0.1", "10.81.0.2"}
    assert ips('testlabel:"nobody used this"') == set()
    # Under NOT too — the predicate is a correlated EXISTS, so this is an anti-join.
    assert ips('NOT testlabel:"web sweep"') == {"10.81.0.2", "10.81.0.3"}
    assert c.ip_address in ips('NOT testlabel:"web sweep" AND NOT testlabel:"smb sweep"')


def test_the_testlabel_predicate_is_a_correlated_exists():
    sql = str(P.test_label_predicate(1, ["x"]))
    assert sql.startswith("EXISTS") and " IN (SELECT" not in sql
    assert "host_tests.host_id = hosts_v2.id" in sql


# --- 4: report job requester -----------------------------------------------------

def test_a_report_job_says_who_requested_it(client, db_session, test_project, test_user):
    job = ReportJob(project_id=test_project.id, report_type="comprehensive", status="queued",
                    format="json", filters={}, requested_by_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    one = client.get(f"{_base(test_project)}/reports/jobs/{job.id}")
    assert one.status_code == 200, one.text
    assert one.json()["requested_by_id"] == test_user.id
    listed = client.get(f"{_base(test_project)}/reports/jobs").json()
    assert [j["requested_by_id"] for j in listed if j["id"] == job.id] == [test_user.id]


# --- 5: streamed routes and the statement timeout ---------------------------------

STREAMED = [
    ("jwt", "/reports/hosts/csv"),
    ("jwt", "/reports/hosts/html"),
    ("jwt", "/names/export"),
    ("jwt", "/names/export?format=csv"),
    ("agent", "/assist/hosts.ndjson"),
    ("agent", "/assist/report-context.ndjson"),
    ("scope", "hosts.ndjson"),
    ("scope", "live-hosts.txt"),
    ("scope", "web-targets.txt"),
    ("scope", "named-targets.ndjson"),
]


@pytest.mark.parametrize("kind,path", STREAMED)
def test_a_streamed_route_lifts_the_statement_timeout_for_its_session(client, db_session, test_project, kind, path):
    """``disable_statement_timeout`` marks the request's session; ``get_db``
    then leaves the limit off for the rest of it.  The suite's ``client``
    hands every request this one session, so the mark is visible here."""
    _host(db_session, test_project, "10.82.0.1")
    scope = Scope(name="s", description="fixture", project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    db_session.add(Subnet(scope_id=scope.id, cidr="10.82.0.0/24"))
    db_session.commit()
    headers, _ = _agent(client, test_project)

    # An ordinary request leaves the limit on.
    db_session.info.pop(_NO_TIMEOUT, None)
    assert client.get(f"{_base(test_project)}/findings").status_code == 200
    assert _NO_TIMEOUT not in db_session.info

    if kind == "jwt":
        response = client.get(f"{_base(test_project)}{path}")
    elif kind == "agent":
        response = client.get(f"/api/v1/agent{path}", headers=headers)
    else:
        response = client.get(f"/api/v1/agent/scopes/{scope.id}/{path}", headers=headers)
    assert response.status_code == 200, response.text[:300]
    assert db_session.info.get(_NO_TIMEOUT) is True, f"{path} runs under the API statement timeout"
    db_session.info.pop(_NO_TIMEOUT, None)


# --- 6: the two perf one-liners --------------------------------------------------

@pytest.mark.parametrize("params", [{"ports": "80"}, {"services": "http"}])
def test_agent_host_filters_confine_the_port_subquery_to_the_project(client, db_session, test_project, monkeypatch, params):
    host = _host(db_session, test_project, "10.83.0.1")
    db_session.add(models.Port(host_id=host.id, port_number=80, protocol="tcp", state="open", service_name="http"))
    db_session.commit()
    headers, _ = _agent(client, test_project)
    seen = []
    real = P.port_match_subquery

    def spy(db, **kwargs):
        seen.append(kwargs.get("project_id"))
        return real(db, **kwargs)

    monkeypatch.setattr(P, "port_match_subquery", spy)
    r = client.get("/api/v1/agent/assist/hosts", headers=headers, params=params)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert seen and all(pid == test_project.id for pid in seen), seen


def test_an_issues_hosts_are_found_by_cve_without_wrapping_the_column(db_session, test_project, test_user):
    """Same hosts as before — a tool that writes the CVE in lower case still
    joins — but the comparison leaves ``cve_id`` bare, so its index is usable."""
    scan = _scan(db_session, test_project)
    ids = []
    for i, cve in enumerate(("CVE-2021-44228", "cve-2021-44228", "CVE-2021-99999")):
        host = models.Host(project_id=test_project.id, ip_address=f"10.84.0.{i + 1}", state="up")
        db_session.add(host)
        db_session.flush()
        vuln = Vulnerability(host_id=host.id, scan_id=scan.id, cve_id=cve, plugin_id=f"p{i}",
                             title=f"Worded differently {i}", severity=VulnerabilitySeverity.CRITICAL,
                             source=VulnerabilitySource.NESSUS)
        db_session.add(vuln)
        db_session.flush()
        ids.append((host.id, vuln.id))
    db_session.commit()

    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        finding = FindingService(db_session).promote_vulnerability(
            vuln=db_session.get(Vulnerability, ids[0][1]), project_id=test_project.id, actor_id=test_user.id,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record)
    db_session.commit()
    on = {r.host_id for r in db_session.query(FindingHost).filter_by(finding_id=finding.id)}
    assert on == {ids[0][0], ids[1][0]}
    assert not any("upper(vulnerabilities.cve_id)" in s.lower() for s in statements), statements


# --- 7: agent parity for the report's new fields -----------------------------------

def test_the_agents_report_read_carries_confirmations_and_a_changed_severity(client, db_session, test_project, test_user):
    a = models.Host(project_id=test_project.id, ip_address="10.85.0.1", state="up")
    db_session.add(a)
    db_session.flush()
    tls = _finding(db_session, test_project, "Weak TLS", "medium", hosts=[a])
    shown = _record(db_session, test_project, tls, a, recorded_by_user_id=test_user.id)
    full = _create(client, test_project)
    headers, _ = _agent(client, test_project)

    draft = client.get(f"/api/v1/agent/assist/client-reports/{full['id']}", headers=headers)
    assert draft.status_code == 200, draft.text
    [finding] = draft.json()["findings"]
    [entry] = finding["confirmations"]
    assert entry["id"] == shown.id and entry["tool"] == "nmap" and entry["host"] == "10.85.0.1"
    assert entry["by"] == "Test Admin" and entry["by_agent"] is False and entry["output"]
    assert not any(k.startswith("_") for k in entry)          # the render's private keys stay home
    assert finding["confirmations_omitted"] == 0
    assert finding["change"] is None and finding["previous_severity"] is None
    summary = draft.json()["summary"]
    assert summary["evidence_records"] == 1 and summary["agent_evidence_records"] == 0

    _issue(client, test_project, full["id"])
    tls.severity = "critical"
    db_session.commit()
    addendum = _create(client, test_project, kind="addendum")
    body = client.get(f"/api/v1/agent/assist/client-reports/{addendum['id']}", headers=headers).json()
    [rerated] = body["findings"]
    assert rerated["change"] == "severity_changed" and rerated["new_affected"] == []
    assert (rerated["previous_severity"], rerated["previous_severity_label"]) == ("medium", "Medium")
    assert rerated["severity"] == "critical" and len(rerated["confirmations"]) == 1
    assert body["delta"]["findings_with_changed_severity"] == 1
    assert body["summary"]["delta"]["findings_with_changed_severity"] == 1
    # The same numbers as the page's own data.
    page = client.get(f"{_base(test_project)}/client-reports/{addendum['id']}").json()
    assert body["summary"] == page["summary"]


# --- 8: the template says whether it prints evidence records -----------------------

def test_a_template_exposes_whether_it_prints_evidence_records(client, db_session, test_project, template_dir, monkeypatch):  # noqa: F811
    assert templates.get_template("pentest").evidence_records is True
    assert templates.get_template("brief").evidence_records is False
    assert templates.get_template("pentest").as_dict()["evidence_records"] is True
    listed = {t["name"]: t["evidence_records"]
              for t in client.get(f"{_base(test_project)}/client-reports/templates").json()}
    assert listed == {"pentest": True, "brief": False}

    # Only a literal true opts in.
    manifest = template_dir / "brief" / "template.json"
    manifest.write_text(manifest.read_text().replace('"title"', '"evidence_records": "yes", "title"'))
    assert templates.get_template("brief").evidence_records is False

    # The report service reads the template object, not the file a second time.
    svc = ClientReportService(db_session)
    assert svc._records_in_report("pentest") is True and svc._records_in_report("brief") is False
    assert svc._records_in_report("no-such-template") is False
    loaded = templates.get_template("pentest")
    (template_dir / "pentest" / "template.json").unlink()
    monkeypatch.setattr(templates, "get_template", lambda name: loaded)
    assert svc._records_in_report("pentest") is True


# --- 10a: a row-keyed observation promoted twice at once ---------------------------

def _row_keyed(db, project_id):
    """A scanner row with no issue identity: no CVE, no check, and a title
    that normalises to nothing — its key is ``row:<id>``, which the unique
    index on scanner findings leaves out.  Commits; returns its id."""
    scan = models.Scan(project_id=project_id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    host = models.Host(project_id=project_id, ip_address="10.86.0.1", state="up")
    db.add(host)
    db.flush()
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, title="!!!", plugin_id="x1",
                         severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS)
    db.add(vuln)
    db.flush()
    assert issue_key_for(vuln) == f"row:{vuln.id}"
    db.commit()
    return vuln.id


def _promote(project_id, user_id, vuln_id):
    def run(db):
        return FindingService(db).promote_vulnerability(
            vuln=db.get(Vulnerability, vuln_id), project_id=project_id, actor_id=user_id, only_this_host=True,
        ).id
    return run


def _scanner_findings(pair, project_id):
    db = pair.fresh()
    try:
        return [f.id for f in db.query(Finding).filter(Finding.project_id == project_id).order_by(Finding.id)]
    finally:
        db.close()


def test_the_second_promotion_of_a_row_keyed_observation_waits_and_joins(two_sessions):  # noqa: F811
    """A promotes and does not commit.  B's promotion of the SAME row sees no
    finding (A's is uncommitted) and no index would refuse its insert — so it
    must wait on the scanner row A locked, then find A's finding.  Before the
    lock B inserted a second finding at once."""
    project, user = two_sessions.project()
    vuln_id = two_sessions.commit(lambda db: _row_keyed(db, project.id))

    a_finding = _promote(project.id, user.id, vuln_id)(two_sessions.a)   # flushed, NOT committed
    outcome = {}

    def b():
        try:
            outcome["id"] = _promote(project.id, user.id, vuln_id)(two_sessions.b)
            two_sessions.b.commit()
        except BaseException as exc:  # noqa: BLE001
            two_sessions.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=b, daemon=True)
    thread.start()
    deadline = time.time() + 10
    waiting = False
    while time.time() < deadline and not waiting and thread.is_alive():
        with two_sessions._engine.connect() as probe:
            waiting = bool(probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar())
        time.sleep(0.05)
    assert waiting and thread.is_alive(), f"B did not wait for A: {outcome}"

    two_sessions.a.commit()
    thread.join(20)
    assert not thread.is_alive(), "B never finished after A committed"
    assert "error" not in outcome, repr(outcome.get("error"))
    assert outcome["id"] == a_finding
    assert _scanner_findings(two_sessions, project.id) == [a_finding]


def test_two_promotions_of_a_row_keyed_observation_released_together_make_one_finding(two_sessions):  # noqa: F811
    project, user = two_sessions.project()
    vuln_id = two_sessions.commit(lambda db: _row_keyed(db, project.id))
    results = two_sessions.race(_promote(project.id, user.id, vuln_id), _promote(project.id, user.id, vuln_id))
    assert not any(isinstance(r, BaseException) for r in results), results
    assert results[0] == results[1]
    assert _scanner_findings(two_sessions, project.id) == [results[0]]


# --- 10b: an observation proposal on an observation that has a finding --------------

def test_an_observation_proposal_tells_the_people_of_the_finding_it_evidences(client, db_session, test_project, test_user):
    author = _member(db_session, test_project, 4601, "author")
    owner = _member(db_session, test_project, 4602, "owner")
    padmin = _member(db_session, test_project, 4603, "padmin", role=ProjectRole.ADMIN)
    host = _host(db_session, test_project, "10.87.0.1")
    scan = _scan(db_session, test_project)
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, title="SMB signing not required",
                         severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NESSUS)
    db_session.add(vuln)
    finding = Finding(project_id=test_project.id, title="SMB signing not required", severity="medium",
                      status="confirmed", source="scanner", created_by_id=author.id, owner_id=owner.id)
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingVulnerability(finding_id=finding.id, vuln_id=vuln.id))
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db_session.commit()
    headers, sid = _agent(client, test_project)           # operator: the fixture admin

    def told(user):
        return (db_session.query(Notification)
                .filter(Notification.user_id == user.id, Notification.type == "proposal").all())

    r = client.post("/api/v1/agent/proposals/observation", headers=headers,
                    json={"vulnerability_id": vuln.id, "action": "dismiss", "agent_model": "model-a"})
    assert r.status_code == 201, r.text
    for person in (author, owner):
        [note] = told(person)
        assert (note.source_type, note.source_id, note.finding_id) == ("agent_session", sid, finding.id)
        assert note.title == "AI proposed changes to your finding: SMB signing not required"
        assert "model-a" in note.body and note.actor_id == test_user.id
    # It is somebody's finding, so not the admins' "on no finding yet" — and
    # never the operator whose agent it is.
    assert told(padmin) == [] and told(test_user) == []

    # A second proposal in the same session keeps the one notification current.
    again = client.post("/api/v1/agent/proposals/observation", headers=headers,
                        json={"vulnerability_id": vuln.id, "action": "promote"})
    assert again.status_code == 201, again.text
    assert len(told(author)) == 1 and len(told(owner)) == 1


# --- 10c: POST /agent/evidence serializes before its commit ------------------------

def test_recording_evidence_serializes_before_it_commits(client, db_session, test_project, monkeypatch):
    host = _host(db_session, test_project, "10.88.0.1")
    headers, _ = _agent(client, test_project)
    order = []
    real = agent_evidence_service.serialize_evidence
    monkeypatch.setattr(agent_evidence_service, "serialize_evidence",
                        lambda record: order.append("serialize") or real(record))

    def committed(_session):
        order.append("commit")

    event.listen(db_session, "before_commit", committed)
    try:
        r = client.post("/api/v1/agent/evidence", headers=headers, json={
            "host_id": host.id, "tool": "curl", "outcome": "info", "summary": "ok", "raw_output": "body",
        })
    finally:
        event.remove(db_session, "before_commit", committed)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] and body["host_ip"] == "10.88.0.1" and body["created_at"] and body["raw_output_bytes"] == 4
    assert "serialize" in order and order[-1] == "commit", order
