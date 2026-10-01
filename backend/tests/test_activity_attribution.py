"""v2.339.0 — the tool-activity surface answers attribution questions:
"was this signature (tool), against this host (target), at this time, ours?"

Pins the two attribution filters and the per-command kind.  Since v2.442.0
that kind is ``evidence`` — one command an agent reported running against a
host, with the tool it named and the address it reached.  Until then it was a
test-plan execution result (``test_result``) or a target probe
(``sanity_check``), inside an ``execution_session`` container row; those three
kinds went with plans and runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord


ANCHOR = datetime(2026, 5, 26, 14, 32, 15, tzinfo=timezone.utc)


def _evidence(db_session, project, host, tool, command, seconds, *,
              observed_ip=None, outcome="no_finding", session=None, executed=True):
    """One evidence record ``seconds`` after the anchor.  ``executed=False``
    leaves ``executed_at`` empty, so the row is anchored on when it was
    recorded instead."""
    when = ANCHOR + timedelta(seconds=seconds)
    row = EvidenceRecord(
        project_id=project.id, host_id=host.id, tool=tool, command=command,
        outcome=outcome, summary="s", observed_ip=observed_ip,
        executed_at=when if executed else None, created_at=when,
        agent_session_id=session.id if session is not None else None,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def attribution_dataset(db_session):
    """One project (the admin test user sees everything): two hosts, a scan
    that observed one of them, and an agent session that recorded one command
    against it."""
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

    session = AgentSession(workflow="project", project_id=project.id, status="active")
    db_session.add(session)
    db_session.commit()
    record = _evidence(db_session, project, seen, "nikto", "nikto -h 10.20.0.5", 10,
                       outcome="finding", session=session)
    return {"project": project, "scan": scan, "other_scan": other_scan,
            "session": session, "record": record, "seen": seen, "unseen": unseen}


def _at(client, **params):
    q = {"ts": ANCHOR.isoformat(), "tolerance_seconds": 600, **params}
    r = client.get("/api/v1/activity/scans-at", params=q)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_both_kinds_appear_including_the_per_command_record(client, attribution_dataset):
    kinds = {i["kind"] for i in _at(client)}
    assert kinds == {"scan", "evidence"}
    cmd = next(i for i in _at(client) if i["kind"] == "evidence")
    assert cmd["ref_id"] == attribution_dataset["record"].id
    assert cmd["label"] == "nikto"
    assert cmd["target"] == "10.20.0.5"
    # The deep link goes to the agent session that recorded it.
    assert cmd["parent_id"] == attribution_dataset["session"].id
    assert cmd["secondary_label"] == "nikto -h 10.20.0.5"
    assert cmd["status"] == "finding"
    assert cmd["has_end_time"] is False and cmd["host_count"] is None


def test_retired_kinds_are_refused_not_silently_empty(client, attribution_dataset):
    """A caller still asking for the plan-era kinds is told so (400), not
    handed an empty answer that reads as "nothing ran"."""
    for retired in ("execution_session", "test_result", "sanity_check"):
        r = client.get("/api/v1/activity/scans-at", params={"ts": ANCHOR.isoformat(), "kinds": retired})
        assert r.status_code == 400, (retired, r.text)
        assert retired in r.json()["detail"]


def test_tool_filter_matches_tool_name_or_command(client, attribution_dataset):
    items = _at(client, tool="NMAP")
    assert [(i["kind"], i["label"]) for i in items] == [("scan", "nmap")]
    items = _at(client, tool="nikto")
    assert {i["kind"] for i in items} == {"evidence"}
    # The command line counts too: "-h 10.20" is in the command, not the tool.
    assert [i["label"] for i in _at(client, tool="-h 10.20")] == ["nikto"]
    # A tool nobody ran: empty.
    assert _at(client, tool="hydra") == []


def test_target_filter_keeps_only_rows_that_touched_the_address(client, attribution_dataset):
    items = _at(client, target="10.20.0.5")
    by_kind = {}
    for i in items:
        by_kind.setdefault(i["kind"], []).append(i)
    # The scan that observed the host — not the one that observed another —
    # and the command against it.
    assert [s["ref_id"] for s in by_kind["scan"]] == [attribution_dataset["scan"].id]
    assert set(by_kind) == {"scan", "evidence"}

    # The other host: its scan only, no command was run against it.
    other = _at(client, target="10.99.9.9")
    assert [(i["kind"], i["ref_id"]) for i in other] == [("scan", attribution_dataset["other_scan"].id)]

    # An address nothing touched: nothing, across every kind.
    assert _at(client, target="192.0.2.1") == []


def test_tool_and_target_combine(client, attribution_dataset):
    items = _at(client, tool="nikto", target="10.20.0.5")
    assert len(items) == 1 and items[0]["kind"] == "evidence"
    assert _at(client, tool="nikto", target="10.99.9.9") == []


def test_target_must_be_an_ip(client, attribution_dataset):
    r = client.get("/api/v1/activity/scans-at", params={"ts": ANCHOR.isoformat(), "target": "not-an-ip"})
    assert r.status_code == 400


def test_between_takes_the_same_filters(client, attribution_dataset):
    window = {
        "from": (ANCHOR - timedelta(hours=1)).isoformat(),
        "to": (ANCHOR + timedelta(hours=1)).isoformat(),
    }
    r = client.get("/api/v1/activity/scans-between", params={**window, "tool": "masscan"})
    assert r.status_code == 200, r.text
    assert [i["label"] for i in r.json()["items"]] == ["masscan"]
    r = client.get("/api/v1/activity/scans-between", params={**window, "tool": "nikto", "target": "10.20.0.5"})
    assert [(i["kind"], i["label"]) for i in r.json()["items"]] == [("evidence", "nikto")]


def test_evidence_outside_the_window_is_not_listed(client, attribution_dataset, db_session):
    d = attribution_dataset
    _evidence(db_session, d["project"], d["seen"], "hydra", "hydra ssh://10.20.0.5", 1500)
    assert _at(client, tool="hydra") == []
    assert [i["label"] for i in _at(client, tool="hydra", tolerance_seconds=3000)] == ["hydra"]


def test_a_record_with_no_execution_time_is_anchored_on_when_it_was_recorded(
    client, attribution_dataset, db_session,
):
    d = attribution_dataset
    row = _evidence(db_session, d["project"], d["seen"], "curl", "curl -I http://10.20.0.5/", 40, executed=False)
    item = next(i for i in _at(client, tool="curl"))
    assert item["ref_id"] == row.id
    assert datetime.fromisoformat(item["start_time"].replace("Z", "+00:00")) == ANCHOR + timedelta(seconds=40)


# --- 2.339.1 review remediation ------------------------------------------


def test_tool_filter_is_settled_in_sql_so_the_cap_cannot_hide_matches(
    client, attribution_dataset, db_session, monkeypatch,
):
    """H1: with the filter applied after the LIMIT, newer rows that did not
    match consumed the budget and the real match was never fetched — and
    truncated stayed False."""
    from app.api.v1.endpoints import activity as activity_module

    d = attribution_dataset
    # Two NEWER nmap commands on the same host.
    _evidence(db_session, d["project"], d["seen"], "nmap", "nmap -sV 10.20.0.5", 20)
    _evidence(db_session, d["project"], d["seen"], "nmap", "nmap -sS 10.20.0.5", 30)
    # Uncapped: only the command that actually ran nikto matches.
    items = _at(client, tool="nikto", kinds="evidence")
    assert [i["label"] for i in items] == ["nikto"]
    assert items[0]["secondary_label"] == "nikto -h 10.20.0.5"

    monkeypatch.setattr(activity_module, "MAX_RESULTS", 1)
    r = client.get("/api/v1/activity/scans-at", params={
        "ts": ANCHOR.isoformat(), "tolerance_seconds": 600, "tool": "nikto", "kinds": "evidence",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["label"] for i in body["items"]] == ["nikto"]
    assert body["truncated"] is False
    # Without the filter the cap IS hit, and says so.
    r = client.get("/api/v1/activity/scans-at", params={
        "ts": ANCHOR.isoformat(), "tolerance_seconds": 600, "kinds": "evidence",
    })
    assert r.json()["truncated"] is True and len(r.json()["items"]) == 1


def test_target_filter_matches_the_address_a_command_actually_hit(
    client, attribution_dataset, db_session,
):
    """H2: observed_ip is the execution evidence; a command that reached
    another binding of the host's endpoint is attributable by that address."""
    d = attribution_dataset
    _evidence(db_session, d["project"], d["seen"], "nikto", "nikto -h 10.20.0.9", 15, observed_ip="10.20.0.9")
    items = _at(client, target="10.20.0.9", kinds="evidence")
    assert [(i["label"], i["target"]) for i in items] == [("nikto", "10.20.0.9")]
    # The host's inventory address still finds both commands.
    targets = sorted(i["target"] for i in _at(client, target="10.20.0.5", kinds="evidence"))
    assert targets == ["10.20.0.5", "10.20.0.9"]


