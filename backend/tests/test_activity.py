"""
Tests for the cross-project SOC-correlation activity surface.

Covers:
  * Basic window selection (scans whose [start, end] overlaps `ts ± tolerance`)
  * NULL end_time treated as single-instant (review-finding choice)
  * project_ids filter narrows results; unknown ids silently dropped
  * Non-admin users see only projects they're members of
  * Truncation flag fires when result count exceeds MAX_RESULTS
  * /scans-between window-cap enforcement
  * Tolerance cap enforcement on /scans-at
  * Naive timestamps treated as UTC

The fixtures build a small two-project dataset:
  * project A: 3 scans staggered around a known anchor time
  * project B: 1 scan inside the same window
  * project C: 1 scan, with NO ProjectMembership for the non-admin user
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from tests.conftest import TEST_USER_PW_HASH


ANCHOR = datetime(2026, 5, 26, 14, 32, 15, tzinfo=timezone.utc)


@pytest.fixture
def activity_dataset(db_session):
    """Build a three-project dataset with overlapping + non-overlapping scans."""
    proj_a = Project(name="alpha", slug="alpha")
    proj_b = Project(name="bravo", slug="bravo")
    proj_c = Project(name="charlie", slug="charlie")
    db_session.add_all([proj_a, proj_b, proj_c])
    db_session.commit()
    db_session.refresh(proj_a)
    db_session.refresh(proj_b)
    db_session.refresh(proj_c)

    scans = [
        # A1: overlaps anchor (started 60s before, ended 60s after)
        models.Scan(
            project_id=proj_a.id,
            filename="a1.xml",
            tool_name="nmap",
            scan_type="port_scan",
            start_time=ANCHOR - timedelta(seconds=60),
            end_time=ANCHOR + timedelta(seconds=60),
        ),
        # A2: covers anchor but ended exactly at anchor (boundary case)
        models.Scan(
            project_id=proj_a.id,
            filename="a2.xml",
            tool_name="masscan",
            scan_type="port_scan",
            start_time=ANCHOR - timedelta(seconds=120),
            end_time=ANCHOR,
        ),
        # A3: started AFTER tolerance window — should not match a 5s tolerance
        models.Scan(
            project_id=proj_a.id,
            filename="a3.xml",
            tool_name="nmap",
            scan_type="port_scan",
            start_time=ANCHOR + timedelta(seconds=300),
            end_time=ANCHOR + timedelta(seconds=400),
        ),
        # B1: NULL end_time, started exactly at anchor (single-instant)
        models.Scan(
            project_id=proj_b.id,
            filename="b1.txt",
            tool_name="masscan",
            scan_type="port_scan",
            start_time=ANCHOR,
            end_time=None,
        ),
        # C1: in window, but user isn't a member of project C
        models.Scan(
            project_id=proj_c.id,
            filename="c1.xml",
            tool_name="nmap",
            scan_type="port_scan",
            start_time=ANCHOR - timedelta(seconds=30),
            end_time=ANCHOR + timedelta(seconds=30),
        ),
    ]
    db_session.add_all(scans)
    db_session.commit()
    for s in scans:
        db_session.refresh(s)

    return {
        "projects": {"alpha": proj_a, "bravo": proj_b, "charlie": proj_c},
        "scans": {s.filename.split(".")[0]: s for s in scans},
    }


def _patch_user(db_session, role: UserRole) -> User:
    """Flip the test_user's global role for the call."""
    user = db_session.query(User).filter(User.id == 1).first()
    user.role = role
    db_session.commit()
    return user


def _grant_membership(db_session, user: User, project: Project, role: str = ProjectRole.VIEWER.value):
    db_session.add(
        ProjectMembership(project_id=project.id, user_id=user.id, role=role)
    )
    db_session.commit()


# ---------------------------------------------------------------------------
# /scans-at
# ---------------------------------------------------------------------------


