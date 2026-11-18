"""The project's changelog: reports, remediation, the project and its people,
agent sessions.

``test_project_changelog.py`` covers the writer, the read and the findings /
hosts lines.  These are the same kind of test for the rest: an action through
its real route leaves ONE line — who, what, to what — and never the text
someone wrote, a URL or a secret.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project, ProjectEvent
from app.db.models_remediation import RemediationPolicy

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"],
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(tmp_path / "report-templates"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "uploads" / "client_reports"))


def _lines(db, project, action=None):
    db.expire_all()
    q = db.query(ProjectEvent).filter(ProjectEvent.project_id == project.id)
    if action:
        q = q.filter(ProjectEvent.action == action)
    return q.order_by(ProjectEvent.id).all()


def _said(line) -> str:
    return f"{line.subject_label or ''} | {line.summary or ''}"


def _base(project) -> str:
    return f"/api/v1/projects/{project.id}"


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _user(db, user_id, username):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def _agent_key(client, project) -> tuple:
    started = client.post(f"{_base(project)}/assist/start", json={})
    assert started.status_code == 201, started.text
    return started.json()["agent_session_id"], {"X-API-Key": started.json()["api_key"]}


# --- reports -----------------------------------------------------------------

def test_a_reports_life_is_a_line_per_action_and_never_its_text(client, db_session, test_project, test_user):
    reports = f"{_base(test_project)}/client-reports"
    draft = client.post(reports, json={"kind": "full", "title": "Q4 assessment"})
    assert draft.status_code == 201, draft.text
    rid = draft.json()["id"]

    changed = client.patch(f"{reports}/{rid}", json={
        "title": "Q4 assessment, final", "settings": {"client_name": "Secret Client Ltd"},
    })
    assert changed.status_code == 200, changed.text
    # The same again changes nothing, so it says nothing.
    assert client.patch(f"{reports}/{rid}", json={
        "title": "Q4 assessment, final", "settings": {"client_name": "Secret Client Ltd"},
    }).status_code == 200
    assert client.delete(f"{reports}/{rid}").status_code == 204

    lines = _lines(db_session, test_project)
    assert [line.action for line in lines] == ["report_created", "report_changed", "report_deleted"]
    assert {(line.subject_type, line.subject_id, line.actor_user_id, line.by_agent) for line in lines} == {
        ("report", rid, test_user.id, False),
    }
    created, edited, deleted = lines
    assert created.subject_label == "Q4 assessment"
    assert (edited.subject_label, edited.summary) == ("Q4 assessment, final", "title, client name")
    assert deleted.subject_label == "Q4 assessment, final"
    assert all("Secret Client" not in _said(line) for line in lines)


def test_issuing_and_revising_a_report_are_lines_with_its_number(client, db_session, test_project):
    reports = f"{_base(test_project)}/client-reports"
    rid = client.post(reports, json={"kind": "full", "title": "The report"}).json()["id"]
    issued = client.post(f"{reports}/{rid}/issue")
    assert issued.status_code == 200, issued.text
    revision = client.post(f"{reports}/{rid}/revise")
    assert revision.status_code == 201, revision.text

    (issue,) = _lines(db_session, test_project, "report_issued")
    assert (issue.subject_id, issue.subject_label, issue.summary) == (rid, "The report", "report 1")
    (revise,) = _lines(db_session, test_project, "report_revised")
    assert (revise.subject_id, revise.summary) == (revision.json()["id"], "revises report 1")
    # The revision is a line of its own kind, not a second "created".
    assert len(_lines(db_session, test_project, "report_created")) == 1


def test_the_report_defaults_say_which_details_changed_whichever_door_wrote_them(
    client, db_session, test_project,
):
    reports = f"{_base(test_project)}/client-reports"
    text = "The estate is flat and nothing is segmented."
    assert client.patch(f"{reports}/profile", json={
        "executive_summary": text, "client_name": "Example Corp",
    }).status_code == 200
    # Sent again unchanged: nothing changed, nothing is recorded.
    assert client.patch(f"{reports}/profile", json={"client_name": "Example Corp"}).status_code == 200

    (line,) = _lines(db_session, test_project, "report_defaults_changed")
    assert (line.summary, line.count, line.subject_label) == ("executive summary, client name", 1, None)
    assert "flat" not in _said(line) and "Example Corp" not in _said(line)

    # The push-up from a draft writes the defaults AND the draft: one user
    # action, one line — the defaults'.
    rid = client.post(reports, json={"kind": "full", "title": "Draft"}).json()["id"]
    assert client.patch(f"{reports}/{rid}", json={"settings": {"classification": "Restricted"}}).status_code == 200
    before = len(_lines(db_session, test_project))
    pushed = client.post(f"{reports}/{rid}/promote", json={
        "values": {"classification": "Restricted"}, "expected": {"classification": "Restricted"},
        "expected_project": {"classification": None},
    })
    assert pushed.status_code == 200, pushed.text
    added = _lines(db_session, test_project)[before:]
    assert [(line.action, line.summary) for line in added] == [("report_defaults_changed", "classification")]


def test_a_report_image_is_a_line_and_a_refused_upload_is_not(client, db_session, test_project):
    images = f"{_base(test_project)}/client-reports/images"
    refused = client.post(images, files={"file": ("notes.txt", b"not an image", "text/plain")})
    assert refused.status_code == 400, refused.text
    assert _lines(db_session, test_project) == []

    stored = client.post(
        images, files={"file": ("diagram.png", PNG, "image/png")}, data={"caption": "The DMZ as built"},
    )
    assert stored.status_code == 201, stored.text
    (line,) = _lines(db_session, test_project)
    assert (line.action, line.subject_type) == ("report_image_added", "report_image")
    assert "DMZ" not in _said(line)


# --- the project and its people ----------------------------------------------

def test_members_coming_changing_and_going_are_lines_about_the_member(
    client, db_session, test_project, test_user,
):
    # Somebody else administers the project, so the member can be removed.
    members = f"{_base(test_project)}/members"
    assert client.post(members, json={"user_id": test_user.id, "role": "admin"}).status_code == 201
    dana = _user(db_session, 4101, "dana")
    assert client.post(members, json={"user_id": dana.id, "role": "analyst"}).status_code == 201
    assert client.put(f"{members}/{dana.id}", json={"role": "auditor"}).status_code == 200
    # The same role again is no change.
    assert client.put(f"{members}/{dana.id}", json={"role": "auditor"}).status_code == 200
    assert client.delete(f"{members}/{dana.id}").status_code == 200

    lines = [line for line in _lines(db_session, test_project) if line.subject_id == dana.id]
    assert [(line.action, line.subject_type, line.subject_label, line.summary) for line in lines] == [
        ("member_added", "member", "dana", "analyst"),
        ("member_role_changed", "member", "dana", "analyst → auditor"),
        ("member_removed", "member", "dana", "auditor"),
    ]
    assert {(line.actor_user_id, line.by_agent) for line in lines} == {(test_user.id, False)}


def test_a_project_update_names_the_fields_and_archiving_is_its_own_line(
    client, db_session, test_project, test_user,
):
    other = Project(name="another", slug="another")          # archiving needs one left active
    db_session.add(other)
    db_session.commit()
    url = f"/api/v1/projects/{test_project.id}"
    assert client.put(url, json={"description": "The client's Vienna office."}).status_code == 200
    assert client.put(url, json={"description": "The client's Vienna office."}).status_code == 200   # no change
    assert client.put(url, json={"status": "archived"}).status_code == 200
    assert client.put(url, json={"status": "active"}).status_code == 200
    assert client.patch(f"{url}/ingest-settings", json={"skip_informational_findings": True}).status_code == 200
    assert client.patch(f"{url}/ingest-settings", json={"skip_informational_findings": True}).status_code == 200

    lines = _lines(db_session, test_project)
    assert [(line.action, line.summary) for line in lines] == [
        ("project_updated", "description"),
        ("project_archived", "status"),
        ("project_unarchived", "status"),
        ("ingest_settings_changed", "informational findings: skipped"),
    ]
    assert {(line.subject_type, line.subject_id, line.actor_user_id) for line in lines} == {
        ("project", test_project.id, test_user.id),
    }
    assert all("Vienna" not in _said(line) for line in lines)
    # Nothing of it reached the other project's changelog.
    assert _lines(db_session, other) == []


def test_a_webhooks_line_carries_its_name_and_never_its_address_or_secret(client, db_session, test_project):
    hooks = f"{_base(test_project)}/webhooks"
    made = client.post(hooks, json={
        "name": "Ticketing", "url": "https://hooks.example.com/t/abc123", "secret": "s3cr3t-signing-key",
    })
    assert made.status_code == 201, made.text
    hook = made.json()["id"]
    assert client.patch(f"{hooks}/{hook}", json={"url": "https://hooks.example.com/t/zzz999"}).status_code == 200
    assert client.patch(f"{hooks}/{hook}", json={"name": "Ticketing"}).status_code == 200      # no change
    assert client.delete(f"{hooks}/{hook}").status_code == 204

    lines = _lines(db_session, test_project)
    assert [(line.action, line.subject_type, line.subject_id, line.subject_label, line.summary) for line in lines] == [
        ("webhook_changed", "webhook", hook, "Ticketing", "created"),
        ("webhook_changed", "webhook", hook, "Ticketing", "changed"),
        ("webhook_changed", "webhook", hook, "Ticketing", "deleted"),
    ]
    for line in lines:
        assert "hooks.example.com" not in _said(line) and "s3cr3t" not in _said(line)


# --- agent sessions ----------------------------------------------------------

def test_starting_resuming_and_ending_a_session_are_the_persons_lines(client, db_session, test_project, test_user):
    sid, _key = _agent_key(client, test_project)
    sessions = f"{_base(test_project)}/agent-sessions"
    assert client.post(f"{sessions}/{sid}/resume").status_code == 200
    assert client.post(f"{sessions}/{sid}/end").status_code == 204
    assert client.post(f"{sessions}/{sid}/end").status_code == 409            # already ended: nothing more

    lines = _lines(db_session, test_project)
    assert [line.action for line in lines] == [
        "agent_session_started", "agent_session_resumed", "agent_session_ended",
    ]
    assert {(line.subject_type, line.subject_id, line.actor_user_id, line.by_agent) for line in lines} == {
        ("agent_session", sid, test_user.id, False),
    }


def test_an_agent_ending_its_own_session_is_the_agents_line(client, db_session, test_project, test_user):
    sid, key = _agent_key(client, test_project)
    ended = client.post("/api/v1/agent/session/end", headers=key, json={"notes": "found the domain admin hash"})
    assert ended.status_code == 200, ended.text

    (start,) = _lines(db_session, test_project, "agent_session_started")
    assert start.by_agent is False
    (line,) = _lines(db_session, test_project, "agent_session_ended")
    assert (line.by_agent, line.agent_session_id, line.subject_id, line.actor_user_id) == (
        True, sid, sid, test_user.id,
    )
    assert "hash" not in _said(line)


def test_a_session_ended_because_its_operator_left_is_not_somebody_ending_it(
    client, db_session, test_project, test_user,
):
    members = f"{_base(test_project)}/members"
    assert client.post(members, json={"user_id": test_user.id, "role": "admin"}).status_code == 201
    erin = _user(db_session, 4102, "erin")
    assert client.post(members, json={"user_id": erin.id, "role": "admin"}).status_code == 201
    _agent_key(client, test_project)
    # The signed-in admin is removed from the project: their session ends with them.
    assert client.delete(f"{members}/{test_user.id}").status_code == 200
    assert _lines(db_session, test_project, "agent_session_ended") == []
    assert len(_lines(db_session, test_project, "member_removed")) == 1


def test_an_agents_host_correction_is_a_line_naming_the_fields(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.61.0.1")
    sid, key = _agent_key(client, test_project)
    url = f"/api/v1/agent/hosts/{host.id}"
    fixed = client.patch(url, headers=key, json={"os_name": "Windows Server 2019", "hostname": "dc01.corp.example"})
    assert fixed.status_code == 200, fixed.text
    assert client.patch(url, headers=key, json={"os_name": "Windows Server 2019"}).status_code == 200   # no change

    (line,) = _lines(db_session, test_project, "host_corrected")
    assert (line.subject_type, line.subject_id, line.subject_label) == ("host", host.id, "10.61.0.1")
    assert line.summary == "hostname, OS"
    assert (line.by_agent, line.agent_session_id, line.actor_user_id) == (True, sid, test_user.id)
    assert "Windows" not in _said(line) and "dc01" not in _said(line)


# --- remediation -------------------------------------------------------------

@pytest.fixture
def tracking(db_session):
    policy = RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                               days_low=120, days_info=None, due_soon_days=7)
    db_session.add(policy)
    db_session.commit()
    return policy


def _finding_on_hosts(db, project, ips):
    finding = Finding(project_id=project.id, title="SMB signing not required", severity="medium",
                      status="confirmed", source="manual")
    db.add(finding)
    db.flush()
    links = []
    for ip in ips:
        host = models.Host(project_id=project.id, ip_address=ip, state="up")
        db.add(host)
        db.flush()
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(link)
        links.append(link)
    db.commit()
    return [(link.id, link.host_id) for link in links]


def test_remediation_entries_and_a_bulk_apply_are_lines_without_their_text(
    client, db_session, test_project, test_user, tracking,
):
    (first, first_host), (second, _), (third, _) = _finding_on_hosts(
        db_session, test_project, ["10.62.0.1", "10.62.0.2", "10.62.0.3"],
    )
    remediation = f"{_base(test_project)}/remediation"

    note = client.post(f"{remediation}/events", json={
        "host_id": first_host, "finding_host_id": first, "body": "Roger says the patch is booked for Friday.",
        "request_key": "note-1",
    })
    assert note.status_code == 201, note.text
    event = note.json()["id"]
    # The same request again is the stored note: not a second entry, not a second line.
    assert client.post(f"{remediation}/events", json={
        "host_id": first_host, "finding_host_id": first, "body": "Roger says the patch is booked for Friday.",
        "request_key": "note-1",
    }).status_code == 200
    assert client.patch(f"{remediation}/events/{event}", json={"body": "Booked for Monday instead."}).status_code == 200
    assert client.patch(f"{remediation}/events/{event}", json={"body": "Booked for Monday instead."}).status_code == 200
    assert client.delete(f"{remediation}/events/{event}").status_code == 204

    # A dry run writes nothing; the real call is ONE line for the rows it wrote to.
    rows = [{"finding_host_id": fh, "contact_email": "roger@example.com"} for fh in (first, second, third)]
    assert client.post(f"{remediation}/apply", json={"rows": rows, "dry_run": True}).status_code == 200
    assert client.post(f"{remediation}/apply", json={"rows": rows}).status_code == 200
    assert client.post(f"{remediation}/apply", json={"rows": rows}).status_code == 200        # nothing left to change

    lines = _lines(db_session, test_project)
    assert [(line.action, line.subject_id, line.subject_label, line.summary, line.count) for line in lines] == [
        ("remediation_recorded", event, "SMB signing not required", None, 1),
        ("remediation_changed", event, "SMB signing not required", "note", 1),
        ("remediation_removed", event, "SMB signing not required", None, 1),
        ("remediation_applied", None, None, "3 records", 3),
    ]
    assert {(line.subject_type, line.actor_user_id, line.by_agent) for line in lines} == {
        ("remediation", test_user.id, False),
    }
    for line in lines:
        assert "Roger" not in _said(line) and "Monday" not in _said(line) and "roger@" not in _said(line)


def test_an_agents_remediation_entry_is_the_agents_line(client, db_session, test_project, test_user, tracking):
    ((link, host_id),) = _finding_on_hosts(db_session, test_project, ["10.62.1.1"])
    # The key's operator must administer the project (a global admin alone does not write remediation).
    assert client.post(
        f"{_base(test_project)}/members", json={"user_id": test_user.id, "role": "admin"},
    ).status_code == 201
    sid, key = _agent_key(client, test_project)
    noted = client.post("/api/v1/agent/remediation/events", headers=key, json={
        "host_id": host_id, "finding_host_id": link, "body": "The contact confirmed the owner.",
    })
    assert noted.status_code == 201, noted.text
    (line,) = _lines(db_session, test_project, "remediation_recorded")
    assert (line.by_agent, line.agent_session_id, line.actor_user_id) == (True, sid, test_user.id)


def test_the_changelog_lists_remediation_lines_only_while_tracking_is_on(
    client, db_session, test_project, tracking,
):
    ((link, host_id),) = _finding_on_hosts(db_session, test_project, ["10.62.2.1"])
    assert client.post(f"{_base(test_project)}/remediation/events", json={
        "host_id": host_id, "finding_host_id": link, "body": "Chased by phone.",
    }).status_code == 201
    assert client.put(f"/api/v1/projects/{test_project.id}", json={"description": "x"}).status_code == 200

    url = f"{_base(test_project)}/changelog"
    on = client.get(url).json()
    assert [line["action"] for line in on["items"]] == ["project_updated", "remediation_recorded"]
    assert "remediation" in on["groups"]
    assert client.get(f"{url}?group=remediation").json()["total"] == 1

    tracking.enabled = False
    db_session.commit()
    off = client.get(url).json()
    assert [line["action"] for line in off["items"]] == ["project_updated"]
    assert off["total"] == 1 and "remediation" not in off["groups"]
    # Still stored: the switch may be turned on again.
    assert len(_lines(db_session, test_project, "remediation_recorded")) == 1
