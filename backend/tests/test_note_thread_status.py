"""Review #5 — activity status filter/counts/history use THREAD (root)
status, not a reply's, so a resolved thread with an open reply is
consistent everywhere.  Notes are created through the API so
``thread_root_id`` is stamped (create_note).  ``client`` is a global admin.
"""
from __future__ import annotations

from app.db import models


def _make_host(db_session, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(h)
    db_session.flush()
    return h


def _notes_base(pid, host_id):
    return f"/api/v1/projects/{pid}/hosts/{host_id}/notes"


def test_thread_status_filter_counts_and_history(client, db_session, test_project):
    host = _make_host(db_session, test_project.id, "10.10.0.1")
    base = _notes_base(test_project.id, host.id)

    # Root note (open) + an open reply.
    root = client.post(base, json={"body": "root", "status": "open"}).json()
    reply = client.post(base, json={"body": "reply", "parent_id": root["id"]}).json()

    # Resolve the thread (root) with a summary.
    r = client.patch(
        f"{base}/{root['id']}",
        json={"status": "resolved", "resolution_summary": "done"},
    )
    assert r.status_code == 200, r.text

    activity_url = f"/api/v1/projects/{test_project.id}/hosts/notes/activity"

    # Filtering by resolved returns the whole thread (root + open reply).
    resolved = client.get(activity_url, params={"status": "resolved"}).json()
    ids = {n["note_id"] for n in resolved["notes"]}
    assert {root["id"], reply["id"]} <= ids
    # Counts are thread-level: this thread counts once as resolved, not open.
    assert resolved["status_counts"]["resolved"] >= 1

    # Filtering by open must NOT return the resolved thread's messages,
    # even though the reply's own status is open.
    open_ = client.get(activity_url, params={"status": "open"}).json()
    open_ids = {n["note_id"] for n in open_["notes"]}
    assert root["id"] not in open_ids
    assert reply["id"] not in open_ids

    # History requested via the REPLY id resolves to the thread root.
    hist = client.get(f"{base}/{reply['id']}/history").json()
    assert any(h["to_status"] == "resolved" for h in hist)