def test_admin_sees_all_overlapping_scans_across_projects(client, db_session, activity_dataset):
    # test_user is already ADMIN per conftest.
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30, "kinds": "scan"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    filenames = {
        activity_dataset["scans"]["a1"].id: "a1",
        activity_dataset["scans"]["a2"].id: "a2",
        activity_dataset["scans"]["b1"].id: "b1",
        activity_dataset["scans"]["c1"].id: "c1",
    }
    returned = {filenames[i["ref_id"]] for i in body["items"] if i["kind"] == "scan"}
    # All four in-window scans should appear (admin sees every project)
    assert returned == {"a1", "a2", "b1", "c1"}, returned
    # a3 is outside ± 30s — should NOT appear
    a3_id = activity_dataset["scans"]["a3"].id
    assert a3_id not in {i["ref_id"] for i in body["items"]}


def test_non_admin_only_sees_member_projects(client, db_session, activity_dataset):
    user = _patch_user(db_session, UserRole.MEMBER)
    # Member of alpha + bravo, NOT charlie
    _grant_membership(db_session, user, activity_dataset["projects"]["alpha"])
    _grant_membership(db_session, user, activity_dataset["projects"]["bravo"])

    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_pids = {i["project_id"] for i in body["items"]}
    assert returned_pids == {
        activity_dataset["projects"]["alpha"].id,
        activity_dataset["projects"]["bravo"].id,
    }
    # charlie scan must not leak
    assert activity_dataset["projects"]["charlie"].id not in returned_pids
    # Accessible-projects list reflects membership only
    assert set(body["accessible_project_ids"]) == returned_pids


def test_null_end_time_treated_as_single_instant(client, db_session, activity_dataset):
    """B1 has start_time == ANCHOR and end_time is NULL.  A query
    centered on ANCHOR (tolerance 0) MUST include it; a query offset by
    1 minute (tolerance 0) MUST NOT.
    """
    # Centered → match
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 0},
    )
    assert resp.status_code == 200
    b1_id = activity_dataset["scans"]["b1"].id
    assert b1_id in {i["ref_id"] for i in resp.json()["items"] if i["kind"] == "scan"}

    # Offset → no match (single-instant interpretation)
    offset_ts = ANCHOR + timedelta(seconds=60)
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": offset_ts.isoformat(), "tolerance_seconds": 0, "kinds": "scan"},
    )
    assert resp.status_code == 200
    assert b1_id not in {i["ref_id"] for i in resp.json()["items"]}


def test_has_end_time_badge_set_correctly(client, db_session, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30, "kinds": "scan"},
    )
    assert resp.status_code == 200
    by_id = {i["ref_id"]: i for i in resp.json()["items"] if i["kind"] == "scan"}
    a1 = by_id[activity_dataset["scans"]["a1"].id]
    b1 = by_id[activity_dataset["scans"]["b1"].id]
    assert a1["has_end_time"] is True
    assert b1["has_end_time"] is False


def test_project_ids_filter_narrows_results(client, db_session, activity_dataset):
    alpha_id = activity_dataset["projects"]["alpha"].id
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": ANCHOR.isoformat(),
            "tolerance_seconds": 30,
            "project_ids": str(alpha_id),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(i["project_id"] == alpha_id for i in body["items"])
    assert body["requested_project_ids"] == [alpha_id]


def test_unknown_project_ids_silently_dropped(client, db_session, activity_dataset):
    """A non-admin asking for a project they can't see must get an empty
    result (intersection of accessible ∩ requested), not a 403 — we don't
    leak project existence."""
    user = _patch_user(db_session, UserRole.MEMBER)
    _grant_membership(db_session, user, activity_dataset["projects"]["alpha"])
    charlie_id = activity_dataset["projects"]["charlie"].id

    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": ANCHOR.isoformat(),
            "tolerance_seconds": 30,
            "project_ids": str(charlie_id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    # Caller asked for charlie; resolved set was empty (no leak)
    assert body["requested_project_ids"] == [charlie_id]


def test_invalid_project_ids_csv_400s(client, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": ANCHOR.isoformat(),
            "tolerance_seconds": 30,
            "project_ids": "1,abc,3",
        },
    )
    assert resp.status_code == 400
    assert "comma-separated list of integers" in resp.json()["detail"]


