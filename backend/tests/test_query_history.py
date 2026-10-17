"""The Query page's history and the editor's column values (v2.512.0).

The owner, 2026-10-17: the history is on the server, per person per project;
an agent's queries appear in its operator's history labelled as an agent's;
the newest 200 are kept (FIFO); a refused query is kept, with why, so it can
be corrected.  The Hosts page's recent queries are the same table and service
(``kind`` 'hosts', its own cap) — the two never show each other's entries.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import Host, Port
from app.db.models_auth import User, UserRole
from app.db.models_project import Project
from app.services import query_history_service


def _url(project, tail=""):
    return f"/api/v1/projects/{project.id}{tail}"


def _run(client, project, q):
    return client.get(_url(project, "/query"), params={"q": q})


def _history(client, project, **params):
    r = client.get(_url(project, "/query/history"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def ports(db_session, test_project):
    """4 hosts: every one has ssh; two have microsoft-ds; one has ms-wbt-server."""
    now = datetime.now(timezone.utc)
    for i in range(1, 5):
        h = Host(project_id=test_project.id, ip_address=f"10.40.0.{i}", state="up", first_seen=now, last_seen=now)
        db_session.add(h)
        db_session.flush()
        db_session.add(Port(host_id=h.id, port_number=22, protocol="tcp", state="open", service_name="ssh"))
        if i % 2 == 0:
            db_session.add(Port(host_id=h.id, port_number=445, protocol="tcp", state="open", service_name="microsoft-ds"))
    db_session.add(Port(host_id=h.id, port_number=3389, protocol="tcp", state="closed", service_name="ms-wbt-server"))
    db_session.commit()


def test_every_run_is_recorded_newest_first_with_its_answer_or_why_it_was_refused(client, test_project, ports):
    assert _run(client, test_project, "hosts").status_code == 200
    assert _run(client, test_project, "hosts | where bogus=1").status_code == 400
    assert _run(client, test_project, "hosts").status_code == 200
    body = _history(client, test_project)
    assert body["total"] == 2
    newest, refused = body["items"]
    assert (newest["q"], newest["run_count"], newest["result_count"], newest["error"]) == ("hosts", 2, 4, None)
    assert newest["by_agent"] is False and newest["first_run_at"] <= newest["last_run_at"]
    assert refused["q"] == "hosts | where bogus=1" and refused["result_count"] is None
    assert "Unknown column 'bogus'" in refused["error"] and refused["error_position"] == 14


def test_a_query_that_runs_after_being_refused_says_so(client, test_project, ports):
    """Same text, later fixed elsewhere (a column added, a typo in a search
    word corrected server-side): the entry's last outcome is what shows."""
    q = "ports | where service=\"ssh\""
    _run(client, test_project, q)
    (entry,) = _history(client, test_project)["items"]
    assert entry["result_count"] == 4 and entry["error"] is None


def test_an_agents_queries_are_its_operators_labelled(client, test_project, ports):
    r = client.post(_url(test_project, "/assist/start"), json={})
    assert r.status_code == 201, r.text
    key = {"X-API-Key": r.json()["api_key"]}
    q = "ports | stats count by service"
    assert client.get("/api/v1/agent/assist/query", headers=key, params={"q": q}).status_code == 200
    assert client.get("/api/v1/agent/assist/query", headers=key,
                      params={"q": "ports | sort bogus"}).status_code == 400
    _run(client, test_project, q)  # the operator runs the same text: a second, unlabelled entry
    items = _history(client, test_project)["items"]
    assert [(e["q"], e["by_agent"]) for e in items] == [
        (q, False), ("ports | sort bogus", True), (q, True),
    ]
    assert items[1]["error"] and items[2]["result_count"] == 3
    # The agent reads the same list, in the agents' page envelope.
    agent = client.get("/api/v1/agent/assist/query-history", headers=key, params={"limit": 2}).json()
    assert [e["q"] for e in agent["items"]] == [e["q"] for e in items[:2]]
    assert (agent["total"], agent["returned"], agent["has_more"]) == (3, 2, True)


