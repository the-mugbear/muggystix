"""Regression tests for list-endpoint pagination caps (v2.86.4).

Every list endpoint that previously accepted a bare ``limit: int = 100``
now enforces ``Query(100, ge=1, le=N)`` so a stray ``?limit=999999`` 422s
at the FastAPI layer instead of pinning a worker with selectinload fanout
and a huge response payload.

We hit each endpoint with ``?limit=999999`` and ``?skip=-1`` and assert
422 — no DB state required; FastAPI rejects on the Query validators
before the dependency chain runs.
"""
from __future__ import annotations


def _project_url(project_id: int, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"


# ---------------------------------------------------------------------------
# Project-scoped endpoints.
# ---------------------------------------------------------------------------


def test_hosts_rejects_oversize_limit(client, test_project):
    r = client.get(_project_url(test_project.id, "/hosts/"), params={"limit": 1_000_000})
    assert r.status_code == 422, r.text


def test_hosts_rejects_negative_skip(client, test_project):
    r = client.get(_project_url(test_project.id, "/hosts/"), params={"skip": -1})
    assert r.status_code == 422, r.text


def test_scans_rejects_oversize_limit(client, test_project):
    r = client.get(_project_url(test_project.id, "/scans/"), params={"limit": 999_999})
    assert r.status_code == 422, r.text


def test_scans_rejects_negative_skip(client, test_project):
    r = client.get(_project_url(test_project.id, "/scans/"), params={"skip": -5})
    assert r.status_code == 422, r.text


def test_parse_errors_rejects_oversize_limit(client, test_project):
    r = client.get(_project_url(test_project.id, "/parse-errors/"), params={"limit": 10_000})
    assert r.status_code == 422, r.text


def test_note_activity_rejects_oversize_limit(client, test_project):
    r = client.get(_project_url(test_project.id, "/hosts/notes/activity"), params={"limit": 10_000})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Top-level (non-project-scoped) admin endpoints.
# ---------------------------------------------------------------------------


def test_users_list_rejects_oversize_limit(client):
    r = client.get("/api/v1/users/", params={"limit": 10_000})
    assert r.status_code == 422, r.text


def test_audit_logs_rejects_oversize_limit(client):
    r = client.get("/api/v1/audit/logs", params={"limit": 10_000})
    assert r.status_code == 422, r.text


def test_audit_logs_rejects_negative_skip(client):
    r = client.get("/api/v1/audit/logs", params={"skip": -1})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Sanity — the in-range default is still accepted.
# ---------------------------------------------------------------------------


def test_hosts_accepts_in_range_limit(client, test_project):
    """At-cap limit should succeed (200, not 422)."""
    r = client.get(_project_url(test_project.id, "/hosts/"), params={"limit": 500})
    assert r.status_code == 200, r.text