def test_blank_tool_is_no_filter(client, attribution_dataset):
    """H3: whitespace used to match everything while hiding kinds."""
    kinds = {i["kind"] for i in _at(client, tool="   ")}
    assert kinds == {"scan", "evidence"}


def test_tool_like_metacharacters_are_literal(client, attribution_dataset):
    # Without escaping, `_` matches any one character: "nmap_" would find "nmap ".
    assert _at(client, tool="nmap_") == []
    assert _at(client, tool="nmap") != []
    # Same for the evidence kind: "nikto_" must not match "nikto -h …".
    assert _at(client, tool="nikto_", kinds="evidence") == []


def test_project_ids_narrows_the_evidence_kind_too(client, attribution_dataset, db_session):
    """The evidence kind honours ``project_ids`` like scans do: narrowed to
    another project, this project's command is not returned."""
    other = Project(name="elsewhere", slug="elsewhere")
    db_session.add(other)
    db_session.commit()
    items = _at(client, kinds="evidence", project_ids=str(other.id))
    assert items == []
    mine = _at(client, kinds="evidence", project_ids=str(attribution_dataset["project"].id))
    assert [i["label"] for i in mine] == ["nikto"]


def test_scans_do_not_echo_the_query_target(client, attribution_dataset):
    scan = next(i for i in _at(client, target="10.20.0.5") if i["kind"] == "scan")
    assert scan["target"] is None and scan["host_count"] == 1
