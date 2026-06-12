"""Host detail endpoint surfaces assignees (audit B8 / 1.2b).

The base detail serializer hardcodes assignees:[] (it needs a user join); the
list endpoint enriched it but the detail endpoint did not — so the inspector
couldn't show or manage a host's owner. get_host_v2 now mirrors the list
endpoint's assignee enrichment.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models import HostFollow, FollowStatus


def test_detail_returns_assignees_for_assigned_host(client, db_session, test_project, test_user):
    host = models.Host(project_id=test_project.id, ip_address="10.1.2.3", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(HostFollow(
        host_id=host.id,
        user_id=test_user.id,
        status=FollowStatus.IN_REVIEW,
        assigned_at=datetime.now(timezone.utc),
        assigned_by_id=test_user.id,
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert resp.status_code == 200
    assignees = resp.json()["assignees"]
    assert [a["user_id"] for a in assignees] == [test_user.id]
    assert assignees[0]["name"]  # full_name or username, non-empty


def test_detail_returns_empty_assignees_for_unassigned_host(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.1.2.4", state="up")
    db_session.add(host)
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert resp.status_code == 200
    assert resp.json()["assignees"] == []
