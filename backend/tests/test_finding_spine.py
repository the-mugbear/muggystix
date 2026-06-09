"""Tests for the Finding spine (foundation phase 5) — the parts most likely
to regress: the cross-tenant authz boundary, promote idempotency, and the
status-transition audit trail.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models import Annotation
from app.db.models_findings import Finding, FindingStatusHistory
from app.db.models_project import Project


def _make_host(db_session, project_id: int, ip: str) -> models.Host:
    host = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(host)
    db_session.flush()
    return host


def _make_note(db_session, host_id: int, user_id: int, body: str) -> Annotation:
    note = Annotation(host_id=host_id, user_id=user_id, body=body, note_type="finding")
    db_session.add(note)
    db_session.flush()
    return note


def test_promote_creates_finding_and_is_idempotent(client, db_session, test_project, test_user):
    """Promoting a note yields a finding from the note's first line; a second
    promote (double-click / retry) returns the SAME finding, never a dup."""
    host = _make_host(db_session, test_project.id, "10.10.0.5")
    note = _make_note(db_session, host.id, test_user.id, "SMB signing disabled\nanon enum works")
    db_session.commit()

    url = f"/api/v1/projects/{test_project.id}/annotations/{note.id}/promote"
    r1 = client.post(url, json={"severity": "high"})
    assert r1.status_code == 201, r1.text
    body1 = r1.json()
    assert body1["title"] == "SMB signing disabled"
    assert body1["severity"] == "high"
    assert body1["status"] == "confirmed"   # classifying a note = a confirmation
    assert body1["source"] == "note"
    assert host.id in [h["host_id"] for h in body1["hosts"]]

    r2 = client.post(url, json={"severity": "low"})  # different severity, same note
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == body1["id"]            # idempotent — same finding
    assert db_session.query(Finding).filter_by(source="note").count() == 1


def test_finding_cross_tenant_host_attach_rejected(client, db_session, test_project, test_user):
    """An analyst cannot attach a host from another project to a finding —
    it would corrupt the finding AND leak the foreign host's IP/hostname back
    in the response. The service guards every write path at one choke point."""
    own_host = _make_host(db_session, test_project.id, "10.10.0.6")
    other_project = Project(
        id=98765, name="other-tenant", slug="other-tenant",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_project)
    db_session.flush()
    foreign_host = _make_host(db_session, other_project.id, "192.168.99.99")
    db_session.commit()

    created = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "t", "severity": "medium", "host_ids": [own_host.id]},
    )
    assert created.status_code == 201, created.text
    fid = created.json()["id"]

    # Attaching the other project's host must be rejected (422), not leaked.
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/findings/{fid}/hosts",
        json={"host_ids": [foreign_host.id]},
    )
    assert resp.status_code == 422, resp.text
    assert "192.168.99.99" not in resp.text  # foreign IP never disclosed


def test_finding_status_transition_records_one_history_row(client, db_session, test_project, test_user):
    """Each real status change writes exactly one finding_status_history row;
    a no-op transition (same status) writes none."""
    created = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "needs retest", "severity": "low"},
    )
    fid = created.json()["id"]
    assert created.json()["status"] == "open"

    url = f"/api/v1/projects/{test_project.id}/findings/{fid}/status"
    assert client.post(url, json={"status": "confirmed"}).status_code == 200
    assert client.post(url, json={"status": "confirmed"}).status_code == 200  # no-op
    assert client.post(url, json={"status": "remediated", "summary": "patched"}).status_code == 200

    # create writes the initial open row; then confirmed + remediated = 3.
    rows = db_session.query(FindingStatusHistory).filter_by(finding_id=fid).count()
    assert rows == 3
