"""v2.339.0 — the tool-activity surface answers attribution questions:
"was this signature (tool), against this host (target), at this time, ours?"

Pins the two attribution filters and the two per-command kinds
(``test_result`` = a command an executing agent reported, with the tool it
used and the host it ran against; ``sanity_check`` = a target probe).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_agent import (
    ExecutionSession,
    ExecutionSessionStatus,
    HostSanityCheck,
    TestExecutionResult,
    TestPlan,
    TestPlanEntry,
    TestPlanStatus,
)
from app.db.models_project import Project


ANCHOR = datetime(2026, 5, 26, 14, 32, 15, tzinfo=timezone.utc)


@pytest.fixture
def attribution_dataset(db_session):
    """One project (the admin test user sees everything): two hosts, a scan
    that observed one of them, a recon run whose scope contains it, and an
    execution run with one command + one probe against it."""
    project = Project(name="attrib", slug="attrib")
    db_session.add(project)
    db_session.flush()
    seen = models.Host(project_id=project.id, ip_address="10.20.0.5", state="up")
    unseen = models.Host(project_id=project.id, ip_address="10.99.9.9", state="up")
    db_session.add_all([seen, unseen])
    db_session.flush()

    scan = models.Scan(
        project_id=project.id, filename="s.xml", tool_name="nmap", scan_type="port_scan",
        command_line="nmap -sV 10.20.0.0/24",
        start_time=ANCHOR - timedelta(seconds=60), end_time=ANCHOR + timedelta(seconds=60),
    )
    other_scan = models.Scan(
        project_id=project.id, filename="m.txt", tool_name="masscan", scan_type="port_scan",
        start_time=ANCHOR - timedelta(seconds=30), end_time=ANCHOR + timedelta(seconds=30),
    )
    db_session.add_all([scan, other_scan])
    db_session.flush()
    db_session.add(models.HostScanHistory(host_id=seen.id, scan_id=scan.id))
    db_session.add(models.HostScanHistory(host_id=unseen.id, scan_id=other_scan.id))

    scope = models.Scope(project_id=project.id, name="dmz", description="")
    db_session.add(scope)
    db_session.flush()
    db_session.add(models.Subnet(scope_id=scope.id, cidr="10.20.0.0/24"))
    from app.db.models_agent import ReconSession, ReconSessionStatus
    db_session.add(ReconSession(
        project_id=project.id, scope_id=scope.id, status=ReconSessionStatus.COMPLETED.value,
        started_at=ANCHOR - timedelta(minutes=5), completed_at=ANCHOR + timedelta(minutes=5),
    ))

    plan = TestPlan(project_id=project.id, version=1, title="attrib plan",
                    status=TestPlanStatus.IN_PROGRESS.value)
    db_session.add(plan)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=plan.id, host_id=seen.id, priority="high", test_phase="enumeration",
        rationale="x", proposed_tests=[{"tool": "nikto", "command": "nikto -h {ip}"}],
    )
    db_session.add(entry)
    db_session.flush()
    run = ExecutionSession(
        test_plan_id=plan.id, status=ExecutionSessionStatus.ACTIVE.value,
        started_at=ANCHOR - timedelta(minutes=2),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(TestExecutionResult(
        execution_session_id=run.id, entry_id=entry.id, test_index=0, status="executed",
        command_run="nikto -h 10.20.0.5", executed_at=ANCHOR + timedelta(seconds=10),
    ))
    db_session.add(HostSanityCheck(
        execution_session_id=run.id, entry_id=entry.id, host_id=seen.id,
        method="ping", target_ip="10.20.0.5", passed=True, checked_at=ANCHOR - timedelta(seconds=5),
    ))
    db_session.commit()
    return {"project": project, "scan": scan, "other_scan": other_scan, "run": run}


def _at(client, **params):
    q = {"ts": ANCHOR.isoformat(), "tolerance_seconds": 600, **params}
    r = client.get("/api/v1/activity/scans-at", params=q)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_all_kinds_appear_including_the_per_command_record(client, attribution_dataset):
    kinds = {i["kind"] for i in _at(client)}
    assert kinds == {"scan", "recon_session", "execution_session", "test_result", "sanity_check"}
    cmd = next(i for i in _at(client) if i["kind"] == "test_result")
    assert cmd["label"] == "nikto"
    assert cmd["target"] == "10.20.0.5"
    assert cmd["parent_id"] == attribution_dataset["run"].id
    assert cmd["secondary_label"] == "nikto -h 10.20.0.5"
    probe = next(i for i in _at(client) if i["kind"] == "sanity_check")
    assert probe["label"] == "sanity: ping" and probe["target"] == "10.20.0.5"
    assert probe["status"] == "passed"


def test_tool_filter_matches_tool_name_or_command_and_drops_the_containers(client, attribution_dataset):
    items = _at(client, tool="NMAP")
    assert [(i["kind"], i["label"]) for i in items] == [("scan", "nmap")]
    items = _at(client, tool="nikto")
    assert {i["kind"] for i in items} == {"test_result"}
    items = _at(client, tool="ping")
    assert {i["kind"] for i in items} == {"sanity_check"}
    # A tool nobody ran: empty, and no run rows padding the answer.
    assert _at(client, tool="hydra") == []


def test_target_filter_keeps_only_rows_that_touched_the_address(client, attribution_dataset):
    items = _at(client, target="10.20.0.5")
    by_kind = {}
    for i in items:
        by_kind.setdefault(i["kind"], []).append(i)
    # The scan that observed the host — not the one that observed another.
    assert [s["ref_id"] for s in by_kind["scan"]] == [attribution_dataset["scan"].id]
    # The recon run whose scope contains it, the execution run whose plan
    # lists it, the command and the probe against it.
    assert set(by_kind) == {"scan", "recon_session", "execution_session", "test_result", "sanity_check"}

    # An address nothing touched: nothing, across every kind.
    assert _at(client, target="192.0.2.1") == []


def test_tool_and_target_combine(client, attribution_dataset):
    items = _at(client, tool="nikto", target="10.20.0.5")
    assert len(items) == 1 and items[0]["kind"] == "test_result"
    assert _at(client, tool="nikto", target="10.99.9.9") == []


def test_target_must_be_an_ip(client, attribution_dataset):
    r = client.get("/api/v1/activity/scans-at", params={"ts": ANCHOR.isoformat(), "target": "not-an-ip"})
    assert r.status_code == 400


def test_between_takes_the_same_filters(client, attribution_dataset):
    r = client.get("/api/v1/activity/scans-between", params={
        "from": (ANCHOR - timedelta(hours=1)).isoformat(),
        "to": (ANCHOR + timedelta(hours=1)).isoformat(),
        "tool": "masscan",
    })
    assert r.status_code == 200, r.text
    assert [i["label"] for i in r.json()["items"]] == ["masscan"]


# --- 2.339.1 review remediation ------------------------------------------


def _add_results(db_session, dataset, proposed_tests, results):
    """A second plan + run on the fixture project (entries are unique per
    plan/host), one entry on the seen host, with ``results`` as
    ``(test_index, command, seconds_after_anchor, observed_ip)``."""
    project = dataset["project"]
    host = db_session.query(models.Host).filter_by(ip_address="10.20.0.5").one()
    plan = TestPlan(project_id=project.id, version=2, title="attrib plan 2",
                    status=TestPlanStatus.IN_PROGRESS.value)
    db_session.add(plan)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=plan.id, host_id=host.id, priority="high", test_phase="enumeration",
        rationale="x", proposed_tests=proposed_tests,
    )
    run = ExecutionSession(
        test_plan_id=plan.id, status=ExecutionSessionStatus.ACTIVE.value,
        started_at=ANCHOR - timedelta(minutes=1),
    )
    db_session.add_all([entry, run])
    db_session.flush()
    rows = []
    for idx, command, offset, observed in results:
        row = TestExecutionResult(
            execution_session_id=run.id, entry_id=entry.id, test_index=idx,
            status="executed", command_run=command,
            executed_at=ANCHOR + timedelta(seconds=offset), observed_ip=observed,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return rows


def test_tool_filter_is_settled_in_sql_so_the_cap_cannot_hide_matches(
    client, attribution_dataset, db_session, monkeypatch,
):
    """H1: with the filter applied after the LIMIT, newer rows that only
    matched through the entry's JSON consumed the budget and the real
    match was never fetched — and truncated stayed False."""
    from app.api.v1.endpoints import activity as activity_module

    # Two NEWER nmap results on an entry whose proposed tests mention nikto.
    _add_results(db_session, attribution_dataset,
                 [{"tool": "nikto"}, {"tool": "nmap"}, {"tool": "nmap"}],
                 [(1, "nmap -sV 10.20.0.5", 20, None), (2, "nmap -sS 10.20.0.5", 30, None)])
    # Uncapped: only the command that actually ran nikto matches.
    items = _at(client, tool="nikto", kinds="test_result")
    assert [i["label"] for i in items] == ["nikto"]
    assert items[0]["secondary_label"] == "nikto -h 10.20.0.5"

    monkeypatch.setattr(activity_module, "MAX_RESULTS", 1)
    r = client.get("/api/v1/activity/scans-at", params={
        "ts": ANCHOR.isoformat(), "tolerance_seconds": 600, "tool": "nikto", "kinds": "test_result",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["label"] for i in body["items"]] == ["nikto"]
    assert body["truncated"] is False


def test_target_filter_matches_the_address_a_command_actually_hit(
    client, attribution_dataset, db_session,
):
    """H2: observed_ip is the execution evidence; a command that reached
    another binding of the entry's endpoint is attributable by that address."""
    _add_results(db_session, attribution_dataset, [{"tool": "nikto"}],
                 [(0, "nikto -h 10.20.0.9", 15, "10.20.0.9")])
    items = _at(client, target="10.20.0.9", kinds="test_result")
    assert [(i["label"], i["target"]) for i in items] == [("nikto", "10.20.0.9")]
    # The entry's inventory address still finds both commands.
    targets = sorted(i["target"] for i in _at(client, target="10.20.0.5", kinds="test_result"))
    assert targets == ["10.20.0.5", "10.20.0.9"]


