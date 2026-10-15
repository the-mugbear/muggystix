"""Defects on the remediation deadlines page (owner, 2026-10-14).

A defect is a finding ON A HOST of the remediation list.  Where it stands is
ONE expression, ``remediation_policy.defect_expr``: ``remediated`` when the
ENDPOINT is remediated (the assessor's retest — never the finding's status,
never the contact's "Reported fixed"), else ``deferred`` when its record is,
else ``open``; a false-positive endpoint is no defect.  The projects table's
Total and Remediated and every cell of the defect metrics are counted from
the list's own rows with that expression, and each opens the list it counts.
Times to remediate run from the ASSIGNED date to the day it was marked
remediated (``finding_hosts.remediated_at``, stamped by the one endpoint
writer) or deferred, or to today for one still open.
"""
from app.db.models_findings import FindingHost
from app.db.models_project import Project
from app.services import remediation_policy, remediation_service
from tests.test_remediation_vocabulary import (  # noqa: F401 — `on` is a fixture
    DEFERRED, OVERVIEW, _finding, apply, base, day, listing, on, paged,
)

# endpoint, record → defect state (None: not a defect)
TRUTH = [
    ("open", None, "open"),
    ("open", "closed", "open"),                 # reported fixed is the contact's claim, not the retest
    ("open", "deferred", "deferred"),
    ("retest", "deferred", "deferred"),
    ("remediated", None, "remediated"),
    ("remediated", "deferred", "remediated"),   # the retest wins over the record
    ("remediated", "closed", "remediated"),
    ("false_positive", "open", None),
]


def _mark(client, project, finding_id, ids, host_status):
    response = client.patch(f"/api/v1/projects/{project.id}/findings/{finding_id}/endpoints",
                            json={"finding_host_ids": ids, "host_status": host_status})
    assert response.status_code == 200, response.text


def test_the_truth_table_of_a_defect(client, db_session, test_project, on):
    ids = _finding(db_session, test_project, [endpoint for endpoint, _, _ in TRUTH])
    rows = []
    for fh_id, (_, record, _) in zip(ids, TRUTH):
        if record == "deferred":
            rows.append({"finding_host_id": fh_id, **DEFERRED})
        elif record == "open":
            rows.append({"finding_host_id": fh_id, "contact_name": "Someone"})
        elif record is not None:
            rows.append({"finding_host_id": fh_id, "status": record})
    apply(client, test_project, rows)
    expr = remediation_policy.defect_expr()
    got = dict(remediation_service._rows(db_session, [test_project.id]).with_entities(FindingHost.id, expr).all())
    assert got == {fh_id: defect for fh_id, (_, _, defect) in zip(ids, TRUTH)}
    for defect in remediation_policy.DEFECTS:
        listed = {r["finding_host_id"] for r in listing(client, base(test_project), defect=defect, limit=200)["items"]}
        assert listed == {fh_id for fh_id, (_, _, d) in zip(ids, TRUTH) if d == defect}, defect


def test_a_finding_judged_false_positive_is_no_defect(client, db_session, test_project, on):
    """Review 2026-10-14, H5: setting the FINDING false positive moves no
    endpoint, and its tracked rows stayed open defects — counted, with an
    MTTR clock running to today, under a page that says false positives are
    left out."""
    ids = _finding(db_session, test_project, ["open", "open"])
    apply(client, test_project, [{"finding_host_id": i, "contact_name": "Someone"} for i in ids])
    finding_id = db_session.get(FindingHost, ids[0]).finding_id
    r = client.post(f"/api/v1/projects/{test_project.id}/findings/{finding_id}/status",
                    json={"status": "false_positive", "summary": "The service is not exposed."})
    assert r.status_code == 200 and r.json()["status"] == "false_positive", r.text
    db_session.expire_all()
    expr = remediation_policy.defect_expr()
    got = dict(remediation_service._rows(db_session, [test_project.id]).with_entities(FindingHost.id, expr).all())
    assert got == {i: None for i in ids}                       # still tracked, never a defect
    for defect in remediation_policy.DEFECTS:
        assert listing(client, base(test_project), defect=defect)["total"] == 0, defect


def test_the_endpoint_writer_stamps_and_clears_the_remediated_day(client, db_session, test_project, on):
    ids = _finding(db_session, test_project, ["open", "open"])
    finding_id = db_session.get(FindingHost, ids[0]).finding_id
    _mark(client, test_project, finding_id, ids, "remediated")
    db_session.expire_all()
    assert all(db_session.get(FindingHost, i).remediated_at is not None for i in ids)
    _mark(client, test_project, finding_id, ids[:1], "retest")
    db_session.expire_all()
    assert db_session.get(FindingHost, ids[0]).remediated_at is None
    assert db_session.get(FindingHost, ids[1]).remediated_at is not None


