"""Two statuses, two facts, one name each.

The remediation record's ``closed`` is the CONTACT's claim ("Reported fixed");
the endpoint's ``remediated`` is the ASSESSOR's conclusion ("Remediated").
Nothing writes one from the other, so the gap between them is derived — one
SQL expression, ``remediation_policy.verification_expr`` — and every count of
it is the list its filter opens.  Stored values and API enums are unchanged.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_remediation import FindingHostRemediation, RemediationPolicy
from app.services import remediation_policy, remediation_service

TODAY = datetime.now(timezone.utc).date()
OVERVIEW = "/api/v1/remediation-overview"
AGENT = "/api/v1/agent/remediation"
NOT_RETESTED, RECORD_OPEN = "reported_fixed_not_retested", "remediated_record_open"


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def base(project) -> str:
    return f"/api/v1/projects/{project.id}/remediation"


@pytest.fixture
def on(db_session):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, days_info=None, due_soon_days=7))
    db_session.commit()


_NET = [0]


def _finding(db, project, endpoints, *, status="confirmed", severity="medium"):
    """One finding on one new host per entry of ``endpoints`` (each an
    endpoint status); returns the finding-on-host ids in that order."""
    _NET[0] += 1
    finding = Finding(project_id=project.id, title=f"issue {_NET[0]}", severity=severity,
                      status=status, source="manual")
    db.add(finding)
    db.flush()
    links = []
    for n, host_status in enumerate(endpoints, start=1):
        host = models.Host(project_id=project.id, ip_address=f"10.{70 + _NET[0]}.{n // 250}.{n % 250 + 1}",
                           state="up")
        db.add(host)
        db.flush()
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status=host_status)
        db.add(link)
        links.append(link)
    db.commit()
    return [link.id for link in links]


def apply(client, project, rows):
    response = client.post(f"{base(project)}/apply", json={"rows": rows, "overwrite": True})
    assert response.status_code == 200, response.text
    return response.json()


def listing(client, url, **params):
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def paged(client, url, *, limit=5, headers=None, **params):
    """Every row of a list, read ``limit`` at a time; ``(ids, first page)``."""
    seen, offset, first = [], 0, None
    while True:
        response = client.get(url, params={**params, "limit": limit, "offset": offset}, headers=headers)
        assert response.status_code == 200, response.text
        page = response.json()
        first = first or page
        assert page["total"] == first["total"]
        seen += page["items"]
        if not page["has_more"]:
            return seen, first
        offset += limit


# What each endpoint status × record status must read as.  ``None`` as the
# record: no row in ``finding_host_remediation`` at all.
TRUTH = [
    # endpoint,         record,     verification,  listed
    ("open",            None,       None,          True),
    ("open",            "open",     None,          True),
    ("open",            "deferred", None,          True),
    ("open",            "closed",   NOT_RETESTED,  True),
    ("retest",          None,       None,          True),
    ("retest",          "open",     None,          True),
    ("retest",          "deferred", None,          True),
    ("retest",          "closed",   NOT_RETESTED,  True),
    ("remediated",      None,       RECORD_OPEN,   True),
    ("remediated",      "open",     RECORD_OPEN,   True),
    ("remediated",      "deferred", RECORD_OPEN,   True),
    ("remediated",      "closed",   None,          True),     # the two agree
    # A false positive is not something to fix: never a gap.  With no record
    # it is not on the list at all (the list's own rule, unchanged).
    ("false_positive",  None,       None,          False),
    ("false_positive",  "open",     None,          True),
    ("false_positive",  "deferred", None,          True),
    ("false_positive",  "closed",   None,          True),
]


def _truth_world(client, db, project):
    ids = _finding(db, project, [endpoint for endpoint, _, _, _ in TRUTH])
    rows = []
    for fh_id, (_, record, _, _) in zip(ids, TRUTH):
        if record == "open":
            rows.append({"finding_host_id": fh_id, "contact_name": "Someone"})       # a record, still open
        elif record is not None:
            rows.append({"finding_host_id": fh_id, "status": record})
    apply(client, project, rows)
    return ids


def test_the_truth_table_of_the_gap(client, db_session, test_project, on):
    ids = _truth_world(client, db_session, test_project)
    page = listing(client, base(test_project), limit=200)
    got = {item["finding_host_id"]: item for item in page["items"]}
    for fh_id, (endpoint, record, verification, listed) in zip(ids, TRUTH):
        assert (fh_id in got) is listed, (endpoint, record)
        if not listed:
            continue
        row = got[fh_id]
        assert row["verification"] == verification, (endpoint, record, row["verification"])
        # Both facts are on the row, under their own stored values.
        assert row["endpoint_status"] == endpoint and row["status"] == (record or "open")
    assert page["verification_counts"] == {NOT_RETESTED: 2, RECORD_OPEN: 3}
    # A record that was never written is exactly that: nothing was created to say so.
    stored = {r.finding_host_id for r in db_session.query(FindingHostRemediation)}
    assert stored == {fh_id for fh_id, (_, record, _, _) in zip(ids, TRUTH) if record is not None}


def test_the_expression_is_the_only_definition(db_session, test_project, on, client):
    """The rows the SQL expression names are the rows the list names: no second
    reading of the two statuses anywhere."""
    ids = _truth_world(client, db_session, test_project)
    expr = remediation_policy.verification_expr()
    from_sql = dict(remediation_service._rows(db_session, [test_project.id])
                    .with_entities(FindingHost.id, expr).all())
    expected = {fh_id: verification for fh_id, (_, _, verification, listed) in zip(ids, TRUTH) if listed}
    assert from_sql == expected
    assert set(remediation_policy.VERIFICATIONS) == {NOT_RETESTED, RECORD_OPEN}


@pytest.fixture
def gaps(client, db_session, test_project, on):
    """More of each gap than a page of five holds, among rows with neither."""
    closed_open = _finding(db_session, test_project, ["open"] * 9 + ["retest"] * 3)        # 12 not retested
    fixed = _finding(db_session, test_project, ["remediated"] * 9, severity="high")       # 7 record open, 2 agree
    plain = _finding(db_session, test_project, ["open"] * 6)
    apply(client, test_project,
          [{"finding_host_id": i, "status": "closed", "closed_on": day(1), "notified_on": day(20)} for i in closed_open]
          + [{"finding_host_id": i, "notified_on": day(100)} for i in fixed[:2]]            # open, overdue
          + [{"finding_host_id": i, "status": "deferred"} for i in fixed[2:4]]
          + [{"finding_host_id": i, "status": "closed"} for i in fixed[7:]]                 # 4..6: no record
          + [{"finding_host_id": i, "notified_on": day(1)} for i in plain[:2]])
    return {NOT_RETESTED: set(closed_open), RECORD_OPEN: set(fixed[:7])}


def test_each_gap_count_is_the_list_it_opens_across_pages(client, test_project, gaps):
    counts = listing(client, base(test_project))["verification_counts"]
    assert counts == {NOT_RETESTED: 12, RECORD_OPEN: 7}
    for verification, expected in gaps.items():
        rows, first = paged(client, base(test_project), verification=verification)
        ids = [row["finding_host_id"] for row in rows]
        assert len(ids) == len(set(ids)) == counts[verification] == first["total"]
        assert set(ids) == expected
        assert all(row["verification"] == verification for row in rows)
        # The counts are taken BEFORE this filter: both stay on every page.
        assert first["verification_counts"] == counts
        # The state chips follow it, and add up to the list.
        assert sum(first["state_counts"].values()) == first["total"]
    assert listing(client, base(test_project), verification=NOT_RETESTED)["state_counts"]["closed"] == 12


def test_the_gap_counts_ignore_the_state_filter_and_follow_the_rest(client, test_project, gaps):
    whole = {NOT_RETESTED: 12, RECORD_OPEN: 7}
    # Before the state and status filters: a count opens its list by itself.
    for params in ({"state": "overdue"}, {"status": "closed"}, {"state": ["deferred", "closed"]},
                   {"overdue_band": "8-30"}, {"no_follow_up_days": 7}):
        assert listing(client, base(test_project), **params)["verification_counts"] == whole, params
    # Part of the selection: severity narrows them, and the list with them.
    high = listing(client, base(test_project), severity="high")
    assert high["verification_counts"] == {NOT_RETESTED: 0, RECORD_OPEN: 7}
    assert listing(client, base(test_project), severity="high", verification=RECORD_OPEN)["total"] == 7
    assert listing(client, base(test_project), severity="high", verification=NOT_RETESTED)["total"] == 0
    # Together with a state, the list is both — and its total says so.
    overdue = listing(client, base(test_project), verification=RECORD_OPEN, state="overdue", limit=200)
    assert overdue["total"] == len(overdue["items"]) == 2
    deferred = listing(client, base(test_project), verification=RECORD_OPEN, status="deferred", limit=200)
    assert deferred["total"] == len(deferred["items"]) == 2
    # The at-risk breakdown follows the filter like the rest of the selection.
    assert listing(client, base(test_project), verification=RECORD_OPEN)["severity_counts"]["high"]["overdue"] == 2
    assert listing(client, base(test_project), verification=NOT_RETESTED)["severity_counts"]["high"]["overdue"] == 0


def test_a_value_that_is_not_one_is_refused(client, test_project, on):
    for url in (base(test_project), OVERVIEW):
        assert client.get(url, params={"verification": "closed"}).status_code == 422


def test_the_agent_and_the_cross_project_routes_take_the_same_filter(client, db_session, test_project, gaps):
    page_counts = listing(client, base(test_project))["verification_counts"]
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert key.status_code == 201, key.text
    headers = {"X-API-Key": key.json()["api_key"]}
    mounts = (
        (AGENT, headers),                                                   # an agent's read
        (OVERVIEW, None),                                                   # across the caller's projects
        (f"{OVERVIEW}/projects/{test_project.id}/remediation", None),       # one project, archived or not
    )
    for url, with_key in mounts:
        for verification, expected in gaps.items():
            rows, first = paged(client, url, headers=with_key, verification=verification)
            assert {row["finding_host_id"] for row in rows} == expected, url
            assert first["verification_counts"] == page_counts, url
            assert all(row["verification"] == verification for row in rows)


def test_a_remediated_endpoint_outside_the_list_stays_outside(client, db_session, test_project, on):
    """The list is what a client report includes plus anything tracked; the
    gap does not widen it.  A finding still under investigation with an
    endpoint someone marked remediated, and no record, is not on it."""
    outside = _finding(db_session, test_project, ["remediated"], status="open")
    inside = _finding(db_session, test_project, ["remediated"])
    page = listing(client, base(test_project), verification=RECORD_OPEN)
    assert [row["finding_host_id"] for row in page["items"]] == inside
    assert page["verification_counts"][RECORD_OPEN] == 1
    # Once it is tracked it is listed, by the list's existing rule.
    apply(client, test_project, [{"finding_host_id": outside[0], "contact_name": "Someone"}])
    assert listing(client, base(test_project))["verification_counts"][RECORD_OPEN] == 2


def test_neither_status_is_written_from_the_other(client, db_session, test_project, on):
    reported, concluded = _finding(db_session, test_project, ["open", "remediated"])
    apply(client, test_project, [{"finding_host_id": reported, "status": "closed", "closed_on": day(0)}])
    listing(client, base(test_project), verification=NOT_RETESTED)
    db_session.expire_all()
    # The contact's report did not conclude anything for the assessor …
    assert db_session.get(FindingHost, reported).host_status == "open"
    # … and the assessor's conclusion wrote no record for the contact.
    assert db_session.query(FindingHostRemediation).filter_by(finding_host_id=concluded).count() == 0
    # The stored value is still ``closed``: only the label changed.
    assert db_session.query(FindingHostRemediation).filter_by(finding_host_id=reported).one().status == "closed"
    assert listing(client, base(test_project), status="closed")["total"] == 1


def test_off_means_no_gap_anywhere(client, db_session, test_project):
    ids = _finding(db_session, test_project, ["remediated", "open"])
    for url in (base(test_project), OVERVIEW, f"{OVERVIEW}/projects/{test_project.id}/remediation"):
        for params in ({}, {"verification": RECORD_OPEN}):
            response = client.get(url, params=params)
            assert response.status_code == 404, (url, response.text)
            assert "not enabled on this installation" in response.json()["detail"]
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]
    assert client.get(AGENT, params={"verification": RECORD_OPEN}, headers={"X-API-Key": key}).status_code == 404
    assert client.get("/api/v1/oversight/dashboard").json()["summary"]["remediation"] is None
    # No other page learned the word.
    finding_id = db_session.get(FindingHost, ids[0]).finding_id
    for url in (f"/api/v1/projects/{test_project.id}/findings/{finding_id}",
                f"/api/v1/projects/{test_project.id}/findings"):
        body = client.get(url)
        assert body.status_code == 200, body.text
        assert "verification" not in body.text and "reported_fixed" not in body.text


def test_the_follow_up_message_never_says_closed(client, db_session, test_project, on):
    late, done = _finding(db_session, test_project, ["open", "open"])
    apply(client, test_project, [
        {"finding_host_id": late, "contact_email": "roger@testdomain.com", "contact_name": "Roger",
         "notified_on": day(100)},
        {"finding_host_id": done, "contact_email": "roger@testdomain.com", "status": "closed", "closed_on": day(2)},
    ])
    message = listing(client, f"{base(test_project)}/follow-up", contact_email="roger@testdomain.com")
    assert message["total"] == 1 and "Past the remediation deadline (1)" in message["text"]
    # What the contact reported fixed is not chased, and no line calls anything closed.
    assert "closed" not in message["text"].lower()
    nothing = remediation_service.follow_up_text("Roger", [], TODAY)
    assert "closed" not in nothing.lower()


def test_the_list_takes_no_more_statements_for_the_gap(client, db_session, test_project, gaps):
    def statements(**params):
        seen: list = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            seen.append(" ".join(statement.split()))

        event.listen(Engine, "after_cursor_execute", _count)
        try:
            listing(client, base(test_project), **params)
        finally:
            event.remove(Engine, "after_cursor_execute", _count)
        # The list's own reads (the rest is who is asking, and the policy).
        return [s for s in seen if "finding_hosts" in s]

    # Three, as before the gap existed: the at-risk breakdown, the state
    # counts — which now carry the gap counts — and the page of rows.
    plain = statements()
    assert len(plain) == 3, "\n".join(plain)
    # Asking for a gap adds nothing, with a state or without, and nothing is
    # read per row however many rows the page holds.
    assert len(statements(verification=NOT_RETESTED)) == 3
    assert len(statements(verification=RECORD_OPEN, state="overdue", limit=200)) == 3
    assert len(statements(limit=1)) == 3


def test_the_routes_and_the_tool_say_what_closed_means():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.main import app

    spec = app.openapi()["paths"]
    for path in ("/api/v1/projects/{project_id}/remediation", "/api/v1/agent/remediation",
                 "/api/v1/remediation-overview",
                 "/api/v1/remediation-overview/projects/{project_id}/remediation"):
        params = {p["name"]: p for p in spec[path]["get"]["parameters"]}
        assert "Reported fixed" in params["status"]["description"], path
        assert "not the assessor" in params["status"]["description"]
        described = params["verification"]["description"]
        assert NOT_RETESTED in described and RECORD_OPEN in described, path

    tool = TOOLS["remediation_list"]
    # The argument is the endpoint's: its two values, nothing typed in the registry.
    offered = tool["input_schema"]["properties"]["verification"]
    assert NOT_RETESTED in str(offered) and RECORD_OPEN in str(offered)
    said = tool["description"]
    assert "REPORTED it fixed" in said and "`verification`" in said and "`verification_counts`" in said
    # The stored value keeps working: the tool still offers `closed`.
    assert "closed" in str(tool["input_schema"]["properties"]["status"])
    assert "REPORTED it fixed" in TOOLS["remediation_apply"]["description"]
