"""The Evidence section of a finding (2.495.0): its written text, and the note
each trailing screenshot is printed after.

* ``evidence_text`` is a sixth report text field: saved through the finding's
  own PATCH like the others (the authored-content rule, the one reference
  grammar, the delete guard), proposed and accepted like the others, printed
  with the images it places — and NOT required text, and not a section the
  in-app drafter writes.
* An image no section places prints under Evidence after THE TEXT OF THE
  NOTE IT IS ATTACHED TO (owner, 2026-10-13).  The dataset names the note on
  the first image of each note within the finding's block
  (``ClientReportService._note_preambles``), in one statement for the whole
  report; each trailing image carries its own data path so the template can
  ask for it; and the summary counts the notes this report prints
  (``evidence_notes``), because a note was written for the team.

What the template then prints is in ``test_report_templates_shipped.py``; the
rendered result in ``test_quarto_render.py`` (Quarto).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment
from app.db.models_findings import Finding, FindingHost
from app.db.models_reports import Report
from app.services import quarto_render, report_images, report_writing_guidance
from app.services.client_report_service import REQUIRED_TEXT, ClientReportService
from app.services.report_text import REPORT_TEXT_FIELDS

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
# As the shipped pentest template does it: the Evidence text with its images,
# then each unplaced image after its note's text.
PENTEST = (
    "---\ntitle: x\n---\n"
    "<% for f in findings %>\n"
    '<% for field in ("description", "impact", "steps_to_reproduce", "recommendation", "references", "evidence_text") %>'
    "<< md(f, field) >><% endfor %>\n"
    '<% for e in f.evidence %><% if e.get("note") %><< md(e, "note", images=False) >><% endif %>'
    "<< image(e) >><% endfor %>\n"
    "<% endfor %>\n"
)


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    for name, declared, body in (
        ("pentest", None, PENTEST),
        # A brief: the recommendation only, no trailing evidence block.
        ("brief", {"fields": [], "trailing": False},
         '---\ntitle: x\n---\n<% for f in findings %><< md(f, "recommendation", images=False) >><% endfor %>\n'),
        # A template written before notes were printed (an operator's own, or
        # the contact's list): the trailing images, without their notes.
        ("imagesonly", {"fields": [], "trailing": True},
         "---\ntitle: x\n---\n<% for f in findings %><% for e in f.evidence %><< image(e) >><% endfor %><% endfor %>\n"),
    ):
        (root / name).mkdir(parents=True)
        manifest = {"title": name, "entry": "report.qmd", "formats": ["html", "docx"]}
        if declared is not None:
            manifest["images"] = declared
        (root / name / "template.json").write_text(json.dumps(manifest))
        (root / name / "report.qmd").write_text(body)
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "uploads" / "client_reports"))
    return tmp_path / "uploads"


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _finding(db, project, title="SMB relay", **text):
    host = models.Host(project_id=project.id, ip_address=f"10.79.0.{db.query(models.Host).count() + 1}", state="up")
    db.add(host)
    db.flush()
    text = {"description": "d", "impact": "i", "recommendation": "r", **text}
    f = Finding(project_id=project.id, title=title, severity="high", status="confirmed", source="manual", **text)
    db.add(f)
    db.flush()
    db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db.commit()
    return f


def _note(client, project, finding, body):
    r = client.post(f"{_base(project)}/findings/{finding.id}/notes", json={"body": body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _image(client, project, finding, note_id, *, name="shot.png", ticked=True):
    r = client.post(
        f"{_base(project)}/findings/{finding.id}/notes/{note_id}/attachments",
        files={"file": (name, PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    if ticked:
        r2 = client.patch(f"{_base(project)}/hosts/notes/attachments/{r.json()['id']}", json={"include_in_report": True})
        assert r2.status_code == 200, r2.text
    return r.json()["id"]


def _set_text(client, project, finding, **fields):
    return client.patch(f"{_base(project)}/findings/{finding.id}", json=fields)


def _draft(client, project, template="pentest"):
    r = client.post(f"{_base(project)}/client-reports", json={"kind": "full", "template": template})
    assert r.status_code == 201, r.text
    return r.json()


def _build(db, report_id):
    db.expire_all()
    return ClientReportService(db).build(db.get(Report, report_id))


def _dataset(db, client, project, template="pentest"):
    dataset, _, summary = _build(db, _draft(client, project, template)["id"])
    return dataset, summary


# ---------------------------------------------------------------------------
# The Evidence text is report text
# ---------------------------------------------------------------------------

def test_the_evidence_text_is_a_report_field_and_not_required_or_drafted():
    assert REPORT_TEXT_FIELDS[-1] == "evidence_text" and len(REPORT_TEXT_FIELDS) == 6
    assert "evidence_text" not in REQUIRED_TEXT
    assert "evidence_text" not in report_writing_guidance.SECTIONS
    assert report_images.FIELD_LABELS["evidence_text"] == "Evidence"
    assert set(report_images.FIELD_LABELS) == set(REPORT_TEXT_FIELDS)        # every field has its name for a refusal


def test_the_evidence_text_is_saved_like_any_section_and_reaches_the_report(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    r = _set_text(client, test_project, finding, evidence_text="  The banner names the build.\n\n| a | b |\n|---|---|\n| 1 | 2 |  ")
    assert r.status_code == 200, r.text
    saved = "The banner names the build.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert r.json()["report_text"]["evidence_text"] == saved
    db_session.expire_all()
    assert db_session.get(Finding, finding.id).evidence_text == saved
    assert client.get(f"{_base(test_project)}/findings/{finding.id}").json()["report_text"]["evidence_text"] == saved

    dataset, summary = _dataset(db_session, client, test_project)
    assert dataset["findings"][0]["evidence_text"] == saved
    # It is never owed: a finding without it is not "missing text" …
    assert summary["missing_text"] == []
    # … and blanking it stores nothing.
    assert _set_text(client, test_project, finding, evidence_text="   ").status_code == 200
    db_session.expire_all()
    assert db_session.get(Finding, finding.id).evidence_text is None
    dataset, summary = _dataset(db_session, client, test_project)
    assert dataset["findings"][0]["evidence_text"] is None and summary["missing_text"] == []
    # The same length limit as the other sections.
    assert _set_text(client, test_project, finding, evidence_text="x" * 32769).status_code == 422


def test_an_image_placed_in_the_evidence_text_prints_there_and_is_protected(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    note = _note(client, test_project, finding, "Where the placed one came from.")
    placed = _image(client, test_project, finding, note, name="placed.png")
    loose = _image(client, test_project, finding, note, name="loose.png")
    # A tolerated spelling is stored in the one form, as in every section.
    r = _set_text(client, test_project, finding, evidence_text=f"Seen here:\n\n![The session](<evidence:{placed}>)")
    assert r.status_code == 200, r.text
    assert r.json()["report_text"]["evidence_text"] == f"Seen here:\n\n![The session](evidence:{placed})"

    dataset, summary = _dataset(db_session, client, test_project)
    item = dataset["findings"][0]
    assert list(item["placed"]) == ["evidence_text"] and list(item["placed"]["evidence_text"]) == [str(placed)]
    images = {i["attachment_id"]: i for i in item["images"]}
    assert images[placed]["placed_in"] == ["evidence_text"] and images[placed]["printed_in"] == ["evidence_text"]
    assert [e["attachment_id"] for e in item["evidence"]] == [loose]          # the trailing block: the other one
    assert (summary["images_placed"], summary["images_unplaced"], summary["images_printed"]) == (1, 1, 1)
    listed = {i["id"]: i for i in client.get(f"{_base(test_project)}/findings/{finding.id}/images").json()["items"]}
    assert listed[placed]["placed_in"] == ["evidence_text"]

    # Deleting or un-ticking it would leave the section pointing at nothing.
    r = client.delete(f"{_base(test_project)}/hosts/notes/attachments/{placed}")
    assert r.status_code == 409 and "Evidence" in r.json()["detail"]
    r = client.patch(f"{_base(test_project)}/hosts/notes/attachments/{placed}", json={"include_in_report": False})
    assert r.status_code == 409 and "Evidence" in r.json()["detail"]
    assert _set_text(client, test_project, finding, evidence_text="Words only.").status_code == 200
    assert client.delete(f"{_base(test_project)}/hosts/notes/attachments/{placed}").status_code == 204


def test_an_agent_proposes_the_evidence_text_and_accepting_writes_it(client, db_session, test_project):
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    key = {"X-API-Key": r.json()["api_key"]}
    finding = _finding(db_session, test_project, created_by_id=None)
    r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding.id, "fields": {"evidence_text": "The response header names the build."},
    })
    assert r.status_code == 201, r.text
    (proposal,) = r.json()["proposals"]
    assert proposal["field"] == "evidence_text"
    db_session.expire_all()
    assert db_session.get(Finding, finding.id).evidence_text is None           # a proposal, not a write
    acc = client.post(f"{_base(test_project)}/proposals/{proposal['id']}/accept", json={})
    assert acc.status_code == 200, acc.text
    db_session.expire_all()
    assert db_session.get(Finding, finding.id).evidence_text == "The response header names the build."
    # The agents' finding read carries it, as the page's does.
    read = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=key)
    assert read.status_code == 200, read.text
    assert read.json()["report_text"]["evidence_text"] == "The response header names the build."
    # A field that is not report text is still refused by name.
    bad = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding.id, "fields": {"evidence": "x"},
    })
    assert bad.status_code == 422 and "evidence" in bad.text


# ---------------------------------------------------------------------------
# The note printed before a trailing image
# ---------------------------------------------------------------------------

def test_a_note_is_named_on_the_first_of_its_unplaced_images_only(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    first = _note(client, test_project, finding, "  Taken from the jump host, **before** the exploit.\n")
    second = _note(client, test_project, finding, "A second note.")
    a1 = _image(client, test_project, finding, first, name="a1.png")
    b1 = _image(client, test_project, finding, second, name="b1.png")
    a2 = _image(client, test_project, finding, first, name="a2.png")
    a3 = _image(client, test_project, finding, first, name="a3.png")
    _image(client, test_project, finding, second, name="unticked.png", ticked=False)

    dataset, summary = _dataset(db_session, client, test_project)
    item = dataset["findings"][0]
    assert [(e["attachment_id"], e["note"]) for e in item["evidence"]] == [
        (a1, "Taken from the jump host, **before** the exploit."),       # as written, trimmed
        (b1, "A second note."),
        (a2, None), (a3, None),                                          # their note was printed before a1
    ]
    # Each has its own place in the data, in block order — what md(e, "note") reads.
    assert [e["_path"] for e in item["evidence"]] == [f"findings.0.evidence.{n}" for n in range(4)]
    assert quarto_render._lookup(dataset, "findings.0.evidence.0.note") == item["evidence"][0]["note"]
    # The note's id was the builder's working value: it is not part of the dataset.
    assert all("note_id" not in e for e in item["evidence"])
    # `images` is every ticked image, without a note: the two lists are separate rows.
    assert all("note" not in i and "_path" not in i for i in item["images"])
    assert summary["evidence_notes"] == 2


def test_a_placed_image_does_not_print_its_note_but_a_later_unplaced_one_does(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    note = _note(client, test_project, finding, "Both screenshots are of the same session.")
    placed = _image(client, test_project, finding, note, name="placed.png")
    loose = _image(client, test_project, finding, note, name="loose.png")
    assert _set_text(client, test_project, finding, description=f"![](evidence:{placed})").status_code == 200

    dataset, summary = _dataset(db_session, client, test_project)
    item = dataset["findings"][0]
    assert [(e["attachment_id"], e["note"]) for e in item["evidence"]] == [
        (loose, "Both screenshots are of the same session."),
    ]
    assert summary["evidence_notes"] == 1
    # The page lists them by finding, with how each begins (walkthrough
    # 2026-10-13: the count alone named nothing a reviewer could open).
    assert summary["evidence_note_findings"] == [{
        "id": finding.id, "ref": item["ref"], "title": item["title"],
        "notes": ["Both screenshots are of the same session."],
    }]
    # Every image placed: nothing trails, and no note is printed.
    assert _set_text(client, test_project, finding, impact=f"![](evidence:{loose})").status_code == 200
    dataset, summary = _dataset(db_session, client, test_project)
    assert dataset["findings"][0]["evidence"] == [] and summary["evidence_notes"] == 0
    assert summary["evidence_note_findings"] == []


def test_a_note_belongs_to_its_findings_block(client, db_session, test_project):
    """"First of its note" is counted per finding: two findings' blocks never
    share a note, and each block's paths start at 0."""
    one = _finding(db_session, test_project, "First finding")
    two = _finding(db_session, test_project, "Second finding")
    n1 = _note(client, test_project, one, "About the first.")
    n2 = _note(client, test_project, two, "About the second.")
    _image(client, test_project, one, n1)
    _image(client, test_project, two, n2)
    _image(client, test_project, two, n2)

    dataset, summary = _dataset(db_session, client, test_project)
    by_title = {f["title"]: f for f in dataset["findings"]}
    assert [e["note"] for e in by_title["First finding"]["evidence"]] == ["About the first."]
    assert [e["note"] for e in by_title["Second finding"]["evidence"]] == ["About the second.", None]
    for f in dataset["findings"]:
        assert [e["_path"] for e in f["evidence"]] == [f"{f['_path']}.evidence.{n}" for n in range(len(f["evidence"]))]
    assert summary["evidence_notes"] == 2


