"""Migration f3a9c1e7d5b2 — a draft's details follow the project unless it
overrides them.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_report_evidence_images_defaults.py``).

Seeded at ``e8c4a6f0b2d7``: a project with report defaults (no saved team),
the installation's author, members of every role (one inactive), and drafts
as the old code left them — one a plain copy of the defaults, one with real
overrides — plus an issued and a superseded report holding the same copy,
and a draft of a project with no defaults at all.

Asserted:

* the upgrade removes from each DRAFT every key equal to what it would
  follow (the team compared against the active analysts and admins, as the
  page builds them) and every blank text; it keeps every override — a
  different team, a distribution overridden to ``[]``, a disclaimer of its
  own; issued and superseded reports are untouched;
* what each draft prints is unchanged (the app's own resolver, on the
  upgraded database, gives the values the old copy held);
* the downgrade writes every key back into the drafts, copied from the
  project (author / disclaimer null — the old "inherited"); up again strips
  them once more.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "e8c4a6f0b2d7"
REVISION = "f3a9c1e7d5b2"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _alembic(url, *args: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "BLUESTICK_SKIP_DB_INIT": "1",
    }
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_f3a9"[:63])
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


CISO = {"name": "CISO", "email": "ciso@example.com"}
# The project's analysts and admins as the page lists them: admins first.
TEAM = [
    {"user_id": 7, "name": "Ada Lovelace", "role": "Engagement lead", "email": "ada@example.com"},
    {"user_id": 8, "name": "bob", "role": "Tester", "email": None},
]
# What the old code wrote into a new draft of project 1.
COPY = {
    "client_name": "Example Corp", "classification": "Confidential", "engagement_type": None,
    "testers": TEAM, "distribution": [CISO], "system_description": "The LAN.",
    "applications": None, "thick_clients": None, "other_targets": None,
    "author_name": None, "disclaimer": None,
}
OVERRIDING = {
    **COPY,
    "client_name": "Other Corp",                    # an override
    "classification": " Confidential ",             # the project's, once trimmed
    "engagement_type": "",                          # blank: follows
    "testers": [TEAM[0]],                           # a different team
    "distribution": [],                             # overridden to empty, on purpose
    "author_name": "Installation Ltd",              # what it follows anyway
    "disclaimer": "Our own terms.",                 # an override
}
BARE = {  # project 2: no defaults, no members — the old copy of nothing
    "client_name": None, "classification": None, "engagement_type": None, "testers": [], "distribution": [],
    "system_description": None, "applications": None, "thick_clients": None, "other_targets": None,
    "author_name": None, "disclaimer": None,
}


def _seed(conn) -> None:
    conn.execute(text(
        "INSERT INTO projects (id, name, slug, status) VALUES (1, 'Reports', 'reports', 'active'), "
        "(2, 'Bare', 'bare', 'active');"
        "INSERT INTO users (id, username, email, full_name, hashed_password, role, is_active) VALUES "
        "(7, 'ada', 'ada@example.com', 'Ada Lovelace', 'x', 'ADMIN', true), "
        "(8, 'bob', NULL, NULL, 'x', 'MEMBER', true), "
        "(9, 'cy', 'cy@example.com', 'Cy Viewer', 'x', 'MEMBER', true), "
        "(10, 'dee', 'dee@example.com', 'Dee Gone', 'x', 'MEMBER', false);"
        "INSERT INTO project_memberships (project_id, user_id, role) VALUES "
        "(1, 7, 'admin'), (1, 8, 'analyst'), (1, 9, 'viewer'), (1, 10, 'analyst');"
        "INSERT INTO report_profiles (id, project_id, client_name, classification, system_description, distribution) "
        "VALUES (1, 1, 'Example Corp', 'Confidential', 'The LAN.', CAST(:dist AS json));"
        "INSERT INTO report_defaults (key, text) VALUES ('author_name', 'Installation Ltd');"
    ), {"dist": json.dumps([CISO])})
    for rid, project, status, settings in (
        (1, 1, "draft", COPY), (2, 1, "draft", OVERRIDING), (3, 1, "issued", COPY),
        (4, 1, "superseded", COPY), (5, 2, "draft", BARE), (6, 1, "draft", None),
    ):
        conn.execute(text(
            "INSERT INTO reports (id, project_id, kind, status, title, template, settings) "
            "VALUES (:id, :p, 'full', :s, 'R', 'pentest', CAST(:settings AS json))"
        ), {"id": rid, "p": project, "s": status, "settings": None if settings is None else json.dumps(settings)})


def _settings(conn) -> dict:
    return {
        rid: settings for rid, settings in
        conn.execute(text("SELECT id, settings FROM reports ORDER BY id")).all()
    }


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_drafts_keep_only_their_overrides_and_print_what_they_printed(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        _seed(conn)
    with eng.connect() as conn:
        before = _settings(conn)

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _revision(conn) == REVISION
        after = _settings(conn)
    assert after[1] == {}                               # a plain copy follows everything
    assert after[2] == {
        "client_name": "Other Corp", "testers": [TEAM[0]], "distribution": [], "disclaimer": "Our own terms.",
    }
    assert after[3] == before[3] == COPY                # issued: untouched
    assert after[4] == before[4] == COPY                # superseded: untouched
    assert after[5] == {}                               # nothing to follow, nothing kept
    assert after[6] is None                             # nothing stored: nothing written

    # What each draft prints is what it printed (the app's resolver, at head:
    # the models are head's, so the database is taken there and back).
    from app.services.client_report_service import ClientReportService

    _alembic(url, "upgrade", "head")
    with Session(eng) as db:
        svc = ClientReportService(db)
        printed = svc.resolved_details(1, after[1])
        assert {k: printed[k] for k in COPY if k != "author_name"} == {k: v for k, v in COPY.items() if k != "author_name"}
        assert printed["author_name"] == "Installation Ltd"   # followed before, too
        own = svc.resolved_details(1, after[2])
        assert own["client_name"] == "Other Corp"
        assert own["classification"] == "Confidential"
        assert own["engagement_type"] is None
        assert own["testers"] == [TEAM[0]] and own["distribution"] == []
        assert own["author_name"] == "Installation Ltd" and own["disclaimer"] == "Our own terms."
        bare = svc.resolved_details(2, after[5])
        assert bare["testers"] == [] and bare["distribution"] == [] and bare["client_name"] is None

        # A change to the project's defaults now reaches the following draft…
        db.execute(text("UPDATE report_profiles SET client_name = 'Renamed Corp' WHERE project_id = 1"))
        assert svc.resolved_details(1, after[1])["client_name"] == "Renamed Corp"
        # …and not the one that overrides it.
        assert svc.resolved_details(1, after[2])["client_name"] == "Other Corp"
        db.rollback()
    _alembic(url, "downgrade", REVISION)

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        down = _settings(conn)
    # Every key back, copied from the project; author and disclaimer null
    # (the old "inherited").
    assert down[1] == COPY
    assert down[2] == {
        **COPY, "client_name": "Other Corp", "testers": [TEAM[0]], "distribution": [],
        "disclaimer": "Our own terms.",
    }
    assert down[3] == down[4] == COPY
    assert down[5] == BARE
    assert down[6] == COPY                              # an empty draft gets every key too

    # ---- and up again --------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        again = _settings(conn)
    assert again[1] == {} and again[5] == {} and again[6] == {}
    assert again[2] == after[2]
    assert again[3] == again[4] == COPY
