"""Migration b7d2e5f9a1c3 — ``finding_hosts.remediated_at``, back-filled.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_hostname_changed.py``).  Seeded at ``f3a9c1e7d5b2``: one
finding on six endpoints and their history lines, as
``FindingService._move_endpoint`` writes them ("Endpoint <label>: <old> →
<new>", optionally " — <note>"; a named endpoint's label is "<name> on
<address>").

Asserted: each endpoint that is remediated NOW takes the day of the LATEST
line that moved IT to remediated — not a line about another endpoint whose
address starts the same, not a line about the same address under a name, not
a move to another state; one with no such line stays NULL, and so does an
endpoint that is not remediated now (whatever its history).  The downgrade
drops the column and the upgrade back-fills again.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine
from tests.test_migration_hostname_changed import _alembic

BEFORE = "f3a9c1e7d5b2"
REVISION = "b7d2e5f9a1c3"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_b7d2"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES (1, 'Fixes', 'fixes', 'active');
INSERT INTO hosts_v2 (id, ip_address, project_id) VALUES
    (1, '10.1.0.1', 1), (2, '10.1.0.10', 1), (3, '10.1.0.3', 1), (4, '10.1.0.4', 1), (5, '10.1.0.5', 1);
INSERT INTO dns_names (id, project_id, fqdn, kind) VALUES (7, 1, 'portal.example.com', 'fqdn');
INSERT INTO findings (id, project_id, title, severity, status, source)
    VALUES (1, 1, 'Weak TLS', 'high', 'confirmed', 'manual');
INSERT INTO finding_hosts (id, finding_id, host_id, name_id, host_status) VALUES
    (11, 1, 1, NULL, 'remediated'),   -- two moves to remediated: the latest wins
    (12, 1, 2, NULL, 'remediated'),   -- 10.1.0.10: only the line about 10.1.0.1 starts the same
    (13, 1, 3, 7, 'remediated'),      -- named: "portal.example.com on 10.1.0.3", with a note
    (14, 1, 3, NULL, 'open'),         -- remediated once, reopened: not remediated now
    (15, 1, 4, NULL, 'remediated'),   -- no line at all
    (16, 1, 5, NULL, 'remediated');   -- only a move to retest
INSERT INTO finding_status_history (finding_id, from_status, to_status, summary, created_at) VALUES
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.1: open → remediated', '2026-03-01 10:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.1: remediated → retest', '2026-03-05 10:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.1: retest → remediated', '2026-03-09 10:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint portal.example.com on 10.1.0.3: open → remediated — retest 2026-04-02',
        '2026-04-02 08:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.3: open → remediated', '2026-04-10 08:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.3: remediated → open', '2026-04-11 08:00+00'),
    (1, 'confirmed', 'confirmed', 'Endpoint 10.1.0.5: open → retest', '2026-05-01 08:00+00');
"""

EXPECTED = {
    11: "2026-03-09 10:00:00+00:00",
    12: None,
    13: "2026-04-02 08:00:00+00:00",
    14: None,
    15: None,
    16: None,
}


def _stamped(conn) -> dict:
    return {row.id: (str(row.remediated_at) if row.remediated_at else None)
            for row in conn.execute(text("SELECT id, remediated_at FROM finding_hosts ORDER BY id"))}


def test_the_remediated_day_is_back_filled_from_the_history(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement))
    with eng.connect() as conn:
        assert "remediated_at" not in {r.column_name for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'finding_hosts'"))}

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        assert _stamped(conn) == EXPECTED

    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert "remediated_at" not in {r.column_name for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'finding_hosts'"))}
        assert conn.execute(text("SELECT count(*) FROM finding_hosts")).scalar() == 6

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        assert _stamped(conn) == EXPECTED
