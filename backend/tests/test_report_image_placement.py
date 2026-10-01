"""Images in a finding's report text: captions, placement, and what protects them.

A finding's image, ticked "In report", is captioned by the person who attached
it and may be PLACED inside one of that finding's written sections with
``![caption](evidence:<id>)``.  A ticked image no section places prints in the
trailing evidence block.  These tests pin:

* what counts as a reference, and which images a section may show (the
  dataset builder decides — the renderer shows nothing else);
* who may caption (the tick's rule: the uploader, a project admin, a global
  admin) and the caption's bounds;
* that an image a section places cannot be deleted or un-ticked, nor the
  comment that holds it (409, naming the section);
* that issuing copies the bytes into the report's own storage, so an image
  deleted afterwards cannot fail the issued report's render;
* that an agent's proposed section references only that finding's images, and
  that accepting never ticks one.

The rendered result (figures inside sections, numbered in document order,
hostile references) is in ``test_quarto_render.py``, which needs Quarto.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportImage
from app.main import app
from app.services import report_images
from app.services.client_report_service import ClientReportService

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    folder = root / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"],
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "uploads" / "client_reports"))
    return tmp_path / "uploads"


@pytest.fixture
def act_as():
    def _act(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


def _member(db, project, user_id, username, role):
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


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _finding(db, project, title="SMB relay", status="confirmed", **text):
    host = models.Host(project_id=project.id, ip_address=f"10.77.0.{db.query(models.Host).count() + 1}", state="up")
    db.add(host)
    db.flush()
    f = Finding(project_id=project.id, title=title, severity="high", status=status, source="manual", **text)
    db.add(f)
    db.flush()
    db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db.commit()
    return f


def _comment(client, project, finding, body="proof"):
    r = client.post(f"{_base(project)}/findings/{finding.id}/notes", json={"body": body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _image(client, project, finding, *, name="shot.png", data=PNG, ctype="image/png", ticked=True, note_id=None):
    note_id = note_id or _comment(client, project, finding)
    r = client.post(
        f"{_base(project)}/findings/{finding.id}/notes/{note_id}/attachments",
        files={"file": (name, data, ctype)},
    )
    assert r.status_code == 200, r.text
    att = r.json()
    if ticked:
        assert _patch(client, project, att["id"], include_in_report=True).status_code == 200
    return att["id"]


def _patch(client, project, attachment_id, **body):
    return client.patch(f"{_base(project)}/hosts/notes/attachments/{attachment_id}", json=body)


def _delete(client, project, attachment_id):
    return client.delete(f"{_base(project)}/hosts/notes/attachments/{attachment_id}")


def _set_text(client, project, finding, **fields):
    r = client.patch(f"{_base(project)}/findings/{finding.id}", json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def _images(client, project, finding):
    r = client.get(f"{_base(project)}/findings/{finding.id}/images")
    assert r.status_code == 200, r.text
    return {img["id"]: img for img in r.json()["items"]}


def _dataset(db, client, project):
    r = client.post(f"{_base(project)}/client-reports", json={"kind": "full"})
    assert r.status_code == 201, r.text
    db.expire_all()
    dataset, _, summary = ClientReportService(db).build(db.get(Report, r.json()["id"]))
    return r.json(), dataset, summary


# ---------------------------------------------------------------------------
# What a reference is
# ---------------------------------------------------------------------------

def test_a_reference_is_a_markdown_image_whose_target_is_evidence_and_an_id():
    ids = report_images.referenced_ids
    assert ids("Before ![The relayed session](evidence:57) after") == [57]
    assert ids("![](evidence:3)\n\n![again](evidence:3) ![x](evidence:9)") == [3, 9]      # once each, in order
    assert ids('![t](evidence:4 "a title")') == [4]
    assert ids(r"![a \] bracket](evidence:5)") == [5]
    assert ids("| a | ![cell](evidence:6) |\n- ![item](evidence:7)") == [6, 7]
    # Not references: a link, another scheme, a path, a non-number, raw HTML.
    for text in (
        "[text](evidence:1)", "![x](https://example.com/evidence:1)", "![x](evidence/1.png)",
        "![x](evidence:abc)", "![x](evidence:1.png)", '<img src="evidence:1">', "evidence:1", None, "",
    ):
        assert ids(text) == [], text
    # Markdown the report's reader has no attribute syntax for: the braces
    # are text after the image, and the image is a reference.
    assert ids("![x](evidence:1){.c onerror=alert(1)}") == [1]


def test_a_caption_is_one_line_of_plain_text_and_bounded():
    assert report_images.clean_caption("  two\nlines\t here \x00 ") == "two lines here"
    assert report_images.clean_caption("   ") is None and report_images.clean_caption(None) is None
    assert len(report_images.clean_caption("c" * report_images.CAPTION_MAX)) == 2000
    with pytest.raises(ValueError, match="at most 2000"):
        report_images.clean_caption("c" * (report_images.CAPTION_MAX + 1))


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def test_the_uploader_or_an_admin_captions_an_image_and_nobody_else(client, db_session, test_project, act_as, test_user):
    ana = _member(db_session, test_project, 501, "ana", ProjectRole.ANALYST)
    ben = _member(db_session, test_project, 502, "ben", ProjectRole.ANALYST)
    adm = _member(db_session, test_project, 503, "adm", ProjectRole.ADMIN)
    aud = _member(db_session, test_project, 504, "aud", ProjectRole.AUDITOR)
    finding = _finding(db_session, test_project)

    act_as(ana)
    att = _image(client, test_project, finding, ticked=False)
    r = _patch(client, test_project, att, caption="  The relayed\nsession  ")
    assert r.status_code == 200, r.text
    assert r.json()["caption"] == "The relayed session" and r.json()["include_in_report"] is False

    act_as(ben)   # another analyst: neither the tick nor the caption is theirs
    assert _patch(client, test_project, att, caption="mine now").status_code == 403
    assert _patch(client, test_project, att, include_in_report=True).status_code == 403
    listed = _images(client, test_project, finding)[att]
    assert listed["caption"] == "The relayed session" and listed["can_edit"] is False

    act_as(aud)   # a reader: the route needs an analyst
    assert _patch(client, test_project, att, caption="x").status_code == 403

    act_as(adm)   # a project admin may, as for the tick
    r = _patch(client, test_project, att, caption="Admin's wording", include_in_report=True)
    assert r.status_code == 200 and r.json()["caption"] == "Admin's wording" and r.json()["include_in_report"] is True
    assert _images(client, test_project, finding)[att]["can_edit"] is True

    act_as(test_user)   # a global admin, with no membership row
    assert _patch(client, test_project, att, caption="").json()["caption"] is None       # cleared
    assert _patch(client, test_project, att, caption="c" * 2000).status_code == 200      # the bound is inclusive
    over = _patch(client, test_project, att, caption="c" * 2001)
    assert over.status_code == 422 and "at most 2000" in over.json()["detail"]
    assert _patch(client, test_project, att).status_code == 422                           # nothing sent
    assert _patch(client, test_project, att, include_in_report=None).status_code == 422
    # The tick alone leaves the caption alone.
    assert _patch(client, test_project, att, include_in_report=False).json()["caption"] == "c" * 2000


def test_the_caption_travels_with_the_comment_thread(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    att = _image(client, test_project, finding)
    _patch(client, test_project, att, caption="What it shows")
    notes = client.get(f"{_base(test_project)}/findings/{finding.id}/notes").json()
    assert notes[0]["attachments"][0]["caption"] == "What it shows"


# ---------------------------------------------------------------------------
# Where an image is placed
# ---------------------------------------------------------------------------

def test_the_findings_images_say_where_each_is_placed(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    other = _finding(db_session, test_project, "Another finding")
    a = _image(client, test_project, finding)
    b = _image(client, test_project, finding)
    c = _image(client, test_project, finding, ticked=False)
    w = _image(client, test_project, finding, name="modern.webp", data=WEBP, ctype="image/webp")
    foreign = _image(client, test_project, other)
    _set_text(
        client, test_project, finding,
        description=f"Intro\n\n![one](evidence:{a})\n\n![again](evidence:{a}) and ![unticked](evidence:{c})",
        impact=f"![](evidence:{a}) ![theirs](evidence:{foreign}) ![gone](evidence:999999)",
    )
    images = _images(client, test_project, finding)
    assert set(images) == {a, b, c, w}                      # this finding's only
    assert images[a]["placed_in"] == ["description", "impact"] and images[a]["in_report"] is True
    assert images[b]["placed_in"] == [] and images[b]["in_report"] is True
    assert images[c]["placed_in"] == ["description"] and images[c]["in_report"] is False
    assert images[w]["printable"] is False and images[a]["printable"] is True
    assert client.get(f"{_base(test_project)}/findings/{finding.id}/images").json()["caption_max"] == 2000
    assert _images(client, test_project, other)[foreign]["placed_in"] == []   # a reference elsewhere places nothing


def test_the_report_places_only_this_findings_ticked_images_and_keeps_the_rest_under_evidence(
    client, db_session, test_project,
):
    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    other = _finding(db_session, test_project, "Another finding", description="d", impact="i", recommendation="r")
    placed = _image(client, test_project, finding, name="relay.png")
    trailing = _image(client, test_project, finding, name="banner.png")
    unticked = _image(client, test_project, finding, ticked=False)
    webp = _image(client, test_project, finding, name="modern.webp", data=WEBP, ctype="image/webp")
    foreign = _image(client, test_project, other, name="theirs.png")
    _patch(client, test_project, placed, caption="The relayed session")
    _set_text(
        client, test_project, finding,
        description=(
            f"![alt](evidence:{placed}) ![alt](evidence:{placed}) ![u](evidence:{unticked}) "
            f"![w](evidence:{webp}) ![f](evidence:{foreign}) ![n](evidence:424242)"
        ),
        steps_to_reproduce=f"1. Relay\n\n![](evidence:{placed})",
    )
    _, dataset, summary = _dataset(db_session, client, test_project)
    by_title = {f["title"]: f for f in dataset["findings"]}
    f = by_title["SMB relay"]

    # Placed: the one ticked, printable image of THIS finding — in both
    # sections that reference it — and nothing else, whatever the text says.
    entry = {"attachment_id": placed, "file": f"evidence/{placed}.png", "caption": "The relayed session"}
    assert f["placed"] == {"description": {str(placed): entry}, "steps_to_reproduce": {str(placed): entry}}
    # Every ticked image, with where it is placed; the caption falls back to
    # the file name.
    assert [(i["attachment_id"], i["caption"], i["placed_in"]) for i in f["images"]] == [
        (placed, "The relayed session", ["description", "steps_to_reproduce"]),
        (trailing, "banner.png", []),
    ]
    # The trailing evidence block: only what no section places.
    assert [i["attachment_id"] for i in f["evidence"]] == [trailing]
    # The other finding keeps its own image, unplaced: a reference in another
    # finding's text is not a placement.
    assert by_title["Another finding"]["placed"] == {}
    assert [i["attachment_id"] for i in by_title["Another finding"]["evidence"]] == [foreign]
    assert (summary["images"], summary["images_placed"], summary["images_unplaced"], summary["images_skipped"]) == (3, 1, 2, 1)


def test_the_block_is_empty_when_every_ticked_image_is_placed(client, db_session, test_project):
    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    a = _image(client, test_project, finding)
    b = _image(client, test_project, finding)
    _set_text(client, test_project, finding, description=f"![](evidence:{a})", impact=f"![](evidence:{b})")
    _, dataset, summary = _dataset(db_session, client, test_project)
    f = dataset["findings"][0]
    assert f["evidence"] == [] and len(f["images"]) == 2
    assert (summary["images"], summary["images_placed"], summary["images_unplaced"]) == (2, 2, 0)


def test_thirty_images_on_one_finding_each_land_in_exactly_one_place(client, db_session, test_project):
    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    note = _comment(client, test_project, finding)
    ids = [_image(client, test_project, finding, name=f"s{n}.png", note_id=note) for n in range(30)]
    _patch(client, test_project, ids[0], caption="c" * 2000)
    _set_text(client, test_project, finding,
              description="\n\n".join(f"![](evidence:{i})" for i in ids[:17]))
    _, dataset, summary = _dataset(db_session, client, test_project)
    f = dataset["findings"][0]
    assert sorted(int(k) for k in f["placed"]["description"]) == ids[:17]
    assert [i["attachment_id"] for i in f["evidence"]] == ids[17:]
    assert f["placed"]["description"][str(ids[0])]["caption"] == "c" * 2000
    assert (summary["images"], summary["images_placed"], summary["images_unplaced"]) == (30, 17, 13)


# ---------------------------------------------------------------------------
# A placed image stays
# ---------------------------------------------------------------------------

def test_a_placed_image_cannot_be_deleted_or_unticked_until_the_reference_is_removed(
    client, db_session, test_project, storage,
):
    finding = _finding(db_session, test_project)
    note = _comment(client, test_project, finding)
    att = _image(client, test_project, finding, note_id=note)
    _set_text(client, test_project, finding, description=f"See ![x](evidence:{att})", impact=f"![](evidence:{att})")

    r = _delete(client, test_project, att)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert f"image {att} is placed in the Description, Impact of finding #{finding.id}" in detail
    assert f"evidence:{att}" in detail and "then delete it" in detail

    r = _patch(client, test_project, att, include_in_report=False)
    assert r.status_code == 409 and "Description, Impact" in r.json()["detail"]
    assert 'then un-tick "In report"' in r.json()["detail"]
    # Its caption can still be edited, and ticking an already-ticked image is fine.
    assert _patch(client, test_project, att, caption="still editable", include_in_report=True).status_code == 200

    # The comment that holds it stays too.
    r = client.delete(f"{_base(test_project)}/findings/{finding.id}/notes/{note}")
    assert r.status_code == 409 and "delete this comment" in r.json()["detail"]

    db_session.expire_all()
    row = db_session.get(NoteAttachment, att)
    assert row is not None and row.include_in_report is True
    assert (storage / "note_attachments" / row.storage_path).is_file()

    # Out of one section it is still placed in the other; out of both it goes.
    _set_text(client, test_project, finding, description="See the figure.")
    assert "the Impact of" in _delete(client, test_project, att).json()["detail"]
    _set_text(client, test_project, finding, impact="No picture.")
    assert _patch(client, test_project, att, include_in_report=False).status_code == 200
    assert _delete(client, test_project, att).status_code == 204
    db_session.expire_all()
    assert db_session.get(NoteAttachment, att) is None


def test_an_image_on_the_source_note_is_protected_like_one_on_a_comment(client, db_session, test_project, test_user, storage):
    host = models.Host(project_id=test_project.id, ip_address="10.77.9.1", state="up")
    db_session.add(host)
    db_session.flush()
    root = Annotation(host_id=host.id, user_id=test_user.id, body="proof", note_type="observation")
    db_session.add(root)
    db_session.flush()
    finding = _finding(db_session, test_project, evidence_annotation_id=root.id)
    r = client.post(
        f"{_base(test_project)}/hosts/{host.id}/notes/{root.id}/attachments",
        files={"file": ("host.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    att = r.json()["id"]
    assert _patch(client, test_project, att, include_in_report=True).status_code == 200
    _set_text(client, test_project, finding, recommendation=f"![](evidence:{att})")
    assert _images(client, test_project, finding)[att]["placed_in"] == ["recommendation"]
    assert _delete(client, test_project, att).status_code == 409
    r = client.delete(f"{_base(test_project)}/hosts/{host.id}/notes/{root.id}")
    assert r.status_code == 409 and "Recommendation" in r.json()["detail"]


def test_an_unplaced_image_is_deleted_and_unticked_as_before(client, db_session, test_project):
    finding = _finding(db_session, test_project, description="No pictures here, only evidence:12 as words.")
    att = _image(client, test_project, finding)
    assert _patch(client, test_project, att, include_in_report=False).status_code == 200
    assert _delete(client, test_project, att).status_code == 204


# ---------------------------------------------------------------------------
# Issuing: the report owns its images
# ---------------------------------------------------------------------------

def _issue(client, project, report_id):
    return client.post(f"{_base(project)}/client-reports/{report_id}/issue")


def test_issuing_copies_the_images_and_the_issued_report_renders_from_its_copies(
    client, db_session, test_project, storage,
):
    from app.services.client_report_render import _issued_evidence_resolver

    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    placed = _image(client, test_project, finding)
    trailing = _image(client, test_project, finding)
    _patch(client, test_project, placed, caption="As issued")
    _set_text(client, test_project, finding, description=f"![](evidence:{placed})")
    report, _, _ = _dataset(db_session, client, test_project)

    issued = _issue(client, test_project, report["id"])
    assert issued.status_code == 200, issued.text
    db_session.expire_all()
    rows = {r.attachment_id: r for r in db_session.query(ReportImage).filter_by(report_id=report["id"])}
    assert set(rows) == {placed, trailing}
    folder = storage / "client_reports" / str(test_project.id) / str(report["id"]) / "evidence"
    assert sorted(p.name for p in folder.iterdir()) == sorted([f"{placed}.png", f"{trailing}.png"])
    for row in rows.values():
        assert row.sha256 == hashlib.sha256(PNG).hexdigest() and row.size_bytes == len(PNG)
        assert (storage / "client_reports" / row.storage_path).read_bytes() == PNG

    # The snapshot froze the caption, the placement and the trailing block.
    snap = db_session.get(Report, report["id"]).snapshot["dataset"]["findings"][0]
    assert snap["placed"]["description"][str(placed)]["caption"] == "As issued"
    assert [i["attachment_id"] for i in snap["evidence"]] == [trailing]
    assert issued.json()["summary"]["images_placed"] == 1 and issued.json()["summary"]["images_unplaced"] == 1

    # Afterwards the author rewrites the section, re-captions, un-ticks and
    # deletes: all allowed — the refusal is about the finding's CURRENT text —
    # and the issued report is untouched.
    _patch(client, test_project, placed, caption="Changed later")
    _set_text(client, test_project, finding, description="No picture any more.")
    assert _patch(client, test_project, trailing, include_in_report=False).status_code == 200
    assert _delete(client, test_project, placed).status_code == 204
    assert _delete(client, test_project, trailing).status_code == 204
    db_session.expire_all()
    assert db_session.query(NoteAttachment).count() == 0

    issued_report = db_session.get(Report, report["id"])
    resolve = _issued_evidence_resolver(db_session, issued_report)
    for item in issued_report.snapshot["dataset"]["findings"][0]["images"]:
        path = resolve(item)
        assert path is not None and path.read_bytes() == PNG and "client_reports" in str(path)
    assert issued_report.snapshot["dataset"]["findings"][0]["images"][0]["caption"] == "As issued"

    # A copy that is no longer the file that was issued is not used.
    (folder / f"{placed}.png").write_bytes(PNG + b"tampered")
    resolve = _issued_evidence_resolver(db_session, issued_report)
    assert resolve({"attachment_id": placed}) is None and resolve({"attachment_id": trailing}) is not None
    assert resolve({"attachment_id": 999999}) is None


def test_issuing_is_refused_when_an_images_file_is_missing_and_leaves_nothing(client, db_session, test_project, storage):
    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    kept = _image(client, test_project, finding)
    gone = _image(client, test_project, finding, name="lost.png")
    row = db_session.get(NoteAttachment, gone)
    (storage / "note_attachments" / row.storage_path).unlink()
    report, _, _ = _dataset(db_session, client, test_project)

    r = _issue(client, test_project, report["id"])
    assert r.status_code == 409, r.text
    assert "lost.png" in r.json()["detail"] and f"image {gone}" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Report, report["id"]).status == "draft"
    assert db_session.query(ReportImage).count() == 0
    assert not (storage / "client_reports" / str(test_project.id) / str(report["id"]) / "evidence").exists()
    # Fixed (the image is taken out), the same draft issues.
    assert _patch(client, test_project, gone, include_in_report=False).status_code == 200
    assert _issue(client, test_project, report["id"]).status_code == 200
    db_session.expire_all()
    assert [r.attachment_id for r in db_session.query(ReportImage)] == [kept]


def test_a_report_issued_before_the_copies_reads_the_live_attachment(client, db_session, test_project, storage):
    """No ``report_images`` rows (issued before this, or a report with no
    images): the resolver is the draft's, as it always was."""
    from app.services.client_report_render import _issued_evidence_resolver

    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    att = _image(client, test_project, finding)
    report, _, _ = _dataset(db_session, client, test_project)
    assert _issue(client, test_project, report["id"]).status_code == 200
    db_session.query(ReportImage).delete()
    db_session.commit()
    resolve = _issued_evidence_resolver(db_session, db_session.get(Report, report["id"]))
    path = resolve({"attachment_id": att})
    assert path is not None and "note_attachments" in str(path)


