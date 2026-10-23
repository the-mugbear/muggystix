"""Backlog 2026-10-22, group A: small correctness defects (section 1) and
security / operations (section 4).  Each test fails on the code before its
change."""
from __future__ import annotations

from app.db.models_project import Notification
from app.db.models_remediation import RemediationPolicy


def _policy(db, enabled: bool) -> None:
    db.add(RemediationPolicy(id=1, enabled=enabled, days_critical=30, days_high=30, days_medium=90,
                             days_low=120, days_info=None, due_soon_days=7))
    db.commit()


def _two_notifications(db, user, project):
    db.add_all([
        Notification(user_id=user.id, project_id=project.id, type="remediation",
                     title="3 findings overdue", source_type="remediation_overdue", source_id=project.id),
        Notification(user_id=user.id, project_id=project.id, type="mention",
                     title="You were mentioned", source_type="note", source_id=1),
    ])
    db.commit()


# --- R-nit: with remediation tracking off, the bell shows nothing about it ---

def test_the_bell_leaves_out_remediation_alerts_while_tracking_is_off(client, db_session, test_user, test_project):
    _two_notifications(db_session, test_user, test_project)

    listed = client.get("/api/v1/notifications/").json()
    assert [n["type"] for n in listed["notifications"]] == ["mention"]
    assert listed["total_unread"] == 1
    assert client.get("/api/v1/notifications/unread-count").json() == {"unread_count": 1}
    assert client.post("/api/v1/notifications/mark-all-read").json() == {"marked_read": 1}
    # Stored, not deleted: the alert is still there, still unread.
    hidden = db_session.query(Notification).filter(Notification.type == "remediation").one()
    assert hidden.is_read is False


def test_the_bell_shows_remediation_alerts_while_tracking_is_on(client, db_session, test_user, test_project):
    _policy(db_session, True)
    _two_notifications(db_session, test_user, test_project)

    listed = client.get("/api/v1/notifications/").json()
    assert sorted(n["type"] for n in listed["notifications"]) == ["mention", "remediation"]
    assert client.get("/api/v1/notifications/unread-count").json() == {"unread_count": 2}


# --- R-nit: tiebreaks ---

def _statements(db):
    from sqlalchemy import event

    seen = []
    conn = db.connection()

    def before(conn_, cursor, statement, params, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(conn, "before_cursor_execute", before)
    return seen, lambda: event.remove(conn, "before_cursor_execute", before)


def test_the_change_detector_ranks_a_hosts_history_with_an_id_tiebreak(db_session):
    from app.services.host_change_service import changed_since_prior_scan_query

    sql = " ".join(str(changed_since_prior_scan_query(db_session, [1]).statement.compile()).split())
    assert "ORDER BY host_scan_history.discovered_at DESC, host_scan_history.id DESC" in sql


def test_the_parse_errors_list_breaks_a_tie_by_id(client, db_session, test_project):
    seen, stop = _statements(db_session)
    try:
        r = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/?sort_by=status&sort_order=asc")
    finally:
        stop()
    assert r.status_code == 200, r.text
    listed = [s for s in seen if "FROM parse_errors" in s and "ORDER BY" in s]
    assert listed and all("parse_errors.status ASC, parse_errors.id DESC" in s for s in listed), listed


# --- R-nit: the engine's session time zone is pinned to UTC ---

def test_every_connection_reads_time_in_utc_whatever_the_databases_default(db_session):
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    url = db_session.get_bind().engine.url
    name = url.database
    admin = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'ALTER DATABASE "{name}" SET timezone TO \'America/New_York\''))
        plain = create_engine(url, poolclass=NullPool)
        pinned = create_engine(url, poolclass=NullPool, connect_args=settings.SQLALCHEMY_CONNECT_ARGS)
        with plain.connect() as conn:
            assert conn.execute(text("SHOW timezone")).scalar() == "America/New_York"
        with pinned.connect() as conn:
            assert conn.execute(text("SHOW timezone")).scalar() == "UTC"
        plain.dispose()
        pinned.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'ALTER DATABASE "{name}" RESET timezone'))
        admin.dispose()


# --- R-stream: a project delete takes its scans' files with it ---

def _scan_files(root, scan_id):
    shots = root / "web_screenshots"
    (shots / str(scan_id)).mkdir(parents=True, exist_ok=True)
    (shots / str(scan_id) / "login.png").write_bytes(b"png")
    (shots / f"report-{scan_id}.json").write_text("{}")
    (shots / f"report-{scan_id}.csv").write_text("Protocol,Port\n")
    from app.services.ingestion_service import scan_file_paths

    paths = scan_file_paths(scan_id)
    assert all(p.exists() for p in paths)
    return paths


