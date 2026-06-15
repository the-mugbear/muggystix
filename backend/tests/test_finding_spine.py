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


def test_terminal_status_requires_justification(client, test_project):
    """A terminal determination (false_positive/accepted_risk/remediated) needs
    a justification summary; working states (confirmed) do not."""
    fid = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "dispute", "severity": "high"},
    ).json()["id"]
    url = f"/api/v1/projects/{test_project.id}/findings/{fid}/status"

    for terminal in ("false_positive", "accepted_risk", "remediated"):
        r = client.post(url, json={"status": terminal})
        assert r.status_code == 422, f"{terminal}: {r.text}"
        assert "justification" in r.text.lower()

    # With a justification it succeeds.
    ok = client.post(url, json={"status": "false_positive", "summary": "duplicate of FND-12"})
    assert ok.status_code == 200, ok.text

    # Working states (confirmed) don't require one.
    fid2 = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "real", "severity": "high"},
    ).json()["id"]
    assert client.post(
        f"/api/v1/projects/{test_project.id}/findings/{fid2}/status",
        json={"status": "confirmed"},
    ).status_code == 200


def test_finding_comment_thread_create_list_and_threading(client, test_project):
    """A finding hosts its own comment/evidence thread: create a root comment,
    reply to it (threaded), and list returns both oldest-first with the reply
    pointing at the root."""
    fid = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "SMB signing disabled", "severity": "high"},
    ).json()["id"]
    base = f"/api/v1/projects/{test_project.id}/findings/{fid}/notes"

    root = client.post(base, json={"body": "Confirmed on DC01 — screenshot attached"})
    assert root.status_code == 200, root.text
    root_id = root.json()["id"]

    reply = client.post(base, json={"body": "Repro: smbclient -L //10.0.0.5", "parent_id": root_id})
    assert reply.status_code == 200, reply.text
    assert reply.json()["parent_id"] == root_id

    listing = client.get(base)
    assert listing.status_code == 200, listing.text
    bodies = [n["body"] for n in listing.json()]
    assert bodies == ["Confirmed on DC01 — screenshot attached", "Repro: smbclient -L //10.0.0.5"]


def test_finding_comment_cross_finding_threading_rejected(client, test_project):
    """A reply's parent_id must reference a comment on the SAME finding —
    threading across findings is rejected (mirrors the host-note guard)."""
    fid_a = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "A", "severity": "low"},
    ).json()["id"]
    fid_b = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "B", "severity": "low"},
    ).json()["id"]
    a_comment = client.post(
        f"/api/v1/projects/{test_project.id}/findings/{fid_a}/notes",
        json={"body": "on A"},
    ).json()["id"]

    bad = client.post(
        f"/api/v1/projects/{test_project.id}/findings/{fid_b}/notes",
        json={"body": "reply on B but parented to A", "parent_id": a_comment},
    )
    assert bad.status_code == 400, bad.text


def test_finding_comment_evidence_reaches_report(client, db_session, test_project, test_user):
    """A finding's comment thread (repro/rationale) is carried into the report
    — both the JSON findings list and the rendered HTML Evidence section."""
    from unittest.mock import MagicMock
    from app.api.v1.endpoints.reports import ReportGenerator

    host = _make_host(db_session, test_project.id, "10.20.0.7")
    db_session.commit()

    fid = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "Anonymous FTP", "severity": "medium", "host_ids": [host.id]},
    ).json()["id"]
    client.post(
        f"/api/v1/projects/{test_project.id}/findings/{fid}/notes",
        json={"body": "Repro: ftp 10.20.0.7, login anonymous / any-password"},
    )

    gen = ReportGenerator(db=db_session, current_user=MagicMock(), project_id=test_project.id)
    data = gen._findings_for_report([host])
    assert data, "finding should be in the report dataset"
    target = next(f for f in data if f["id"] == fid)
    assert any("anonymous" in c["body"].lower() for c in target["comments"])

    html_out = gen._html_findings_index(gen._findings_for_report([host]))
    assert "Evidence" in html_out
    assert "login anonymous" in html_out


def test_findings_sort_by_severity_rank(client, test_project):
    """sort=severity orders by rank (critical-first), not alphabetically; the
    default stays newest-first; dir=desc reverses to least-severe-first."""
    for sev in ("low", "critical", "medium"):
        client.post(f"/api/v1/projects/{test_project.id}/findings",
                    json={"title": f"{sev} one", "severity": sev})
    base = f"/api/v1/projects/{test_project.id}/findings"

    asc = client.get(f"{base}?sort=severity")
    assert asc.status_code == 200
    sevs = [f["severity"] for f in asc.json()["items"]]
    assert sevs == ["critical", "medium", "low"], sevs   # rank order, not a-z

    desc = client.get(f"{base}?sort=severity&dir=desc")
    assert [f["severity"] for f in desc.json()["items"]] == ["low", "medium", "critical"]

    # Default ordering is unchanged (newest-first) and an unknown sort is ignored.
    default = client.get(f"{base}?sort=bogus")
    assert default.status_code == 200
    assert len(default.json()["items"]) == 3