def test_a_deleted_projects_report_storage_is_removed_and_nothing_else(storage):
    from app.services.client_report_service import discard_project_report_files

    root = Path(settings.REPORT_FILES_DIR)
    for project in ("7", "70"):
        (root / project / "3" / "evidence").mkdir(parents=True)
        (root / project / "3" / "evidence" / "1.png").write_bytes(PNG)
    discard_project_report_files(7)
    assert not (root / "7").exists() and (root / "70" / "3" / "evidence" / "1.png").is_file()


# ---------------------------------------------------------------------------
# Agents: proposals keep references honest; the read lists the images
# ---------------------------------------------------------------------------

def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _propose(client, key, finding, **fields):
    return client.post("/api/v1/agent/proposals/finding-text", headers=key,
                       json={"finding_id": finding.id, "fields": fields})


def test_a_proposed_section_may_only_place_this_findings_images(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(db_session, test_project)
    other = _finding(db_session, test_project, "Another finding")
    mine = _image(client, test_project, finding)
    theirs = _image(client, test_project, other)
    webp = _image(client, test_project, finding, name="m.webp", data=WEBP, ctype="image/webp")

    r = _propose(client, key, finding, description=f"![ok](evidence:{mine}) ![no](evidence:{theirs}) ![no](evidence:777777)")
    assert r.status_code == 422, r.text
    assert f"evidence:{theirs}, evidence:777777" in r.json()["detail"]
    assert f"finding #{finding.id}" in r.json()["detail"] and f"evidence:{mine}" not in r.json()["detail"]
    assert _propose(client, key, finding, impact=f"![w](evidence:{webp})").status_code == 422   # not printable
    from app.db.models_proposals import AgentProposal
    assert db_session.query(AgentProposal).count() == 0

    # A new finding has no images to place.
    host = db_session.query(models.Host).first()
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "New", "severity": "low", "host_ids": [host.id],
        "report_text": {"description": f"![x](evidence:{mine})"},
    })
    assert r.status_code == 422 and "a new finding has none yet" in r.json()["detail"]


