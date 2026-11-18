"""The project's changelog: a line per action, written with the change.

``test_project_events_coverage.py`` holds the vocabulary, the route map and
the writer together.  These tests are behaviour: an action through its real
route leaves its line — who, what, to what, how many — and the read answers
it to the people who may read it.
"""
from __future__ import annotations

import pytest

from app.db import models
from app.db.models_project import ProjectEvent
from app.services import project_events as pe


def _lines(db, project, action=None):
    db.expire_all()
    q = db.query(ProjectEvent).filter(ProjectEvent.project_id == project.id)
    if action:
        q = q.filter(ProjectEvent.action == action)
    return q.order_by(ProjectEvent.id).all()


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


# --- the writer --------------------------------------------------------------

def test_a_line_is_written_in_the_callers_transaction_and_leaves_with_it(db_session, test_project, test_user):
    pe.note_actor(db_session, user_id=test_user.id)
    pe.record(db_session, test_project.id, "project_updated", summary="kept")
    db_session.commit()
    pe.record(db_session, test_project.id, "project_updated", summary="rolled back")
    db_session.rollback()
    assert [line.summary for line in _lines(db_session, test_project)] == ["kept"]


def test_the_writer_refuses_an_unknown_action_and_records_nothing_for_nobody_or_nothing(
    db_session, test_project, test_user,
):
    with pytest.raises(ValueError):
        pe.record(db_session, test_project.id, "something_else")
    db_session.info.pop("bluestick_actor", None)
    assert pe.record(db_session, test_project.id, "project_updated") is None        # no actor
    pe.note_actor(db_session, user_id=test_user.id)
    assert pe.record(db_session, test_project.id, "host_review_changed", count=0) is None   # nothing changed
    db_session.commit()
    assert _lines(db_session, test_project) == []


# --- findings ----------------------------------------------------------------

def test_a_findings_life_is_a_line_per_action(client, db_session, test_project, test_user):
    a, b = _host(db_session, test_project, "10.60.0.1"), _host(db_session, test_project, "10.60.0.2")
    base = f"/api/v1/projects/{test_project.id}/findings"
    fid = client.post(base, json={"title": "Weak TLS", "severity": "medium", "host_ids": [a.id]}).json()["id"]
    assert client.patch(f"{base}/{fid}", json={"severity": "high"}).status_code == 200
    assert client.post(f"{base}/{fid}/status", json={"status": "confirmed"}).status_code == 200
    assert client.post(f"{base}/{fid}/hosts", json={"host_ids": [b.id]}).status_code == 200
    assert client.post(f"{base}/{fid}/notes", json={"body": "seen on both"}).status_code in (200, 201)
    assert client.delete(f"{base}/{fid}").status_code in (200, 204)

    lines = _lines(db_session, test_project)
    assert [line.action for line in lines] == [
        "finding_created", "finding_edited", "finding_status_changed", "finding_endpoints_added",
        "finding_comment_added", "finding_deleted",
    ]
    # Who, what it was called, and the detail — never a field's text.
    assert {line.actor_user_id for line in lines} == {test_user.id}
    assert {line.subject_label for line in lines} == {"Weak TLS"}
    assert {line.by_agent for line in lines} == {False}
    by_action = {line.action: line for line in lines}
    assert by_action["finding_edited"].summary == "Severity"
    assert by_action["finding_status_changed"].summary == "open → confirmed"
    assert by_action["finding_endpoints_added"].summary == "10.60.0.2"
    # The finding is gone; its lines are not, and still say what it was.
    assert by_action["finding_deleted"].subject_id == fid


def test_a_bulk_change_is_one_line_with_its_count(client, db_session, test_project):
    base = f"/api/v1/projects/{test_project.id}/findings"
    ids = [client.post(base, json={"title": f"F{n}", "severity": "low"}).json()["id"] for n in range(3)]
    answer = client.post(f"{base}/bulk/status", json={"finding_ids": ids, "status": "confirmed"})
    assert answer.status_code == 200, answer.text
    (line,) = _lines(db_session, test_project, "finding_status_changed")
    assert (line.count, line.summary, line.subject_id) == (3, "3 findings → confirmed", None)


# --- hosts -------------------------------------------------------------------