def test_findings_title_search(client, test_project):
    """?search= does a case-insensitive substring match on the title (§15), and
    the severity rollup respects it too."""
    base = f"/api/v1/projects/{test_project.id}/findings"
    client.post(base, json={"title": "SMB signing disabled", "severity": "high"})
    client.post(base, json={"title": "Anonymous FTP login", "severity": "low"})

    res = client.get(f"{base}?search=smb").json()
    assert [f["title"] for f in res["items"]] == ["SMB signing disabled"]
    assert res["total"] == 1
    # severity_counts is scoped to the search too.
    assert res["severity_counts"].get("high") == 1
    assert "low" not in res["severity_counts"]

    assert client.get(f"{base}?search=nomatch").json()["total"] == 0


def test_findings_status_group_filter(client, test_project):
    """status=active filters to the working set (open/confirmed/retest);
    status=resolved to terminal dispositions — the groups the dashboards
    drill against, kept in sync with the list."""
    base = f"/api/v1/projects/{test_project.id}/findings"
    # open (stays active)
    client.post(base, json={"title": "still open", "severity": "low"})
    # one we drive to a terminal (resolved) status
    rid = client.post(base, json={"title": "fixed one", "severity": "high"}).json()["id"]
    assert client.post(f"{base}/{rid}/status",
                       json={"status": "remediated", "summary": "patched"}).status_code == 200

    active = client.get(f"{base}?status=active").json()
    assert [f["title"] for f in active["items"]] == ["still open"]
    assert active["total"] == 1

    resolved = client.get(f"{base}?status=resolved").json()
    assert [f["title"] for f in resolved["items"]] == ["fixed one"]
    assert resolved["total"] == 1


def test_findings_unowned_filter(client, db_session, test_project, test_user):
    """unowned=true returns only findings with no owner (and overrides
    owner_id) — the ownership drill-down from posture."""
    base = f"/api/v1/projects/{test_project.id}/findings"
    owned = client.post(base, json={"title": "owned", "severity": "low",
                                    "owner_id": test_user.id}).json()
    client.post(base, json={"title": "nobody", "severity": "low"})
    assert owned["owner_id"] == test_user.id

    res = client.get(f"{base}?unowned=true").json()
    assert [f["title"] for f in res["items"]] == ["nobody"]
    # unowned overrides an owner_id passed alongside it.
    res2 = client.get(f"{base}?unowned=true&owner_id={test_user.id}").json()
    assert [f["title"] for f in res2["items"]] == ["nobody"]


def test_vuln_promote_preview_blast_radius(client, db_session, test_project, test_user):
    """The preview reports how many project hosts share the plugin_id (and so
    would be attached on promote) WITHOUT mutating anything — the §11 guard
    against a silent cross-host fan-out."""
    from app.db.models import Scan
    from app.db.models_vulnerability import (
        Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
    )
    scan = Scan(project_id=test_project.id, filename="nessus.xml", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()
    h1 = _make_host(db_session, test_project.id, "10.20.0.1")
    h2 = _make_host(db_session, test_project.id, "10.20.0.2")
    vulns = []
    for h in (h1, h2):
        v = Vulnerability(
            host_id=h.id, scan_id=scan.id, plugin_id="55555",
            title="SMB signing not required",
            severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS,
        )
        db_session.add(v)
        vulns.append(v)
    db_session.commit()

    base = f"/api/v1/projects/{test_project.id}/vulnerabilities/{vulns[0].id}"
    preview = client.get(f"{base}/promote-preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["affected_host_count"] == 2   # both hosts share plugin 55555
    assert body["already_promoted"] is False
    assert "10.20.0.1" in body["affected_host_sample"]
    # The preview is read-only — no finding was created.
    assert db_session.query(Finding).filter_by(source="scanner").count() == 0

    # After promoting, the preview reports it's already promoted.
    promoted = client.post(f"{base}/promote",
                           json={"vuln_id": vulns[0].id, "summary": "confirmed by hand"})
    assert promoted.status_code == 201, promoted.text
    assert promoted.json()["host_count"] == 2
    again = client.get(f"{base}/promote-preview").json()
    assert again["already_promoted"] is True
    assert again["finding_id"] == promoted.json()["id"]
