"""Migration e8c4a6f0b2d7 — a finding's Evidence text, the report's own
images, and the report's author and disclaimer.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_hostname_changed.py``; the suite's own schema is
``create_all()`` from the models and already has all of it).

Seeded at ``d7b3e9a1c5f2``: a project with a finding, a note on it holding a
screenshot, and its report defaults.

Asserted, in order:

* before the revision none of it exists: no ``findings.evidence_text``, no
  ``report_profiles.author_name`` / ``.disclaimer``, no ``report_defaults``,
  and ``note_attachments.annotation_id`` is NOT NULL;
* the upgrade adds the three columns NULL on every existing row, makes
  ``annotation_id`` nullable under the check ``ck_note_attachment_owner``,
  creates ``report_defaults`` empty — and rewrites no row;
* a report image (no note) can then be stored, a row owned by nobody is
  refused, and a deleted user leaves an installation default in place
  (``updated_by_id`` SET NULL);
* the downgrade DELETES the report's own images — the old schema cannot hold
  a row without a note — and keeps every note's image; drops the columns and
  the table; restores NOT NULL;
* the upgrade again brings everything back empty: what a downgrade loses is
  the Evidence texts, the authors and disclaimers, and the report images'
  rows.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "d7b3e9a1c5f2"
REVISION = "e8c4a6f0b2d7"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _alembic(url, *args: str) -> None:
    """Run alembic against the scratch database, in its own process: env.py
    reads the URL from the settings, which this process has already loaded."""
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
    }
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    """A database migrated by the chain to the revision before this one."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_e8c4"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES (1, 'Reports', 'reports', 'active');
INSERT INTO users (id, username, email, hashed_password, role, is_active)
    VALUES (7, 'ada', 'ada@example.com', 'x', 'ADMIN', true);
INSERT INTO findings (id, project_id, title, severity, status, source, description)
    VALUES (1, 1, 'SMB relay', 'high', 'confirmed', 'manual', 'As written before.');
INSERT INTO annotations (id, finding_id, body, pinned) VALUES (10, 1, 'proof', false);
INSERT INTO note_attachments (id, annotation_id, project_id, filename, content_type, size_bytes, storage_path,
                              include_in_report, caption)
    VALUES (100, 10, 1, 'shot.png', 'image/png', 68, '10/abc.png', true, 'The relayed session');
