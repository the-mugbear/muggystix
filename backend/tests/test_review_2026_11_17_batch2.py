"""Review 2026-11-17, batch 2 — the small correctness and security fixes.

* A6 — a refused sign-in wrote an audit row per request, for ever;
* A9 — the agents' finding read listed every comment, uncapped;
* A14 — a skipped record's warning was the database error's own text;
* A17 — restoring a named endpoint skipped "the name is observed at the host";
* A19 — a failed or cancelled report job was never removed;
* A20 — an unreadable webhook secret sent the webhook unsigned, a retry took a
  delivered or in-flight row, two metadata addresses were reachable, and an
  inbound request id was logged as written.

A7 (the removed completion route, the connection test's transaction, the
guard over every provider call) is in ``test_llm_provider_account_floor.py``;
A8 (target text is data) in ``test_agent_safety_policy.py``; A18 (a report
file's hash) in ``test_agent_assist_reporting.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.core.request_context import _incoming_request_id
from app.db import models
from app.db.models import ReportJob
from app.db.models_auth import AuditLog
from app.db.models_project import WebhookConfig, WebhookDelivery, WebhookDeliveryStatus
from app.parsers.parser_utils import SKIP_REASONS_SHOWN, note_skip, skip_reason, skip_summary
from app.services import url_validator
from app.services import webhook_dispatcher as wd
from app.services.dns_name_service import NAME_NOT_AT_HOST, record_observation
from app.services.llm_provider_service import encrypt_secret
from app.services.report_job_service import ReportJobService


# --- A6: a refused sign-in is recorded once a window ------------------------

def _sign_in(client, username="nobody-by-that-name", password="wrong", agent=None):
    headers = {"User-Agent": agent} if agent else {}
    return client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=headers)


def test_refused_sign_ins_write_one_row_a_window_not_one_a_request(client, db_session):
    for _ in range(security.LOGIN_LOCKOUT_FAILURES):
        assert _sign_in(client).status_code == 401
    refused = [_sign_in(client).status_code for _ in range(12)]
    assert set(refused) == {429}

    def rows(action):
        return db_session.query(AuditLog).filter(
            AuditLog.action == action, AuditLog.username == "nobody-by-that-name",
        ).count()

    # The failures that tripped the limit are all there; the twelve refusals
    # after it are one line.
    assert rows("login_failed") == security.LOGIN_LOCKOUT_FAILURES
    assert rows("login_throttled") == 1

    # Another name from the same address has its own line.
    for _ in range(security.LOGIN_LOCKOUT_FAILURES):
        _sign_in(client, username="someone-else")
    assert _sign_in(client, username="someone-else").status_code == 429
    assert db_session.query(AuditLog).filter(AuditLog.action == "login_throttled").count() == 2


def test_an_audit_row_keeps_a_bounded_user_agent(client, db_session):
    _sign_in(client, username="long-agent", agent="A" * 5000)
    row = db_session.query(AuditLog).filter(AuditLog.username == "long-agent").one()
    assert len(row.user_agent) == security.AUDIT_USER_AGENT_MAX


# --- A9: the agents' finding read caps its discussion -----------------------

def test_the_agents_finding_lists_the_newest_comments_and_says_how_many(
    client, db_session, test_project, monkeypatch,
):
    from app.api.v1.endpoints import agent_assist

    monkeypatch.setattr(agent_assist, "_FINDING_NOTE_CAP", 2)
    base = f"/api/v1/projects/{test_project.id}/findings"
    fid = client.post(base, json={"title": "Discussed at length", "severity": "low"}).json()["id"]
    for n in range(1, 5):
        assert client.post(f"{base}/{fid}/notes", json={"body": f"comment {n}"}).status_code in (200, 201)

    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "read"})
    headers = {"X-API-Key": started.json()["api_key"]}
    answer = client.get(f"/api/v1/agent/assist/findings/{fid}", headers=headers)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    # The newest two, oldest first — and the whole thread counted.
    assert [c["body"] for c in body["comments"]] == ["comment 3", "comment 4"]
    assert (body["comments_total"], body["comments_truncated"]) == (4, True)
    assert (body["evidence_thread_total"], body["evidence_thread_truncated"]) == (0, False)


# --- A14: why a record was skipped, without the statement -------------------

class _DriverError(Exception):
    pass


def test_a_skip_reason_never_carries_the_statement_or_its_values():
    database = IntegrityError(
        "INSERT INTO ports_v2 (host_id, service_tunnel) VALUES (%(host_id)s, %(tunnel)s)",
        {"host_id": 7, "tunnel": "s3cr3t-looking-value"},
        _DriverError("value too long for type character varying(20)\nDETAIL:  Failing row contains (s3cr3t-looking-value)."),
    )
    reason = skip_reason(database)
    assert reason == "database refused the record (value too long for type character varying(20))"
    for leaked in ("INSERT", "[SQL", "parameters", "s3cr3t", "DETAIL"):
        assert leaked not in reason

    assert skip_reason(ValueError("bad   port\nnumber")) == "ValueError: bad port number"
    assert len(skip_reason(ValueError("x" * 5000))) < 250


def test_skips_are_all_counted_and_only_the_first_few_named():
    reasons: list = []
    for n in range(70_000):
        note_skip(reasons, ValueError(f"host {n}"))
    assert len(reasons) == SKIP_REASONS_SHOWN
    line = skip_summary(reasons, 70_000, "host")
    assert line.startswith("70,000 hosts were skipped: ValueError: host 0;")
    assert line.endswith(f"and {70_000 - SKIP_REASONS_SHOWN:,} more") and len(line) < 400
    assert skip_summary([], 0, "host") is None
    assert skip_summary(["ValueError: x"], 1, "host") == "1 host was skipped: ValueError: x"


# --- A17: a named endpoint added to an existing finding ---------------------

def _name(db, project, fqdn, ip):
    record_observation(db, project_id=project.id, name=fqdn, record_type="A", value=ip)
    db.commit()
    return db.query(models.DNSName).filter_by(project_id=project.id, fqdn=fqdn).one()


def test_a_name_goes_on_an_existing_finding_only_where_it_is_observed_or_was_removed(
    client, db_session, test_project,
):
    here = models.Host(project_id=test_project.id, ip_address="10.47.0.1", state="up")
    there = models.Host(project_id=test_project.id, ip_address="10.47.0.2", state="up")
    db_session.add_all([here, there])
    db_session.commit()
    at_here = _name(db_session, test_project, "portal.example.org", here.ip_address)
    base = f"/api/v1/projects/{test_project.id}/findings"
    fid = client.post(base, json={"title": "On a name", "severity": "low", "host_ids": [here.id]}).json()["id"]

    def add(host, name):
        return client.post(f"{base}/{fid}/hosts", json={"endpoints": [{"host_id": host.id, "name_id": name.id}]})

    # A name never seen at that host: refused with the rule's own words —
    # the door used to ask only that the name be in the project.
    refused = add(there, at_here)
    assert refused.status_code == 422 and refused.json()["detail"] == NAME_NOT_AT_HOST

    # Observed there: attached.
    added = add(here, at_here)
    assert added.status_code == 200, added.text
    row = next(h for h in added.json()["hosts"] if h["name_id"] == at_here.id)

    # Undo of a removal is let through whatever became of the observation.
    assert client.delete(f"{base}/{fid}/endpoints/{row['id']}").status_code in (200, 204)
    db_session.query(models.DNSRecord).filter_by(name_id=at_here.id).delete()
    db_session.commit()
    restored = add(here, at_here)
    assert restored.status_code == 200, restored.text
    assert any(h["name_id"] == at_here.id for h in restored.json()["hosts"])


# --- A19: failed and cancelled report jobs leave in time --------------------

def test_failed_and_cancelled_report_jobs_are_removed_after_their_retention(
    db_session, test_project, tmp_path, monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REPORT_STORAGE_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    old, recent = now - timedelta(days=45), now - timedelta(days=3)

    def job(status, completed_at):
        row = ReportJob(project_id=test_project.id, format="json", report_type="comprehensive",
                        filters={}, status=status, completed_at=completed_at)
        db_session.add(row)
        db_session.commit()
        return row.id

    gone = {job("failed", old), job("cancelled", old)}
    kept = {job("failed", recent), job("cancelled", recent), job("queued", None), job("processing", None)}

    assert ReportJobService().cleanup_expired() == 2
    db_session.expire_all()
    left = {row.id for row in db_session.query(ReportJob).filter(ReportJob.project_id == test_project.id)}
    assert left == kept and not (left & gone)

    # 0 keeps them for ever, as every earlier release did.
    monkeypatch.setattr(settings, "REPORT_FAILED_JOB_RETENTION_DAYS", 0)
    again = job("failed", old)
    assert ReportJobService().cleanup_expired() == 0
    assert db_session.get(ReportJob, again) is not None


# --- A20: webhooks -----------------------------------------------------------

@pytest.fixture
def signed_webhook(db_session, test_project, test_user):
    cfg = WebhookConfig(
        project_id=test_project.id, name="signed", url="https://receiver.invalid/hook", events=[],
        is_active=True, created_by_id=test_user.id, secret_encrypted=encrypt_secret("shared-secret"),
    )
    db_session.add(cfg)
    db_session.commit()
    return cfg


def test_a_webhook_whose_secret_cannot_be_read_is_not_sent(db_session, signed_webhook, monkeypatch):
    posted: list = []
    monkeypatch.setattr(wd, "_post", lambda *a, **k: posted.append(a))

    assert wd.signing_secret(signed_webhook) == "shared-secret"

    # The encryption key changed: the stored value no longer decrypts.
    monkeypatch.setattr(wd, "decrypt_secret", lambda stored: None)
    secret = wd.signing_secret(signed_webhook)
    assert secret is wd._UNREADABLE and "shared-secret" not in repr(secret)
    assert wd._attempt_delivery(signed_webhook.url, secret, {"event": "x"}, delivery_id=1) == (
        False, None, wd.SECRET_UNREADABLE,
    )
    assert wd.WebhookDispatcher(db_session).deliver_test(signed_webhook) == {
        "ok": False, "error": wd.SECRET_UNREADABLE,
    }
    assert posted == []                      # nothing went out unsigned

    # A webhook with no secret is still simply unsigned.
    signed_webhook.secret_encrypted = None
    assert wd.signing_secret(signed_webhook) is None


@pytest.mark.parametrize("status, said", [
    (WebhookDeliveryStatus.DELIVERED.value, "already delivered"),
    (WebhookDeliveryStatus.SENDING.value, "being sent now"),
])
def test_only_a_failed_delivery_is_requeued(client, db_session, test_project, signed_webhook, monkeypatch, status, said):
    monkeypatch.setattr(wd, "_ensure_workers", lambda: None)
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: None)
    wd.WebhookDispatcher(db_session).stage(project_id=test_project.id, event="host_assigned", title="t")
    db_session.commit()
    row = db_session.query(WebhookDelivery).one()
    row.status = status
    db_session.commit()

    answer = client.post(f"/api/v1/projects/{test_project.id}/webhooks/deliveries/{row.id}/retry")
    assert answer.status_code == 409 and said in answer.json()["detail"]
    db_session.refresh(row)
    assert row.status == status


# --- A20: outbound addresses and the inbound request id ----------------------

@pytest.mark.parametrize("address", ["100.100.100.200", "192.0.0.192", "169.254.169.254"])
def test_a_metadata_address_is_refused_even_where_private_hosts_are_allowed(address):
    import ipaddress

    ip = ipaddress.ip_address(address)
    assert any(ip in network for network in url_validator._ALWAYS_FORBIDDEN_NETWORKS)
    # …and the neighbouring shared-address space is still the private tier's.
    assert not any(
        ipaddress.ip_address("100.64.0.1") in network for network in url_validator._ALWAYS_FORBIDDEN_NETWORKS
    )


@pytest.mark.parametrize("sent, kept", [
    (b"corr-abc-123", "corr-abc-123"),
    (b"A" * 64, "A" * 64),
    (b"A" * 65, None),
    (b"abc status=200 user=admin", None),
    (b"abc\tdef", None),
    (b"", None),
])
def test_an_inbound_request_id_is_kept_only_when_it_is_an_ids_shape(sent, kept):
    assert _incoming_request_id({"headers": [(b"x-request-id", sent)]}) == kept


def test_a_request_with_a_misshapen_id_gets_one_of_ours(client):
    answer = client.get("/health", headers={"X-Request-ID": "abc status=200"})
    minted = answer.headers["x-request-id"]
    assert minted and " " not in minted and "status" not in minted