def test_blank_tool_is_no_filter(client, attribution_dataset):
    """H3: whitespace used to match everything while hiding the run kinds."""
    kinds = {i["kind"] for i in _at(client, tool="   ")}
    assert kinds == {"scan", "recon_session", "execution_session", "test_result", "sanity_check"}


def test_tool_like_metacharacters_are_literal(client, attribution_dataset):
    # Without escaping, `_` matches any one character: "nmap_" would find "nmap ".
    assert _at(client, tool="nmap_") == []
    assert _at(client, tool="nmap") != []


def test_tool_label_skips_command_wrappers(client, attribution_dataset, db_session):
    _add_results(db_session, attribution_dataset, ["free text, no tool key"],
                 [(0, "sudo /usr/bin/nmap -sS 10.20.0.5", 25, None)])
    items = _at(client, tool="sudo", kinds="test_result")
    assert [i["label"] for i in items] == ["nmap"]


def test_tool_with_only_run_kinds_is_a_400(client, attribution_dataset):
    r = client.get("/api/v1/activity/scans-at", params={
        "ts": ANCHOR.isoformat(), "tool": "nmap", "kinds": "recon_session,execution_session",
    })
    assert r.status_code == 400, r.text
    # Mixed kinds still work — the runs are dropped, the rest answer.
    assert {i["kind"] for i in _at(client, tool="nmap", kinds="scan,execution_session")} == {"scan"}


def test_scans_do_not_echo_the_query_target(client, attribution_dataset):
    scan = next(i for i in _at(client, target="10.20.0.5") if i["kind"] == "scan")
    assert scan["target"] is None and scan["host_count"] == 1