def test_deleting_a_project_removes_its_scans_files_and_no_others(
    client, db_session, test_project, tmp_path, monkeypatch,
):
    from app.core.config import settings
    from app.db import models
    from app.db.models_project import Project

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    doomed = Project(name="doomed", slug="doomed", description="x")
    db_session.add(doomed)
    db_session.commit()
    gone = models.Scan(filename="ew.zip", tool_name="eyewitness", scan_type="web", project_id=doomed.id)
    kept = models.Scan(filename="ew.zip", tool_name="eyewitness", scan_type="web", project_id=test_project.id)
    db_session.add_all([gone, kept])
    db_session.commit()
    gone_files, kept_files = _scan_files(tmp_path, gone.id), _scan_files(tmp_path, kept.id)

    r = client.delete(f"/api/v1/projects/{doomed.id}")
    assert r.status_code == 200, r.text
    assert not any(p.exists() for p in gone_files)
    assert all(p.exists() for p in kept_files)


# --- R-stream D5: a scan that recorded no host state has no "up" count ---

def test_scan_compare_says_null_up_hosts_when_the_scan_recorded_no_state(db_session, test_project):
    from app.db import models
    from app.services.scan_diff_service import compute_scan_diff

    pid = test_project.id
    dns = models.Scan(filename="names.csv", tool_name="dns", scan_type="dns", project_id=pid)
    nmap = models.Scan(filename="n.xml", tool_name="nmap", scan_type="nmap", project_id=pid)
    host = models.Host(ip_address="10.70.0.1", project_id=pid, state="up")
    other = models.Host(ip_address="10.70.0.2", project_id=pid, state="up")
    db_session.add_all([dns, nmap, host, other])
    db_session.flush()
    db_session.add_all([
        models.HostScanHistory(host_id=host.id, scan_id=dns.id, state_at_scan=None),
        models.HostScanHistory(host_id=other.id, scan_id=dns.id, state_at_scan="unknown"),
        models.HostScanHistory(host_id=host.id, scan_id=nmap.id, state_at_scan="up"),
        models.HostScanHistory(host_id=other.id, scan_id=nmap.id, state_at_scan="down"),
    ])
    db_session.commit()

    diff = compute_scan_diff(db_session, pid, dns.id, nmap.id)
    assert (diff.scan_a.total_hosts, diff.scan_a.up_hosts) == (2, None)
    assert (diff.scan_b.total_hosts, diff.scan_b.up_hosts) == (2, 1)


# --- P-G11b / P-new: agent sessions ---

def _session(db, project_id, *, started_days_ago: int, workflow: str = "project", status: str = "active"):
    from datetime import datetime, timedelta, timezone

    from app.db.models_agent import AgentSession

    row = AgentSession(project_id=project_id, workflow=workflow, status=status,
                       started_at=datetime.now(timezone.utc) - timedelta(days=started_days_ago))
    db.add(row)
    db.flush()
    return row


def _call(db, project_id, session_id):
    from app.db.models_agent import AgentApiCall

    db.add(AgentApiCall(project_id=project_id, agent_session_id=session_id, method="GET",
                        path="/api/v1/agent/identity", path_template="/agent/identity",
                        status_code=200, duration_ms=3))
    db.flush()


def test_sessions_whose_agent_never_connected_are_listed_after_the_ones_that_called(db_session, test_project):
    from app.services.agent_session_service import list_agent_sessions

    pid = test_project.id
    old_called = _session(db_session, pid, started_days_ago=5)
    new_silent = _session(db_session, pid, started_days_ago=0)
    mid_called = _session(db_session, pid, started_days_ago=2)
    _call(db_session, pid, old_called.id)
    _call(db_session, pid, mid_called.id)
    db_session.commit()

    rows = list_agent_sessions(db_session, pid)
    assert [r.id for r in rows] == [mid_called.id, old_called.id, new_silent.id]
    assert [r.connection for r in rows] == ["curl", "curl", "none"]


def test_session_hygiene_counts_only_the_sessions_the_list_has(client, db_session, test_project):
    pid = test_project.id
    _session(db_session, pid, started_days_ago=1)
    _session(db_session, pid, started_days_ago=1, workflow="recon")
    _session(db_session, pid, started_days_ago=1, workflow="execution", status="ended")
    db_session.commit()

    r = client.get(f"/api/v1/projects/{pid}/agent-activity/summary")
    assert r.status_code == 200, r.text
    hygiene = r.json()["session_hygiene"]
    assert (hygiene["sessions_started"], hygiene["sessions_active"], hygiene["sessions_ended"]) == (1, 1, 0)