def test_each_project_count_is_the_list_it_opens(client, db_session, test_project, on):
    fixed = _finding(db_session, test_project, ["remediated"] * 7 + ["open"] * 6 + ["false_positive"])
    apply(client, test_project, [{"finding_host_id": fixed[-1], "contact_name": "Tracked"}])   # listed, not a defect
    answer = listing(client, f"{OVERVIEW}/projects")
    row = next(p for p in answer["items"] if p["project_id"] == test_project.id)
    assert row["total"] == 14 and row["remediated"] == 7
    assert answer["defect_totals"] == {"total": 14, "remediated": 7}
    assert sum(row["states"].values()) == row["total"]          # Total is every row the list holds
    rows, first = paged(client, OVERVIEW, project_id=test_project.id, defect="remediated")
    assert first["total"] == len(rows) == 7
    rows, first = paged(client, OVERVIEW, project_id=test_project.id)
    assert first["total"] == len(rows) == 14


def test_each_metric_is_the_list_it_opens_across_pages_and_projects(client, db_session, test_project, on):
    archived = Project(name="archived", slug="archived-defects", description="")
    db_session.add(archived)
    db_session.commit()
    for project in (test_project, archived):
        _finding(db_session, project, ["remediated"] * 3 + ["open"] * 4, severity="critical")
        _finding(db_session, project, ["remediated"] * 2 + ["open"] * 6, severity="high")
        deferred = _finding(db_session, project, ["open"] * 3, severity="medium")
        apply(client, project, [{"finding_host_id": i, **DEFERRED} for i in deferred])
        _finding(db_session, project, ["open"] * 2, severity="low")
    archived.status = "archived"            # remediation runs on after testing ends
    db_session.commit()

    for project_id, active_only in ((None, True), (test_project.id, None), (archived.id, None)):
        params = {"project_id": project_id} if project_id else {}
        metrics = listing(client, f"{OVERVIEW}/defect-metrics", **params)
        assert metrics["projects"] == 1                          # all ACTIVE projects: the archived one is left out
        by_key = {r["key"]: r for r in metrics["rows"]}
        assert [r["key"] for r in metrics["rows"]] == ["overall", "critical_high", "critical", "high", "medium", "low"]
        assert by_key["overall"]["defects"] == 20 and by_key["critical_high"]["defects"] == 15
        assert by_key["critical"]["remediation_rate"] == round(100 * 3 / 7, 1)
        for row in metrics["rows"]:
            assert row["defects"] == row["open"] + row["remediated"] + row["deferred"]
            for defect in (None, *remediation_policy.DEFECTS):
                count = row["defects"] if defect is None else row[defect]
                query = {**params, "defect": list(remediation_policy.DEFECTS) if defect is None else defect}
                if row["severities"]:
                    query["severity"] = row["severities"]
                if active_only:
                    query["active_only"] = "true"
                listed, first = paged(client, OVERVIEW, **query)
                assert first["total"] == len(listed) == count, (project_id, row["key"], defect)


def test_time_to_remediate_runs_from_the_assigned_day(client, db_session, test_project, on):
    fixed, deferred, still_open, unassigned = _finding(db_session, test_project, ["open"] * 4, severity="high")
    finding_id = db_session.get(FindingHost, fixed).finding_id
    apply(client, test_project, [
        {"finding_host_id": fixed, "notified_on": day(20)},
        {"finding_host_id": deferred, "notified_on": day(10), **DEFERRED},     # deferred today
        {"finding_host_id": still_open, "notified_on": day(5)},
    ])
    _mark(client, test_project, finding_id, [fixed], "remediated")              # remediated today
    # A remediated endpoint whose day was never recorded is not measured.
    unrecorded = _finding(db_session, test_project, ["remediated"], severity="high")[0]
    apply(client, test_project, [{"finding_host_id": unrecorded, "notified_on": day(3)}])

    high = next(r for r in listing(client, f"{OVERVIEW}/defect-metrics")["rows"] if r["key"] == "high")
    assert (high["defects"], high["open"], high["remediated"], high["deferred"]) == (5, 2, 2, 1)
    assert high["mttr_measured"] == 3 and high["mttr_days_total"] == 20 + 10 + 5
    assert high["mttr_days"] == round(35 / 3, 1)
    assert high["mttr_closed_measured"] == 2 and high["mttr_closed_days"] == 15.0
    assert high["not_assigned"] == 1 and high["not_measured"] == 1
