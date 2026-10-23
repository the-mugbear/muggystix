"""ONE import history (owner decision 2026-10-22, backlog G15, option A).

The Scans page had two lists of the same uploads: the import history (scans
and batches) and the Ingestion results view (every upload).  Most rows of the
second were completed imports the first already listed.  Now the history
lists the uploads that were NOT imported as rows of its own order — for
analysts and above — and a status filter turns it into the flat list of the
uploads one of Ingestion results' views names, in the same server order.

Pinned here:
* the order interleaves batches, single files and unsuccessful uploads by
  upload time, and paging through it neither repeats nor skips a row;
* a completed import is its scan row, never also an upload row; a batched
  failure is its batch's, never a row of its own;
* every status count == the list it opens (paged through, past a page), and
  "Needs attention" is ``blocked_import_condition`` — the Operations figure;
* a viewer gets no upload rows, no counts, and a 403 for a status filter.
"""
from datetime import datetime, timedelta, timezone

from app.api.deps import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.services.operations_read_service import blocked_import_condition
from app.services.staged_import_service import DISCARDED_MESSAGE, EXPIRED_MESSAGE_PREFIX


NOW = datetime.now(timezone.utc)


def _at(hours):
    return NOW - timedelta(hours=hours)


def _fixture(db, project):
    """Newest first (hours ago):
    0.5 upload `f_new` (failed, unbatched)
    1   scan s3 (its job `s3_job` completed — the scan row, not an upload row)
    1.5 upload `expired`
    2   batch B (b1 at 2.4h, plus a FAILED batched job `b_fail` at 2.2h)
    2.5 upload `discarded`
    3   scan s2 · its job completed PARTIAL (`partial_job`) — needs attention, a scan row
    3.5 upload `f_dismissed` (failed, dismissed)
    4   scan s1
    4.5 upload `staged` (in flight: the queue's, not a history row)
    5   upload `f_old` (failed)
    """
    def batch(label, hours):
        b = models.ScanBatch(project_id=project.id, label=label, created_at=_at(hours))
        db.add(b)
        db.flush()
        return b

    def scan(name, hours, batch_id=None):
        s = models.Scan(project_id=project.id, filename=name, tool_name="nmap",
                        scan_type="port_scan", created_at=_at(hours), batch_id=batch_id)
        db.add(s)
        db.flush()
        return s

    def job(name, hours, status="failed", **kw):
        j = models.IngestionJob(project_id=project.id, filename=name, original_filename=name,
                                storage_path="/x", status=status, created_at=_at(hours), **kw)
        db.add(j)
        db.flush()
        return j

    b = batch("B", 2.5)
    out = {
        "B": b,
        "b1": scan("b1.xml", 2.4, batch_id=b.id),
        "s1": scan("s1.xml", 4), "s2": scan("s2.xml", 3), "s3": scan("s3.xml", 1),
    }
    out.update({
        "f_new": job("new.xml", 0.5, error_message="SMBMap parser found 0 hosts"),
        "s3_job": job("s3.xml", 1, status="completed", scan_id=out["s3"].id),
        # An expiry and a discard are written as dismissed failures, as the server does.
        "expired": job("exp.xml", 1.5, error_message=f"{EXPIRED_MESSAGE_PREFIX} after 24 hours",
                       dismissed_at=_at(1.4)),
        "b_fail": job("bfail.xml", 2.2, batch_id=b.id, error_message="bad"),
        "discarded": job("disc.xml", 2.5, error_message=DISCARDED_MESSAGE, dismissed_at=_at(2.4)),
        "partial_job": job("s2.xml", 3, status="completed", partial=True, scan_id=out["s2"].id),
        "f_dismissed": job("dism.xml", 3.5, error_message="bad", dismissed_at=_at(3.4)),
        "staged": job("staged.xml", 4.5, status="staged"),
        "f_old": job("old.xml", 5, error_message="bad dmz"),
    })
    db.commit()
    return out


def _url(project):
    return f"/api/v1/projects/{project.id}/scans/history"


def _keys(body):
    return [(e["kind"], e["id"]) for e in body["items"]]


def _as_member(db, project, role):
    user = User(id=9000 + len(role.value), username=f"u-{role.value}", email=f"{role.value}@example.com", hashed_password="x",
                role=UserRole.MEMBER, is_active=True, created_at=NOW)
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    return user