# --- R10: the sign-in limiter reads a typed audit_logs.username ---

def test_an_audit_row_naming_an_account_carries_it_in_the_typed_column(db_session):
    from app.core.security import log_audit_event
    from app.db.models_auth import AuditLog

    by_helper = log_audit_event(db_session, None, "login_failed", details={"username": "alice"})
    direct = AuditLog(action="login_failed", details={"username": "bob"})
    nameless = AuditLog(action="project_created", details={"name": "p"})
    db_session.add_all([direct, nameless])
    db_session.commit()
    assert db_session.get(AuditLog, by_helper).username == "alice"
    assert (direct.username, nameless.username) == ("bob", None)


def test_the_sign_in_limits_count_by_the_column_not_the_blob(db_session):
    """A failure whose name is in the column only (as a back-filled row may
    stand) is counted; one whose blob alone names the account is not —
    the old JSON filter read the blob."""
    from app.core.security import (
        LOGIN_LOCKOUT_FAILURES, LOGIN_THROTTLE_PER_USERNAME, login_lockout_active,
        login_throttle_exceeded,
    )
    from app.db.models_auth import AuditLog

    for _ in range(LOGIN_LOCKOUT_FAILURES):
        db_session.add(AuditLog(action="login_failed", username="carol", details={}, ip_address="10.1.1.1"))
    db_session.commit()
    assert login_lockout_active(db_session, "carol", "10.1.1.1") is True

    for _ in range(LOGIN_THROTTLE_PER_USERNAME):
        db_session.add(AuditLog(action="login_failed", username="dave", details={}, ip_address=None))
    db_session.commit()
    assert login_throttle_exceeded(db_session, "dave", None) is True


# --- R-S4: the retention pass purges MCP telemetry and ended sessions ---

