"""v2.379.0 — a finding's report text and the report-image opt-in.

Report text (description, impact, recommendation, references, steps to
reproduce, CVSS) is authored content: the finding's author, a project admin or
a global admin edits it — the rename rule.  It is seeded once at promotion from
the scanner row or the source note and never rewritten by a later
corroborating scanner.  A 3.x / 2.0 vector decides the score.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import Annotation, NoteAttachment
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.main import app
from app.services.cvss_service import CvssError, normalize_cvss, score_vector
from app.services.report_text import references_markdown


# --- CVSS ---------------------------------------------------------------------

@pytest.mark.parametrize("vector,expected", [
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
    ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 7.8),
    ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N", 5.9),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
    ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N", 5.3),
    # Temporal metrics are allowed and do not change the BASE score.
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:P/RL:O", 9.8),
    ("AV:N/AC:L/Au:N/C:P/I:P/A:P", 7.5),
    ("AV:N/AC:L/Au:N/C:C/I:C/A:C", 10.0),
    ("CVSS2#AV:N/AC:M/Au:N/C:P/I:N/A:N", 4.3),
])
def test_known_vectors_score_as_the_specification_says(vector, expected):
    assert score_vector(vector)[1] == expected


def test_a_cvss4_vector_is_checked_but_keeps_the_entered_score():
    v4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    assert normalize_cvss(v4, 9.3) == (v4, 9.3)
    with pytest.raises(CvssError):
        normalize_cvss("CVSS:4.0/AV:N/AC:L", 9.3)


@pytest.mark.parametrize("vector", [
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H",        # missing A
    "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",    # bad value
    "CVSS:3.1/AV:N/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "not a vector",
])
def test_unreadable_vectors_are_refused_strictly_and_dropped_when_seeding(vector):
    with pytest.raises(CvssError):
        normalize_cvss(vector, None)
    assert normalize_cvss(vector, 6.5, strict=False) == (None, 6.5)


def test_a_vector_decides_the_score_over_a_typed_one():
    assert normalize_cvss("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 2.0)[1] == 9.8


def test_score_range_is_enforced():
    with pytest.raises(CvssError):
        normalize_cvss(None, 11)
    assert normalize_cvss(None, 7.25) == (None, 7.2)


def test_scanner_references_become_a_markdown_list():
    assert references_markdown('["https://a.example/1", "CVE-2020-1"]') == (
        "- https://a.example/1\n- CVE-2020-1"
    )
    assert references_markdown("see vendor advisory") == "see vendor advisory"
    assert references_markdown(None) is None


# --- API ------------------------------------------------------------------------

def _member(db_session, project, user_id: int, username: str, role: ProjectRole) -> User:
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


@pytest.fixture
def act_as():
    def _act(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


@pytest.fixture
def people(db_session, test_project):
    return {
        "alice": _member(db_session, test_project, 301, "alice", ProjectRole.ANALYST),
        "bob": _member(db_session, test_project, 302, "bob", ProjectRole.ANALYST),
        "padmin": _member(db_session, test_project, 303, "padmin", ProjectRole.ADMIN),
    }


def _url(project, fid=None):
    base = f"/api/v1/projects/{project.id}/findings"
    return base if fid is None else f"{base}/{fid}"


def test_report_text_is_the_authors_and_triage_stays_open(client, test_project, people, act_as):
    act_as(people["alice"])
    f = client.post(_url(test_project), json={"title": "Weak TLS", "severity": "medium"}).json()
    url = _url(test_project, f["id"])
    r = client.patch(url, json={
        "description": "  TLS 1.0 is **enabled**.  ",
        "recommendation": "Disable TLS 1.0.",
        "cvss_vector": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N",
        "cvss_score": 1.0,
    })
    assert r.status_code == 200, r.text
    text = r.json()["report_text"]
    assert text["description"] == "TLS 1.0 is **enabled**."
    assert text["cvss_score"] == 5.9 and text["cvss_score_from_vector"] is True

    act_as(people["bob"])
    r = client.patch(url, json={"description": "Bob's words"})
    assert r.status_code == 403, r.text
    assert "report text" in r.json()["detail"]
    # Triage, plus the unchanged text resent by a form, is not refused.
    r = client.patch(url, json={
        "severity": "low", "description": "TLS 1.0 is **enabled**.",
        "cvss_vector": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N",
    })
    assert r.status_code == 200, r.text
    assert r.json()["severity"] == "low"

    act_as(people["padmin"])
    r = client.patch(url, json={"description": "", "impact": "Traffic can be read."})
    assert r.status_code == 200, r.text
    assert r.json()["report_text"]["description"] is None
    assert r.json()["report_text"]["impact"] == "Traffic can be read."


def test_an_invalid_vector_is_refused(client, test_project):
    f = client.post(_url(test_project), json={"title": "X", "severity": "low"}).json()
    r = client.patch(_url(test_project, f["id"]), json={"cvss_vector": "CVSS:3.1/AV:N"})
    assert r.status_code == 422, r.text


def test_the_list_leaves_report_text_out(client, test_project):
    f = client.post(_url(test_project), json={"title": "X", "severity": "low"}).json()
    client.patch(_url(test_project, f["id"]), json={"description": "long text"})
    listed = client.get(_url(test_project)).json()["items"][0]
    assert listed["report_text"] is None
    assert client.get(_url(test_project, f["id"])).json()["report_text"]["description"] == "long text"


def _vuln(db_session, project, ip, **kw):
    scan = models.Scan(project_id=project.id, filename=f"{ip}.nessus", tool_name="nessus")
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    fields = dict(
        host_id=host.id, scan_id=scan.id, plugin_id="57608", title="SMB Signing not required",
        severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NESSUS,
    )
    fields.update(kw)
    vuln = Vulnerability(**fields)
    db_session.add(vuln)
    db_session.commit()
    return vuln


def test_scanner_promotion_seeds_once_and_corroboration_never_rewrites(client, db_session, test_project):
    first = _vuln(
        db_session, test_project, "10.30.0.1",
        description="Signing is not required on the SMB server.",
        solution="Require message signing.",
        references='["https://learn.example/smb"]',
        cvss_vector="CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N", cvss_score=5.0,
    )
    r = client.post(
        f"/api/v1/projects/{test_project.id}/vulnerabilities/{first.id}/promote",
        json={"vuln_id": first.id},
    )
    assert r.status_code == 201, r.text
    text = r.json()["report_text"]
    assert text["description"] == "Signing is not required on the SMB server."
    assert text["recommendation"] == "Require message signing."
    assert text["references"] == "- https://learn.example/smb"
    # The vector decides: 5.3, not the scanner's 5.0.
    assert text["cvss_score"] == 5.3
    fid = r.json()["id"]

    client.patch(_url(test_project, fid), json={"description": "Edited by the author."})
    # A second scanner's row for the same issue joins the finding.
    second = _vuln(
        db_session, test_project, "10.30.0.2", source=VulnerabilitySource.OPENVAS,
        plugin_id="1.3.6.1", description="OpenVAS says otherwise.", solution="Other advice.",
    )
    r = client.post(
        f"/api/v1/projects/{test_project.id}/vulnerabilities/{second.id}/promote",
        json={"vuln_id": second.id},
    )
    assert r.json()["id"] == fid
    assert r.json()["report_text"]["description"] == "Edited by the author."
    assert r.json()["report_text"]["recommendation"] == "Require message signing."


def test_note_promotion_seeds_the_description_from_the_note(client, db_session, test_project, test_user):
    host = models.Host(project_id=test_project.id, ip_address="10.30.0.9", state="up")
    db_session.add(host)
    db_session.flush()
    note = Annotation(host_id=host.id, user_id=test_user.id, body="Anonymous LDAP bind\nReturns the full tree.", note_type="finding")
    db_session.add(note)
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{test_project.id}/annotations/{note.id}/promote", json={"severity": "high"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Anonymous LDAP bind"
    assert r.json()["report_text"]["description"] == "Anonymous LDAP bind\nReturns the full tree."


def test_report_image_opt_in_is_the_uploaders_or_an_admins(client, db_session, test_project, people, act_as):
    host = models.Host(project_id=test_project.id, ip_address="10.30.0.20", state="up")
    db_session.add(host)
    db_session.flush()
    note = Annotation(host_id=host.id, user_id=people["alice"].id, body="proof", note_type="finding")
    db_session.add(note)
    db_session.flush()
    att = NoteAttachment(
        annotation_id=note.id, project_id=test_project.id, filename="proof.png",
        content_type="image/png", size_bytes=10, storage_path="x/proof.png",
        uploaded_by_id=people["alice"].id,
    )
    db_session.add(att)
    db_session.commit()
    assert att.include_in_report is False
    url = f"/api/v1/projects/{test_project.id}/hosts/notes/attachments/{att.id}"

    act_as(people["bob"])
    assert client.patch(url, json={"include_in_report": True}).status_code == 403

    act_as(people["alice"])
    r = client.patch(url, json={"include_in_report": True})
    assert r.status_code == 200, r.text
    assert r.json()["include_in_report"] is True

    act_as(people["padmin"])
    r = client.patch(url, json={"include_in_report": False})
    assert r.status_code == 200 and r.json()["include_in_report"] is False
