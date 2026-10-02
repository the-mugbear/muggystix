"""Review 2026-10-01, branch findings S2 / S3 / M1 — the API statement timeout
on paths it was not designed for.

* **S2** — deleting a project or a scan is one cascading statement by design;
  under the 30 s limit it becomes a 503 ("narrow the filter") nobody can act
  on.  Both routes lift the limit for their session.
* **S3** — a page section that is allowed to fail swallowed EVERY exception and
  carried on.  After a cancelled statement that is wrong twice: the timeout is
  the request's answer (503), and any database error has aborted the
  transaction, so the next query fails with "current transaction is aborted"
  and the page answered 500.
* **M1** — a bare word in the Hosts query (``apache``) searched every
  project's ports inside its subquery.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.endpoints import dashboard as dashboard_module
from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db import session as session_module
from app.db.models import Scope, Subnet
from app.db.models_project import Project
from app.db.session import _NO_TIMEOUT, get_db
from app.main import app
from app.services import host_query_dsl
from app.services.host_follow_service import HostFollowService
from app.services.vulnerability_service import VulnerabilityService


def _base(project) -> str:
    return f"/api/v1/projects/{project.id}"


def _host(db, project, ip="10.90.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


# --- S2: the delete routes run without the limit ----------------------------------

def test_deleting_a_project_lifts_the_statement_timeout(client, db_session, test_project):
    doomed = Project(name="doomed", slug="doomed", description="x")
    db_session.add(doomed)
    db_session.commit()
    _host(db_session, doomed)
    doomed_id = doomed.id

    db_session.info.pop(_NO_TIMEOUT, None)
    assert client.get(f"{_base(test_project)}/findings").status_code == 200
    assert _NO_TIMEOUT not in db_session.info  # an ordinary request leaves it on

    response = client.delete(f"/api/v1/projects/{doomed_id}")
    assert response.status_code == 200, response.text
    assert db_session.info.get(_NO_TIMEOUT) is True, "project delete runs under the API statement timeout"
    db_session.info.pop(_NO_TIMEOUT, None)
    assert db_session.query(Project).filter(Project.id == doomed_id).count() == 0


def test_deleting_a_scan_lifts_the_statement_timeout(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="n.xml", tool_name="nmap", scan_type="nmap")
    db_session.add(scan)
    db_session.commit()
    scan_id = scan.id

    db_session.info.pop(_NO_TIMEOUT, None)
    response = client.delete(f"{_base(test_project)}/scans/{scan_id}")
    assert response.status_code == 200, response.text
    assert db_session.info.get(_NO_TIMEOUT) is True, "scan delete runs under the API statement timeout"
    db_session.info.pop(_NO_TIMEOUT, None)
    assert db_session.query(models.Scan).filter(models.Scan.id == scan_id).count() == 0


def _scope_with_subnet(db, project):
    scope = Scope(name="s", description="fixture", project_id=project.id)
    db.add(scope)
    db.flush()
    subnet = Subnet(scope_id=scope.id, cidr="10.90.0.0/24")
    db.add(subnet)
    db.commit()
    return scope, subnet


def _delete_scope(client, db, project):
    scope, _ = _scope_with_subnet(db, project)
    return client.delete(f"{_base(project)}/scopes/{scope.id}")


def _delete_subnet(client, db, project):
    scope, subnet = _scope_with_subnet(db, project)
    return client.delete(f"{_base(project)}/scopes/{scope.id}/subnets/{subnet.id}")


def _correlate_all(client, db, project):
    _scope_with_subnet(db, project)
    return client.post(f"{_base(project)}/scopes/correlate-all")


def _upload_scope_file(client, db, project):
    return client.post(
        f"{_base(project)}/scopes/upload-subnets",
        files={"file": ("subnets.txt", b"10.91.0.0/24\n", "text/plain")},
    )


def _delete_user(client, db, project):
    from app.db.models_auth import User, UserRole

    doomed = User(
        id=4242, username="doomed", email="doomed@example.com", full_name="Doomed",
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
    )
    db.add(doomed)
    db.commit()
    return client.delete("/api/v1/users/4242")


BULK_ROUTES = {
    "delete scope": _delete_scope,
    "delete subnet": _delete_subnet,
    "correlate all hosts": _correlate_all,
    "scope upload that adds a subnet": _upload_scope_file,
    "delete user": _delete_user,
}


@pytest.mark.parametrize("name", sorted(BULK_ROUTES))
def test_a_bulk_route_lifts_the_statement_timeout(client, db_session, test_project, name):
    """The other routes that run one cascading / project-wide statement by
    design: deleting a scope or a subnet (their host mappings go with them),
    the project-wide subnet correlation (asked for, and after a scope upload
    that adds a subnet) and deleting a user (its id is cleared across every
    table that names one)."""
    _host(db_session, test_project)
    db_session.info.pop(_NO_TIMEOUT, None)
    assert client.get(f"{_base(test_project)}/findings").status_code == 200
    assert _NO_TIMEOUT not in db_session.info  # an ordinary request leaves it on

    response = BULK_ROUTES[name](client, db_session, test_project)
    assert response.status_code in (200, 204), response.text
    assert db_session.info.get(_NO_TIMEOUT) is True, f"{name} runs under the API statement timeout"
    db_session.info.pop(_NO_TIMEOUT, None)


def test_a_scope_upload_that_adds_nothing_keeps_the_limit(client, db_session, test_project):
    """Only the rebuild is exempt: an upload of subnets the scope already has
    runs no project-wide statement, so it stays an ordinary request."""
    assert _upload_scope_file(client, db_session, test_project).status_code == 200
    db_session.info.pop(_NO_TIMEOUT, None)
    assert _upload_scope_file(client, db_session, test_project).status_code == 200
    assert _NO_TIMEOUT not in db_session.info


# --- S3: a section that may fail, after a database error --------------------------

@pytest.fixture
def real_db_client(db_session, test_user, monkeypatch):
    """The app on the REAL ``get_db`` — the suite's ``client`` replaces it, and
    the 503 translation and the per-transaction limit live there.  ``db_session``
    has already pointed ``SessionLocal`` at the test connection, so the
    request's own session works inside the test's transaction.  Yields
    ``(client, sessions)``; ``sessions[-1]`` is the session of the request in
    progress, for a stand-in that has to run SQL on it.
    """
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("statement_timeout is Postgres-only")
    monkeypatch.setattr(session_module.settings, "API_STATEMENT_TIMEOUT_MS", 200, raising=False)
    sessions = []

    def recording_get_db():
        inner = get_db()
        db = next(inner)
        sessions.append(db)
        try:
            yield db
        except BaseException as exc:
            # Hand the handler's exception to the real dependency, as FastAPI
            # would: that is where a cancelled statement becomes a 503.
            try:
                inner.throw(exc)
            except StopIteration:
                pass
        else:
            inner.close()

    app.dependency_overrides[get_db] = recording_get_db
    app.dependency_overrides[get_current_user] = lambda: test_user
    try:
        with TestClient(app) as test_client:
            yield test_client, sessions
    finally:
        app.dependency_overrides.clear()


def _times_out(sessions):
    def stand_in(*_args, **_kwargs):
        sessions[-1].execute(text("SELECT pg_sleep(2)"))
    return stand_in


def _breaks_the_transaction(sessions):
    def stand_in(*_args, **_kwargs):
        sessions[-1].execute(text("SELECT 1 / (SELECT 0)"))  # aborts the transaction
    return stand_in


def _patch_section(monkeypatch, section, stand_in):
    if section == "host-vulnerabilities":
        monkeypatch.setattr(VulnerabilityService, "get_bulk_host_vulnerability_summaries", stand_in)
    elif section == "subnets":
        monkeypatch.setattr(
            dashboard_module.SubnetCalculator, "calculate_subnet_metrics", staticmethod(stand_in),
        )
    elif section == "vulnerabilities":
        monkeypatch.setattr(VulnerabilityService, "get_dashboard_statistics", stand_in)
    else:
        monkeypatch.setattr(HostFollowService, "get_dashboard_activity", stand_in)


def _path(project, section) -> str:
    return f"{_base(project)}/hosts/" if section == "host-vulnerabilities" else f"{_base(project)}/dashboard/stats"


SECTIONS = ["host-vulnerabilities", "subnets", "vulnerabilities", "note-activity"]


@pytest.fixture
def populated(db_session, test_project):
    _host(db_session, test_project)
    scope = Scope(name="s", description="fixture", project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    db_session.add(Subnet(scope_id=scope.id, cidr="10.90.0.0/24"))
    db_session.commit()
    return test_project


@pytest.mark.parametrize("section", SECTIONS)
def test_a_cancelled_statement_in_an_optional_section_is_a_503(real_db_client, populated, monkeypatch, section):
    test_client, sessions = real_db_client
    assert test_client.get(_path(populated, section)).status_code == 200  # the page works
    _patch_section(monkeypatch, section, _times_out(sessions))
    response = test_client.get(_path(populated, section))
    assert response.status_code == 503, response.text[:400]
    assert "ran longer than the server allows" in response.json()["detail"]


@pytest.mark.parametrize("section", SECTIONS)
def test_a_failed_optional_section_does_not_take_the_page_with_it(real_db_client, populated, monkeypatch, section):
    """The section's failed statement aborted the transaction; without a
    rollback the NEXT query on the page fails and the answer is a 500."""
    test_client, sessions = real_db_client
    _patch_section(monkeypatch, section, _breaks_the_transaction(sessions))
    response = test_client.get(_path(populated, section))
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    if section == "host-vulnerabilities":
        assert body["vulnerability_error"] is True
        assert [h["ip_address"] for h in body["items"]] == ["10.90.0.1"]
    else:
        assert body["total_hosts"] == 1
        # The sections AFTER the failed one still answer: before the rollback
        # each of them failed in turn on the aborted transaction.
        if section == "subnets":
            assert body["subnet_stats"] == []
            assert body["vulnerability_stats"] is not None
            assert body["note_activity"] is not None
        elif section == "vulnerabilities":
            assert body["vulnerability_stats"] is None
            assert body["note_activity"] is not None
        else:
            assert body["note_activity"] is None


# --- M1: the bare-word search is confined to the project --------------------------

def test_a_bare_word_query_confines_its_port_subquery_to_the_project(db_session, test_project, test_user):
    ctx = host_query_dsl.BuildCtx(db=db_session, current_user=test_user, project_id=test_project.id)
    predicate = host_query_dsl.evaluate(host_query_dsl.parse_query("apache"), ctx)
    sql = str(predicate.compile(
        dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True},
    ))
    subquery = sql[sql.index("IN (SELECT"):]
    assert "ports_v2" in subquery
    assert f"hosts_v2.project_id = {test_project.id}" in subquery, subquery


def test_a_bare_word_query_still_finds_only_this_projects_hosts(client, db_session, test_project):
    mine = _host(db_session, test_project, "10.91.0.1")
    other_project = Project(name="other", slug="other", description="x")
    db_session.add(other_project)
    db_session.commit()
    theirs = _host(db_session, other_project, "10.91.0.2")
    for host in (mine, theirs):
        db_session.add(models.Port(
            host_id=host.id, port_number=80, protocol="tcp", state="open",
            service_name="http", service_product="Apache httpd",
        ))
    db_session.commit()
    response = client.get(f"{_base(test_project)}/hosts/", params={"q": "apache"})
    assert response.status_code == 200, response.text
    assert [h["ip_address"] for h in response.json()["items"]] == ["10.91.0.1"]