def test_the_retention_pass_purges_old_mcp_telemetry_and_ended_sessions(db_session, test_user, monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone

    from app import startup
    from app.db.models_agent import McpToolCall
    from app.db.models_auth import UserSession

    now = datetime.now(timezone.utc)
    old, recent = now - timedelta(days=100), now - timedelta(days=5)
    db_session.add_all([
        McpToolCall(rpc_method="tools/call", outcome="rejected", created_at=old),
        McpToolCall(rpc_method="tools/call", outcome="ok", created_at=recent),
    ])

    def session(jti, *, expires, revoked=None):
        db_session.add(UserSession(user_id=test_user.id, token_jti=jti, expires_at=expires, revoked_at=revoked))

    session("revoked-long-ago", expires=old + timedelta(hours=1), revoked=old)
    session("expired-long-ago", expires=old)
    session("revoked-lately", expires=recent + timedelta(hours=1), revoked=recent)
    session("live", expires=now + timedelta(hours=1))
    # Started long ago and revoked lately: it ended lately, so it stays.
    session("long-lived", expires=old, revoked=recent)
    db_session.commit()

    @contextmanager
    def leader(_lock):
        yield db_session

    monkeypatch.setattr(startup, "_housekeeping_leader", leader)
    calls, mcp, sessions, audits = startup._purge_retained(0, 0, 90, 90)

    assert (calls, mcp, sessions, audits) == (0, 1, 2, 0)
    assert [c.outcome for c in db_session.query(McpToolCall)] == ["ok"]
    assert sorted(s.token_jti for s in db_session.query(UserSession)) == ["live", "long-lived", "revoked-lately"]


def test_the_two_new_retention_settings_default_to_90_days_and_0_keeps(monkeypatch):
    from app import startup

    monkeypatch.delenv("MCP_TOOL_CALL_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("USER_SESSION_RETENTION_DAYS", raising=False)
    assert (startup.mcp_tool_call_retention_days(), startup.user_session_retention_days()) == (90, 90)
    monkeypatch.setenv("MCP_TOOL_CALL_RETENTION_DAYS", "0")
    monkeypatch.setenv("USER_SESSION_RETENTION_DAYS", "-3")
    assert (startup.mcp_tool_call_retention_days(), startup.user_session_retention_days()) == (0, 0)


# --- R-S5: an address is validated and written one way at the host upsert ---

def _nessus(db, project_id, tmp_path, report_hosts):
    from app.services.nessus_integration_service import NessusIntegrationService

    path = tmp_path / "s5.nessus"
    path.write_text(
        '<?xml version="1.0" ?><NessusClientData_v2><Report name="s5">'
        + "".join(report_hosts) + "</Report></NessusClientData_v2>"
    )
    result = NessusIntegrationService(db).process_nessus_file(str(path), project_id=project_id)
    assert result["scan_id"], result
    return result


def _report_host(name, host_ip=None):
    props = f'<tag name="host-ip">{host_ip}</tag>' if host_ip else '<tag name="host-fqdn">x</tag>'
    item = ('<ReportItem port="22" svc_name="ssh" protocol="tcp" severity="3" pluginID="90317" '
            'pluginName="SSH Weak Algorithms Supported"><description>Weak.</description>'
            '<risk_factor>High</risk_factor></ReportItem>')
    return f'<ReportHost name="{name}"><HostProperties>{props}</HostProperties>{item}</ReportHost>'


def _addresses(db, project_id):
    from app.db import models

    return sorted(a for (a,) in db.query(models.Host.ip_address).filter(models.Host.project_id == project_id))


def test_a_nessus_host_named_by_a_hostname_with_no_host_ip_is_not_a_host(db_session, test_project, tmp_path):
    pid = test_project.id   # the import expunges the session
    _nessus(db_session, pid, tmp_path, [
        _report_host("intranet.example.test"),            # unresolved target: no host-ip
        _report_host("10.71.0.1", host_ip="10.71.0.1"),
        _report_host("10.71.0.2"),                         # no host-ip, but the name IS an address
    ])
    assert _addresses(db_session, pid) == ["10.71.0.1", "10.71.0.2"]


def test_an_ipv6_address_written_two_ways_is_one_host(db_session, test_project, tmp_path):
    pid = test_project.id
    _nessus(db_session, pid, tmp_path, [_report_host("h", host_ip="2001:DB8:0:0::1")])
    _nessus(db_session, pid, tmp_path, [_report_host("h", host_ip="2001:db8::1")])
    assert _addresses(db_session, pid) == ["2001:db8::1"]


def test_the_upsert_refuses_a_non_address_and_finds_a_row_stored_in_another_spelling(db_session, test_project):
    import pytest

    from app.db import models
    from app.services.host_deduplication_service import HostDeduplicationService

    pid = test_project.id
    scan = models.Scan(filename="s.xml", tool_name="nmap", scan_type="nmap", project_id=pid)
    # A row written before addresses were normalised.
    legacy = models.Host(ip_address="2001:DB8::5", project_id=pid, state="up")
    db_session.add_all([scan, legacy])
    db_session.flush()
    dedup = HostDeduplicationService(db_session)

    with pytest.raises(ValueError, match="Not an IP address"):
        dedup.find_or_create_host("intranet.example.test", scan.id, {"state": "up"}, project_id=pid)
    same = dedup.find_or_create_host("2001:DB8::5", scan.id, {"state": "up"}, project_id=pid)
    assert same.id == legacy.id
    fresh = dedup.find_or_create_host(" 2001:DB8::6 ", scan.id, {"state": "up"}, project_id=pid)
    assert fresh.ip_address == "2001:db8::6"


# --- R10: no transaction stays open through Quarto ---

def test_the_renderer_ends_the_callers_transaction_before_quarto_runs(tmp_path, monkeypatch):
    from app.services import quarto_render

    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "report.qmd").write_text("---\ntitle: x\n---\nbody\n")
    quarto = tmp_path / "quarto"
    quarto.write_text("#!/bin/sh\necho '<html></html>' > report.html\n")
    quarto.chmod(0o755)
    events = []
    real = quarto_render._run_group

    def run_group(*args, **kwargs):
        events.append("quarto")
        return real(*args, **kwargs)

    monkeypatch.setattr(quarto_render, "_run_group", run_group)
    quarto_render.render(folder, "report.qmd", {}, ["html"], tmp_path / "out", timeout=30,
                         quarto=str(quarto), before_quarto=lambda: events.append("transaction ended"))
    assert events == ["transaction ended", "quarto"]


def test_the_client_report_renderer_ends_the_transaction_before_quarto(monkeypatch, tmp_path):
    """A client report's render (preview and issue share ``_render``) hands
    the renderer the session's rollback."""
    import json

    from app.core.config import settings
    from app.db.models_reports import Report
    from app.services import client_report_render, quarto_render

    class Session:
        rolled_back = 0

        def rollback(self):
            Session.rolled_back += 1

    def fake_render(*args, **kwargs):
        kwargs["before_quarto"]()
        raise quarto_render.RenderError("stop here")

    monkeypatch.setattr(quarto_render, "render", fake_render)
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({"title": "T", "formats": ["html"]}))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(folder.parent))
    monkeypatch.setattr(client_report_render, "live_evidence_resolver", lambda *a, **k: None)
    report = Report(id=1, project_id=1, template="pentest")
    try:
        client_report_render._render(Session(), report, {"findings": []}, ["html"], tmp_path / "out", "x")
    except quarto_render.RenderError:
        pass
    assert Session.rolled_back == 1
