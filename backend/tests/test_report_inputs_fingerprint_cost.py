"""Review 2026-10-21, A3 — a draft's freshness check reads its images in a
bounded number of statements.

``inputs_fingerprint`` is asked whenever the report page is focused, a save
lands or a preview completes.  It resolved each image the dataset names with
its own ``get`` — a statement per screenshot, thousands on a large report
(the session no longer holds the rows ``build`` loaded: it hands back plain
data).  The live resolver now reads every named image up front, a thousand
to a statement, this project's only; what it answers is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event

from app.db.models import NoteAttachment
from app.db.models_project import Project
from app.services import client_report_render as render


def _attachments(db, project, n):
    rows = [
        NoteAttachment(project_id=project.id, filename=f"shot-{i}.png", content_type="image/png",
                       storage_path=f"missing/{i}.png", size_bytes=1)
        for i in range(n)
    ]
    db.add_all(rows)
    db.commit()
    ids = [r.id for r in rows]
    _forget_attachments(db)  # as after `build`: nothing of them kept in the session
    return ids


def _forget_attachments(db):
    for obj in list(db.identity_map.values()):
        if isinstance(obj, NoteAttachment):
            db.expunge(obj)


def _count_attachment_reads(db, fn):
    seen = []
    listener = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        result = fn()
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)
    return result, sum(1 for s in seen if "note_attachments" in s)


def test_the_fingerprint_reads_a_reports_images_in_bounded_statements(db_session, test_project):
    ids = _attachments(db_session, test_project, 150)
    other = Project(name="elsewhere", slug="fp-elsewhere")
    db_session.add(other)
    db_session.commit()
    (foreign,) = _attachments(db_session, other, 1)
    dataset = {"report": {"date": "today"},
               "findings": [{"images": [{"attachment_id": i} for i in ids + [foreign, 999_999]]}]}
    report = SimpleNamespace(project_id=test_project.id, template="pentest")

    first, reads = _count_attachment_reads(db_session, lambda: render.inputs_fingerprint(db_session, report, dataset))
    assert reads == 1, reads
    # The same answer as the one-by-one resolver gave: every image named,
    # none on disk here, another project's and a missing one as "missing".
    resolve = render.live_evidence_resolver(db_session, test_project.id, dataset)
    assert resolve({"attachment_id": foreign}) is None and resolve({"attachment_id": 999_999}) is None
    _forget_attachments(db_session)
    second, _ = _count_attachment_reads(db_session, lambda: render.inputs_fingerprint(db_session, report, dataset))
    assert first == second


def test_a_resolver_without_the_dataset_still_answers_one_by_one(db_session, test_project):
    (att_id,) = _attachments(db_session, test_project, 1)
    resolve = render.live_evidence_resolver(db_session, test_project.id)
    assert resolve({"attachment_id": att_id}) is None  # its file is not on disk
    assert resolve({"attachment_id": 123_456}) is None
