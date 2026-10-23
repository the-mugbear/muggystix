"""Migration d4a8c2e6f1b3 (a removed endpoint's Undo record becomes typed
columns, review 2026-10-23b TRI-5), walked on a scratch database BUILT BY THE
CHAIN (the pattern of ``test_migration_review_marks_outlive_their_reviewer.py``).

Seeded at ``9c4e2b7d1a3f`` (its parent): two removal lines as 2.526.0 wrote them (one with
a retest request, one on a host deleted since), an edit line and a status line.

Asserted:

* up: each removal line's record is in ``removed_*`` and its ``before_value``
  is cleared; a host gone since is NULL; the edit and status lines are
  untouched; deleting a host SETs NULL its lines;
* down: the records are JSON in ``before_value`` again, as 2.526.0 reads them;
* up again works on what the downgrade left.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "9c4e2b7d1a3f"
HEAD = "d4a8c2e6f1b3"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _alembic(url, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_d4a8"[:63])
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


ASKED = "2026-10-20T09:30:00.123456+00:00"
SEED = f"""
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active');
INSERT INTO hosts_v2 (id, project_id, ip_address, state) VALUES (1, 1, '10.0.0.1', 'up'), (2, 1, '10.0.0.2', 'up');
INSERT INTO findings (id, project_id, title, severity, status, source) VALUES (1, 1, 'F', 'high', 'confirmed', 'manual');
INSERT INTO finding_status_history (id, finding_id, from_status, to_status, summary, field, before_value, after_value)
VALUES
    (1, 1, 'confirmed', 'confirmed', 'Removed endpoint 10.0.0.1 (was retest)', NULL,
     '{{"host_id": 1, "name_id": null, "retest_requested_at": "{ASKED}"}}', NULL),
    (2, 1, 'confirmed', 'confirmed', 'Removed endpoint 10.0.0.9 (was open)', NULL,
     '{{"host_id": 99, "name_id": null, "retest_requested_at": null}}', NULL),
    (3, 1, 'confirmed', 'confirmed', 'Renamed', 'title', 'Old', 'F'),
    (4, 1, NULL, 'confirmed', 'Created', NULL, NULL, NULL);
"""


def _rows(conn, columns):
    return [tuple(r) for r in conn.execute(text(f"SELECT id, {columns} FROM finding_status_history ORDER BY id"))]


def test_removed_endpoint_columns_up_and_down(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement))

    # ---- up ------------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.begin() as conn:
        rows = _rows(conn, "removed_host_id, removed_name_id, removed_retest_requested_at, field, before_value")
        assert rows == [
            (1, 1, None, datetime.fromisoformat(ASKED), None, None),
            (2, None, None, None, None, None),
            (3, None, None, None, "title", "Old"),
            (4, None, None, None, None, None),
        ]
        conn.execute(text("DELETE FROM hosts_v2 WHERE id = 1"))
        assert _rows(conn, "removed_host_id")[0] == (1, None)
        # Put the host back for the round trip.
        conn.execute(text("INSERT INTO hosts_v2 (id, project_id, ip_address, state) VALUES (1, 1, '10.0.0.1', 'up')"))
        conn.execute(text("UPDATE finding_status_history SET removed_host_id = 1 WHERE id = 1"))

    # ---- down ----------------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.begin() as conn:
        record = dict(_rows(conn, "before_value"))
        assert json.loads(record[1]) == {"host_id": 1, "name_id": None, "retest_requested_at": ASKED}
        assert datetime.fromisoformat(json.loads(record[1])["retest_requested_at"]) == datetime.fromisoformat(ASKED)
        # The line whose host was gone has nothing to restore any more.
        assert record[2] is None and record[3] == "Old" and record[4] is None

    # ---- up again --------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.begin() as conn:
        assert _rows(conn, "removed_host_id, before_value")[0] == (1, 1, None)