INSERT INTO report_profiles (id, project_id, client_name, classification) VALUES (1, 1, 'Example Corp', 'Confidential');
"""

ATTACHMENTS = (
    "SELECT id, annotation_id, project_id, filename, content_type, size_bytes, storage_path, "
    "include_in_report, caption FROM note_attachments ORDER BY id"
)
FINDINGS = "SELECT id, project_id, title, severity, status, source, description FROM findings ORDER BY id"
PROFILES = "SELECT id, project_id, client_name, classification FROM report_profiles ORDER BY id"


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _column(conn, table: str, column: str):
    """``(data_type, is_nullable)`` or None when the column does not exist."""
    row = conn.execute(text(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).first()
    return tuple(row) if row else None


def _has_table(conn, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def _checks(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid = to_regclass(:t)"
        ), {"t": table})
    }


def _indexes(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = :t"
        ), {"t": table})
    }


def _assert_before(conn) -> None:
    assert _revision(conn) == BEFORE
    assert _column(conn, "findings", "evidence_text") is None
    assert _column(conn, "report_profiles", "author_name") is None
    assert _column(conn, "report_profiles", "disclaimer") is None
    assert not _has_table(conn, "report_defaults")
    assert _column(conn, "note_attachments", "annotation_id") == ("integer", "NO")
    assert "ck_note_attachment_owner" not in _checks(conn, "note_attachments")


def _assert_on_revision(conn) -> None:
    assert _revision(conn) == REVISION
    assert _column(conn, "findings", "evidence_text") == ("text", "YES")
    assert _column(conn, "report_profiles", "author_name") == ("character varying", "YES")
    assert _column(conn, "report_profiles", "disclaimer") == ("text", "YES")
    assert _column(conn, "note_attachments", "annotation_id") == ("integer", "YES")
    assert "ck_note_attachment_owner" in _checks(conn, "note_attachments")
    assert _has_table(conn, "report_defaults")
    assert _column(conn, "report_defaults", "key") == ("character varying", "NO")
    assert _column(conn, "report_defaults", "text") == ("text", "NO")
    assert _column(conn, "report_defaults", "updated_by_id") == ("integer", "YES")
    assert _column(conn, "report_defaults", "updated_at") == ("timestamp with time zone", "NO")


def test_the_report_columns_arrive_empty_are_dropped_and_come_back_empty(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
    with eng.connect() as conn:
        _assert_before(conn)
        attachments_before = conn.execute(text(ATTACHMENTS)).all()
        findings_before = conn.execute(text(FINDINGS)).all()
        profiles_before = conn.execute(text(PROFILES)).all()
        indexes_before = {t: _indexes(conn, t) for t in ("findings", "note_attachments", "report_profiles")}
        # The old schema cannot hold an image without a note.
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO note_attachments (id, annotation_id, project_id, filename, content_type, size_bytes, "
                "storage_path) VALUES (900, NULL, 1, 'd.png', 'image/png', 1, 'report/1/d.png')"
            ))
        conn.rollback()

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_on_revision(conn)
        # Catalogue changes only: every row is as it was, the new columns NULL.
        assert conn.execute(text(ATTACHMENTS)).all() == attachments_before
        assert conn.execute(text(FINDINGS)).all() == findings_before
        assert conn.execute(text(PROFILES)).all() == profiles_before
        assert conn.execute(text("SELECT evidence_text FROM findings")).scalars().all() == [None]
        assert conn.execute(text("SELECT author_name, disclaimer FROM report_profiles")).all() == [(None, None)]
        assert conn.execute(text("SELECT count(*) FROM report_defaults")).scalar() == 0
        # No index built (the migration says so).
        for table, before in indexes_before.items():
            assert _indexes(conn, table) == before, table

    with eng.begin() as conn:
        conn.execute(text("UPDATE findings SET evidence_text = 'The banner names the build.' WHERE id = 1"))
        conn.execute(text(
            "UPDATE report_profiles SET author_name = 'Project Team', disclaimer = 'The project''s terms.' WHERE id = 1"
        ))
        conn.execute(text(
            "INSERT INTO report_defaults (key, text, updated_by_id) VALUES "
            "('author_name', 'Installation Ltd', 7), ('disclaimer', 'The installation''s terms.', 7)"
        ))
        # The report's own images: no note, the project named.
        conn.execute(text(
            "INSERT INTO note_attachments (id, annotation_id, project_id, filename, content_type, size_bytes, "
            "storage_path, include_in_report, caption) VALUES "
            "(200, NULL, 1, 'diagram.png', 'image/png', 68, 'report/1/d.png', true, 'The assessed network'), "
            "(201, NULL, 1, 'second.png', 'image/png', 68, 'report/1/e.png', true, NULL)"
        ))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT updated_at IS NOT NULL FROM report_defaults")).scalars().all() == [True, True]
        # A row that belongs to nothing is refused; so is a second row for a key.
        with pytest.raises(IntegrityError) as owner:
            conn.execute(text(
                "INSERT INTO note_attachments (id, annotation_id, project_id, filename, content_type, size_bytes, "
                "storage_path) VALUES (901, NULL, NULL, 'x.png', 'image/png', 1, 'x')"
            ))
        assert "ck_note_attachment_owner" in str(owner.value)
        conn.rollback()
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO report_defaults (key, text) VALUES ('author_name', 'Twice')"))
        conn.rollback()
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO report_defaults (key, text) VALUES ('other', NULL)"))
        conn.rollback()
    with eng.begin() as conn:
        # The account that wrote a default goes: the default stays, written by nobody.
        conn.execute(text("DELETE FROM users WHERE id = 7"))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT key, text, updated_by_id FROM report_defaults ORDER BY key")).all() == [
            ("author_name", "Installation Ltd", None), ("disclaimer", "The installation's terms.", None),
        ]
        assert conn.execute(text("SELECT id FROM note_attachments ORDER BY id")).scalars().all() == [100, 200, 201]

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        _assert_before(conn)
        # The report's own images are deleted (said in the revision); the
        # note's image, the finding and the profile keep every other value.
        assert conn.execute(text(ATTACHMENTS)).all() == attachments_before
        assert conn.execute(text(FINDINGS)).all() == findings_before
        assert conn.execute(text(PROFILES)).all() == profiles_before
        for table, before in indexes_before.items():
            assert _indexes(conn, table) == before, table

    # ---- and up again --------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_on_revision(conn)
        assert conn.execute(text(ATTACHMENTS)).all() == attachments_before
        assert conn.execute(text("SELECT evidence_text FROM findings")).scalars().all() == [None]
        assert conn.execute(text("SELECT author_name, disclaimer FROM report_profiles")).all() == [(None, None)]
        assert conn.execute(text("SELECT count(*) FROM report_defaults")).scalar() == 0
    with eng.begin() as conn:
        # … and it is usable again.
        conn.execute(text(
            "INSERT INTO note_attachments (id, annotation_id, project_id, filename, content_type, size_bytes, "
            "storage_path, include_in_report) VALUES (300, NULL, 1, 'again.png', 'image/png', 68, 'report/1/f.png', true)"
        ))
        # A project that goes takes its report images with it.
        conn.execute(text("DELETE FROM projects WHERE id = 1"))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM note_attachments")).scalar() == 0
