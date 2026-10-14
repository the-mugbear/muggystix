"""Two FIRST writes of report defaults at once (review 2026-10-14, H3).

`FOR UPDATE` on a row that does not exist locks nothing: two saves that both
found no row both inserted, and the second failed on the unique key / project
— a valid save answered with a 500.  The installation's defaults
(`report_defaults.save`) and a project's (`client_reports._write_profile`)
now create the row with `INSERT … ON CONFLICT DO NOTHING` and lock it after.

Deterministic: A writes and does not commit; B's write must WAIT for A (not
fail, not finish); A commits; B completes, and both changes stand.  Before the
fix B waited on A's row in the unique index and was then refused.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import text

from app.api.v1.endpoints.client_reports import _write_profile
from app.db.models_reports import ReportDefault, ReportProfile
from app.services import report_defaults
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _b_waits_then_finishes(two_sessions, write_b):
    """Run ``write_b`` on session B on a thread; assert it waits on A's lock;
    commit A; answer what B returned (or raised)."""
    outcome = {}

    def b():
        try:
            outcome["result"] = write_b(two_sessions.b)
            two_sessions.b.commit()
        except BaseException as exc:  # noqa: BLE001 — handed back to the test
            two_sessions.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=b, daemon=True)
    thread.start()
    deadline = time.time() + 10
    waiting = False
    while time.time() < deadline and not waiting and thread.is_alive():
        with two_sessions._engine.connect() as probe:
            waiting = bool(probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar())
        time.sleep(0.05)
    assert waiting and thread.is_alive(), f"B did not wait for A's write: {outcome}"

    two_sessions.a.commit()
    thread.join(20)
    assert not thread.is_alive(), "B never finished after A committed"
    assert "error" not in outcome, repr(outcome.get("error"))
    return outcome["result"]


def _clear_installation_defaults(two_sessions):
    db = two_sessions.fresh()
    try:
        db.query(ReportDefault).delete()
        db.commit()
    finally:
        db.close()


def test_two_first_saves_of_an_installation_default_both_succeed(two_sessions):
    _, user = two_sessions.project()
    _clear_installation_defaults(two_sessions)
    try:
        first = report_defaults.save(two_sessions.a, {"author_name": "Team A"}, user.id)   # NOT committed
        assert first == {"author_name": {"from": None, "to": "Team A"}}

        second = _b_waits_then_finishes(
            two_sessions, lambda db: report_defaults.save(db, {"author_name": "Team B"}, user.id))
        # B saw A's committed row: its audit "before" is A's text, not nothing.
        assert second == {"author_name": {"from": "Team A", "to": "Team B"}}

        db = two_sessions.fresh()
        try:
            assert [(r.key, r.text) for r in db.query(ReportDefault).all()] == [("author_name", "Team B")]
        finally:
            db.close()
    finally:
        _clear_installation_defaults(two_sessions)


def test_two_first_saves_of_different_installation_defaults_both_stand(two_sessions):
    _, user = two_sessions.project()
    _clear_installation_defaults(two_sessions)
    try:
        report_defaults.save(two_sessions.a, {"author_name": "Team A", "disclaimer": "A's words."}, user.id)
        second = _b_waits_then_finishes(
            two_sessions, lambda db: report_defaults.save(db, {"disclaimer": "B's words."}, user.id))
        assert second == {"disclaimer": {"from": "A's words.", "to": "B's words."}}
        db = two_sessions.fresh()
        try:
            assert dict((r.key, r.text) for r in db.query(ReportDefault).all()) == {
                "author_name": "Team A", "disclaimer": "B's words."}
        finally:
            db.close()
    finally:
        _clear_installation_defaults(two_sessions)


def test_two_first_writes_of_a_projects_defaults_keep_both_details(two_sessions):
    project, user = two_sessions.project()
    _write_profile(two_sessions.a, project.id, {"client_name": "Client A"}, user.id)    # NOT committed

    _b_waits_then_finishes(
        two_sessions, lambda db: _write_profile(db, project.id, {"author_name": "Team B"}, user.id))

    db = two_sessions.fresh()
    try:
        rows = db.query(ReportProfile).filter(ReportProfile.project_id == project.id).all()
        assert [(r.client_name, r.author_name) for r in rows] == [("Client A", "Team B")]
    finally:
        db.close()