def test_a_note_with_no_words_prints_nothing(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    note = _note(client, test_project, finding, "placeholder")
    att = _image(client, test_project, finding, note)
    db_session.query(Annotation).filter(Annotation.id == note).update({"body": " \n\t "})
    db_session.commit()
    dataset, summary = _dataset(db_session, client, test_project)
    assert [(e["attachment_id"], e["note"]) for e in dataset["findings"][0]["evidence"]] == [(att, None)]
    assert summary["evidence_notes"] == 0


def test_the_notes_are_read_in_one_statement_whatever_the_number_of_images(client, db_session, test_project):
    """A regression test must fail on a per-image read: the build of a report
    with one trailing image and of one with nine, on three notes of two
    findings, issues the same number of statements."""
    finding = _finding(db_session, test_project)
    _image(client, test_project, finding, _note(client, test_project, finding, "The only note."))
    report_id = _draft(client, test_project)["id"]

    def statements() -> list:
        seen = []
        listener = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
        db_session.expire_all()
        report = db_session.get(Report, report_id)
        event.listen(db_session.bind, "before_cursor_execute", listener)
        try:
            dataset, _, summary = ClientReportService(db_session).build(report)
        finally:
            event.remove(db_session.bind, "before_cursor_execute", listener)
        return seen, dataset, summary

    few, dataset, _ = statements()
    assert len(dataset["findings"][0]["evidence"]) == 1

    other = _finding(db_session, test_project, "Another finding")
    for owner, bodies in ((finding, ("Second note.", "Third note.")), (other, ("Fourth note.",))):
        for body in bodies:
            note = _note(client, test_project, owner, body)
            for n in range(3 if owner is finding else 2):
                _image(client, test_project, owner, note, name=f"{n}.png")
    many, dataset, summary = statements()
    assert sum(len(f["evidence"]) for f in dataset["findings"]) == 9
    assert summary["evidence_notes"] == 4

    def reads_of_notes(seen):
        return [s for s in seen if "annotations.body" in s and "note_attachments" not in s]

    assert len(reads_of_notes(few)) == len(reads_of_notes(many)) == 1
    # The second finding adds its own per-report statements nowhere: the
    # whole build grew by none for eight more images.
    assert len(many) == len(few), (len(few), len(many))


def test_the_summary_counts_the_notes_this_report_prints(client, db_session, test_project):
    finding = _finding(db_session, test_project)
    note = _note(client, test_project, finding, "Written for the team.")
    _image(client, test_project, finding, note)
    _image(client, test_project, finding, note)

    full = _draft(client, test_project)
    assert full["summary"]["evidence_notes"] == 1
    # The page's summary is the builder's.
    assert client.get(f"{_base(test_project)}/client-reports/{full['id']}").json()["summary"]["evidence_notes"] == 1
    dataset, _ = _dataset(db_session, client, test_project)
    assert [e["note"] for e in dataset["findings"][0]["evidence"]] == ["Written for the team.", None]

    # What a report prints is measured, never assumed.  A template with no
    # trailing evidence block prints no image there, so no note …
    dataset, summary = _dataset(db_session, client, test_project, template="brief")
    assert summary["evidence_notes"] == 0 and summary["images_not_printed"] == 2
    assert [e["note"] for e in dataset["findings"][0]["evidence"]] == [None, None]
    # … and one that prints the images WITHOUT asking for their notes prints
    # none either: the page must not say a note goes to the client, and the
    # report's data (its snapshot, its source bundle) does not carry a note
    # written for the team that the report never shows.
    dataset, summary = _dataset(db_session, client, test_project, template="imagesonly")
    assert summary["images_trailing"] == 2 and summary["evidence_notes"] == 0
    assert [e["note"] for e in dataset["findings"][0]["evidence"]] == [None, None]
    assert "Written for the team." not in json.dumps(dataset)
    # A finding with no image at all: nothing to count.
    db_session.query(NoteAttachment).delete()
    db_session.commit()
    assert _draft(client, test_project)["summary"]["evidence_notes"] == 0


# ---------------------------------------------------------------------------
# The renderer's step keeps each trailing image's data path true
# ---------------------------------------------------------------------------

def test_a_dropped_image_does_not_leave_the_next_one_reading_its_note(tmp_path):
    """The file of the first trailing image is missing, so the render drops
    it — and the entries move up.  ``md(e, "note")`` reads by path: the path
    is stamped again, or the second image would ask for index 1 of a list
    that now has one entry at 0 and print no note (or another image's)."""
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    def entry(n, att_id, note):
        return {"_path": f"findings.1.evidence.{n}", "attachment_id": att_id, "file": f"evidence/{att_id}.png",
                "caption": f"Shot {att_id}", "placed_in": [], "note": note}

    dataset = {"findings": [
        {"_path": "findings.0", "images": [], "placed": {}, "evidence": []},
        {"_path": "findings.1", "placed": {},
         "evidence": [entry(0, 5, "The first note."), entry(1, 6, "The second note."), entry(2, 7, None)],
         "images": [{"attachment_id": n, "file": f"evidence/{n}.png", "caption": f"Shot {n}", "placed_in": []}
                    for n in (5, 6, 7)]},
    ]}
    work = tmp_path / "work"
    work.mkdir()
    missing = quarto_render._place_evidence(dataset, work, lambda item: None if item["attachment_id"] == 5 else shot)
    assert missing == ["Shot 5"]
    evidence = dataset["findings"][1]["evidence"]
    assert [(e["attachment_id"], e["_path"], e["note"]) for e in evidence] == [
        (6, "findings.1.evidence.0", "The second note."), (7, "findings.1.evidence.1", None),
    ]
    # What the template would now ask for is what the data holds there.
    for e in evidence:
        assert quarto_render._lookup(dataset, f"{e['_path']}.note") == e["note"]
    md = quarto_render._md_factory(dataset)
    assert 'key="findings.1.evidence.0.note" images="none"' in md(evidence[0], "note", images=False)

    # A dataset frozen before the paths existed gains none.
    old = {"findings": [{"_path": "findings.0", "evidence": [
        {"attachment_id": 5, "file": "evidence/5.png", "caption": "Gone"},
        {"attachment_id": 6, "file": "evidence/6.png", "caption": "Kept"},
    ]}]}
    quarto_render._place_evidence(old, work, lambda item: None if item["attachment_id"] == 5 else shot)
    assert old["findings"][0]["evidence"] == [{"attachment_id": 6, "file": "evidence/6.png", "caption": "Kept"}]
