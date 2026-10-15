"""Migration e5c1a7b3d9f2 — the retest workflow (owner, 2026-10-14).

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_finding_hosts_remediated_at.py``), seeded at
``d4b8e2a6c9f3``.

Asserted:

* ``finding_hosts.retest_requested_at`` exists after, not before;
* a finding whose status was ``retest`` is ``open``, with a history line
  saying why; its endpoints that were ``open`` are ``retest`` (stamped),
  the others untouched; another finding is untouched;
* a "Reported fixed" record with no date takes the day of its latest
  timeline move to ``closed``; one with no such move stays undated; one with
  a date keeps it;
* a "Reported fixed" record's host still present awaits a retest (stamped,
  one history line each) — the claim asks for it, made before the upgrade
  or not — only with remediation tracking ON and for a finding not judged a
  false positive (second review 2026-10-14, H3);
* the downgrade drops the column; upgrading again changes nothing more.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine
from tests.test_migration_hostname_changed import _alembic

BEFORE = "d4b8e2a6c9f3"
REVISION = "e5c1a7b3d9f2"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


@pytest.fixture
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_e5c1"[:63])
    _drop_database(url)
    _ensure_database(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        eng = create_engine(url)
        try:
            yield url, eng
        finally:
            eng.dispose()
    finally:
        _drop_database(url)


SEED = """
INSERT INTO projects (id, name, slug, status) VALUES (1, 'Retests', 'retests', 'active');
INSERT INTO hosts_v2 (id, ip_address, project_id) VALUES (1, '10.2.0.1', 1), (2, '10.2.0.2', 1), (3, '10.2.0.3', 1);
INSERT INTO findings (id, project_id, title, severity, status, source) VALUES
    (1, 1, 'Weak TLS', 'high', 'retest', 'manual'),
    (2, 1, 'SMB signing', 'medium', 'confirmed', 'manual'),
    (3, 1, 'Judged not an issue', 'low', 'false_positive', 'manual');
INSERT INTO finding_hosts (id, finding_id, host_id, host_status) VALUES
    (11, 1, 1, 'open'),            -- still present on a retest finding: now awaits a retest
    (12, 1, 2, 'remediated'),      -- left alone
    (21, 2, 1, 'open'),
    (22, 2, 2, 'open'),
    (23, 2, 3, 'open'),
    (31, 3, 1, 'open');            -- a false-positive finding's: never asked for
INSERT INTO finding_host_remediation (finding_host_id, project_id, status, closed_on) VALUES
    (21, 1, 'closed', NULL),          -- dated from its timeline
    (22, 1, 'closed', NULL),          -- no move recorded: stays undated
    (23, 1, 'closed', '2026-08-01'),  -- has a date: kept
    (31, 1, 'closed', '2026-08-02');
INSERT INTO remediation_events (project_id, host_id, finding_id, finding_host_id, kind, field, old_value, new_value,
                                occurred_at, created_at) VALUES
    (1, 1, 2, 21, 'change', 'status', 'open', 'closed', '2026-09-01 10:00+00', '2026-09-01 10:00+00'),
    (1, 1, 2, 21, 'change', 'status', 'closed', 'open', '2026-09-02 10:00+00', '2026-09-02 10:00+00'),
    (1, 1, 2, 21, 'change', 'status', 'open', 'closed', '2026-09-03 23:30+00', '2026-09-03 23:30+00'),
    (1, 3, 2, 23, 'change', 'status', 'open', 'closed', '2026-09-05 10:00+00', '2026-09-05 10:00+00');
"""


def _columns(conn) -> set:
    return {r.column_name for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'finding_hosts'"))}


def _state(conn) -> dict:
    return {
        "findings": dict(conn.execute(text("SELECT id, status FROM findings ORDER BY id")).all()),
        "endpoints": dict(conn.execute(text("SELECT id, host_status FROM finding_hosts ORDER BY id")).all()),
        "dates": {fh: (str(day) if day else None) for fh, day in conn.execute(text(
            "SELECT finding_host_id, closed_on FROM finding_host_remediation ORDER BY finding_host_id")).all()},
    }


EXPECTED = {
    "findings": {1: "open", 2: "confirmed", 3: "false_positive"},
    # 21–23 carry a "Reported fixed" record on a host still present: the claim
    # asks for the retest, made before the upgrade or not — with tracking ON;
    # 31's finding was judged a false positive: never.
    "endpoints": {11: "retest", 12: "remediated", 21: "retest", 22: "retest", 23: "retest", 31: "open"},
    "dates": {21: "2026-09-03", 22: None, 23: "2026-08-01", 31: "2026-08-02"},
}


@pytest.mark.parametrize("policy", [None, False], ids=["no policy row", "tracking off"])
def test_with_tracking_off_a_record_asks_for_no_retest(scratch, policy):
    """Second review 2026-10-14, H3: step 4 ran whatever the switch said, so
    an installation that had tried tracking and turned it off found hosts
    "Retest pending" after the upgrade.  The finding-level retest still moves
    onto its hosts (that is not remediation), and the dates are the records'
    own (invisible with tracking off)."""
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement))
        if policy is not None:
            # A zone ahead of UTC: the record's day is the installation's.
            conn.execute(text("INSERT INTO remediation_policy (id, enabled, time_zone) "
                              "VALUES (1, :on, 'Europe/Berlin')"), {"on": policy})
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        state = _state(conn)
        assert state["endpoints"] == {11: "retest", 12: "remediated", 21: "open", 22: "open", 23: "open", 31: "open"}
        # 2026-09-03 23:30 UTC is the 4th in Berlin; UTC with no policy row.
        dated = "2026-09-04" if policy is not None else "2026-09-03"
        assert state["dates"] == {**EXPECTED["dates"], 21: dated}
        assert conn.execute(text("SELECT count(*) FROM finding_status_history WHERE finding_id = 2")).scalar() == 0


def test_the_finding_level_retest_moves_onto_its_hosts_and_reported_fixed_is_dated(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO remediation_policy (id, enabled) VALUES (1, true)"))
    with eng.connect() as conn:
        assert "retest_requested_at" not in _columns(conn)

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert "retest_requested_at" in _columns(conn)
        assert _state(conn) == EXPECTED
        stamped = dict(conn.execute(text(
            "SELECT id, retest_requested_at IS NOT NULL FROM finding_hosts ORDER BY id")).all())
        assert stamped == {11: True, 12: False, 21: True, 22: True, 23: True, 31: False}
        assert conn.execute(text("SELECT count(*) FROM finding_status_history WHERE finding_id = 3")).scalar() == 0
        line = conn.execute(text(
            "SELECT from_status, to_status, summary FROM finding_status_history WHERE finding_id = 1")).one()
        assert (line.from_status, line.to_status) == ("retest", "open")
        assert "retest is now per host" in line.summary
        asked = [r.summary for r in conn.execute(text(
            "SELECT summary FROM finding_status_history WHERE finding_id = 2 ORDER BY summary"))]
        assert asked == [f"Endpoint 10.2.0.{n}: open → retest — the contact had reported it fixed "
                         "(retests are asked for since this upgrade)" for n in (1, 2, 3)]

    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert "retest_requested_at" not in _columns(conn)
        assert conn.execute(text("SELECT count(*) FROM finding_hosts")).scalar() == 6

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _state(conn) == EXPECTED
        assert conn.execute(text("SELECT count(*) FROM finding_status_history WHERE finding_id = 1")).scalar() == 1
        # Asked once: the endpoints were already in retest the second time.
        assert conn.execute(text("SELECT count(*) FROM finding_status_history WHERE finding_id = 2")).scalar() == 3