def test_the_history_interleaves_the_uploads_that_were_not_imported(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    body = client.get(_url(test_project)).json()
    assert _keys(body) == [
        ("upload", f["f_new"].id),
        ("scan", f["s3"].id),
        ("upload", f["expired"].id),
        ("batch", f["B"].id),
        ("upload", f["discarded"].id),
        ("scan", f["s2"].id),
        ("upload", f["f_dismissed"].id),
        ("scan", f["s1"].id),
        ("upload", f["f_old"].id),
    ]
    # A completed import is its scan row (never also an upload row); a batched
    # failure is its batch's; a staged upload is the queue's.
    uploads = {i for k, i in _keys(body) if k == "upload"}
    assert not uploads & {f["s3_job"].id, f["partial_job"].id, f["b_fail"].id, f["staged"].id}
    assert (body["total"], body["batch_total"], body["scan_total"], body["upload_total"]) == (9, 1, 3, 5)


def test_paging_through_the_merged_order_repeats_and_skips_nothing(client, db_session, test_project):
    _fixture(db_session, test_project)
    whole = _keys(client.get(_url(test_project)).json())
    for size in (1, 2, 4):
        seen, skip, more = [], 0, True
        while more:
            page = client.get(_url(test_project), params={"skip": skip, "limit": size}).json()
            seen += _keys(page)
            more = page["has_more"]
            skip += size
        assert seen == whole, size


def test_every_status_count_is_the_list_it_opens(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    first = client.get(_url(test_project)).json()
    counts = first["status_counts"]
    for status, count in counts.items():
        ids, skip, more = [], 0, True
        while more:   # paged by 2: every list here is longer than a page or empty
            page = client.get(_url(test_project), params={"status": status, "skip": skip, "limit": 2}).json()
            assert all(e["kind"] == "upload" for e in page["items"])
            assert page["total"] == count, status
            ids += [e["id"] for e in page["items"]]
            more = page["has_more"]
            skip += 2
        assert len(ids) == len(set(ids)) == count, status

    # "Needs attention" is the one definition of imports that need someone:
    # failed or partial, not dismissed, not superseded — batched ones included.
    blocked = {
        jid for (jid,) in db_session.query(models.IngestionJob.id).filter(
            models.IngestionJob.project_id == test_project.id, blocked_import_condition(),
        )
    }
    listed = {e["id"] for e in client.get(
        _url(test_project), params={"status": "needs_attention", "limit": 50},
    ).json()["items"]}
    assert listed == blocked == {
        f["f_new"].id, f["b_fail"].id, f["partial_job"].id, f["f_old"].id,
    }
    assert counts["needs_attention"] == len(blocked)
    # G7: Failed says how many of it are dismissed, and that part is its own list.
    assert (counts["failed"], counts["failed_dismissed"]) == (4, 1)
    assert counts["expired"] == counts["discarded"] == 1


def test_the_counts_follow_the_history_filters_and_still_open_their_list(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    params = {"search": "dmz"}
    counts = client.get(_url(test_project), params=params).json()["status_counts"]
    assert counts["needs_attention"] == 1
    listed = client.get(_url(test_project), params={**params, "status": "needs_attention"}).json()
    assert [e["id"] for e in listed["items"]] == [f["f_old"].id]
    # The grouped history under a search lists the upload that matches it.
    assert ("upload", f["f_old"].id) in _keys(client.get(_url(test_project), params=params).json())


def test_a_link_to_one_upload_lists_that_upload(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    pe = models.ParseError(project_id=test_project.id, filename="old.xml", file_type="xml",
                           error_type="parsing_error", error_message="bad dmz", status="unresolved")
    db_session.add(pe)
    db_session.flush()
    f["f_old"].parse_error_id = pe.id
    db_session.commit()
    by_job = client.get(_url(test_project), params={"job_id": f["s3_job"].id}).json()
    assert _keys(by_job) == [("upload", f["s3_job"].id)]
    by_error = client.get(_url(test_project), params={"error_id": pe.id}).json()
    assert _keys(by_error) == [("upload", f["f_old"].id)]


def test_an_unknown_status_is_refused(client, db_session, test_project):
    r = client.get(_url(test_project), params={"status": "broken"})
    assert r.status_code == 422
    assert "needs_attention" in r.text


def test_a_viewer_reads_the_history_without_the_uploads(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    _as_member(db_session, test_project, ProjectRole.VIEWER)
    body = client.get(_url(test_project)).json()
    assert _keys(body) == [
        ("scan", f["s3"].id), ("batch", f["B"].id), ("scan", f["s2"].id), ("scan", f["s1"].id),
    ]
    assert (body["total"], body["upload_total"], body["status_counts"]) == (4, 0, None)
    for params in ({"status": "needs_attention"}, {"job_id": f["f_old"].id}, {"error_id": 1}):
        assert client.get(_url(test_project), params=params).status_code == 403
    # The rows' detail stays the analyst's read.
    assert client.get(
        f"/api/v1/projects/{test_project.id}/ingestion-results", params={"ids": str(f["f_old"].id)},
    ).status_code == 403


def test_an_analyst_gets_the_uploads(client, db_session, test_project):
    f = _fixture(db_session, test_project)
    _as_member(db_session, test_project, ProjectRole.ANALYST)
    body = client.get(_url(test_project)).json()
    assert body["upload_total"] == 5
    assert body["status_counts"]["needs_attention"] == 4
    rows = client.get(
        f"/api/v1/projects/{test_project.id}/ingestion-results",
        params={"ids": f"{f['f_old'].id},{f['expired'].id}"},
    ).json()
    assert sorted(r["id"] for r in rows["items"]) == sorted([f["f_old"].id, f["expired"].id])
    assert rows["total"] == 2