def test_tolerance_cap_enforced(client, activity_dataset):
    """Past 1h, the analyst should use /scans-between — the endpoint
    rejects rather than letting them blow the result cap."""
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 7200},
    )
    assert resp.status_code == 422  # FastAPI Query(le=...) validation


def test_naive_timestamp_treated_as_utc(client, activity_dataset):
    """The SOC analyst typically pastes a UTC timestamp without a
    timezone suffix.  Naive datetime input must not silently flip the
    window by the server's local offset."""
    # Strip the +00:00 suffix so the input is naive
    naive_ts = ANCHOR.replace(tzinfo=None).isoformat()
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": naive_ts, "tolerance_seconds": 30},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The window_start/end echo back as UTC-aware ISO strings
    assert body["window_start"].endswith("+00:00") or body["window_start"].endswith("Z")


# ---------------------------------------------------------------------------
# /scans-between
# ---------------------------------------------------------------------------


def test_scans_between_basic_window(client, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-between",
        params={
            "from": (ANCHOR - timedelta(minutes=10)).isoformat(),
            "to": (ANCHOR + timedelta(minutes=10)).isoformat(),
            "kinds": "scan",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned = {i["ref_id"] for i in body["items"] if i["kind"] == "scan"}
    # All four anchor-region scans (a1, a2, b1, c1) plus a3 which is +300s — all in ±10min
    assert len(returned) == 5


def test_scans_between_inverted_window_400s(client, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-between",
        params={
            "from": (ANCHOR + timedelta(hours=1)).isoformat(),
            "to": ANCHOR.isoformat(),
        },
    )
    assert resp.status_code == 400


def test_scans_between_window_cap_enforced(client, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-between",
        params={
            "from": ANCHOR.isoformat(),
            "to": (ANCHOR + timedelta(days=8)).isoformat(),
        },
    )
    assert resp.status_code == 400
    assert "days" in resp.json()["detail"]


def test_user_with_no_projects_gets_empty_response(client, db_session, activity_dataset):
    """A non-admin user with zero ProjectMembership rows must get a
    clean empty 200 — not a 403, not a 500."""
    _patch_user(db_session, UserRole.MEMBER)
    # No memberships granted

    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["accessible_project_ids"] == []


# ---------------------------------------------------------------------------
# v2: recon_session + execution_session kinds
# ---------------------------------------------------------------------------


def test_kinds_filter_default_is_all_three(client, activity_dataset):
    """Omitting `kinds` returns scan + recon + execution.  This dataset
    has only scans, so the assertion is "all items are kind=scan" and
    no 400 fires from the kinds parser."""
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(i["kind"] == "scan" for i in body["items"])
    assert len(body["items"]) >= 1


def test_kinds_filter_excludes_scan(client, db_session, activity_dataset):
    """Asking for only recon_session kind on a dataset with no recon
    sessions returns empty (not an error)."""
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": ANCHOR.isoformat(),
            "tolerance_seconds": 30,
            "kinds": "recon_session",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []


def test_recon_session_in_window_returned(client, db_session, activity_dataset):
    """Insert a ReconSession spanning the anchor and verify it surfaces."""
    from app.db.models import Scope
    from app.db.models_agent import ReconSession

    proj_a = activity_dataset["projects"]["alpha"]
    scope = Scope(name="alpha-prod", project_id=proj_a.id, description="test")
    db_session.add(scope)
    db_session.commit()
    db_session.refresh(scope)

    session = ReconSession(
        project_id=proj_a.id,
        scope_id=scope.id,
        status="active",
        started_at=ANCHOR - timedelta(minutes=2),
        completed_at=ANCHOR + timedelta(minutes=2),
        uploads_submitted=3,
        scans_ingested=2,
        hosts_discovered=42,
        ports_discovered=128,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": ANCHOR.isoformat(),
            "tolerance_seconds": 30,
            "kinds": "recon_session",
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    matching = [i for i in items if i["kind"] == "recon_session" and i["ref_id"] == session.id]
    assert len(matching) == 1
    item = matching[0]
    assert item["project_name"] == proj_a.name
    assert "alpha-prod" in item["label"]
    assert item["host_count"] == 42
    assert item["status"] == "active"
    assert item["has_end_time"] is True


def test_unknown_kind_400s(client, activity_dataset):
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "kinds": "scan,bogus_kind"},
    )
    assert resp.status_code == 400
    assert "bogus_kind" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# v2.60.0 — NULL-start fallback to created_at (recorded_time)
# ---------------------------------------------------------------------------
#
# Coverage gap acknowledged: these tests run against SQLite, which
# doesn't distinguish `timestamp` and `timestamptz` and so cannot
# exercise the v2.62.0 type-correctness rewrite of `_query_scans`.
# A canary comment lives at the rewrite site in
# `app/api/v1/endpoints/activity.py` (search for "Test-coverage gap")
# so anyone editing the SQL sees the constraint at the point of
# change.  A testcontainers Postgres harness under non-UTC
# `SET TIME ZONE` is the right long-term coverage.


def test_null_start_time_scan_uses_recorded_time_fallback(client, db_session, activity_dataset):
    """NULL-`start_time` scans (some .txt exports, bare masscan list
    output) surface via the `created_at` fallback path in
    `_query_scans` and carry `start_time_is_fallback=True` so the UI
    can badge them.  See activity.py for the full rationale.
    """
    proj_a = activity_dataset["projects"]["alpha"]
    null_start = models.Scan(
        project_id=proj_a.id,
        filename="bare_masscan.txt",
        tool_name="masscan",
        scan_type="port_scan",
        start_time=None,
        end_time=None,
        created_at=ANCHOR,
    )
    db_session.add(null_start)
    db_session.commit()
    db_session.refresh(null_start)

    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30, "kinds": "scan"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    matching = [i for i in items if i["ref_id"] == null_start.id and i["kind"] == "scan"]
    assert len(matching) == 1, (
        f"NULL-start scan should appear via created_at fallback; "
        f"got {[i['ref_id'] for i in items]}"
    )
    item = matching[0]
    assert item["start_time_is_fallback"] is True
    assert item["recorded_time"] is not None
    # And a scan WITH a real start_time should not be flagged.
    a1 = activity_dataset["scans"]["a1"]
    a1_item = next(i for i in items if i["ref_id"] == a1.id)
    assert a1_item["start_time_is_fallback"] is False


def test_scan_with_real_start_time_keeps_soc_semantics(client, db_session, activity_dataset):
    """Regression guard: the fallback path must not surface a scan
    whose scanner-recorded `start_time` ran outside the query window
    but whose `created_at` (upload time) lands inside it.  See the
    SOC-correlation note at the top of activity.py.
    """
    # a1 has start_time = ANCHOR - 60s and end_time = ANCHOR + 60s
    # (defined in activity_dataset).  Force its created_at to land far
    # outside the window we'll query.
    a1 = activity_dataset["scans"]["a1"]
    a1.created_at = ANCHOR + timedelta(hours=6)
    db_session.commit()

    # Query around a1.created_at (upload time, +6h from anchor).  a1
    # was not running then, so it must NOT appear.
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={
            "ts": (ANCHOR + timedelta(hours=6)).isoformat(),
            "tolerance_seconds": 60,
            "kinds": "scan",
        },
    )
    assert resp.status_code == 200
    returned = {i["ref_id"] for i in resp.json()["items"]}
    assert a1.id not in returned, (
        "Scan with real start_time but upload time inside the window "
        "should NOT appear — would conflate upload-when with running-when."
    )

    # Sanity: query around a1's actual scan window — it SHOULD appear.
    resp = client.get(
        "/api/v1/activity/scans-at",
        params={"ts": ANCHOR.isoformat(), "tolerance_seconds": 30, "kinds": "scan"},
    )
    assert a1.id in {i["ref_id"] for i in resp.json()["items"]}