def test_accepting_never_ticks_an_image_and_a_rewrite_may_drop_a_reference(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(db_session, test_project, created_by_id=None)
    ticked = _image(client, test_project, finding)
    unticked = _image(client, test_project, finding, ticked=False)
    _set_text(client, test_project, finding, description=f"Old text ![](evidence:{ticked})")

    # Proposing with an un-ticked image of the finding is allowed…
    r = _propose(client, key, finding,
                 description=f"New text ![kept](evidence:{ticked}) ![also](evidence:{unticked})")
    assert r.status_code == 201, r.text
    pid = r.json()["proposals"][0]["id"]
    # …accepting it is not, and it does not tick the image: the proposal stays
    # pending with the reason.
    acc = client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={})
    assert acc.status_code == 409, acc.text
    assert f"image {unticked}" in acc.json()["detail"] and 'not ticked "In report"' in acc.json()["detail"]
    db_session.expire_all()
    assert db_session.get(NoteAttachment, unticked).include_in_report is False
    assert db_session.get(Finding, finding.id).description == f"Old text ![](evidence:{ticked})"
    pending = client.get(f"{_base(test_project)}/proposals", params={"finding_id": finding.id}).json()["items"]
    assert pending[0]["status"] == "pending" and "In report" in pending[0]["error"]

    # Edited on accept to drop the un-ticked reference: applied.
    acc = client.post(f"{_base(test_project)}/proposals/{pid}/accept",
                      json={"edited_value": f"New text ![kept](evidence:{ticked})"})
    assert acc.status_code == 200, acc.text
    assert _images(client, test_project, finding)[ticked]["placed_in"] == ["description"]

    # A rewrite that drops the reference is allowed; the image prints under
    # Evidence again.
    r = _propose(client, key, finding, description="Rewritten without the picture.")
    assert r.status_code == 201
    pid = r.json()["proposals"][0]["id"]
    assert client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={}).status_code == 200
    assert _images(client, test_project, finding)[ticked]["placed_in"] == []

    # An image deleted between the proposal and the accept: 422, naming it.
    r = _propose(client, key, finding, impact=f"![](evidence:{unticked})")
    pid = r.json()["proposals"][0]["id"]
    assert _delete(client, test_project, unticked).status_code == 204
    acc = client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={})
    assert acc.status_code == 422 and f"evidence:{unticked}" in acc.json()["detail"]


