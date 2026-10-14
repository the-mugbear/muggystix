"""The report's own images (2.495.0) — a network diagram in the system
description.

They are ``note_attachments`` rows with NO note (``annotation_id`` NULL): one
id space with the findings' images, so one reference grammar
(``![…](evidence:<id>)``), one serve route, one caption rule and one copy at
issue.  Uploaded on the Reports page, they belong to the project; a report
prints the ones ITS system description places.  These tests pin:

* the upload (PNG ok, WebP refused, analyst floor, the caption's bounds) and
  that another project can neither list, read nor delete the image;
* the list's ``placed_in`` — the project's report defaults and each draft —
  and that a reference is stored in the one form on both saves;
* what protects a placed image (409 on delete, naming where; 422 on taking
  it "out of the report") and that an issued report never holds one back;
* the dataset: ``engagement.images`` / ``engagement.placed`` hold only what
  this report's text places, in a printable format; a finding's image is not
  placed by the system description, nor a report image by a finding;
* that what the template PRINTS is measured, and issuing copies exactly
  those into the report's own storage — the issued render reads the copy;
* the files: a deleted project's are removed, and the orphan reconciler never
  takes the folder for a note's.

The rendered figure (numbered with the rest, hostile captions) is in
``test_quarto_render.py``, which needs Quarto.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models import NoteAttachment
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportImage
from app.main import app
from app.services import note_attachment_service, quarto_render, report_images
from app.services.client_report_service import ClientReportService

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
FINDINGS = (
    "<% for f in findings %>\n"
    '<% for field in ("description", "impact", "steps_to_reproduce", "recommendation", "references") %>'
    "<< md(f, field) >><% endfor %>\n"
    "<% for e in f.evidence %><< image(e) >><% endfor %>\n"
    "<% endfor %>\n"
)


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    """Three templates: ``pentest`` prints the system description with its
    images (as the shipped one does), ``plain`` prints it WITHOUT them and
    ``nosys`` does not print it at all."""
    root = tmp_path / "report-templates"
    for name, front in (
        ("pentest", '<< md("engagement.system_description") >>\n'),
        ("plain", '<< md("engagement.system_description", images=False) >>\n'),
        ("nosys", ""),
    ):
        (root / name).mkdir(parents=True)
        (root / name / "template.json").write_text(json.dumps({
            "title": name, "entry": "report.qmd", "formats": ["html", "docx"],
        }))
        (root / name / "report.qmd").write_text("---\ntitle: x\n---\n" + front + FINDINGS)
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
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def _other_project(db):
    project = Project(name="other-project", slug="other-project", description="another")
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _reports(project):
    return f"{_base(project)}/client-reports"


def _upload(client, project, *, name="diagram.png", data=PNG, ctype="image/png", caption=None):
    return client.post(
        f"{_reports(project)}/images", files={"file": (name, data, ctype)},
        data={"caption": caption} if caption is not None else None,
    )


def _image(client, project, **kwargs):
    r = _upload(client, project, **kwargs)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _listed(client, project):
    r = client.get(f"{_reports(project)}/images")
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()}


def _attachment(project, attachment_id):
    return f"{_base(project)}/hosts/notes/attachments/{attachment_id}"


def _draft(client, project, template="pentest", **settings_):
    r = client.post(_reports(project), json={"kind": "full", "template": template})
    assert r.status_code == 201, r.text
    if settings_:
        r = client.patch(f"{_reports(project)}/{r.json()['id']}", json={"settings": settings_})
        assert r.status_code == 200, r.text
    return r.json()


def _build(db, report_id):
    db.expire_all()
    return ClientReportService(db).build(db.get(Report, report_id))


def _finding(db, project, title="SMB relay", **text):
    host = models.Host(project_id=project.id, ip_address=f"10.78.0.{db.query(models.Host).count() + 1}", state="up")
    db.add(host)
    db.flush()
    f = Finding(project_id=project.id, title=title, severity="high", status="confirmed", source="manual",
                description="d", impact="i", recommendation="r", **text)
    db.add(f)
    db.flush()
    db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db.commit()
    return f


def _finding_image(client, project, finding):
    note = client.post(f"{_base(project)}/findings/{finding.id}/notes", json={"body": "proof"})
    assert note.status_code == 200, note.text
    r = client.post(
        f"{_base(project)}/findings/{finding.id}/notes/{note.json()['id']}/attachments",
        files={"file": ("shot.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert client.patch(_attachment(project, r.json()["id"]), json={"include_in_report": True}).status_code == 200
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Upload, and whose it is
# ---------------------------------------------------------------------------

def test_an_uploaded_image_is_the_projects_and_in_the_report_from_the_start(
    client, db_session, test_project, test_user, storage,
):
    r = _upload(client, test_project, caption="  The network,\n as assessed  ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "diagram.png" and body["content_type"] == "image/png"
    assert body["caption"] == "The network, as assessed"          # one line, as every caption
    assert body["size_bytes"] == len(PNG) and body["uploaded_by_id"] == test_user.id
    assert body["placed_in"] == [] and body["can_modify"] is True

    row = db_session.get(NoteAttachment, body["id"])
    assert row.annotation_id is None and row.project_id == test_project.id
    assert row.include_in_report is True
    # Kept apart from the notes' folders, by project.
    assert row.storage_path.startswith(f"report/{test_project.id}/")
    assert (storage / "note_attachments" / row.storage_path).read_bytes() == PNG

    # Served by the attachments' own route; listed for the page.
    served = client.get(_attachment(test_project, body["id"]))
    assert served.status_code == 200 and served.content == PNG
    assert set(_listed(client, test_project)) == {body["id"]}
    # A GIF is a format every report prints.
    assert _upload(client, test_project, name="anim.gif", data=GIF, ctype="image/gif").status_code == 201


def test_an_image_no_report_can_print_is_refused(client, db_session, test_project):
    r = _upload(client, test_project, name="modern.webp", data=WEBP, ctype="image/webp")
    assert r.status_code == 400 and "PNG, JPEG or GIF" in r.json()["detail"]
    # The bytes decide, not the name or the declared type.
    assert _upload(client, test_project, data=b"<svg onload=alert(1)>", ctype="image/png").status_code == 400
    assert _upload(client, test_project, name="x.svg", data=b"<svg/>", ctype="image/svg+xml").status_code == 400
    r = _upload(client, test_project, caption="c" * (report_images.CAPTION_MAX + 1))
    assert r.status_code == 422 and str(report_images.CAPTION_MAX) in r.json()["detail"]
    assert db_session.query(NoteAttachment).count() == 0


def test_an_analyst_uploads_an_auditor_only_reads(client, db_session, test_project, act_as):
    auditor = _member(db_session, test_project, 501, "aud", ProjectRole.AUDITOR)
    analyst = _member(db_session, test_project, 502, "ana", ProjectRole.ANALYST)
    other = _member(db_session, test_project, 503, "ben", ProjectRole.ANALYST)
    admin = _member(db_session, test_project, 504, "adm", ProjectRole.ADMIN)

    act_as(auditor)
    assert _upload(client, test_project).status_code == 403
    assert _listed(client, test_project) == {}
    act_as(analyst)
    att = _image(client, test_project)
    assert _listed(client, test_project)[att]["can_modify"] is True

    # The caption and the delete are the uploader's or a project admin's —
    # the rule every attachment has — and the list says so.
    act_as(other)
    assert _listed(client, test_project)[att]["can_modify"] is False
    assert client.patch(_attachment(test_project, att), json={"caption": "Not mine"}).status_code == 403
    assert client.delete(_attachment(test_project, att)).status_code == 403
    act_as(auditor)
    assert _listed(client, test_project)[att]["can_modify"] is False
    assert client.patch(_attachment(test_project, att), json={"caption": "Read only"}).status_code == 403
    act_as(admin)
    assert _listed(client, test_project)[att]["can_modify"] is True
    r = client.patch(_attachment(test_project, att), json={"caption": "The admin's words"})
    assert r.status_code == 200 and r.json()["caption"] == "The admin's words"
    assert client.delete(_attachment(test_project, att)).status_code == 204


def test_another_project_can_neither_list_read_nor_delete_it(client, db_session, test_project):
    other = _other_project(db_session)
    att = _image(client, test_project)
    theirs = _image(client, other, name="theirs.png")

    assert set(_listed(client, test_project)) == {att} and set(_listed(client, other)) == {theirs}
    assert client.get(_attachment(other, att)).status_code == 404
    assert client.patch(_attachment(other, att), json={"caption": "x"}).status_code == 404
    assert client.delete(_attachment(other, att)).status_code == 404
    # A reference to it in the other project's text places nothing there.
    draft = _draft(client, other, system_description=f"![x](evidence:{att}) ![y](evidence:{theirs})")
    dataset, _, _ = _build(db_session, draft["id"])
    assert [i["attachment_id"] for i in dataset["engagement"]["images"]] == [theirs]
    assert list(dataset["engagement"]["placed"]["system_description"]) == [str(theirs)]
    assert _listed(client, test_project)[att]["placed_in"] == []
    assert db_session.get(NoteAttachment, att) is not None


# ---------------------------------------------------------------------------
# Where it is placed, and what that protects
# ---------------------------------------------------------------------------

def test_the_list_says_where_the_projects_text_places_each_image(client, db_session, test_project):
    a = _image(client, test_project, name="a.png")
    b = _image(client, test_project, name="b.png")
    loose = _image(client, test_project, name="loose.png")

    # A tolerated spelling is stored in the one form, on both saves.
    r = client.put(f"{_reports(test_project)}/profile", json={
        "system_description": f"The network.\n\n![Diagram](< evidence:{a} >)",
    })
    assert r.status_code == 200, r.text
    assert r.json()["system_description"] == f"The network.\n\n![Diagram](evidence:{a})"
    first = _draft(client, test_project)                       # starts from the defaults
    assert first["settings"]["system_description"] == f"The network.\n\n![Diagram](evidence:{a})"
    second = _draft(
        client, test_project,
        system_description=f"![](<evidence:{b}>) and again ![]( evidence:{b} ) `![code](evidence:{loose})`",
    )
    assert second["settings"]["system_description"].startswith(f"![](evidence:{b}) and again")

    listed = _listed(client, test_project)
    assert listed[a]["placed_in"] == ["defaults", f"report {first['id']}"]
    assert listed[b]["placed_in"] == [f"report {second['id']}"]          # once, however often it is placed
    assert listed[loose]["placed_in"] == []                               # a reference in code places nothing
    assert report_images.report_text_placements(db_session, test_project.id) == {
        a: ["defaults", f"report {first['id']}"], b: [f"report {second['id']}"],
    }


def test_a_placed_image_stays_until_the_text_lets_go_of_it(client, db_session, test_project, storage):
    att = _image(client, test_project)
    client.put(f"{_reports(test_project)}/profile", json={"system_description": f"![](evidence:{att})"})
    draft = _draft(client, test_project)
    path = storage / "note_attachments" / db_session.get(NoteAttachment, att).storage_path

    r = client.delete(_attachment(test_project, att))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "the project's report defaults" in detail and f"draft #{draft['id']}" in detail
    assert f"evidence:{att}" in detail
    # It is always in the report: there is no "take it out" for it.
    r = client.patch(_attachment(test_project, att), json={"include_in_report": False})
    assert r.status_code == 422 and "always in the report" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(NoteAttachment, att).include_in_report is True and path.is_file()
    # Its caption is still its people's to write, and ticking it again is a no-op.
    assert client.patch(_attachment(test_project, att), json={"caption": "As built", "include_in_report": True}).status_code == 200

    # The defaults let go: the draft still holds it.
    client.put(f"{_reports(test_project)}/profile", json={"system_description": "No picture."})
    r = client.delete(_attachment(test_project, att))
    assert r.status_code == 409 and "report defaults" not in r.json()["detail"]
    assert f"draft #{draft['id']}" in r.json()["detail"]
    # The draft lets go: it can be deleted, row and file.
    assert client.patch(f"{_reports(test_project)}/{draft['id']}", json={
        "settings": {"system_description": "No picture."},
    }).status_code == 200
    assert client.delete(_attachment(test_project, att)).status_code == 204
    db_session.expire_all()
    assert db_session.get(NoteAttachment, att) is None and not path.exists()


def test_an_unplaced_image_is_simply_deleted(client, db_session, test_project):
    att = _image(client, test_project)
    _draft(client, test_project, system_description="No picture.")
    assert client.delete(_attachment(test_project, att)).status_code == 204
    assert _listed(client, test_project) == {}


# ---------------------------------------------------------------------------
# The dataset
# ---------------------------------------------------------------------------

def test_the_dataset_holds_the_images_this_reports_text_places(client, db_session, test_project):
    placed = _image(client, test_project, name="net.png", caption="The assessed network")
    bare = _image(client, test_project, name="uncaptioned.png")
    unused = _image(client, test_project, name="unused.png")
    draft = _draft(
        client, test_project,
        system_description=f"Overview.\n\n![alt](evidence:{placed})\n\n![](evidence:{bare}) ![again](evidence:{placed}) "
                           f"![gone](evidence:987654)",
        applications=f"![elsewhere](evidence:{unused})",          # not a field that places images
    )
    dataset, _, _ = _build(db_session, draft["id"])
    engagement = dataset["engagement"]
    entry = {"attachment_id": placed, "file": f"evidence/{placed}.png", "caption": "The assessed network"}
    # Only what the system description places; the caption falls back to the file's name.
    assert engagement["placed"] == {"system_description": {
        str(placed): entry,
        str(bare): {"attachment_id": bare, "file": f"evidence/{bare}.png", "caption": "uncaptioned.png"},
    }}
    assert [(i["attachment_id"], i["caption"], i["placed_in"]) for i in engagement["images"]] == [
        (placed, "The assessed network", ["system_description"]),
        (bare, "uncaptioned.png", ["system_description"]),
    ]
    # Measured from the template's fill: this one prints the text with its images.
    assert all(i["printed"] is True and i["printed_in"] == ["system_description"] for i in engagement["images"])
    # The settings keys are still the report's own; nothing leaked into them.
    db_session.expire_all()
    stored = db_session.get(Report, draft["id"]).settings
    assert "images" not in stored and "placed" not in stored

    # A draft that places none has an empty list and an empty map — never the project's images.
    empty, _, _ = _build(db_session, _draft(client, test_project, system_description="Words only.")["id"])
    assert empty["engagement"]["images"] == [] and empty["engagement"]["placed"] == {}


def test_a_findings_image_and_the_reports_image_never_cross(client, db_session, test_project):
    """One id space, two owners: the system description shows only the
    report's own images and a finding's section only that finding's —
    whatever either text references."""
    finding = _finding(db_session, test_project)
    shot = _finding_image(client, test_project, finding)
    diagram = _image(client, test_project)
    r = client.patch(f"{_base(test_project)}/findings/{finding.id}", json={
        "description": f"![mine](evidence:{shot}) ![the report's](evidence:{diagram})",
    })
    assert r.status_code == 200, r.text
    draft = _draft(client, test_project,
                   system_description=f"![ours](evidence:{diagram}) ![a finding's](evidence:{shot})")

    dataset, _, summary = _build(db_session, draft["id"])
    engagement, item = dataset["engagement"], dataset["findings"][0]
    assert list(engagement["placed"]["system_description"]) == [str(diagram)]
    assert [i["attachment_id"] for i in engagement["images"]] == [diagram]
    assert list(item["placed"]["description"]) == [str(shot)]
    assert [i["attachment_id"] for i in item["images"]] == [shot] and item["evidence"] == []
    # The finding counts are the findings': the report's image is not one of them.
    assert (summary["images"], summary["images_placed"], summary["images_unplaced"]) == (1, 1, 0)
    # Nor is it listed among the finding's images, or the finding's among the report's.
    listed = client.get(f"{_base(test_project)}/findings/{finding.id}/images").json()["items"]
    assert [i["id"] for i in listed] == [shot]
    assert set(_listed(client, test_project)) == {diagram}
    # The finding's reference to the diagram does not hold the diagram back …
    client.patch(f"{_reports(test_project)}/{draft['id']}", json={"settings": {"system_description": "x"}})
    assert client.delete(_attachment(test_project, diagram)).status_code == 204
    # … and the system description's reference to the screenshot did not place it.
    assert client.get(f"{_base(test_project)}/findings/{finding.id}/images").json()["items"][0]["placed_in"] == [
        "description",
    ]


@pytest.mark.parametrize("template,printed", [("pentest", True), ("plain", False), ("nosys", False)])
def test_what_the_template_prints_of_them_is_measured(client, db_session, test_project, template, printed):
    att = _image(client, test_project)
    draft = _draft(client, test_project, template=template, system_description=f"![](evidence:{att})")
    dataset, _, _ = _build(db_session, draft["id"])
    (image,) = dataset["engagement"]["images"]
    assert image["printed"] is printed
    assert image["printed_in"] == (["system_description"] if printed else [])


# ---------------------------------------------------------------------------
# Issuing: the report's own copy
# ---------------------------------------------------------------------------

def _issue(client, project, report_id):
    return client.post(f"{_reports(project)}/{report_id}/issue")


def test_issuing_copies_the_printed_report_image_and_the_issued_report_reads_its_copy(
    client, db_session, test_project, storage,
):
    from app.services.client_report_render import _issued_evidence_resolver

    finding = _finding(db_session, test_project)
    shot = _finding_image(client, test_project, finding)
    diagram = _image(client, test_project, caption="As issued")
    unused = _image(client, test_project, name="unused.png")
    draft = _draft(client, test_project, system_description=f"![](evidence:{diagram})")

    issued = _issue(client, test_project, draft["id"])
    assert issued.status_code == 200, issued.text
    db_session.expire_all()
    rows = {r.attachment_id: r for r in db_session.query(ReportImage).filter_by(report_id=draft["id"])}
    assert set(rows) == {shot, diagram}                       # not the one no text places
    folder = storage / "client_reports" / str(test_project.id) / str(draft["id"]) / "evidence"
    assert sorted(p.name for p in folder.iterdir()) == sorted([f"{shot}.png", f"{diagram}.png"])
    assert rows[diagram].sha256 == hashlib.sha256(PNG).hexdigest()
    frozen = db_session.get(Report, draft["id"]).snapshot["dataset"]["engagement"]
    assert frozen["placed"]["system_description"][str(diagram)]["caption"] == "As issued"
    assert [i["attachment_id"] for i in frozen["images"]] == [diagram]

    # The issued report no longer holds the live image: it can be re-captioned
    # and deleted, and the report still resolves its own copy.
    assert client.patch(_attachment(test_project, diagram), json={"caption": "Changed later"}).status_code == 200
    assert _listed(client, test_project)[diagram]["placed_in"] == []
    assert client.delete(_attachment(test_project, diagram)).status_code == 204
    assert client.delete(_attachment(test_project, unused)).status_code == 204
    db_session.expire_all()
    report = db_session.get(Report, draft["id"])
    resolve = _issued_evidence_resolver(db_session, report)
    path = resolve(report.snapshot["dataset"]["engagement"]["images"][0])
    assert path is not None and path.read_bytes() == PNG and "client_reports" in str(path)

    # … and the renderer's own step places it from there, with the finding's.
    dataset = json.loads(json.dumps(report.snapshot["dataset"]))
    work = storage / "work"
    work.mkdir()
    assert quarto_render._place_evidence(dataset, work, resolve) == []
    assert sorted(p.name for p in (work / "evidence").iterdir()) == sorted([f"{shot}.png", f"{diagram}.png"])
    assert list(dataset["engagement"]["placed"]["system_description"]) == [str(diagram)]


def test_issuing_needs_the_file_of_a_report_image_it_prints_and_of_no_other(client, db_session, test_project, storage):
    diagram = _image(client, test_project, caption="The lost diagram")
    row = db_session.get(NoteAttachment, diagram)
    (storage / "note_attachments" / row.storage_path).unlink()

    printing = _draft(client, test_project, system_description=f"![](evidence:{diagram})")
    r = _issue(client, test_project, printing["id"])
    assert r.status_code == 409, r.text
    assert "The system description" in r.json()["detail"] and f"image {diagram}" in r.json()["detail"]
    assert "The lost diagram" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Report, printing["id"]).status == "draft" and db_session.query(ReportImage).count() == 0

    # A template that prints the text without its images is not held up by it.
    plain = _draft(client, test_project, template="plain", system_description=f"![](evidence:{diagram})")
    assert _issue(client, test_project, plain["id"]).status_code == 200
    db_session.expire_all()
    assert db_session.query(ReportImage).count() == 0


# ---------------------------------------------------------------------------
# The renderer's step (no Quarto): copy what is there, drop what is not
# ---------------------------------------------------------------------------

def test_the_renderer_places_the_reports_images_and_prunes_a_missing_one(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    one = {"attachment_id": 1, "file": "evidence/1.png", "caption": "Diagram"}
    two = {"attachment_id": 2, "file": "evidence/2.png", "caption": "Gone"}
    three = {"attachment_id": 3, "file": "evidence/3.png", "caption": "Not printed"}
    dataset = {
        "findings": [],
        "engagement": {
            "client_name": "Example", "system_description": "![](evidence:1) ![](evidence:2) ![](evidence:3)",
            "images": [
                {**one, "placed_in": ["system_description"], "printed": True},
                {**two, "placed_in": ["system_description"], "printed": True},
                {**three, "placed_in": ["system_description"], "printed": False},
            ],
            "placed": {"system_description": {"1": dict(one), "2": dict(two), "3": dict(three),
                                              "9": {"attachment_id": 9, "file": "/etc/passwd", "caption": "x"}}},
        },
    }
    asked = []

    def resolve(item):
        asked.append(item.get("attachment_id"))
        return shot if item.get("attachment_id") == 1 else None

    work = tmp_path / "work"
    work.mkdir()
    missing = quarto_render._place_evidence(dataset, work, resolve)
    assert missing == ["Gone"]
    assert [p.name for p in (work / "evidence").iterdir()] == ["1.png"]
    engagement = dataset["engagement"]
    # The missing one is dropped (its reference then prints as alt text); the
    # one this template does not print is neither looked for nor dropped;
    # a file that is not an evidence file is never read.
    assert [i["attachment_id"] for i in engagement["images"]] == [1, 3]
    assert list(engagement["placed"]["system_description"]) == ["1", "3"]
    assert sorted(set(asked)) == [1, 2]
    # It has no trailing block, and gains none.
    assert "evidence" not in engagement and engagement["client_name"] == "Example"

    # A dataset frozen before the report had images of its own is untouched.
    old = {"findings": [], "engagement": {"client_name": "Example", "system_description": "![](evidence:1)"}}
    assert quarto_render._place_evidence(old, work, resolve) == []
    assert old["engagement"] == {"client_name": "Example", "system_description": "![](evidence:1)"}


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------

def test_deleting_a_project_removes_its_report_images_and_no_other_projects(client, db_session, test_project, storage):
    other = _other_project(db_session)
    mine = _image(client, test_project)
    theirs = _image(client, other)
    root = storage / "note_attachments" / "report"
    assert (root / str(test_project.id)).is_dir() and (root / str(other.id)).is_dir()
    project_id = test_project.id

    r = client.delete(f"/api/v1/projects/{project_id}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(NoteAttachment, mine) is None            # the row cascaded
    assert not (root / str(project_id)).exists()                   # and the folder went after it
    assert db_session.get(NoteAttachment, theirs) is not None
    assert len(list((root / str(other.id)).iterdir())) == 1


def test_the_purge_takes_one_projects_folder_and_nothing_else(storage):
    root = storage / "note_attachments"
    for folder in ("report/7", "report/70", "7"):
        (root / folder).mkdir(parents=True)
        (root / folder / "x.png").write_bytes(PNG)
    note_attachment_service.purge_project_report_images(7)
    assert not (root / "report" / "7").exists()
    assert (root / "report" / "70" / "x.png").is_file() and (root / "7" / "x.png").is_file()
    for hostile in ("../7", "70/..", None, "x"):
        note_attachment_service.purge_project_report_images(hostile)       # never raises, never climbs
    assert (root / "report" / "70" / "x.png").is_file() and (root / "7" / "x.png").is_file()
    assert (root / "report").is_dir()


def test_the_orphan_reconciler_leaves_the_report_folder_alone(client, db_session, test_project, storage):
    """It reaps NOTE folders (named by a note id) that no attachment row
    names.  The report images' folder is not one — and a project id is not a
    note id: an aged ``report`` tree stays, whatever rows exist."""
    att = _image(client, test_project)
    root = storage / "note_attachments"
    orphan = root / "900001"
    orphan.mkdir()
    (orphan / "shot.png").write_bytes(PNG)
    aged = time.time() - note_attachment_service._RECONCILE_GRACE_SECONDS - 60
    for path in (orphan, root / "report", root / "report" / str(test_project.id)):
        os.utime(path, (aged, aged))

    assert note_attachment_service.reconcile_orphan_attachments(db_session) == 1
    assert not orphan.exists()
    stored = root / db_session.get(NoteAttachment, att).storage_path
    assert stored.is_file() and stored.read_bytes() == PNG
    assert client.get(_attachment(test_project, att)).status_code == 200
