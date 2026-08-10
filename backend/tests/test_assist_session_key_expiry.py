"""The assist session list must report when its key stops working (v2.240.0).

The operator's practical question about a live session is "end it now, or let
it lapse?", which needs an expiry. It can't be computed client-side from
``started_at`` plus a hardcoded 4 hours: ``AGENT_KEY_TTL_HOURS`` can override
the default and ``ttl_hours`` is a per-start parameter, so a derived expiry
would be quietly wrong exactly where it mattered.

It is the KEY's expiry, not the session's — the session row has no lifetime of
its own and can outlive its key. A session whose key is gone is dead in
practice while ``status`` still reads 'active', and that state is worth
surfacing rather than hiding behind a stale-looking timestamp.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_auth import APIKey


def _start(client, project_id, **body):
    r = client.post(f"/api/v1/projects/{project_id}/assist/start", json=body or {})
    assert r.status_code == 201, r.text
    return r.json()


def _list(client, project_id):
    r = client.get(f"/api/v1/projects/{project_id}/assist/sessions")
    assert r.status_code == 200, r.text
    return r.json()


def test_list_reports_the_key_expiry_for_an_active_session(
    client, db_session, test_project,
):
    started = _start(client, test_project.id, purpose="ftp sweep", ttl_hours=6)
    rows = _list(client, test_project.id)
    row = next(r for r in rows if r["id"] == started["assist_session_id"])

    assert row["key_expires_at"] is not None
    expires = datetime.fromisoformat(row["key_expires_at"].replace("Z", "+00:00"))
    # Matches the requested TTL, not the 4h default — the whole reason this
    # can't be derived from started_at client-side.
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(hours=5) < delta < timedelta(hours=7), delta


def test_key_expiry_is_null_once_no_active_key_remains(
    client, db_session, test_project,
):
    """Ending a session revokes its key; the row must stop advertising access."""
    started = _start(client, test_project.id)
    sid = started["assist_session_id"]

    db_session.query(APIKey).filter(APIKey.assist_session_id == sid).update(
        {"is_active": False}, synchronize_session=False
    )
    db_session.commit()

    row = next(r for r in _list(client, test_project.id) if r["id"] == sid)
    assert row["key_expires_at"] is None, (
        "a session with no live key must not report an expiry — it reads as "
        "'still has access until X' when access is already gone"
    )


def test_expiry_lookup_does_not_scale_with_session_count(
    client, db_session, test_project,
):
    """Guards the grouped query against regressing to a per-row lookup."""
    ids = [_start(client, test_project.id)["assist_session_id"] for _ in range(5)]

    from sqlalchemy import event
    from app.db.session import engine  # noqa: F401

    statements = []

    conn = db_session.connection()

    def _record(conn_, cursor, statement, params, context, executemany):
        if "api_keys" in statement.lower():
            statements.append(statement)

    event.listen(conn.engine, "before_cursor_execute", _record)
    try:
        rows = _list(client, test_project.id)
    finally:
        event.remove(conn.engine, "before_cursor_execute", _record)

    assert all(
        r["key_expires_at"] is not None for r in rows if r["id"] in ids
    ), "every freshly started session should report an expiry"
    # One SELECT against api_keys for the whole list, not one per session.
    selects = [s for s in statements if s.lower().lstrip().startswith("select")]
    # Guard against the assertion passing vacuously (listener not wired to the
    # connection the request actually used) — there must be exactly one.
    assert len(selects) == 1, f"expected a single grouped lookup, saw {len(selects)}"
