"""The Collaboration feed's host-note threads (file named for review #5's
thread-status rules; notes lost their status in v2.446.0, see
``test_notes_as_work.py``).  What is pinned here: the feed carries no
status, a combined reply PATCH answers with the reply, and a thread's size
is the whole thread.  Notes are created through the API so
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


def test_the_feed_has_no_note_status(client, db_session, test_project):
    host = _make_host(db_session, test_project.id, "10.10.0.1")
    base = _notes_base(test_project.id, host.id)
    root = client.post(base, json={"body": "root"}).json()
    reply = client.post(base, json={"body": "reply", "parent_id": root["id"]}).json()

    activity_url = f"/api/v1/projects/{test_project.id}/hosts/notes/activity"
    # A leftover ?status= from an old link narrows nothing.
    feed = client.get(activity_url, params={"status": "resolved"}).json()
    assert {root["id"], reply["id"]} <= {n["note_id"] for n in feed["notes"]}
    assert "status_counts" not in feed
    assert all("status" not in n and "thread_root_status" not in n for n in feed["notes"])
    assert all(n["thread_root_id"] == root["id"] for n in feed["notes"])


def test_combined_reply_patch_returns_reply_not_root(client, db_session, test_project):
    """CR3-#4 — a reply PATCH with body + a thread label returns the EDITED
    REPLY (and applies the label to the root), not the root."""
    host = _make_host(db_session, test_project.id, "10.10.1.1")
    base = _notes_base(test_project.id, host.id)
    root = client.post(base, json={"body": "root body"}).json()
    reply = client.post(base, json={"body": "reply body", "parent_id": root["id"]}).json()

    r = client.patch(f"{base}/{reply['id']}", json={"body": "edited reply", "pinned": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # Response is the edited reply, with its new body — not the root.
    assert body["id"] == reply["id"]
    assert body["body"] == "edited reply"

    # The root took the label.
    roots = client.get(base).json()
    root_now = next(n for n in roots if n["id"] == root["id"])
    assert root_now["pinned"] is True


def test_thread_note_count_is_the_whole_thread_not_the_page(client, db_session, test_project):
    """Review 2026-09-23 B-UI-5 — a thread split across Activity pages
    reported only the entries on the current page."""
    host = _make_host(db_session, test_project.id, "10.10.2.1")
    base = _notes_base(test_project.id, host.id)
    root = client.post(base, json={"body": "root"}).json()
    for i in range(3):
        client.post(base, json={"body": f"reply {i}", "parent_id": root["id"]})

    activity_url = f"/api/v1/projects/{test_project.id}/hosts/notes/activity"
    page = client.get(activity_url, params={"limit": 2}).json()["notes"]
    assert len(page) == 2
    assert {n["thread_note_count"] for n in page} == {4}