def test_the_agents_finding_read_lists_the_images_the_page_lists(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(db_session, test_project)
    a = _image(client, test_project, finding)
    b = _image(client, test_project, finding, ticked=False)
    _patch(client, test_project, a, caption="The relayed session")
    _set_text(client, test_project, finding, description=f"![](evidence:{a})")

    detail = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=key)
    assert detail.status_code == 200, detail.text
    images = {img["id"]: img for img in detail.json()["images"]}
    page = _images(client, test_project, finding)
    assert set(images) == set(page) == {a, b}
    for att_id, img in images.items():
        for key_name in ("caption", "filename", "in_report", "printable", "placed_in"):
            assert img[key_name] == page[att_id][key_name], key_name
        assert img["download_path"] == f"/api/v1/agent/assist/attachments/{att_id}"
    assert images[a]["caption"] == "The relayed session" and images[a]["placed_in"] == ["description"]
    assert images[b]["in_report"] is False and images[b]["placed_in"] == []
    # The comment's attachment carries the caption too.
    on_comments = {att["id"]: att for c in detail.json()["comments"] for att in c["attachments"]}
    assert on_comments[a]["caption"] == "The relayed session" and on_comments[b]["caption"] is None


def test_the_agents_report_read_says_which_images_are_placed(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(db_session, test_project, description="d", impact="i", recommendation="r")
    a = _image(client, test_project, finding)
    b = _image(client, test_project, finding)
    _set_text(client, test_project, finding, impact=f"![](evidence:{a})")
    report, _, _ = _dataset(db_session, client, test_project)
    body = client.get(f"/api/v1/agent/assist/client-reports/{report['id']}", headers=key)
    assert body.status_code == 200, body.text
    f = body.json()["findings"][0]
    assert [(i["attachment_id"], i["placed_in"]) for i in f["images"]] == [(a, ["impact"]), (b, [])]
    assert [i["attachment_id"] for i in f["evidence"]] == [b]
    assert "placed" not in f
