"""Migration c9e4a7b2d6f1 — the executive summary follows the project.

A populated walk on a scratch database BUILT BY THE CHAIN.  Seeded at
``b7d2e5f9a1c3``: a full draft and an addendum draft with a summary, a draft
without one, an issued and a superseded report (their summary also in the
frozen dataset).

Asserted: the upgrade gives each draft with text its summary as an override
(other overrides kept), touches no other report, adds an empty
``report_profiles.executive_summary`` and drops ``reports.executive_summary``.
The downgrade brings the column back — a draft's override (removed from its
settings), else for a full report the project's summary, an issued report's
from its frozen data — and drops the profile's.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine
from tests.test_migration_hostname_changed import _alembic

BEFORE = "b7d2e5f9a1c3"
REVISION = "c9e4a7b2d6f1"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_c9e4"[:63])
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


def _columns(conn, table) -> set:
    return {r.column_name for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})}


def _settings(conn) -> dict:
    return {rid: s for rid, s in conn.execute(text("SELECT id, settings FROM reports ORDER BY id")).all()}


def test_a_drafts_summary_becomes_its_override_and_comes_back(scratch):
    url, eng = scratch
    issued = json.dumps({"dataset": {"executive_summary": "As issued."}, "reported": {}})
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO projects (id, name, slug, status) VALUES (1, 'R', 'r', 'active')"))
        for rid, kind, status, settings, summary, snapshot in (
            (1, "full", "draft", {"client_name": "Ours"}, "  Draft summary. ", None),
            (2, "addendum", "draft", None, "Since #1.", None),
            (3, "full", "draft", {}, None, None),
            (4, "full", "issued", {}, "As issued.", issued),
            (5, "full", "superseded", {}, "As issued.", issued),
        ):
            conn.execute(text(
                "INSERT INTO reports (id, project_id, kind, status, title, template, settings, executive_summary, "
                "snapshot) VALUES (:id, 1, :k, :s, 'R', 'pentest', CAST(:settings AS json), :sum, "
                "CAST(:snap AS json))"
            ), {"id": rid, "k": kind, "s": status, "settings": None if settings is None else json.dumps(settings),
                "sum": summary, "snap": snapshot})

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert "executive_summary" not in _columns(conn, "reports")
        assert "executive_summary" in _columns(conn, "report_profiles")
        after = _settings(conn)
    assert after[1] == {"client_name": "Ours", "executive_summary": "Draft summary."}
    assert after[2] == {"executive_summary": "Since #1."}
    assert after[3] == {} and after[4] == {} and after[5] == {}

    # The project gets a summary; the downgrade gives it to the full draft
    # that followed it — not to the addendum, whose summary is its own.
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO report_profiles (project_id, executive_summary) VALUES (1, 'Project.')"))
        conn.execute(text("UPDATE reports SET settings = CAST('{}' AS json) WHERE id = 2"))
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert "executive_summary" in _columns(conn, "reports")
        assert "executive_summary" not in _columns(conn, "report_profiles")
        summaries = dict(conn.execute(text("SELECT id, executive_summary FROM reports ORDER BY id")).all())
        down = _settings(conn)
    assert summaries == {1: "Draft summary.", 2: None, 3: "Project.", 4: "As issued.", 5: "As issued."}
    assert down[1] == {"client_name": "Ours"}

    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _settings(conn)[1] == {"client_name": "Ours", "executive_summary": "Draft summary."}
        assert _settings(conn)[3] == {"executive_summary": "Project."}
