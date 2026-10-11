"""Per-endpoint state on a finding (v2.349.0; design review item 7).

A finding's status is the issue's; each affected endpoint keeps its own
state, so confirmation or remediation on one host never implies it on the
others.  Pins:

* the response rolls the endpoint states up (``endpoint_status_counts``);
* PATCH on one endpoint row changes only that row and leaves the finding's
  status alone;
* the move is written to the finding's history with the endpoint named;
* an unknown state or a row that is not on the finding is refused.
"""
from app.db import models
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding_with_hosts(client, db_session, project_id, ips):
    hosts = [_host(db_session, project_id, ip) for ip in ips]
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{project_id}/findings",
        json={"title": "SMB signing disabled", "severity": "high", "host_ids": [h.id for h in hosts]},
    )
    assert r.status_code == 201, r.text
    return r.json(), hosts


def test_endpoint_state_is_per_row_and_rolled_up(client, db_session, test_project):
    finding, hosts = _finding_with_hosts(client, db_session, test_project.id, ["10.4.0.1", "10.4.0.2", "10.4.0.3"])
    assert finding["endpoint_status_counts"] == {"open": 3}
    row = next(h for h in finding["hosts"] if h["ip_address"] == "10.4.0.2")

    # Confirm the finding as an issue; every endpoint stays "open here".
    r = client.post(f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/status", json={"status": "confirmed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["endpoint_status_counts"] == {"open": 3}

    # Remediate ONE endpoint: the finding's status is untouched, the row moved.
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/endpoints/{row['id']}",
        json={"host_status": "remediated"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["endpoint_status_counts"] == {"open": 2, "remediated": 1}
    states = {h["ip_address"]: h["host_status"] for h in body["hosts"]}
    assert states == {"10.4.0.1": "open", "10.4.0.2": "remediated", "10.4.0.3": "open"}

    # The move is on the trail, naming the endpoint, without a status change.
    entries = (
        db_session.query(FindingStatusHistory)
        .filter(FindingStatusHistory.finding_id == finding["id"])
        .order_by(FindingStatusHistory.id)
        .all()
    )
    last = entries[-1]
    assert last.from_status == "confirmed" and last.to_status == "confirmed"
    assert "10.4.0.2" in last.summary and "open → remediated" in last.summary

    # The list row carries the same rollup.
    listed = client.get(
        f"/api/v1/projects/{test_project.id}/findings", params={"search": "SMB signing"},
    ).json()
    assert [f["id"] for f in listed["items"]] == [finding["id"]], listed
    assert listed["items"][0]["endpoint_status_counts"] == {"open": 2, "remediated": 1}


def test_endpoint_state_validation(client, db_session, test_project):
    finding, hosts = _finding_with_hosts(client, db_session, test_project.id, ["10.4.1.1"])
    row_id = finding["hosts"][0]["id"]
    bad = client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/endpoints/{row_id}",
        json={"host_status": "confirmed"},
    )
    assert bad.status_code == 422
    assert "host_status" in bad.json()["detail"]

    missing = client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/endpoints/999999",
        json={"host_status": "retest"},
    )
    assert missing.status_code == 404

    # Setting the same state again is a no-op that writes no history line.
    before = db_session.query(FindingStatusHistory).filter(FindingStatusHistory.finding_id == finding["id"]).count()
    same = client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/endpoints/{row_id}",
        json={"host_status": "open"},
    )
    assert same.status_code == 200
    after = db_session.query(FindingStatusHistory).filter(FindingStatusHistory.finding_id == finding["id"]).count()
    assert after == before


# ---------------------------------------------------------------------------
# Review 2026-10-10 H2 — every writer of an endpoint's state is the one step
# ---------------------------------------------------------------------------

def _trail(db, finding_id):
    return [
        e.summary for e in db.query(FindingStatusHistory)
        .filter(FindingStatusHistory.finding_id == finding_id)
        .order_by(FindingStatusHistory.id)
    ]


def test_restoring_onto_an_attached_endpoint_is_the_one_step(client, db_session, test_project):
    """``POST /findings/{id}/hosts {endpoints: [...]}`` for a host ALREADY on
    the finding set its state with no check and no history line."""
    finding, hosts = _finding_with_hosts(client, db_session, test_project.id, ["10.4.2.1", "10.4.2.2"])
    url = f"/api/v1/projects/{test_project.id}/findings/{finding['id']}/hosts"
    before = _trail(db_session, finding["id"])

    # A value that is no endpoint state is refused, and nothing is stored.
    for bad in ("confirmed", "x" * 300):
        r = client.post(url, json={"endpoints": [{"host_id": hosts[0].id, "host_status": bad}]})
        assert r.status_code == 422, r.text
    db_session.expire_all()
    assert {r.host_status for r in db_session.query(FindingHost).filter_by(finding_id=finding["id"])} == {"open"}
    assert _trail(db_session, finding["id"]) == before

    # A real state lands through the endpoint step: the row moves and the
    # trail says which endpoint, from what, to what.
    r = client.post(url, json={"endpoints": [{"host_id": hosts[0].id, "host_status": "remediated"}]})
    assert r.status_code == 200, r.text
    assert r.json()["endpoint_status_counts"] == {"open": 1, "remediated": 1}
    added = _trail(db_session, finding["id"])[len(before):]
    assert len(added) == 1, added
    assert "10.4.2.1" in added[0] and "open → remediated" in added[0]

    # The same state again writes nothing.
    r = client.post(url, json={"endpoints": [{"host_id": hosts[0].id, "host_status": "remediated"}]})
    assert r.status_code == 200, r.text
    assert len(_trail(db_session, finding["id"])) == len(before) + 1


def test_removing_and_restoring_an_endpoint_is_on_the_trail(client, db_session, test_project):
    finding, hosts = _finding_with_hosts(client, db_session, test_project.id, ["10.4.3.1", "10.4.3.2", "10.4.3.3"])
    base = f"/api/v1/projects/{test_project.id}/findings/{finding['id']}"
    row = next(h for h in finding["hosts"] if h["ip_address"] == "10.4.3.2")
    assert client.patch(f"{base}/endpoints/{row['id']}", json={"host_status": "retest"}).status_code == 200
    before = _trail(db_session, finding["id"])

    # One endpoint row: the line names it and the state it was in.
    assert client.delete(f"{base}/endpoints/{row['id']}").status_code == 200
    added = _trail(db_session, finding["id"])[len(before):]
    assert len(added) == 1 and "Removed endpoint 10.4.3.2" in added[0] and "retest" in added[0], added

    # The Undo: the row is back in its state, and both steps are said.
    r = client.post(f"{base}/hosts", json={"endpoints": [{"host_id": row["host_id"], "host_status": "retest"}]})
    assert r.status_code == 200, r.text
    assert {h["ip_address"]: h["host_status"] for h in r.json()["hosts"]}["10.4.3.2"] == "retest"
    added = _trail(db_session, finding["id"])[len(before) + 1:]
    assert len(added) == 2, added
    assert "Restored endpoint 10.4.3.2" in added[0]
    assert "10.4.3.2" in added[1] and "open → retest" in added[1]

    # Every endpoint of a host.
    before = _trail(db_session, finding["id"])
    assert client.delete(f"{base}/hosts/{hosts[2].id}").status_code == 200
    added = _trail(db_session, finding["id"])[len(before):]
    assert len(added) == 1 and "Removed endpoint 10.4.3.3" in added[0] and "(was open)" in added[0], added
    assert db_session.query(FindingHost).filter_by(finding_id=finding["id"], host_id=hosts[2].id).count() == 0
