"""Review 2026-11-20, batch 1 — the small, certain fixes.

* C2 — a closed-set field of the Hosts language took a value outside its set:
  ``scope:nome`` was an empty list and ``NOT scope:nome`` the whole project;
* C8 — ``compute_operations`` caught a statement the API timeout cancelled and
  went on to the next section;
* a review status already held wrote a changelog line;
* a remediation note could be dated in the future;
* the webhook "Send test" posted with the request's transaction open.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db import models
from app.db.models import FollowStatus
from app.db.models_project import ProjectEvent, WebhookConfig
from app.db.session import QUERY_CANCELED_SQLSTATE
from app.schemas.remediation_schemas import NoteIn, NoteUpdate
from app.services import host_query_dsl as dsl
from app.services import operations_service
from app.services import project_events
from app.services import webhook_dispatcher as wd
from app.services.host_follow_service import HostFollowService
from app.services.llm_provider_service import encrypt_secret


# --- C2: a closed-set field refuses a value outside its set -----------------

_ENUM_FIELDS = sorted(
    name for name, spec in dsl.FIELD_BUILDERS.items() if spec.value_source == "enum"
)


def test_the_fields_this_is_about_are_closed_sets():
    # The five that had no check, and the four that had one written by hand.
    for name in ("state", "portstate", "scope", "kind", "check", "has", "follow", "gap", "vulnscan"):
        assert name in _ENUM_FIELDS


@pytest.mark.parametrize("name", _ENUM_FIELDS)
def test_an_unknown_value_is_refused_naming_the_field(name):
    with pytest.raises(dsl.DSLError) as refused:
        dsl.parse_query(f"{name}:no-such-value-zz")
    assert name in str(refused.value)


@pytest.mark.parametrize("name", _ENUM_FIELDS)
def test_every_value_a_field_offers_is_accepted(name):
    for value in dsl.FIELD_BUILDERS[name].enum_values:
        dsl.parse_query(f'{name}:"{value}"')


@pytest.mark.parametrize("q", [
    "NOT scope:nome",                 # was: every host of the project
    "scope:subnet,nome",              # was: the bad value dropped
    "os:windows AND NOT kind:vulnerabilty",
    "state:upp",
    "check:smb_sining",
])
def test_a_typo_is_never_an_answer(q):
    with pytest.raises(dsl.DSLError):
        dsl.parse_query(q)


def test_a_port_state_the_field_does_not_offer_but_accepts_still_parses():
    # ``portstate:`` offers three and accepts every stored state.
    dsl.parse_query('portstate:"open|filtered"')
    dsl.parse_query("portstate:unfiltered")
    dsl.parse_query("STATE:UP")


# --- C8: a cancelled statement is the request's answer ----------------------

def _cancelled():
    exc = RuntimeError("canceling statement due to statement timeout")
    exc.orig = SimpleNamespace(pgcode=QUERY_CANCELED_SQLSTATE)
    return exc


@pytest.mark.parametrize("section", ["compute_review_followups", "compute_blockers"])
def test_a_section_the_timeout_cancelled_ends_the_request(
    db_session, test_user, test_project, monkeypatch, section,
):
    def cancelled(*args, **kwargs):
        raise _cancelled()

    monkeypatch.setattr(operations_service, section, cancelled)
    with pytest.raises(RuntimeError):
        operations_service.compute_operations(
            db_session, test_user, test_project, include_investigate=False)


def test_a_section_that_failed_otherwise_is_still_said_not_raised(
    db_session, test_user, test_project, monkeypatch,
):
    def broken(*args, **kwargs):
        raise ValueError("a bug in one section")

    monkeypatch.setattr(operations_service, "compute_blockers", broken)
    answer = operations_service.compute_operations(
        db_session, test_user, test_project, include_investigate=False)
    assert answer.blockers_unavailable is True


# --- a review status already held records nothing ---------------------------

def _review_lines(db, project_id):
    return (db.query(ProjectEvent)
            .filter(ProjectEvent.project_id == project_id,
                    ProjectEvent.action == "host_review_changed").count())


def test_marking_the_status_a_host_already_has_writes_no_line(db_session, test_project, test_user):
    host = models.Host(ip_address="10.20.1.5", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    project_events.note_actor(db_session, user_id=test_user.id)      # as a request's gate does
    service = HostFollowService(db_session)

    service.set_follow_status(host.id, test_user.id, FollowStatus.IN_REVIEW)
    assert _review_lines(db_session, test_project.id) == 1
    service.set_follow_status(host.id, test_user.id, FollowStatus.IN_REVIEW)
    assert _review_lines(db_session, test_project.id) == 1          # nothing changed

    service.set_follow_status(host.id, test_user.id, FollowStatus.REVIEWED)
    assert _review_lines(db_session, test_project.id) == 2
    # Reviewed again re-stamps the review's moment: that is a change.
    service.set_follow_status(host.id, test_user.id, FollowStatus.REVIEWED)
    assert _review_lines(db_session, test_project.id) == 3


# --- a remediation note is about something that happened --------------------

def test_a_note_cannot_be_dated_in_the_future():
    later = datetime.now(timezone.utc) + timedelta(days=365)
    with pytest.raises(ValidationError):
        NoteIn(body="called the contact", occurred_at=later)
    with pytest.raises(ValidationError):
        NoteUpdate(occurred_at=later)
    # A naive moment is read as UTC, as everywhere else.
    with pytest.raises(ValidationError):
        NoteIn(body="x", occurred_at=later.replace(tzinfo=None))


def test_a_past_moment_now_and_a_slightly_fast_clock_are_accepted():
    now = datetime.now(timezone.utc)
    assert NoteIn(body="x", occurred_at=now - timedelta(days=3)).occurred_at is not None
    assert NoteIn(body="x", occurred_at=now + timedelta(minutes=2)).occurred_at is not None
    assert NoteIn(body="x").occurred_at is None
    assert NoteUpdate(body="y").occurred_at is None


# --- "Send test" ends the request's read before it posts --------------------

def test_the_webhook_test_posts_with_no_transaction_open(db_session, test_project, test_user, monkeypatch):
    cfg = WebhookConfig(
        project_id=test_project.id, name="hook", url="https://receiver.invalid/hook", events=[],
        is_active=True, created_by_id=test_user.id, secret_encrypted=encrypt_secret("shared-secret"),
    )
    db_session.add(cfg)
    db_session.commit()
    cfg = db_session.get(WebhookConfig, cfg.id)          # the request's read
    seen = {}

    def post(url, secret, payload):
        seen["open"] = db_session.in_transaction()
        seen["url"] = url
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(wd, "_post", post)
    assert wd.WebhookDispatcher(db_session).deliver_test(cfg) == {"ok": True, "status_code": 204}
    assert seen == {"open": False, "url": "https://receiver.invalid/hook"}