def test_a_review_mark_and_a_note_are_lines_about_the_host(client, db_session, test_project):
    host = _host(db_session, test_project, "10.60.1.1")
    hosts = f"/api/v1/projects/{test_project.id}/hosts"
    assert client.post(f"{hosts}/{host.id}/follow", json={"status": "in_review"}).status_code == 200
    assert client.post(f"{hosts}/{host.id}/notes", json={"body": "looked at SMB"}).status_code in (200, 201)
    assert client.delete(f"{hosts}/{host.id}/follow").status_code in (200, 204)

    lines = _lines(db_session, test_project)
    assert [(line.action, line.summary) for line in lines] == [
        ("host_review_changed", "in review"), ("host_note_added", None), ("host_review_changed", "cleared"),
    ]
    assert {(line.subject_type, line.subject_id, line.subject_label) for line in lines} == {
        ("host", host.id, "10.60.1.1"),
    }
    # A note's text is never copied into the changelog.
    assert all("SMB" not in (line.summary or "") for line in lines)


# --- an agent's action is an agent's -----------------------------------------

def test_an_agents_action_is_marked_as_an_agents_under_its_operator(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.60.2.1")
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "notes"})
    assert started.status_code == 201, started.text
    key = {"X-API-Key": started.json()["api_key"]}
    assert client.post(
        f"/api/v1/agent/hosts/{host.id}/notes", headers=key, json={"body": "banner says nginx"},
    ).status_code in (200, 201)

    (line,) = _lines(db_session, test_project, "host_note_added")
    assert line.by_agent is True and line.agent_session_id is not None
    assert line.actor_user_id == test_user.id          # the operator whose authority it used


# --- the read ----------------------------------------------------------------

def test_the_changelog_reads_newest_first_filters_and_pages(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.60.3.1")
    base = f"/api/v1/projects/{test_project.id}"
    for n in range(3):
        client.post(f"{base}/findings", json={"title": f"F{n}", "severity": "low"})
    client.post(f"{base}/hosts/{host.id}/follow", json={"status": "reviewed"})

    page = client.get(f"{base}/changelog?limit=2").json()
    assert (page["total"], page["returned"], page["has_more"], page["trimmed"]) == (4, 2, True, False)
    first = page["items"][0]
    assert (first["action"], first["group"], first["words"]) == (
        "host_review_changed", "hosts", "changed the review status of",
    )
    assert first["actor"]["id"] == test_user.id and first["by_agent"] is False
    assert [person["id"] for person in page["people"]] == [test_user.id]
    assert "remediation" not in page["groups"]           # the installation does not track it

    findings = client.get(f"{base}/changelog?group=findings").json()
    assert findings["total"] == 3 and {line["group"] for line in findings["items"]} == {"findings"}
    assert client.get(f"{base}/changelog?by_agent=true").json()["total"] == 0
    assert client.get(f"{base}/changelog?actor_id={test_user.id}").json()["total"] == 4
    assert client.get(f"{base}/changelog?since=2999-01-01").json()["total"] == 0

    # A filter value that cannot be understood is refused, never dropped.
    assert client.get(f"{base}/changelog?group=nonsense").status_code == 422
    assert client.get(f"{base}/changelog?group=remediation").status_code == 422
    assert client.get(f"{base}/changelog?since=yesterday").status_code == 422


# --- the cap -----------------------------------------------------------------

def test_the_oldest_lines_beyond_the_cap_are_trimmed_and_the_read_says_so(
    client, db_session, test_project, test_user, monkeypatch,
):
    from app.core.config import settings

    pe.note_actor(db_session, user_id=test_user.id)
    for n in range(7):
        pe.record(db_session, test_project.id, "project_updated", summary=str(n))
    db_session.commit()

    monkeypatch.setattr(settings, "PROJECT_EVENTS_MAX_PER_PROJECT", 5)
    assert pe.trim_to_cap(db_session) == 2
    db_session.commit()
    assert [line.summary for line in _lines(db_session, test_project)] == ["2", "3", "4", "5", "6"]
    assert client.get(f"/api/v1/projects/{test_project.id}/changelog").json()["trimmed"] is True

    monkeypatch.setattr(settings, "PROJECT_EVENTS_MAX_PER_PROJECT", 0)        # no cap
    assert pe.trim_to_cap(db_session) == 0
