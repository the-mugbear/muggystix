"""Regression tests for the v2.86.13 ``Paginated[T]`` envelope.

Four endpoints flipped from a bare ``List[T]`` (or a wrapper carrying
``X-Total-Count`` as a transitional measure) to the standard
``{items, total, skip, limit, has_more}`` envelope.

This test file confirms the envelope fields are populated correctly
on each endpoint and that ``has_more`` is server-computed (so the
client never has to do the ``loaded < total`` math itself).
"""
from __future__ import annotations

from app.db import models


def test_recon_sessions_envelope_shape(client, db_session, test_project):
    """The /recon-sessions/ endpoint returns ``items`` + ``total`` +
    ``skip`` + ``limit`` + ``has_more``."""
    # No recon sessions seeded → total=0, items=[], has_more=False.
    r = client.get(f"/api/v1/projects/{test_project.id}/recon-sessions/")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"items", "total", "skip", "limit", "has_more"}
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False
    # Default limit is 100; the wrapper passes nothing → server default.
    assert body["limit"] == 100
    assert body["skip"] == 0


def test_execution_sessions_envelope_shape(client, db_session, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/execution-sessions/")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"items", "total", "skip", "limit", "has_more"}
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


def test_scope_host_mappings_envelope_has_more_boundary(
    client, db_session, test_project,
):
    """Confirms server-computed ``has_more`` at the exact boundary
    where ``skip + len(items) == total`` (last page, fits exactly)."""
    scope = models.Scope(project_id=test_project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24", description="")
    db_session.add(subnet)
    db_session.flush()
    for i in range(3):
        h = models.Host(project_id=test_project.id, ip_address=f"10.0.0.{i+1}", state="up")
        db_session.add(h)
        db_session.flush()
        db_session.add(models.HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
        params={"skip": 0, "limit": 3},
    )
    body = r.json()
    # Exact-fit page: 3 items, total 3, has_more must be False.
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["has_more"] is False, "boundary case (loaded == total) should not say has_more"


def test_out_of_scope_envelope_has_more_with_overflow(
    client, db_session, test_project,
):
    """When total > skip + len(items), ``has_more`` is True."""
    scan = models.Scan(project_id=test_project.id, filename="fix.json", scan_type="nmap")
    db_session.add(scan)
    db_session.flush()
    for i in range(5):
        db_session.add(models.OutOfScopeHost(
            project_id=test_project.id, scan_id=scan.id,
            ip_address=f"10.10.0.{i+1}",
            hostname=f"h{i}.example", reason="overflow fixture",
        ))
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/scans/out-of-scope",
        params={"skip": 0, "limit": 2},
    )
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["has_more"] is True


def test_envelope_no_x_total_count_header_anymore(client, test_project):
    """v2.86.13 retired the ``X-Total-Count`` header on the recon /
    execution endpoints — the in-body ``total`` field is the only
    source of truth now.  This guards against accidentally
    reintroducing the dual-source pattern."""
    r = client.get(f"/api/v1/projects/{test_project.id}/recon-sessions/")
    assert "x-total-count" not in {k.lower() for k in r.headers.keys()}, \
        "X-Total-Count header should be gone; total lives in the body now"
