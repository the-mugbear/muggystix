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

    db_session.query(APIKey).filter(
        APIKey.agent_session_id == _agent_session_id_for(db_session, sid)
    ).update(
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


# ---------------------------------------------------------------------------
# Lapsing (v2.283.0)
#
# `status` only ever left 'active' when an operator pressed End, so the Start
# AI Assist dialog's "you have N active sessions" panel accumulated every
# session they had ever started — rows badged `key expired`, listed as active,
# that no agent could use. The operator's question ("am I supposed to remove
# these myself?") should answer itself: no.
# ---------------------------------------------------------------------------

def _agent_session_id_for(db_session, assist_session_id):
    """Resolve an assist session's agent_session_id — api_keys binds to keys
    through it now (the api_keys.assist_session_id column was dropped)."""
    from app.db.models_agent import AssistSession
    return (
        db_session.query(AssistSession.agent_session_id)
        .filter(AssistSession.id == assist_session_id)
        .scalar()
    )


def _expire_keys(db_session, session_id, *, when=None):
    """Age the session's key out, the way the TTL would."""
    db_session.query(APIKey).filter(
        APIKey.agent_session_id == _agent_session_id_for(db_session, session_id)
    ).update(
        {"expires_at": when or (datetime.now(timezone.utc) - timedelta(minutes=5))},
        synchronize_session=False,
    )
    db_session.commit()


def test_a_session_whose_key_expired_is_not_reported_active(
    client, db_session, test_project,
):
    """The listing is what the operator's session panel filters on, so it has
    to be right the moment they open the dialog — an hour-stale sweep would
    show them dead sessions to tidy up by hand."""
    sid = _start(client, test_project.id)["assist_session_id"]
    assert next(r for r in _list(client, test_project.id) if r["id"] == sid)["status"] == "active"

    _expire_keys(db_session, sid)

    row = next(r for r in _list(client, test_project.id) if r["id"] == sid)
    assert row["status"] == "ended", (
        "a session with no usable key is not active; reporting it as active is "
        "what made the operator's list accumulate"
    )


def test_a_live_session_is_left_alone(client, db_session, test_project):
    """The sweep must not end sessions an agent is still using — that would
    revoke work in progress on a timer nobody asked for."""
    from app.services.assist_session_service import lapse_expired_assist_sessions
    from app.db.models_agent import AssistSession

    sid = _start(client, test_project.id, ttl_hours=6)["assist_session_id"]
    assert lapse_expired_assist_sessions(db_session) == 0

    db_session.expire_all()
    assert db_session.get(AssistSession, sid).status == "active"


def test_the_sweep_ends_lapsed_sessions_and_dates_them_honestly(
    client, db_session, test_project,
):
    """`ended_at` records when access actually stopped — the key's expiry, not
    when the hourly sweep happened to run, which would misdate every lapse by
    up to an hour."""
    from app.services.assist_session_service import lapse_expired_assist_sessions
    from app.db.models_agent import AssistSession

    sid = _start(client, test_project.id)["assist_session_id"]
    expired_at = datetime.now(timezone.utc) - timedelta(hours=3)
    _expire_keys(db_session, sid, when=expired_at)

    assert lapse_expired_assist_sessions(db_session) == 1

    db_session.expire_all()
    session = db_session.get(AssistSession, sid)
    assert session.status == "ended"
    assert session.ended_at is not None
    assert abs((session.ended_at - expired_at).total_seconds()) < 2, (
        "ended_at should be when the key died, not when the sweep ran"
    )

    # Idempotent: a second pass finds nothing, so concurrent workers can't
    # double-end or rewrite the timestamp.
    assert lapse_expired_assist_sessions(db_session) == 0


def test_the_sweep_keeps_the_session_record(client, db_session, test_project):
    """Lapsing is a status change, not a delete — the audit trail is the reason
    the row exists after the key is gone."""
    from app.services.assist_session_service import lapse_expired_assist_sessions
    from app.db.models_agent import AssistSession

    sid = _start(client, test_project.id, purpose="ftp sweep")["assist_session_id"]
    _expire_keys(db_session, sid)
    lapse_expired_assist_sessions(db_session)

    db_session.expire_all()
    session = db_session.get(AssistSession, sid)
    assert session is not None and session.purpose == "ftp sweep"
