"""Host tests shared by JWT and agent callers. Callers own transactions."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_host_tests import HostTest
from app.db.models_project import ProjectMembership
from app.db.models_proposals import EvidenceRecord
from app.services.dns_name_service import InvalidName, normalize_fqdn


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def validate_assignee(db, project_id, user_id):
    """A test is assigned to someone who can work it: a project analyst or
    admin, or an active global admin — who writes to every project without a
    membership row, and could otherwise neither add a test for themself nor
    claim one (found in the browser, v2.447.0)."""
    if user_id is None:
        return
    if db.query(ProjectMembership.id).filter(
        ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id,
        ProjectMembership.role.in_(("admin", "analyst")),
    ).first():
        return
    if db.query(User.id).filter(
        User.id == user_id, User.is_active.is_(True), User.role == UserRole.ADMIN,
    ).first():
        return
    raise HTTPException(422, "Assignee must be a project analyst or administrator")


def resolve_target_name(db, project_id, host, fqdn):
    if not fqdn:
        return None, None
    try:
        fqdn, kind = normalize_fqdn(fqdn)
    except InvalidName as exc:
        raise HTTPException(422, str(exc)) from exc
    name = db.query(models.DNSName).filter(
        models.DNSName.project_id == project_id, models.DNSName.fqdn == fqdn,
    ).first()
    if kind != "fqdn" or name is None or not db.query(models.DNSRecord.id).filter(
        models.DNSRecord.name_id == name.id, models.DNSRecord.value == host.ip_address,
        models.DNSRecord.record_type.in_(models.DNS_ADDRESS_VALUED_TYPES),
    ).first():
        raise HTTPException(422, "Target name must be observed at this host in this project")
    return name.id, name.fqdn


def resolve_issue(db, host_id, vulnerability_id):
    """The weakness a test confirms, as the issue's identity on this host
    (v2.445.0).  The observation must be one of THIS host's."""
    if vulnerability_id is None:
        return {}
    from app.db.models_vulnerability import Vulnerability
    from app.services.vuln_identity import issue_key_for

    vuln = db.query(Vulnerability).filter(
        Vulnerability.id == vulnerability_id, Vulnerability.host_id == host_id,
    ).first()
    if vuln is None:
        raise HTTPException(422, "vulnerability_id must be a scanner observation on this host")
    return {"issue_key": issue_key_for(vuln), "issue_title": (vuln.title or "")[:500] or None}


def get_test(db, project_id, test_id):
    row = db.query(HostTest).filter(HostTest.project_id == project_id, HostTest.id == test_id).first()
    if row is None:
        raise HTTPException(404, "Host test not found in this project")
    return row


def create_tests(db, project_id, tests, who):
    keys = [item.request_key for item in tests]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "Each test in a batch needs a distinct request_key")
    hosts = {h.id: h for h in db.query(models.Host).filter(
        models.Host.project_id == project_id, models.Host.id.in_({item.host_id for item in tests}),
    )}
    existing = {t.request_key: t for t in db.query(HostTest).filter(
        HostTest.project_id == project_id, HostTest.request_key.in_(keys),
    )}
    prepared = []
    for item in tests:
        values = item.model_dump()
        digest = payload_hash({**values, "actor": who.user_id})
        row = existing.get(item.request_key)
        if row:
            if row.request_hash != digest:
                raise HTTPException(409, "request_key already used for a different test")
            prepared.append(row)
            continue
        if item.host_id not in hosts:
            raise HTTPException(404, "Host not found in this project")
        validate_assignee(db, project_id, item.assigned_to_id)
        name_id, fqdn = resolve_target_name(db, project_id, hosts[item.host_id], item.target_fqdn)
        values.update(target_fqdn=fqdn, name_id=name_id, request_hash=digest)
        values.update(resolve_issue(db, item.host_id, values.pop("vulnerability_id", None)))
        prepared.append(HostTest(
            **values, project_id=project_id, source="agent" if who.session else "person",
            agent_session_id=who.session.id if who.session else None, created_by_user_id=who.user_id,
            agent_model=who.model, agent_client=who.client, prompt_version=who.prompt_version,
        ))
    # Savepoints isolate uniqueness races; the outer transaction keeps the batch atomic.
    result = []
    created = []
    for row in prepared:
        if row.id is None:
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
                created.append(row)
            except IntegrityError:
                other = db.query(HostTest).filter(
                    HostTest.project_id == project_id, HostTest.request_key == row.request_key,
                ).first()
                if other is None:
                    raise
                if other.request_hash != row.request_hash:
                    raise HTTPException(409, "request_key already used for a different test")
                row = other
        result.append(row)
    # Review 2026-10-01 B9 — a test proposed FOR someone tells them (one
    # notification per assignee for the batch; a replayed test tells nobody).
    # An agent's tests are one notification per assignee per SESSION: it may
    # send its batch as that many single-test calls.
    _notify_assigned(db, created, who.user_id, agent_session_id=who.session.id if who.session else None)
    return result


