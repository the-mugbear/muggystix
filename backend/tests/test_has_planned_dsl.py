"""
`has:planned`, `has:tested` and `has:untouched` over host tests and evidence.

`has:planned` (v2.234.0) exists because /operations reported "not yet planned"
as a coverage gap that nothing could list: `has:tested` answers "has been
*tested*", and there was no predicate for the earlier stage.

Since v2.442.0 the two are defined on host tests and evidence records
(``host_test_queries``), not on test plans:

* planned  — the host has a test that is proposed or in progress;
* tested   — the host has an evidence record whose outcome is finding,
             no_finding or inconclusive (a failed attempt or an informational
             record is not a test);
* untouched — no follow, note, finding endpoint, evidence, or test that was
             not dismissed (``host_query_predicates.untouched_conditions``).

The crux is host B: a test is proposed on it and nothing has run.  It must
match `has:planned` and NOT `has:tested`, or the predicate is just an alias.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import Host
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.services.host_query_dsl import BuildCtx, evaluate, parse_query


def _host(db, project_id, ip):
    h = Host(
        project_id=project_id, ip_address=ip, state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _test(db, project_id, host, status="proposed"):
    key = str(uuid.uuid4())
    row = HostTest(
        project_id=project_id, host_id=host.id, tool="nmap", description="nmap -sV",
        rationale="coverage fixture", priority="medium", status=status, source="person",
        request_key=key, request_hash=key,
        dismissed_reason="not this engagement" if status == "dismissed" else None,
    )
    db.add(row)
    db.commit()
    return row


def _evidence(db, project_id, host, outcome, test=None):
    row = EvidenceRecord(
        project_id=project_id, host_id=host.id, tool="nmap", outcome=outcome,
        summary="coverage fixture", host_test_id=test.id if test is not None else None,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def coverage_hosts(db_session, test_project):
    """A = a test in progress AND a real result, B = a proposed test only,
    C = neither."""
    pid = test_project.id
    a = _host(db_session, pid, "10.44.0.1")
    b = _host(db_session, pid, "10.44.0.2")
    c = _host(db_session, pid, "10.44.0.3")
    test_a = _test(db_session, pid, a, status="in_progress")
    test_b = _test(db_session, pid, b)
    # Only host A actually got tested.
    _evidence(db_session, pid, a, "no_finding", test_a)
    return {"planned_and_tested": a, "planned_only": b, "neither": c, "test_b": test_b}


def _matching_ips(db, project_id, user, q):
    return {
        h.ip_address
        for h in db.query(Host)
        .filter(Host.project_id == project_id)
        .filter(evaluate(parse_query(q), BuildCtx(db, user, project_id)))
        .all()
    }


def test_planned_includes_hosts_never_tested(
    db_session, test_project, test_user, coverage_hosts
):
    """The whole point — a host with a proposed test that nobody ran counts
    as planned."""
    ips = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    assert ips == {"10.44.0.1", "10.44.0.2"}


def test_tested_is_stricter_than_planned(
    db_session, test_project, test_user, coverage_hosts
):
    """If these matched the same set, `has:planned` would be a useless alias."""
    planned = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    tested = _matching_ips(db_session, test_project.id, test_user, "has:tested")
    assert tested == {"10.44.0.1"}
    assert tested < planned


def test_not_planned_is_the_operations_coverage_gap(
    db_session, test_project, test_user, coverage_hosts
):
    """This is the query the /operations 'not yet planned' count links to."""
    ips = _matching_ips(db_session, test_project.id, test_user, "NOT has:planned")
    assert ips == {"10.44.0.3"}


def test_not_tested_keeps_planned_but_untested_hosts(
    db_session, test_project, test_user, coverage_hosts
):
    """The 'not yet tested' gap must include hosts with a proposed test nobody
    ran — those are exactly the ones an operator needs to chase."""
    ips = _matching_ips(db_session, test_project.id, test_user, "NOT has:tested")
    assert ips == {"10.44.0.2", "10.44.0.3"}


@pytest.mark.parametrize("status", ["done", "dismissed"])
def test_a_finished_or_dismissed_test_is_no_longer_planned(
    db_session, test_project, test_user, coverage_hosts, status
):
    """"Planned" is work still to do.  Closing or dismissing a host's only
    test takes the host out of it — and does not, by itself, make it tested."""
    test_b = coverage_hosts["test_b"]
    test_b.status = status
    db_session.commit()

    planned = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    assert planned == {"10.44.0.1"}
    tested = _matching_ips(db_session, test_project.id, test_user, "has:tested")
    assert tested == {"10.44.0.1"}


@pytest.mark.parametrize("outcome", ["failed", "info"])
def test_a_record_that_is_not_a_test_result_does_not_make_a_host_tested(
    client, db_session, test_project, test_user, coverage_hosts, outcome
):
    """An evidence record is not necessarily a test.  Counting any row let
    /operations say "all hosts tested" over hosts whose only record was an
    attempt that could not run; the tile and the `has:tested` list it links
    to must agree."""
    _evidence(
        db_session, test_project.id, coverage_hosts["planned_only"], outcome,
        coverage_hosts["test_b"],
    )

    tested = _matching_ips(db_session, test_project.id, test_user, "has:tested")
    assert tested == {"10.44.0.1"}

    body = client.get(f"/api/v1/projects/{test_project.id}/coverage/").json()
    assert body["hosts_with_execution_result"] == 1
    assert body["hosts_no_execution"] == 2


@pytest.mark.parametrize("outcome", ["finding", "no_finding", "inconclusive"])
def test_each_real_outcome_makes_a_host_tested_with_or_without_a_test(
    client, db_session, test_project, test_user, coverage_hosts, outcome
):
    """Evidence need not answer a proposed test: an agent that ran something
    against host C and recorded what came back has tested it."""
    _evidence(db_session, test_project.id, coverage_hosts["neither"], outcome)

    tested = _matching_ips(db_session, test_project.id, test_user, "has:tested")
    assert tested == {"10.44.0.1", "10.44.0.3"}
    # …and that does not make C planned.
    planned = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    assert "10.44.0.3" not in planned

    body = client.get(f"/api/v1/projects/{test_project.id}/coverage/").json()
    assert body["hosts_with_execution_result"] == 2
    assert body["hosts_with_plan_entry"] == 2


def test_untouched_follows_tests_and_evidence(
    db_session, test_project, test_user, coverage_hosts
):
    """A proposed test or any evidence record is somebody touching the host.
    A test that was DISMISSED is not: the host goes back to "worth a look"."""
    pid = test_project.id
    assert _matching_ips(db_session, pid, test_user, "has:untouched") == {"10.44.0.3"}

    test_b = coverage_hosts["test_b"]
    test_b.status = "dismissed"
    db_session.commit()
    assert _matching_ips(db_session, pid, test_user, "has:untouched") == {"10.44.0.2", "10.44.0.3"}

    # Even a record that is not a test result (info) is contact with the host.
    _evidence(db_session, pid, coverage_hosts["neither"], "info")
    assert _matching_ips(db_session, pid, test_user, "has:untouched") == {"10.44.0.2"}
