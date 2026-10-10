"""Deleting a scan by hand (``DELETE /scans/{id}``), and what it shares with
the cleanup of an import that did not finish (owner decisions, 2026-10-08).

A. Work on a host the delete removes is named in the preview, and the delete
   is refused (409 ``hosts_with_work``) until the request confirms.  "Work" is
   ``host_work.work_kinds`` — the list the cleanup keeps hosts by.
B. The scan's own DNS observations go; the same answer seen by another scan
   is that scan's row and stays.
C. A name only the deleted scan observed, that nothing else refers to, goes
   with it — on both delete paths.
D. The delete removes everything the scan brought that nothing else holds —
   the observations only it reported, the ports it created on hosts that
   stay — keeps a host another scan attached something to, is refused while
   an import of the project runs, and its preview says exactly what it does.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import event, exists, select, text
from sqlalchemy.orm import aliased

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding, FindingHost, FindingVulnerability
from app.db.models_host_tests import HostTest
from app.db.models_project import Project
from app.db.models_proposals import AgentProposal, EvidenceRecord
from app.db.models_remediation import RemediationEvent
from app.db.models_vulnerability import (
    HostAttribute,
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySource,
)
from app.services import dns_name_service, host_work, scan_sightings
from app.services import ingestion_service as ingestion_module
from app.services.ingestion_service import (
    _host_is_only_this_attempts,
    _seen_by_another_scan,
    delete_partial_scan,
)
from tests.ingestion_job_harness import live_attempt

NOW = datetime.now(timezone.utc)


def _scan(db, pid, name):
    scan = models.Scan(project_id=pid, filename=name, tool_name="nmap", scan_type="nmap_xml")
    db.add(scan)
    db.flush()
    return scan


def _host(db, pid, scan, ip, *, created=True, **fields):
    host = models.Host(project_id=pid, ip_address=ip, state="up", last_updated_scan_id=scan.id, **fields)
    db.add(host)
    db.flush()
    _saw(db, host, scan, created=created)
    return host


def _saw(db, host, scan, *, created=False):
    db.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan="up", discovered_at=NOW, host_created=created,
    ))
    db.flush()


def _vuln(db, host, scan, title="observed", **fields):
    vuln = Vulnerability(
        host_id=host.id, scan_id=scan.id if scan is not None else None, title=title,
        severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS, **fields,
    )
    db.add(vuln)
    db.flush()
    return vuln


def _finding(db, pid):
    finding = Finding(project_id=pid, title="Issue", severity="high", status="open", source="manual")
    db.add(finding)
    db.flush()
    return finding


def _test(db, pid, host, key, **fields):
    row = HostTest(
        project_id=pid, host_id=host.id, tool="nmap", description="d", rationale="r", priority="medium",
        status="proposed", source="person", request_key=key, request_hash="0" * 64, **fields,
    )
    db.add(row)
    db.flush()
    return row


def _evidence(db, pid, host):
    row = EvidenceRecord(project_id=pid, host_id=host.id, tool="nmap", outcome="info", summary="s")
    db.add(row)
    db.flush()
    return row


# One way to put each kind of work on a host; the value is how many pieces.
def _note(db, pid, host, user):
    db.add_all([models.Annotation(host_id=host.id, user_id=user.id, body=f"note {i}") for i in range(2)])
    return 2


def _review(db, pid, host, user):
    db.add(models.HostFollow(host_id=host.id, user_id=user.id))
    return 1


def _tag(db, pid, host, user):
    tag = models.HostTag(project_id=pid, name=f"tag-{host.id}")
    db.add(tag)
    db.flush()
    db.add(models.HostTagAssignment(host_id=host.id, tag_id=tag.id))
    return 1


def _finding_endpoint(db, pid, host, user):
    db.add(FindingHost(finding_id=_finding(db, pid).id, host_id=host.id))
    return 1


def _host_tests(db, pid, host, user):
    for i in range(3):
        _test(db, pid, host, f"work-{host.id}-{i}")
    return 3


def _evidence_record(db, pid, host, user):
    _evidence(db, pid, host)
    return 1


def _remediation_entry(db, pid, host, user):
    db.add(RemediationEvent(
        project_id=pid, host_id=host.id, kind="note", body="Owner is the plant team",
        author_id=user.id, occurred_at=NOW,
    ))
    return 1


def _proposal(db, pid, host, user):
    # The observation is this scan's own, so only the proposal protects it.
    scan_id = db.query(models.HostScanHistory.scan_id).filter_by(host_id=host.id).scalar()
    vuln = Vulnerability(
        host_id=host.id, scan_id=scan_id, title="proposed about",
        severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS,
    )
    db.add(vuln)
    db.flush()
    db.add(AgentProposal(
        project_id=pid, kind="promote_observation", status="pending", source="agent",
        vulnerability_id=vuln.id, payload={},
    ))
    return 1


def _correction(db, pid, host, user):
    host.hostname, host.hostname_source = "typed.example.com", "operator"
    return 1


WORK = {
    "notes": _note,
    "reviews": _review,
    "tags": _tag,
    "findings": _finding_endpoint,
    "tests": _host_tests,
    "evidence": _evidence_record,
    "remediation_entries": _remediation_entry,
    "proposals": _proposal,
    "corrections": _correction,
}


def _one_host_per_kind(db, pid, scan, user, net="10.61.0"):
    """{kind: (host, how many)} — hosts only ``scan`` saw, one per kind of
    work, addresses in registry order."""
    made = {}
    for i, (kind, add) in enumerate(WORK.items(), start=1):
        host = _host(db, pid, scan, f"{net}.{i}")
        made[kind] = (host, add(db, pid, host, user))
    db.flush()
    return made


def _url(pid, scan_id, tail=""):
    return f"/api/v1/projects/{pid}/scans/{scan_id}{tail}"


def _host_ids(db, pid):
    db.expire_all()
    return {row[0] for row in db.query(models.Host.id).filter_by(project_id=pid)}


# ---------------------------------------------------------------------------
# A — work on the hosts that go
# ---------------------------------------------------------------------------

def test_the_registry_names_every_kind_the_client_is_told_about():
    assert [entry.kind for entry in host_work.work_kinds()] == list(WORK)


def test_the_preview_lists_the_hosts_with_work_and_how_much(client, db_session, test_project, test_user):
    pid = test_project.id
    target, other = _scan(db_session, pid, "target.xml"), _scan(db_session, pid, "other.xml")
    made = _one_host_per_kind(db_session, pid, target, test_user)
    _host(db_session, pid, target, "10.61.0.200")                      # removed, no work
    shared = _host(db_session, pid, target, "10.61.0.201")             # has work, but stays
    _saw(db_session, shared, other)
    _note(db_session, pid, shared, test_user)
    db_session.commit()

    body = client.get(_url(pid, target.id, "/deletion-impact")).json()

    assert body["hosts_removed"] == len(WORK) + 1 and body["hosts_kept"] == 1
    assert body["hosts_with_work"] == len(WORK)
    assert body["hosts_with_work_sample"] == [
        {"host_id": host.id, "ip_address": host.ip_address, "hostname": host.hostname, "work": {kind: n}}
        for kind, (host, n) in made.items()
    ]


def test_the_sample_is_fifty_hosts_in_address_order(client, db_session, test_project, test_user):
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    # Written highest first, with addresses a string sort would misplace.
    for last in sorted(range(1, 61), reverse=True):
        _review(db_session, pid, _host(db_session, pid, target, f"10.62.0.{last}"), test_user)
    db_session.commit()

    body = client.get(_url(pid, target.id, "/deletion-impact")).json()

    assert body["hosts_with_work"] == 60
    assert [h["ip_address"] for h in body["hosts_with_work_sample"]] == [f"10.62.0.{n}" for n in range(1, 51)]


def test_a_delete_that_removes_work_is_refused_until_confirmed(client, db_session, test_project, test_user):
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    made = _one_host_per_kind(db_session, pid, target, test_user)
    _host(db_session, pid, target, "10.61.0.200")
    db_session.commit()
    scan_id = target.id
    before, notes = _host_ids(db_session, pid), db_session.query(models.Annotation).count()

    refused = client.delete(_url(pid, scan_id))

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert set(detail) == {"error", "hosts_with_work", "message"}
    assert detail["error"] == "hosts_with_work" and detail["hosts_with_work"] == len(made)
    assert str(len(made)) in detail["message"]
    # Nothing was changed.
    assert _host_ids(db_session, pid) == before
    assert db_session.query(models.Annotation).count() == notes == 2
    assert db_session.query(models.Scan).filter_by(id=scan_id).count() == 1
    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan_id).count() == len(before)

    done = client.delete(_url(pid, scan_id), params={"confirm_hosts_with_work": "true"})

    assert done.status_code == 200 and done.json()["hosts_removed"] == len(before)
    assert _host_ids(db_session, pid) == set()
    assert db_session.query(models.Scan).filter_by(id=scan_id).count() == 0


def test_a_delete_with_no_work_at_risk_needs_no_parameter(client, db_session, test_project, test_user):
    pid = test_project.id
    target, other = _scan(db_session, pid, "target.xml"), _scan(db_session, pid, "other.xml")
    _host(db_session, pid, target, "10.61.1.1")
    shared = _host(db_session, pid, target, "10.61.1.2")
    _saw(db_session, shared, other)
    _note(db_session, pid, shared, test_user)      # work on a host that stays is not at risk
    db_session.commit()

    response = client.delete(_url(pid, target.id))

    assert response.status_code == 200 and response.json()["hosts_removed"] == 1
    assert _host_ids(db_session, pid) == {shared.id}
    assert db_session.query(models.Annotation).filter_by(host_id=shared.id).count() == 2


def test_work_added_after_the_preview_is_caught_by_the_delete(client, db_session, test_project, test_user):
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    host = _host(db_session, pid, target, "10.61.2.1")
    db_session.commit()
    assert client.get(_url(pid, target.id, "/deletion-impact")).json()["hosts_with_work"] == 0

    _note(db_session, pid, host, test_user)
    db_session.commit()

    refused = client.delete(_url(pid, target.id))
    assert refused.status_code == 409 and refused.json()["detail"]["hosts_with_work"] == 1
    assert _host_ids(db_session, pid) == {host.id}


def test_the_preview_runs_the_same_statements_for_one_host_and_for_sixty(
    client, db_session, test_project, test_user,
):
    pid = test_project.id
    small, large = _scan(db_session, pid, "small.xml"), _scan(db_session, pid, "large.xml")
    _note(db_session, pid, _host(db_session, pid, small, "10.63.0.1"), test_user)
    for n in range(1, 61):
        host = _host(db_session, pid, large, f"10.63.1.{n}")
        _note(db_session, pid, host, test_user)
        _test(db_session, pid, host, f"count-{n}")
    db_session.commit()

    small_id, large_id = small.id, large.id

    def statements(scan_id):
        seen = []

        def count(conn, cursor, statement, params, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):  # not the harness's SAVEPOINTs
                seen.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", count)
        try:
            body = client.get(_url(pid, scan_id, "/deletion-impact")).json()
        finally:
            event.remove(bind, "before_cursor_execute", count)
        return body, seen

    statements(small_id)  # the first request after a commit reloads the signed-in user
    one, one_statements = statements(small_id)
    sixty, sixty_statements = statements(large_id)

    assert (one["hosts_with_work"], sixty["hosts_with_work"]) == (1, 60)
    assert sixty["hosts_with_work_sample"][0]["work"] == {"notes": 2, "tests": 1}
    assert one_statements == sixty_statements


def _guards_before_the_registry(host_id, scan_id):
    """``_host_is_only_this_attempts`` as it was written before the list of
    work moved to ``host_work`` — kept here to prove the cleanup still
    selects exactly the hosts it did."""
    other_hh = aliased(models.HostScanHistory)
    this_host = aliased(models.Host)
    host_port = aliased(models.Port)
    port_ph = aliased(models.PortScanHistory)
    host_vuln = aliased(Vulnerability)
    proposed_vuln = aliased(Vulnerability)

    def _no(column):
        return ~exists().where(column == host_id)

    def _none_from_another_scan(model):
        return ~exists().where(model.host_id == host_id, model.scan_id != scan_id)

    return [
        ~exists().where(other_hh.host_id == host_id, other_hh.scan_id != scan_id),
        _no(models.Annotation.host_id),
        _no(models.HostFollow.host_id),
        _no(models.HostTagAssignment.host_id),
        _no(FindingHost.host_id),
        _no(HostTest.host_id),
        _no(EvidenceRecord.host_id),
        _no(RemediationEvent.host_id),
        ~exists().where(proposed_vuln.host_id == host_id, AgentProposal.vulnerability_id == proposed_vuln.id),
        ~exists().where(
            this_host.id == host_id,
            (this_host.last_updated_scan_id != scan_id) | (this_host.hostname_source == "operator"),
        ),
        ~exists().where(host_vuln.host_id == host_id, _seen_by_another_scan(host_vuln, scan_id)),
        ~exists().where(host_port.host_id == host_id, host_port.last_updated_scan_id != scan_id),
        ~exists().where(
            host_port.host_id == host_id, port_ph.port_id == host_port.id, port_ph.scan_id != scan_id,
        ),
        _none_from_another_scan(models.HostScript),
        _none_from_another_scan(HostAttribute),
        _none_from_another_scan(models.WebInterface),
        _none_from_another_scan(models.WebPath),
        _none_from_another_scan(NetexecResult),
    ]


def test_the_cleanups_guard_selects_the_hosts_it_always_did(db_session, test_project, test_user):
    pid = test_project.id
    partial, other = _scan(db_session, pid, "partial.xml"), _scan(db_session, pid, "other.xml")
    _one_host_per_kind(db_session, pid, partial, test_user)
    clean = [_host(db_session, pid, partial, f"10.61.3.{n}") for n in (1, 2)]
    # Provenance, not work: each of these keeps its host too.
    seen_twice = _host(db_session, pid, partial, "10.61.3.10")
    _saw(db_session, seen_twice, other)
    updated = _host(db_session, pid, partial, "10.61.3.11")
    updated.last_updated_scan_id = other.id
    reobserved = _host(db_session, pid, partial, "10.61.3.12")
    _vuln(db_session, reobserved, partial, last_seen_scan_id=other.id)
    with_port = _host(db_session, pid, partial, "10.61.3.13")
    db_session.add(models.Port(
        host_id=with_port.id, port_number=80, protocol="tcp", state="open", last_updated_scan_id=other.id,
    ))
    web = _host(db_session, pid, partial, "10.61.3.14")
    db_session.add(models.WebInterface(scan_id=other.id, host_id=web.id, source="httpx", url="http://10.61.3.14/"))
    db_session.commit()

    HH = models.HostScanHistory

    def selected(guards):
        return set(db_session.execute(
            select(HH.host_id).where(HH.scan_id == partial.id, HH.host_created.is_(True)).where(*guards)
        ).scalars())

    now = selected(_host_is_only_this_attempts(HH.host_id, partial.id))
    assert now == selected(_guards_before_the_registry(HH.host_id, partial.id))
    assert now == {host.id for host in clean}


def test_the_cleanup_keeps_every_host_the_warning_would_name(db_session, test_project, test_user):
    pid = test_project.id
    partial = _scan(db_session, pid, "partial.xml")
    made = _one_host_per_kind(db_session, pid, partial, test_user)
    gone = _host(db_session, pid, partial, "10.61.4.1")
    db_session.commit()
    worked = {host.id for host, _ in made.values()}
    assert set(host_work.work_on_hosts(db_session, [*worked, gone.id])) == worked

    removed = delete_partial_scan(db_session, partial.id)
    db_session.commit()

    assert removed["hosts"] == 1
    assert _host_ids(db_session, pid) == worked


# ---------------------------------------------------------------------------
# B — the scan's own DNS observations
# ---------------------------------------------------------------------------

def _observe(db, pid, name, value, scan=None, record_type="A", **fields):
    record_id = dns_name_service.record_observation(
        db, project_id=pid, name=name, record_type=record_type, value=value,
        scan_id=scan.id if scan is not None else None, **fields,
    )
    assert record_id is not None
    db.flush()
    return record_id


def _records(db, pid):
    db.expire_all()
    return {
        (r.domain, r.record_type, r.value, r.scan_id)
        for r in db.query(models.DNSRecord).filter_by(project_id=pid)
    }


def _names(db, pid):
    db.expire_all()
    return {row[0] for row in db.query(models.DNSName.fqdn).filter_by(project_id=pid)}


def test_a_scans_own_observations_go_and_another_scans_copy_stays(client, db_session, test_project, test_user):
    pid = test_project.id
    earlier, target, later = (_scan(db_session, pid, f"{n}.json") for n in ("earlier", "target", "later"))
    host = _host(db_session, pid, earlier, "10.64.0.1")
    for scan in (earlier, target, later):
        _observe(db_session, pid, "seen-thrice.example.com", "10.64.0.1", scan)
    _observe(db_session, pid, "seen-before.example.com", "10.64.0.2", earlier)
    _observe(db_session, pid, "seen-before.example.com", "10.64.0.2", target)
    _observe(db_session, pid, "seen-after.example.com", "10.64.0.3", target)
    _observe(db_session, pid, "seen-after.example.com", "10.64.0.3", later)
    _observe(db_session, pid, "moved.example.com", "10.64.0.4", earlier)
    _observe(db_session, pid, "moved.example.com", "10.64.0.5", target)       # an answer only it saw
    # A test's observation: keyed to its evidence record, no scan.
    evidence = _evidence(db_session, pid, host)
    _observe(db_session, pid, "tested.example.com", "10.64.0.1", None, "TESTED", evidence_record_id=evidence.id)
    _observe(db_session, pid, "tested.example.com", "10.64.0.1", target)
    db_session.commit()

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target.id))

    assert response.status_code == 200
    assert preview["dns_records_removed"] == response.json()["dns_records_removed"] == 5
    assert preview["dns_names_removed"] == response.json()["dns_names_removed"] == 0
    assert _records(db_session, pid) == {
        ("seen-thrice.example.com", "A", "10.64.0.1", earlier.id),
        ("seen-thrice.example.com", "A", "10.64.0.1", later.id),
        ("seen-before.example.com", "A", "10.64.0.2", earlier.id),
        ("seen-after.example.com", "A", "10.64.0.3", later.id),
        ("moved.example.com", "A", "10.64.0.4", earlier.id),
        ("tested.example.com", "TESTED", "10.64.0.1", None),
    }
    assert len(_names(db_session, pid)) == 5


# ---------------------------------------------------------------------------
# C — the names only the deleted scan observed
# ---------------------------------------------------------------------------

KEPT = {
    "also-seen.example.com", "added-by-hand.example.com", "exact.in-scope.example",
    "deep.under.wild.example", "has-test.example.com", "has-endpoint.example.com",
    "has-observation.example.com", "has-web.example.com", "imported.example.com",
}


def _names_fixture(db, pid, user):
    """A target scan that observed one name nothing else holds, and one name
    for every reason a name is kept.  Returns (target, other)."""
    other, target = _scan(db, pid, "other.json"), _scan(db, pid, "target.json")
    kept_host = _host(db, pid, other, "10.65.0.1")

    for name in ("only-this-scan.example.com", "own-web-row.example.com", *sorted(KEPT)):
        _observe(db, pid, name, "10.65.0.9", target)
    name_id = {n.fqdn: n.id for n in db.query(models.DNSName).filter_by(project_id=pid)}

    _observe(db, pid, "also-seen.example.com", "10.65.0.9", other)
    _observe(db, pid, "imported.example.com", "imported.example.com", None, "IMPORT")
    db.query(models.DNSName).filter_by(id=name_id["added-by-hand.example.com"]).update({"created_by_id": user.id})
    scope = models.Scope(project_id=pid, name="default")
    db.add(scope)
    db.flush()
    db.add_all([
        models.ScopeDomain(scope_id=scope.id, domain="exact.in-scope.example"),
        models.ScopeDomain(scope_id=scope.id, domain="wild.example", include_subdomains=True),
    ])
    _test(db, pid, kept_host, "name-test", name_id=name_id["has-test.example.com"])
    db.add(FindingHost(
        finding_id=_finding(db, pid).id, host_id=kept_host.id, name_id=name_id["has-endpoint.example.com"],
    ))
    _vuln(db, kept_host, other, name_id=name_id["has-observation.example.com"])
    db.add(models.WebInterface(
        scan_id=other.id, host_id=kept_host.id, source="httpx", url="http://has-web.example.com/",
        name_id=name_id["has-web.example.com"],
    ))
    # The deleted scan's own web row goes with it, so it holds nothing.
    db.add(models.WebInterface(
        scan_id=target.id, host_id=kept_host.id, source="httpx", url="http://own-web-row.example.com/",
        name_id=name_id["own-web-row.example.com"],
    ))
    db.commit()
    return target, other


def test_a_hand_delete_removes_the_names_only_that_scan_observed(client, db_session, test_project, test_user):
    pid = test_project.id
    target, _other = _names_fixture(db_session, pid, test_user)
    target_id = target.id

    preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target_id))

    assert response.status_code == 200
    assert _names(db_session, pid) == KEPT
    assert preview["dns_names_removed"] == response.json()["dns_names_removed"] == 2
    assert preview["dns_records_removed"] == response.json()["dns_records_removed"] == len(KEPT) + 2
    assert all(scan_id != target_id for *_rest, scan_id in _records(db_session, pid))


def test_the_cleanup_of_an_unfinished_import_removes_the_same_names(db_session, test_project, test_user):
    pid = test_project.id
    target, _other = _names_fixture(db_session, pid, test_user)
    # The cleanup deletes scanner rows only this scan reported; nothing in
    # the fixture is one, so both paths face the same rows.

    removed = delete_partial_scan(db_session, target.id)
    db_session.commit()

    assert _names(db_session, pid) == KEPT
    assert (removed["dns_names"], removed["dns_records"]) == (2, len(KEPT) + 2)


def test_the_preview_counts_a_name_held_only_by_a_host_that_goes(client, db_session, test_project, test_user):
    """A test aimed at a name sits on a host the delete removes: the test
    goes with its host, so the name goes too — and the preview says so."""
    pid = test_project.id
    target = _scan(db_session, pid, "target.json")
    orphan = _host(db_session, pid, target, "10.66.0.1")
    _observe(db_session, pid, "on-orphan.example.com", "10.66.0.1", target)
    name = db_session.query(models.DNSName).filter_by(project_id=pid).one()
    _test(db_session, pid, orphan, "orphan-test", name_id=name.id)
    _vuln(db_session, orphan, target, name_id=name.id)
    db_session.commit()

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    assert (preview["hosts_with_work"], preview["dns_names_removed"]) == (1, 1)

    response = client.delete(_url(pid, target.id), params={"confirm_hosts_with_work": "true"})

    assert response.status_code == 200 and response.json()["dns_names_removed"] == 1
    assert _names(db_session, pid) == set()


@pytest.mark.parametrize("wildcard", ["*.wild.example"])
def test_a_wildcard_under_an_in_scope_domain_is_kept(client, db_session, test_project, wildcard):
    pid = test_project.id
    target = _scan(db_session, pid, "target.json")
    scope = models.Scope(project_id=pid, name="default")
    db_session.add(scope)
    db_session.flush()
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="wild.example", include_subdomains=True))
    _observe(db_session, pid, wildcard, "amass", target, "DISCOVERED")
    db_session.commit()

    assert client.delete(_url(pid, target.id)).json()["dns_names_removed"] == 0
    assert _names(db_session, pid) == {wildcard}


# ---------------------------------------------------------------------------
# D — everything the scan brought goes, and nothing another scan also has
# ---------------------------------------------------------------------------

def _port(db, host, number, *, stamp=None, updated_by=None):
    port = models.Port(
        host_id=host.id, port_number=number, protocol="tcp", state="open",
        created_scan_id=stamp.id if stamp is not None else None,
        last_updated_scan_id=(updated_by or stamp).id if (updated_by or stamp) is not None else None,
    )
    db.add(port)
    db.flush()
    return port


def _port_seen(db, port, scan, *, created=False):
    db.add(models.PortScanHistory(port_id=port.id, scan_id=scan.id, state_at_scan="open", port_created=created))
    db.flush()


def _reported(db, vuln, *scans):
    for scan in scans:
        scan_sightings.see(db, scan_sightings.VULNERABILITY, vuln.id, scan.id)


def _shared_host(db, pid, target, other, ip):
    host = _host(db, pid, other, ip)
    _saw(db, host, target)
    return host


def _port_numbers(db, host_id):
    db.expire_all()
    return sorted(row[0] for row in db.query(models.Port.port_number).filter_by(host_id=host_id))


def test_an_observation_only_this_scan_reported_is_deleted_and_the_others_stay(
    client, db_session, test_project,
):
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.nessus"), _scan(db_session, pid, "target.nessus")
    host = _shared_host(db_session, pid, target, other, "10.67.0.1")
    alone = _vuln(db_session, host, target, "only the target")
    _reported(db_session, alone, target)
    both = _vuln(db_session, host, target, "both scans", last_seen_scan_id=other.id)
    _reported(db_session, both, target, other)
    promoted = _vuln(db_session, host, target, "a finding refers")
    _reported(db_session, promoted, target)
    db_session.add(FindingVulnerability(finding_id=_finding(db_session, pid).id, vuln_id=promoted.id))
    proposed = _vuln(db_session, host, target, "a proposal refers")
    db_session.add(AgentProposal(
        project_id=pid, kind="promote_observation", status="pending", source="agent",
        vulnerability_id=proposed.id, payload={},
    ))
    db_session.commit()
    alone_id, both_id, promoted_id, proposed_id, other_id = alone.id, both.id, promoted.id, proposed.id, other.id

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target.id))

    assert response.status_code == 200, response.text
    assert (preview["vulnerabilities_removed"], preview["vulnerabilities_kept"]) == (1, 2)
    assert response.json()["vulnerabilities_removed"] == 1
    db_session.expire_all()
    rows = {v.id: (v.scan_id, v.last_seen_scan_id) for v in db_session.query(Vulnerability)}
    assert alone_id not in rows
    # Handed to the scan that also reported it.
    assert rows[both_id] == (other_id, other_id)
    # Someone's work refers to these: kept, with no scan behind them.
    assert rows[promoted_id] == rows[proposed_id] == (None, None)


def test_a_finished_scans_ports_on_a_host_that_stays_go_unless_another_scan_saw_them(
    client, db_session, test_project,
):
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.xml"), _scan(db_session, pid, "target.xml")
    host = _shared_host(db_session, pid, target, other, "10.67.1.1")
    _port_seen(db_session, _port(db_session, host, 22, stamp=other), other, created=True)
    _port(db_session, host, 8001, stamp=target)                               # found by its stamp
    _port_seen(db_session, _port(db_session, host, 8002), target, created=True)   # found by port history
    seen_again = _port(db_session, host, 8003, stamp=target)                  # another scan has history for it
    _port_seen(db_session, seen_again, target, created=True)
    _port_seen(db_session, seen_again, other)
    _port(db_session, host, 8004, stamp=target, updated_by=other)             # another scan updated it
    noted = _port(db_session, host, 8005, stamp=target)                       # a finding endpoint names it
    db_session.add(FindingHost(finding_id=_finding(db_session, pid).id, host_id=host.id, port_id=noted.id))
    db_session.commit()
    host_id = host.id
    # No import job points at the scan: it is a finished import's.
    assert db_session.query(models.IngestionJob).filter_by(in_progress_scan_id=target.id).count() == 0

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target.id))

    assert response.status_code == 200, response.text
    assert preview["ports_removed_on_kept_hosts"] == response.json()["ports_removed_on_kept_hosts"] == 2
    assert (preview["ports_removed"], preview["hosts_removed"]) == (0, 0)
    assert _port_numbers(db_session, host_id) == [22, 8003, 8004, 8005]


def _ports_under_observations(db, pid):
    """A host that stays, with ports only ``target`` created: three carry an
    observation someone's work refers to (a finding's link, a finding's own
    pointer, a proposal), one an observation nobody refers to, one nothing.
    Returns ``(target, host id, {port number: observation id})``."""
    other, target = _scan(db, pid, "other.nessus"), _scan(db, pid, "target.nessus")
    host = _shared_host(db, pid, target, other, "10.67.2.1")
    _port_seen(db, _port(db, host, 22, stamp=other), other, created=True)
    on_port = {}

    def observed(number, *, by_history=False):
        port = _port(db, host, number, stamp=None if by_history else target)
        if by_history:
            _port_seen(db, port, target, created=True)
        vuln = _vuln(db, host, target, f"on {number}", port_id=port.id)
        _reported(db, vuln, target)
        on_port[number] = vuln.id
        return vuln

    linked = observed(8443)
    db.add(FindingVulnerability(finding_id=_finding(db, pid).id, vuln_id=linked.id))
    promoted = observed(8444, by_history=True)                   # the port is found by its history
    db.add(Finding(project_id=pid, title="Promoted", severity="high", status="open", source="scanner",
                   vuln_id=promoted.id))
    proposed = observed(8445)
    db.add(AgentProposal(project_id=pid, kind="promote_observation", status="pending", source="agent",
                         vulnerability_id=proposed.id, payload={}))
    observed(8446)                                               # nobody refers to it: both go
    _port(db, host, 8447, stamp=target)                          # nothing on it
    db.commit()
    return target, host.id, on_port


def _observation_ports(db, host_id):
    db.expire_all()
    numbers = dict(db.query(models.Port.id, models.Port.port_number).filter_by(host_id=host_id))
    return {v.id: numbers.get(v.port_id) for v in db.query(Vulnerability).filter_by(host_id=host_id)}


def test_a_kept_observation_keeps_its_port_and_a_port_with_none_still_goes(client, db_session, test_project):
    """Owner decision 2026-10-10: an observation kept because a finding or a
    proposal refers to it keeps the port it sits on, even when only the
    deleted scan created that port.  The preview says the same."""
    pid = test_project.id
    target, host_id, on_port = _ports_under_observations(db_session, pid)
    target_id = target.id

    preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target_id))

    assert response.status_code == 200, response.text
    assert _port_numbers(db_session, host_id) == [22, 8443, 8444, 8445]
    # Each kept observation still names its port; the one nobody referred to is gone.
    assert _observation_ports(db_session, host_id) == {
        on_port[8443]: 8443, on_port[8444]: 8444, on_port[8445]: 8445}
    assert preview["ports_removed_on_kept_hosts"] == response.json()["ports_removed_on_kept_hosts"] == 2
    assert (preview["vulnerabilities_removed"], preview["vulnerabilities_kept"]) == (1, 3)
    assert response.json()["vulnerabilities_removed"] == 1


def test_the_cleanup_of_an_unfinished_import_keeps_the_same_ports(db_session, test_project):
    """The port step is shared: the automatic cleanup follows the same rule."""
    target, host_id, on_port = _ports_under_observations(db_session, test_project.id)

    removed = delete_partial_scan(db_session, target.id)
    db_session.commit()

    assert _port_numbers(db_session, host_id) == [22, 8443, 8444, 8445]
    assert _observation_ports(db_session, host_id) == {
        on_port[8443]: 8443, on_port[8444]: 8444, on_port[8445]: 8445}
    assert (removed["ports"], removed["observations"]) == (2, 1)


def _attach_update_pointer(db, host, target, other):
    host.last_updated_scan_id = other.id


def _attach_web_row(db, host, target, other):
    db.add(models.WebInterface(scan_id=other.id, host_id=host.id, source="httpx", url=f"http://{host.ip_address}/"))


def _attach_port_update(db, host, target, other):
    _port(db, host, 80, stamp=target, updated_by=other)


def _attach_reobserved_attribute(db, host, target, other):
    # The row still names the deleted scan as its first recorder: only the
    # sighting says another scan reported it too.
    attribute = HostAttribute(
        host_id=host.id, attribute_type="os_name", value="Linux", source="nessus", scan_id=target.id,
    )
    db.add(attribute)
    db.flush()
    scan_sightings.see_many(db, scan_sightings.HOST_ATTRIBUTE, [(attribute.id, target.id), (attribute.id, other.id)])


def _attach_reobserved_observation(db, host, target, other):
    _reported(db, _vuln(db, host, target, "seen twice"), target, other)


ATTACHED_BY_ANOTHER_SCAN = {
    "update pointer": _attach_update_pointer,
    "web row": _attach_web_row,
    "port update": _attach_port_update,
    "attribute sighting": _attach_reobserved_attribute,
    "observation sighting": _attach_reobserved_observation,
}


@pytest.mark.parametrize("attach", ATTACHED_BY_ANOTHER_SCAN.values(), ids=ATTACHED_BY_ANOTHER_SCAN.keys())
def test_a_host_another_scan_attached_something_to_is_kept(client, db_session, test_project, attach):
    """Only the deleted scan has host history for it, yet the host was
    already there for another scan: it stays, and so does what that scan
    attached."""
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.xml"), _scan(db_session, pid, "target.xml")
    attached = _host(db_session, pid, target, "10.67.2.1")
    attach(db_session, attached, target, other)
    brought = _host(db_session, pid, target, "10.67.2.2")
    db_session.commit()
    attached_id, brought_id = attached.id, brought.id

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target.id))

    assert response.status_code == 200, response.text
    assert (preview["hosts_removed"], preview["hosts_kept"]) == (1, 1)
    assert preview["sample_removed_ips"] == ["10.67.2.2"]
    assert response.json()["hosts_removed"] == 1
    assert _host_ids(db_session, pid) == {attached_id}
    assert brought_id not in _host_ids(db_session, pid)


def test_work_on_a_host_another_scan_attached_to_needs_no_confirmation(
    client, db_session, test_project, test_user,
):
    """The refusal is about the hosts the delete removes — the same set."""
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.xml"), _scan(db_session, pid, "target.xml")
    attached = _host(db_session, pid, target, "10.67.2.9")
    _attach_web_row(db_session, attached, target, other)
    _note(db_session, pid, attached, test_user)
    db_session.commit()

    assert client.get(_url(pid, target.id, "/deletion-impact")).json()["hosts_with_work"] == 0
    assert client.delete(_url(pid, target.id)).status_code == 200
    assert db_session.query(models.Annotation).count() == 2


def _processing_job(db, pid, name="big-sweep.nessus"):
    job = models.IngestionJob(
        project_id=pid, filename="stored", original_filename=name, storage_path="/nonexistent/stored",
        status="processing", started_at=NOW, last_heartbeat=NOW, retry_count=0, options={},
    )
    db.add(job)
    db.flush()
    return job


def _everything(db, pid):
    db.expire_all()
    return {
        model.__tablename__: db.query(model).count()
        for model in (models.Scan, models.Host, models.Port, Vulnerability, models.DNSRecord, models.DNSName)
    }


def test_the_delete_is_refused_while_an_import_of_the_project_is_running(
    client, db_session, test_project,
):
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.xml"), _scan(db_session, pid, "target.xml")
    host = _shared_host(db_session, pid, target, other, "10.67.3.1")
    _host(db_session, pid, target, "10.67.3.2")
    _port(db_session, host, 8001, stamp=target)
    _vuln(db_session, host, target)
    _observe(db_session, pid, "running.example.com", "10.67.3.1", target)
    job = _processing_job(db_session, pid)
    db_session.commit()
    job_id, target_id = job.id, target.id
    before = _everything(db_session, pid)

    with live_attempt(db_session, job_id):
        preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
        refused = client.delete(_url(pid, target_id))

    assert (preview["import_running"], preview["import_running_filename"]) == (True, "big-sweep.nessus")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert set(detail) == {"error", "message"} and detail["error"] == "import_running"
    assert detail["message"] == (
        'An import of "big-sweep.nessus" is running in this project. Nothing was changed; '
        "try again when it finishes."
    )
    assert _everything(db_session, pid) == before


def test_an_attempt_whose_worker_is_gone_does_not_block_the_delete(client, db_session, test_project):
    """The row still says ``processing``; nobody holds the attempt's liveness
    lock, so the attempt is over."""
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    _host(db_session, pid, target, "10.67.3.9")
    _processing_job(db_session, pid)
    db_session.commit()

    preview = client.get(_url(pid, target.id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target.id))

    assert preview["import_running"] is False and preview["import_running_filename"] is None
    assert response.status_code == 200, response.text
    assert _host_ids(db_session, pid) == set()


def test_an_import_in_another_project_does_not_block_the_delete(client, db_session, test_project):
    pid = test_project.id
    elsewhere = Project(name="elsewhere", slug="elsewhere-d")
    db_session.add(elsewhere)
    db_session.flush()
    target = _scan(db_session, pid, "target.xml")
    job = _processing_job(db_session, elsewhere.id)
    db_session.commit()

    with live_attempt(db_session, job.id):
        assert client.delete(_url(pid, target.id)).status_code == 200


def test_the_delete_holds_the_projects_cleanup_lock_whatever_the_scan(client, db_session, test_project):
    """A finished scan's delete too: its guards are NOT EXISTS checks, safe
    only while no import of the project has a batch open."""
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    db_session.commit()
    locks = []

    def record(conn, cursor, statement, params, context, executemany):
        if "pg_advisory_xact_lock(" in statement:
            locks.append(params)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        assert client.delete(_url(pid, target.id)).status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert len(locks) == 1 and pid in set(locks[0].values() if isinstance(locks[0], dict) else locks[0])


def test_a_delete_that_cannot_get_the_lock_is_refused_with_nothing_changed(
    client, db_session, test_project, test_engine, monkeypatch,
):
    """An import between heartbeats holds the lock in shared mode on its own
    connection; the delete waits for the bounded time and then says so."""
    if test_engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are PostgreSQL's")
    monkeypatch.setattr(ingestion_module, "_API_CLEANUP_LOCK_TIMEOUT_S", 1)
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    _host(db_session, pid, target, "10.67.4.1")
    db_session.commit()
    target_id = target.id
    before = _everything(db_session, pid)

    with test_engine.connect() as importer:
        importer.execute(
            text("SELECT pg_advisory_xact_lock_shared(:cls, :project)"),
            {"cls": ingestion_module._PROJECT_IMPORT_LOCK_CLASS, "project": pid},
        )
        refused = client.delete(_url(pid, target_id))
        importer.rollback()

    assert refused.status_code == 409
    assert refused.json()["detail"] == {
        "error": "import_running",
        "message": "An import is writing to this project right now. Nothing was changed; try again when it finishes.",
    }
    assert _everything(db_session, pid) == before
    # The import has let go: the same request now goes through.
    assert client.delete(_url(pid, target_id)).status_code == 200


def test_the_preview_says_exactly_what_the_delete_then_does(client, db_session, test_project, test_user):
    """One scan that exercises every rule: each figure of the preview equals
    the rows that are gone afterwards."""
    pid = test_project.id
    other, target = _scan(db_session, pid, "other.xml"), _scan(db_session, pid, "target.xml")

    # Hosts only the target brought: they go with all that is on them.
    brought = [_host(db_session, pid, target, f"10.68.0.{n}") for n in (1, 2, 3)]
    for host in brought:
        _port(db_session, host, 80, stamp=target)
        _port(db_session, host, 443, stamp=target)
        _reported(db_session, _vuln(db_session, host, target, "on a host that goes"), target)
    db_session.add(models.WebInterface(
        scan_id=target.id, host_id=brought[0].id, source="httpx", url="http://10.68.0.1/"))
    _note(db_session, pid, brought[1], test_user)                 # work: the delete must be confirmed

    # A host both scans saw: it stays.
    shared = _shared_host(db_session, pid, target, other, "10.68.1.1")
    _port_seen(db_session, _port(db_session, shared, 22, stamp=other), other, created=True)
    _port(db_session, shared, 8001, stamp=target)
    _port_seen(db_session, _port(db_session, shared, 8002), target, created=True)
    _port(db_session, shared, 8003, stamp=target, updated_by=other)
    for n in range(3):
        _reported(db_session, _vuln(db_session, shared, target, f"only the target {n}"), target)
    handed = _vuln(db_session, shared, target, "both", last_seen_scan_id=other.id)
    _reported(db_session, handed, target, other)
    promoted = _vuln(db_session, shared, target, "promoted")
    _reported(db_session, promoted, target)
    db_session.add(FindingVulnerability(finding_id=_finding(db_session, pid).id, vuln_id=promoted.id))
    # The kept observation sits on a port only the target created: the port stays with it.
    promoted.port_id = _port(db_session, shared, 8004, stamp=target).id
    db_session.add(models.WebInterface(
        scan_id=target.id, host_id=shared.id, source="httpx", url="http://10.68.1.1/"))

    # Only the target has history for these, but another scan attached to them.
    attached = _host(db_session, pid, target, "10.68.2.1")
    _attach_web_row(db_session, attached, target, other)
    _port(db_session, attached, 8080, stamp=target)
    _reported(db_session, _vuln(db_session, attached, target, "on an attached host"), target)
    resighted = _host(db_session, pid, target, "10.68.2.2")
    _attach_reobserved_attribute(db_session, resighted, target, other)

    # Names: one only the target observed; one an observation that goes
    # names; one a kept observation names; one another scan observed too.
    for name in ("alone", "on-removed-observation", "on-kept-observation", "also-seen"):
        _observe(db_session, pid, f"{name}.example.org", "10.68.1.1", target)
    _observe(db_session, pid, "also-seen.example.org", "10.68.1.1", other)
    name_id = {n.fqdn: n.id for n in db_session.query(models.DNSName).filter_by(project_id=pid)}
    _reported(db_session, _vuln(
        db_session, shared, target, "named, goes", name_id=name_id["on-removed-observation.example.org"]), target)
    promoted.name_id = name_id["on-kept-observation.example.org"]
    db_session.commit()

    target_id = target.id
    removed_ids = {host.id for host in brought}
    kept_ids = {shared.id, attached.id, resighted.id}

    def count(model, *where):
        db_session.expire_all()
        return db_session.query(model).filter(*where).count()

    def figures():
        return {
            "hosts": count(models.Host, models.Host.project_id == pid),
            "ports_on_removed": count(models.Port, models.Port.host_id.in_(removed_ids)),
            "ports_on_kept": count(models.Port, models.Port.host_id.in_(kept_ids)),
            "observations_on_kept": count(Vulnerability, Vulnerability.host_id.in_(kept_ids)),
            "web": count(models.WebInterface),
            "records": count(models.DNSRecord, models.DNSRecord.project_id == pid),
            "names": count(models.DNSName, models.DNSName.project_id == pid),
        }

    before = figures()
    preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
    assert client.delete(_url(pid, target_id)).status_code == 409           # the note
    assert figures() == before
    response = client.delete(_url(pid, target_id), params={"confirm_hosts_with_work": "true"})
    assert response.status_code == 200, response.text
    after = figures()
    done = response.json()

    gone = {key: before[key] - after[key] for key in before}
    assert preview["hosts_removed"] == done["hosts_removed"] == gone["hosts"] == 3
    assert preview["hosts_kept"] == after["hosts"] == 3
    assert preview["hosts_with_work"] == 1
    assert preview["ports_removed"] == gone["ports_on_removed"] == 6
    assert preview["ports_removed_on_kept_hosts"] == done["ports_removed_on_kept_hosts"] == gone["ports_on_kept"] == 3
    assert preview["vulnerabilities_removed"] == done["vulnerabilities_removed"] == gone["observations_on_kept"] == 5
    assert preview["vulnerabilities_kept"] == count(
        Vulnerability, Vulnerability.host_id.in_(kept_ids), Vulnerability.scan_id.is_(None)) == 1
    assert preview["web_interfaces_removed"] == gone["web"] == 2
    assert preview["dns_records_removed"] == done["dns_records_removed"] == gone["records"] == 4
    assert preview["dns_names_removed"] == done["dns_names_removed"] == gone["names"] == 2
    assert _names(db_session, pid) == {"on-kept-observation.example.org", "also-seen.example.org"}
    assert preview["import_running"] is False


# ---------------------------------------------------------------------------
# E — a pointer that names no scan (plan C5, 2026-10-10)
#
# ``host_provenance_conditions`` compared every pointer with ``!= :scan``, and
# in SQL a NULL pointer then says nothing.  Whether that is right depends on
# what a NULL means in the column:
#
# * "first recorded by" on a host script / host attribute — every writer
#   stamps it and no scan can be the first recorder of a row that names none,
#   so a NULL row is never the deleted scan's first recording: the host is
#   kept, as the scanner-observation guard has always kept it.  The one
#   exception is the row ``release_scan`` deletes as the scan's alone (only
#   this scan has a sighting of it).
# * "last updated by" on a host or a port — NULL is what deleting the last
#   updater leaves, and what the web parsers leave on a host they create.  It
#   does not protect; the last test here shows what would break if it did.
# ---------------------------------------------------------------------------

def _host_script(db, host, scan=None, *seen_by):
    row = models.HostScript(
        host_id=host.id, script_id="smb-os-discovery", output="o", scan_id=scan.id if scan is not None else None,
    )
    db.add(row)
    db.flush()
    scan_sightings.see_many(db, scan_sightings.HOST_SCRIPT, [(row.id, s.id) for s in seen_by])
    return row


def _host_attribute(db, host, scan=None, *seen_by):
    row = HostAttribute(
        host_id=host.id, attribute_type="os_name", value="Linux", source="nessus",
        scan_id=scan.id if scan is not None else None,
    )
    db.add(row)
    db.flush()
    scan_sightings.see_many(db, scan_sightings.HOST_ATTRIBUTE, [(row.id, s.id) for s in seen_by])
    return row


# What sits on the host, with a first recorder that names NO scan: the row
# maker, and which scans have a sighting of it.
RECORDED_BY_NO_NAMED_SCAN = {
    "host script nobody has a sighting of": (_host_script, ()),
    "host attribute nobody has a sighting of": (_host_attribute, ()),
    "host script only another scan has a sighting of": (_host_script, ("other",)),
    "host script both scans have a sighting of": (_host_script, ("target", "other")),
    "host attribute both scans have a sighting of": (_host_attribute, ("target", "other")),
}


def _unnamed_estate(db, pid, make, seen_by):
    """``target`` has the only host history for two hosts it created.  One
    carries a row whose first recorder names no scan; the other is plain."""
    other, target = _scan(db, pid, "other.xml"), _scan(db, pid, "target.xml")
    scans = {"other": other, "target": target}
    carrying = _host(db, pid, target, "10.69.0.1")
    row = make(db, carrying, None, *[scans[name] for name in seen_by])
    plain = _host(db, pid, target, "10.69.0.2")
    db.commit()
    return target.id, carrying.id, plain.id, type(row), row.id


@pytest.mark.parametrize(
    "make,seen_by", RECORDED_BY_NO_NAMED_SCAN.values(), ids=RECORDED_BY_NO_NAMED_SCAN.keys(),
)
def test_a_hand_delete_keeps_a_host_carrying_a_row_no_named_scan_first_recorded(
    client, db_session, test_project, make, seen_by,
):
    """The row is not the deleted scan's first recording — something else put
    it on the host — so the host was not brought by that scan alone."""
    pid = test_project.id
    target_id, carrying_id, plain_id, model, row_id = _unnamed_estate(db_session, pid, make, seen_by)

    preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
    response = client.delete(_url(pid, target_id))

    assert response.status_code == 200, response.text
    assert _host_ids(db_session, pid) == {carrying_id}
    assert db_session.query(model).filter_by(id=row_id).count() == 1
    assert (preview["hosts_removed"], preview["hosts_kept"]) == (1, 1)     # the preview counted the same
    assert preview["sample_removed_ips"] == ["10.69.0.2"]
    assert response.json()["hosts_removed"] == 1


@pytest.mark.parametrize(
    "make,seen_by", RECORDED_BY_NO_NAMED_SCAN.values(), ids=RECORDED_BY_NO_NAMED_SCAN.keys(),
)
def test_the_cleanup_keeps_a_host_carrying_a_row_no_named_scan_first_recorded(
    db_session, test_project, make, seen_by,
):
    """The same guard after ``release_scan`` — which never deletes these rows
    (no sighting of this scan's, or another scan's beside it)."""
    pid = test_project.id
    target_id, carrying_id, plain_id, model, row_id = _unnamed_estate(db_session, pid, make, seen_by)

    removed = delete_partial_scan(db_session, target_id)
    db_session.commit()

    assert removed["hosts"] == 1
    assert _host_ids(db_session, pid) == {carrying_id}
    assert db_session.query(model).filter_by(id=row_id).count() == 1


@pytest.mark.parametrize("make", [_host_script, _host_attribute], ids=["host script", "host attribute"])
@pytest.mark.parametrize("by_hand", [True, False], ids=["hand delete", "cleanup"])
def test_a_row_naming_no_scan_that_only_this_scan_reported_goes_with_its_host(
    client, db_session, test_project, make, by_hand,
):
    """``release_scan`` deletes such a row as the scan's alone (its only
    sighting is this scan's), so it does not hold the host either — the same
    answer before release (the hand delete, its preview) and after it (the
    cleanup).  Unchanged by C5."""
    pid = test_project.id
    target_id, carrying_id, plain_id, model, row_id = _unnamed_estate(db_session, pid, make, ("target",))

    if by_hand:
        preview = client.get(_url(pid, target_id, "/deletion-impact")).json()
        assert (preview["hosts_removed"], preview["hosts_kept"]) == (2, 0)
        assert client.delete(_url(pid, target_id)).json()["hosts_removed"] == 2
    else:
        assert delete_partial_scan(db_session, target_id)["hosts"] == 2
        db_session.commit()

    assert _host_ids(db_session, pid) == set()
    assert db_session.query(model).filter_by(id=row_id).count() == 0


@pytest.mark.parametrize("by_hand", [True, False], ids=["hand delete", "cleanup"])
def test_an_update_pointer_naming_no_scan_does_not_keep_a_host(client, db_session, test_project, by_hand):
    """Deliberate, and unchanged by C5: on ``hosts_v2`` and ``ports_v2`` the
    pointer is "last updated by".  The web parsers leave it NULL on a host
    they create, and deleting the last updater clears it — neither says
    another scan has the host."""
    pid = test_project.id
    target = _scan(db_session, pid, "target.xml")
    unstamped = _host(db_session, pid, target, "10.69.1.1")
    unstamped.last_updated_scan_id = None                     # as httpx / eyewitness create it
    port_cleared = _host(db_session, pid, target, "10.69.1.2")
    port = _port(db_session, port_cleared, 443, stamp=target)
    port.last_updated_scan_id = None                          # its later updater was deleted
    db_session.commit()
    target_id = target.id

    if by_hand:
        assert client.get(_url(pid, target_id, "/deletion-impact")).json()["hosts_removed"] == 2
        assert client.delete(_url(pid, target_id)).json()["hosts_removed"] == 2
    else:
        assert delete_partial_scan(db_session, target_id)["hosts"] == 2
        db_session.commit()
    assert _host_ids(db_session, pid) == set()


def test_deleting_the_later_scan_and_then_the_first_removes_the_host(client, db_session, test_project):
    """Why a NULL update pointer cannot protect.  A Nessus-like import (no
    port history) brings a host and a port; a later scan updates both and is
    then deleted, which leaves the port's pointer naming no scan (there is no
    port history to hand it back by); deleting the first import must still
    remove the host it brought — with ``IS DISTINCT FROM`` on the port's
    update pointer it never could."""
    pid = test_project.id
    first, later = _scan(db_session, pid, "first.nessus"), _scan(db_session, pid, "later.xml")
    host = _host(db_session, pid, first, "10.69.2.1")
    port = _port(db_session, host, 445, stamp=first, updated_by=later)
    _saw(db_session, host, later)
    host.last_updated_scan_id = later.id
    db_session.commit()
    host_id, port_id, first_id, later_id = host.id, port.id, first.id, later.id

    assert client.delete(_url(pid, later_id)).json()["hosts_removed"] == 0
    db_session.expire_all()
    assert _host_ids(db_session, pid) == {host_id}
    assert db_session.get(models.Host, host_id).last_updated_scan_id == first_id   # handed back by host history
    assert db_session.get(models.Port, port_id).last_updated_scan_id is None

    assert client.get(_url(pid, first_id, "/deletion-impact")).json()["hosts_removed"] == 1
    assert client.delete(_url(pid, first_id)).json()["hosts_removed"] == 1
    assert _host_ids(db_session, pid) == set()