def _notify_assigned(db, tests, actor_id, agent_session_id=None):
    """In the caller's transaction, so a rolled-back change notifies nobody."""
    from app.services.notification_service import NotificationService

    if tests:
        NotificationService(db).notify_host_tests_assigned(tests, actor_id, agent_session_id=agent_session_id)


def update_test(db, project_id, test_id, body, user_id):
    row = get_test(db, project_id, test_id)
    assigned_before = row.assigned_to_id
    values = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if "status" in values and values["status"] is None:
        raise HTTPException(422, "status cannot be null")
    if "assigned_to_id" in values:
        validate_assignee(db, project_id, values["assigned_to_id"])
    if values.get("status") == "dismissed":
        if not values.get("dismissed_reason"):
            raise HTTPException(422, "A dismissal reason is required")
        values.update(dismissed_by_id=user_id, dismissed_at=datetime.now(timezone.utc))
    elif "dismissed_reason" in values:
        raise HTTPException(422, "A dismissal reason accompanies status=dismissed")
    if values.get("status") in ("proposed", "in_progress", "done"):
        values.update(dismissed_by_id=None, dismissed_at=None, dismissed_reason=None)
    # What the row will BE, not what the request names: clearing the summary of
    # a finished test must meet the same rule as finishing it.
    status_after = values.get("status", row.status)
    summary_after = values["tester_summary"] if "tester_summary" in values else row.tester_summary
    if status_after == "done" and not summary_after and ("status" in values or "tester_summary" in values):
        if not db.query(EvidenceRecord.id).filter(EvidenceRecord.host_test_id == row.id).first():
            raise HTTPException(422, "Record evidence or explain why no test was run in tester_summary")
    changed = db.execute(update(HostTest).where(
        HostTest.id == row.id, HostTest.project_id == project_id,
        HostTest.revision == body.expected_revision,
    ).values(**values, revision=HostTest.revision + 1, updated_at=datetime.now(timezone.utc)),
        execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        raise HTTPException(409, "This test changed; refresh before updating it")
    db.refresh(row)
    # B9 — handing a test to someone else tells them; claiming it yourself, or
    # re-sending the assignee it already had, does not.
    if values.get("assigned_to_id") is not None and row.assigned_to_id != assigned_before:
        _notify_assigned(db, [row], user_id)
    return row


# Outcomes that settle a test; the others leave it open for another attempt.
CLOSING_OUTCOMES = ("finding", "no_finding")


def record_result(db, project_id, test_id, body, user_id):
    """A person's result for a test: the evidence record (the same one an agent
    writes, through the same service) and the test's next status, in the
    caller's transaction — a stale revision rolls both back.  A finding or
    no-finding outcome closes the test; inconclusive or could-not-run leaves
    it in progress.  Returns ``(test, evidence)``."""
    from app.schemas.host_test_schemas import HostTestUpdate
    from app.services import agent_evidence_service

    test = get_test(db, project_id, test_id)
    command = body.command
    if command is None and test.command:
        command = test.command.replace("{ip}", test.host.ip_address).replace("{fqdn}", test.target_fqdn or "{fqdn}")

    def store():
        return agent_evidence_service.record_evidence_once(
            db, project_id=project_id, host_id=test.host_id, host_test_id=test.id,
            request_key=body.request_key, tool=test.tool or "manual", outcome=body.outcome,
            summary=body.summary, command=command, raw_output=body.raw_output,
            observed_ip=body.observed_ip, executed_at=datetime.now(timezone.utc),
            recorded_by_user_id=user_id, server_timed=True,
        )

    # A retry (double click, lost response) returns what the first call stored
    # and writes nothing.  The evidence service decides whether it IS the same
    # result: a changed outcome, summary, output or author under the key is a
    # 409, never a success that kept the old record.
    if db.query(EvidenceRecord.id).filter(
        EvidenceRecord.project_id == project_id, EvidenceRecord.request_key == body.request_key,
    ).first() is not None:
        return test, store()[0]
    # Refuse a stale copy before anything is written.
    if test.revision != body.expected_revision:
        raise HTTPException(409, "This test changed; refresh before updating it")
    record, created = store()
    if not created:
        # The same key was stored by a request that ran alongside this one (a
        # double click): the check above saw nothing, the insert met the
        # other's row, and the evidence service has confirmed it IS this
        # result.  That request already moved the test, so taking the revision
        # again would refuse a retry that succeeded (review 2026-10-01 N8).
        db.refresh(test)
        return test, record
    # Every NEW result takes its revision, including one that leaves the status
    # as it was: the UPDATE's WHERE is the only check a concurrent change
    # cannot slip past, and the caller's rollback then discards the record.
    status = "done" if body.outcome in CLOSING_OUTCOMES else "in_progress"
    test = update_test(
        db, project_id, test_id,
        HostTestUpdate(expected_revision=body.expected_revision, status=status), user_id,
    )
    return test, record


def list_tests(db, project_id, user_id, *, host_id=None, status=None, label=None,
               assigned_to_id=None, agent_session_id=None, mine=False, q=None, active_only=False, limit=50, offset=0):
    query = db.query(HostTest).filter(HostTest.project_id == project_id)
    if active_only:
        from app.db.models_host_tests import ACTIVE_TEST_STATUSES
        query = query.filter(HostTest.status.in_(ACTIVE_TEST_STATUSES))
    for column, value in ((HostTest.host_id, host_id), (HostTest.status, status),
                          (HostTest.label, label), (HostTest.agent_session_id, agent_session_id),
                          (HostTest.assigned_to_id, user_id if mine else assigned_to_id)):
        if value is not None:
            query = query.filter(column == value)
    if q:
        from app.services.host_query import build_filtered_host_query
        hosts = build_filtered_host_query(db, db.get(User, user_id), project_id=project_id, q=q)
        query = query.filter(HostTest.host_id.in_(hosts.with_entities(models.Host.id)))
    total = query.count()
    rows = query.order_by(HostTest.created_at.desc(), HostTest.id.desc()).offset(offset).limit(limit).all()
    return {"items": serialize_many(db, rows), "total": total, "has_more": offset + len(rows) < total}


def _display(user):
    """A person as the pages show them: display name, else username."""
    return (user.full_name or user.username) if user is not None else None


def serialize_many(db, rows):
    ids = [row.id for row in rows]
    if not ids:
        return []
    loaded = {r.id: r for r in db.query(HostTest).options(
        joinedload(HostTest.host), joinedload(HostTest.assigned_to), joinedload(HostTest.created_by),
    ).filter(HostTest.id.in_(ids))}
    # One query for what each test's results say (v2.445.0): how many, the
    # latest outcome, and whether a result that showed an issue is still not
    # on a finding — what the one-line row and the weakness marker read.
    results = {}
    for test_id, outcome, finding_id in db.query(
        EvidenceRecord.host_test_id, EvidenceRecord.outcome, EvidenceRecord.finding_id,
    ).filter(EvidenceRecord.host_test_id.in_(ids)).order_by(EvidenceRecord.created_at, EvidenceRecord.id):
        r = results.setdefault(test_id, {"count": 0, "last": None, "open": 0, "finding_ids": []})
        r["count"] += 1
        r["last"] = outcome
        if outcome == "finding" and finding_id is None:
            r["open"] += 1
        if finding_id is not None and finding_id not in r["finding_ids"]:
            r["finding_ids"].append(finding_id)
    result = []
    for test_id in ids:
        row = loaded[test_id]
        data = {c.name: getattr(row, c.name) for c in HostTest.__table__.columns
                if c.name not in ("request_hash", "request_key")}
        r = results.get(row.id, {"count": 0, "last": None, "open": 0, "finding_ids": []})
        data.update(host_ip=row.host.ip_address, evidence_count=r["count"],
                    last_outcome=r["last"], unpromoted_findings=r["open"], finding_ids=r["finding_ids"],
                    assigned_to=_display(row.assigned_to), created_by=_display(row.created_by))
        result.append(data)
    return result