def test_the_newest_are_kept_and_the_oldest_dropped(client, test_project, ports, monkeypatch):
    monkeypatch.setitem(query_history_service.CAPS, query_history_service.QUERY, 3)
    for n in range(1, 6):
        _run(client, test_project, f"hosts | head {n}")
    _run(client, test_project, "hosts | head 3")  # a rerun moves up; nothing else goes
    body = _history(client, test_project)
    assert [e["q"] for e in body["items"]] == ["hosts | head 3", "hosts | head 5", "hosts | head 4"]
    assert body["total"] == 3


def test_the_history_narrows_pages_forgets_and_is_ones_own(client, db_session, test_project, ports):
    for q in ("hosts", "ports", "ports | stats count by service"):
        _run(client, test_project, q)
    narrowed = _history(client, test_project, contains="PORTS")
    assert narrowed["total"] == 2 and len(narrowed["items"]) == 2
    page = _history(client, test_project, limit=1, offset=1)
    assert page["total"] == 3 and [e["q"] for e in page["items"]] == ["ports"]
    # Another person's entries, and the Hosts page's, are not in it.
    other = User(id=4242, username="other-hq", email="o@example.com", full_name="O", hashed_password="x",
                 role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    query_history_service.record(db_session, user_id=other.id, project_id=test_project.id,
                                 kind=query_history_service.QUERY, text="hosts | where os=\"secret\"")
    client.post(_url(test_project, "/hosts/query/history"), json={"q": "port:445"})
    db_session.commit()
    assert {e["q"] for e in _history(client, test_project)["items"]} == {
        "hosts", "ports", "ports | stats count by service"}
    assert [e["q"] for e in client.get(_url(test_project, "/hosts/query/history")).json()] == ["port:445"]
    # Forget one, then all — another person's entry stays.
    first = _history(client, test_project)["items"][0]
    assert client.delete(_url(test_project, f"/query/history/{first['id']}")).status_code == 204
    assert _history(client, test_project)["total"] == 2
    assert client.delete(_url(test_project, "/query/history")).status_code == 204
    assert _history(client, test_project)["total"] == 0
    assert db_session.query(models.QueryHistory).filter_by(user_id=other.id).count() == 1


def test_the_export_is_not_a_run_and_a_recording_failure_never_fails_the_query(
        client, test_project, ports, monkeypatch):
    assert client.get(_url(test_project, "/query/export.csv"), params={"q": "hosts"}).status_code == 200
    assert _history(client, test_project)["total"] == 0

    def broken(*args, **kwargs):
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(query_history_service, "record", broken)
    r = _run(client, test_project, "hosts")
    assert r.status_code == 200 and r.json()["total"] == 4


def test_a_text_columns_values_most_rows_first(client, db_session, test_project, ports):
    def values(**params):
        r = client.get(_url(test_project, "/query/values"), params=params)
        assert r.status_code == 200, r.text
        return r.json()

    body = values(dataset="ports", column="service", contains="S")
    assert body["supported"] is True
    assert [(v["value"], v["count"]) for v in body["values"]] == [
        ("ssh", 4), ("microsoft-ds", 2), ("ms-wbt-server", 1)]
    assert [v["value"] for v in values(dataset="ports", column="service", contains="wbt")["values"]] == [
        "ms-wbt-server"]
    # A literal % or _ is text, not a wildcard.
    assert values(dataset="ports", column="service", contains="%")["values"] == []
    # Not text: nothing to offer (an enum's values are in the schema).
    assert values(dataset="ports", column="port")["supported"] is False
    assert values(dataset="observations", column="severity")["supported"] is False
    # One project's values only.
    other = Project(name="Elsewhere", slug="elsewhere-hq")
    db_session.add(other)
    db_session.flush()
    h = Host(project_id=other.id, ip_address="10.41.0.1", state="up")
    db_session.add(h)
    db_session.flush()
    db_session.add(Port(host_id=h.id, port_number=21, protocol="tcp", state="open", service_name="ftp"))
    db_session.commit()
    assert values(dataset="ports", column="service", contains="ftp")["values"] == []
    bad = client.get(_url(test_project, "/query/values"), params={"dataset": "widgets", "column": "x"})
    assert bad.status_code == 400 and "Unknown dataset" in bad.json()["detail"]
    assert "Unknown column" in client.get(_url(test_project, "/query/values"),
                                          params={"dataset": "ports", "column": "x"}).json()["detail"]
