"""A finding made from a note names the host its evidence thread is on.

Review 2026-10-22, N1: the finding page guessed the source note's host as the
first endpoint (``finding.hosts[0]``) — wrong once that endpoint is removed,
or when the note is on a host that is not the first endpoint.
``GET /findings/{id}`` returns ``evidence_host_id``, the NOTE's host; a list
row leaves it null (it is a single-finding field).
"""
from app.db import models
from app.db.models import Annotation
from app.db.models_findings import Finding, FindingHost


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    return host


def _note_finding(db, project, user, note_host, endpoint_hosts):
    root = Annotation(host_id=note_host.id, user_id=user.id, body="anonymous FTP accepted")
    db.add(root)
    db.flush()
    root.thread_root_id = root.id
    finding = Finding(
        project_id=project.id, title="Anonymous FTP", severity="high", status="open",
        source="note", evidence_annotation_id=root.id, created_by_id=user.id,
    )
    db.add(finding)
    db.flush()
    for host in endpoint_hosts:
        db.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db.commit()
    return finding.id


def test_the_evidence_host_is_the_notes_host_not_the_first_endpoint(client, db_session, test_project, test_user):
    first = _host(db_session, test_project, "10.0.0.1")
    note_host = _host(db_session, test_project, "10.0.0.2")
    fid = _note_finding(db_session, test_project, test_user, note_host, [first, note_host])

    r = client.get(f"/api/v1/projects/{test_project.id}/findings/{fid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hosts"][0]["host_id"] == first.id
    assert body["evidence_host_id"] == note_host.id


def test_the_evidence_host_stays_when_its_endpoint_is_gone(client, db_session, test_project, test_user):
    note_host = _host(db_session, test_project, "10.0.0.2")
    other = _host(db_session, test_project, "10.0.0.3")
    # The note's host is no longer an endpoint of the finding.
    fid = _note_finding(db_session, test_project, test_user, note_host, [other])

    body = client.get(f"/api/v1/projects/{test_project.id}/findings/{fid}").json()
    assert body["evidence_host_id"] == note_host.id


def test_a_finding_with_no_source_note_has_no_evidence_host(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.0.0.4")
    finding = Finding(project_id=test_project.id, title="Manual", severity="low", status="open",
                      source="manual", created_by_id=test_user.id)
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/findings/{finding.id}").json()
    assert body["evidence_host_id"] is None


def test_a_list_row_leaves_it_null(client, db_session, test_project, test_user):
    note_host = _host(db_session, test_project, "10.0.0.2")
    _note_finding(db_session, test_project, test_user, note_host, [note_host])

    r = client.get(f"/api/v1/projects/{test_project.id}/findings")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and all(row["evidence_host_id"] is None for row in items)
