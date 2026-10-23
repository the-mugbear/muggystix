"""A finding's revision rises with every write (backlog 2026-10-22, H-f).

Every finding write route answers with the whole finding as it stood when
that write committed; two writes of different fields can answer in the other
order than they committed.  The page keeps the higher of two answers by
``revision`` (``findingCache.putFindingAnswer``), so the number must rise on
EVERY write of the finding row or one of its endpoints, whoever makes it — and
it has one writer, ``models_findings._count_finding_revisions``.

Pins:
* each write route of a finding (single, bulk, endpoints, hosts) answers with
  a higher revision than the finding had, and the stored number is it;
* the listener counts a change of the row, and an endpoint added, changed or
  removed — with the finding loaded in the session or not — once per COMMIT
  (several flushes and a savepoint are one; a savepoint's release counts
  nothing yet);
* a commit that changes nothing of the finding leaves it alone, and a finding
  inserted starts at 1;
* nothing else in ``app/`` writes the column.
"""
import pathlib
import re

from app.db import models
from app.db.models_findings import Finding, FindingHost


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _create(client, db_session, project_id, ips):
    hosts = [_host(db_session, project_id, ip) for ip in ips]
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{project_id}/findings",
        json={"title": "SMB signing disabled", "severity": "high", "host_ids": [h.id for h in hosts]},
    )
    assert r.status_code == 201, r.text
    return r.json(), hosts


def _stored(db_session, finding_id):
    db_session.expire_all()
    return db_session.get(Finding, finding_id).revision


def _stored_now(db_session, finding_id):
    """The stored number, read without flushing or expiring anything."""
    from sqlalchemy import text
    return db_session.connection().execute(
        text("SELECT revision FROM findings WHERE id = :i"), {"i": finding_id},
    ).scalar()


def test_every_write_route_answers_with_a_higher_revision(client, db_session, test_project, test_user):
    pid = test_project.id
    finding, hosts = _create(client, db_session, pid, ["10.9.0.1", "10.9.0.2", "10.9.0.3"])
    assert finding["revision"] >= 1
    base = f"/api/v1/projects/{pid}/findings/{finding['id']}"
    extra = _host(db_session, pid, "10.9.0.4")
    db_session.commit()
    rows = {h["ip_address"]: h["id"] for h in finding["hosts"]}

    writes = [
        ("rename", lambda: client.patch(base, json={"title": "SMB signing not required"})),
        ("severity", lambda: client.patch(base, json={"severity": "medium"})),
        ("owner", lambda: client.patch(base, json={"owner_id": test_user.id})),
        ("report text", lambda: client.patch(base, json={"description": "Signing is off."})),
        ("status", lambda: client.post(f"{base}/status", json={"status": "confirmed"})),
        ("one endpoint", lambda: client.patch(
            f"{base}/endpoints/{rows['10.9.0.1']}", json={"host_status": "retest"})),
        ("several endpoints", lambda: client.patch(
            f"{base}/endpoints",
            json={"finding_host_ids": [rows["10.9.0.2"], rows["10.9.0.3"]], "host_status": "remediated"})),
        ("add hosts", lambda: client.post(f"{base}/hosts", json={"host_ids": [extra.id]})),
        ("detach an endpoint", lambda: client.delete(f"{base}/endpoints/{rows['10.9.0.3']}")),
        ("detach a host", lambda: client.delete(f"{base}/hosts/{hosts[1].id}")),
    ]
    seen = finding["revision"]
    for name, write in writes:
        r = write()
        assert r.status_code == 200, (name, r.text)
        answered = r.json()["revision"]
        assert answered > seen, f"{name}: answered revision {answered} after {seen}"
        assert _stored(db_session, finding["id"]) == answered, name
        seen = answered


def test_the_bulk_routes_raise_each_findings_revision(client, db_session, test_project, test_user):
    pid = test_project.id
    first, _ = _create(client, db_session, pid, ["10.9.1.1"])
    second, _ = _create(client, db_session, pid, ["10.9.1.2"])
    ids = [first["id"], second["id"]]
    r = client.post(f"/api/v1/projects/{pid}/findings/bulk/status", json={"finding_ids": ids, "status": "confirmed"})
    assert r.status_code == 200, r.text
    after_status = [_stored(db_session, i) for i in ids]
    assert after_status == [first["revision"] + 1, second["revision"] + 1]
    r = client.post(f"/api/v1/projects/{pid}/findings/bulk/assign", json={"finding_ids": ids, "assignee_user_id": test_user.id})
    assert r.status_code == 200, r.text
    assert [_stored(db_session, i) for i in ids] == [n + 1 for n in after_status]


def _finding(db, project_id, *ips):
    """A finding and its endpoints, inserted in ONE flush."""
    host_ids = [_host(db, project_id, ip).id for ip in ips]
    f = Finding(project_id=project_id, title="t", severity="high", status="open", source="manual")
    f.hosts = [FindingHost(host_id=h) for h in host_ids]
    db.add(f)
    db.commit()
    return f


def test_a_finding_inserted_with_its_endpoints_starts_at_one(db_session, test_project):
    f = _finding(db_session, test_project.id, "10.9.2.1", "10.9.2.2")
    assert _stored(db_session, f.id) == 1


def test_the_listener_counts_the_row_and_each_endpoint_change(db_session, test_project):
    pid = test_project.id
    f = _finding(db_session, pid, "10.9.3.1")
    fid = f.id

    # The row: two fields over two flushes and a savepoint are one commit.
    f.title = "renamed"
    db_session.flush()
    with db_session.begin_nested():
        f.severity = "low"
    assert _stored_now(db_session, fid) == 1, "a savepoint's release is not the commit"
    db_session.commit()
    assert _stored(db_session, fid) == 2

    # An endpoint changed, with the finding not loaded in the session.
    db_session.expunge_all()
    row = db_session.query(FindingHost).filter_by(finding_id=fid).one()
    row.host_status = "remediated"
    db_session.commit()
    assert _stored(db_session, fid) == 3

    # An endpoint added through its id, and one removed.
    db_session.expunge_all()
    db_session.add(FindingHost(finding_id=fid, host_id=_host(db_session, pid, "10.9.3.2").id))
    db_session.commit()
    assert _stored(db_session, fid) == 4
    db_session.delete(db_session.query(FindingHost).filter_by(finding_id=fid).first())
    db_session.commit()
    assert _stored(db_session, fid) == 5


def test_a_flush_that_changes_nothing_of_the_finding_leaves_it_alone(db_session, test_project):
    f = _finding(db_session, test_project.id, "10.9.4.1")
    fid = f.id
    f.title = f.title                                   # the same value: no change
    _host(db_session, test_project.id, "10.9.4.2")      # another table
    db_session.commit()
    assert _stored(db_session, fid) == 1


def test_only_the_listener_writes_the_revision():
    """``Finding.revision`` is named in the model module alone, and no
    finding-named object has its ``revision`` assigned anywhere else."""
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    elsewhere = []
    for path in app.rglob("*.py"):
        if path.name == "models_findings.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "Finding.revision" in line or re.search(r"\b\w*finding\w*\.revision\s*=(?!=)", line, re.I):
                elsewhere.append(f"{path.relative_to(app)}:{n}")
    assert elsewhere == []
