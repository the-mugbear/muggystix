"""Assist tools must agree with each other, not merely each work alone.

v2.313.0.  An agent exercised all 27 assist MCP tools end to end and found four
defects that every existing focused test missed — because each test asserted one
endpoint's behaviour in isolation, and all four bugs live in the *gaps between*
endpoints:

  * a note written through one tool was invisible to another,
  * one tool said the environment was probed and another said it was not,
  * a tool's advertised input could not express what the endpoint requires,
  * two tools reported different totals for the same thing.

None of those can be caught by testing an endpoint on its own, which is why
they survived. These tests are deliberately written as "write with tool A, read
with tool B" rather than as per-endpoint assertions.
"""

from datetime import datetime, timezone

import pytest


def _start(client, project_id, purpose="cross-tool consistency"):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": purpose},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _host(db_session, project, ip="10.77.0.1"):
    from app.db import models
    host = models.Host(
        project_id=project.id, ip_address=ip, state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


# ---------------------------------------------------------------------------
# 1. A note written on a host is findable in the project's recent notes.
# ---------------------------------------------------------------------------

def test_a_note_written_through_the_agent_appears_in_recent_notes(
    client, db_session, test_project,
):
    """The reported bug: `assist_add_note` succeeded, `assist_get_host_notes`
    showed the note, and `assist_list_recent_notes` was empty.

    Cause was neither tool. Recent-notes filtered `Annotation.project_id`,
    which is not a scope column: it is one of seven MUTUALLY EXCLUSIVE targets
    (`ck_annotations_exactly_one_target`), so a note carrying it is a note on
    the *project itself*, and a host note necessarily has it NULL. The filter
    selected the one target nothing writes, and the endpoint returned nothing
    for every project for its whole life. No single-endpoint test noticed,
    because an empty list is a valid response.
    """
    started = _start(client, test_project.id)
    headers = {"X-API-Key": started["api_key"]}
    host = _host(db_session, test_project, "10.77.1.1")

    created = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers,
        json={"body": "cross-tool: written on the host", "status": "open"},
    )
    assert created.status_code in (200, 201), created.text
    note_id = created.json()["id"]

    on_host = client.get(f"/api/v1/agent/hosts/{host.id}/notes", headers=headers)
    assert on_host.status_code == 200, on_host.text
    assert note_id in [n["id"] for n in on_host.json()]

    recent = client.get("/api/v1/agent/assist/notes", headers=headers)
    assert recent.status_code == 200, recent.text
    assert note_id in [n["id"] for n in recent.json()], (
        "a note readable on its host was missing from the project's recent "
        "notes — the two tools disagree about whether the note exists"
    )


def test_a_note_typed_by_a_person_is_in_recent_notes_too(
    client, db_session, test_project, test_user,
):
    """The bug was never agent-specific — the UI's note route reaches the same
    table, so operator-typed notes were equally invisible."""
    from app.services.host_follow_service import HostFollowService

    host = _host(db_session, test_project, "10.77.1.2")
    note = HostFollowService(db_session).create_note(
        host.id, test_user.id, "cross-tool: typed by a person",
    )
    started = _start(client, test_project.id)
    recent = client.get(
        "/api/v1/agent/assist/notes",
        headers={"X-API-Key": started["api_key"]},
    )
    assert recent.status_code == 200, recent.text
    assert note.id in [n["id"] for n in recent.json()]


def test_a_finding_comment_reaches_recent_notes(
    client, db_session, test_project, test_user,
):
    """The other Annotation target that carries real work. Its project is
    reached through the finding, not through a column on the note."""
    from app.db.models_findings import (
        Finding, FindingSeverity, FindingStatus, FindingSource,
    )
    from app.services.finding_service import FindingService

    finding = Finding(
        project_id=test_project.id, title="cross-tool finding",
        severity=FindingSeverity.MEDIUM.value, status=FindingStatus.OPEN.value,
        source=FindingSource.MANUAL.value,
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    note = FindingService(db_session).create_finding_note(
        finding_id=finding.id, user_id=test_user.id, body="a comment",
    )
    started = _start(client, test_project.id)
    recent = client.get(
        "/api/v1/agent/assist/notes",
        headers={"X-API-Key": started["api_key"]},
    )
    assert recent.status_code == 200, recent.text
    assert note.id in [n["id"] for n in recent.json()]


def test_recent_notes_stays_inside_the_project(client, db_session, test_project):
    """Reaching the project through six join branches is exactly the shape that
    leaks across projects if one branch is written wrong, and no per-endpoint
    test would see it. A note on another project's host must not appear."""
    from app.db.models_project import Project
    from app.services.host_follow_service import HostFollowService

    other = Project(name="cross-tool-other-project", slug="cross-tool-other")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    foreign_host = _host(db_session, other, "10.88.0.1")
    foreign = HostFollowService(db_session).create_note(
        foreign_host.id, None, "belongs to another engagement",
    )

    mine_host = _host(db_session, test_project, "10.77.1.3")
    mine = HostFollowService(db_session).create_note(
        mine_host.id, None, "belongs to this engagement",
    )

    started = _start(client, test_project.id)
    recent = client.get(
        "/api/v1/agent/assist/notes",
        headers={"X-API-Key": started["api_key"]},
    )
    assert recent.status_code == 200, recent.text
    ids = [n["id"] for n in recent.json()]
    assert mine.id in ids
    assert foreign.id not in ids, "recent notes leaked another project's note"


# ---------------------------------------------------------------------------
# 2. Every tool that reports "has this session probed its environment" agrees.
# ---------------------------------------------------------------------------

def test_all_three_tools_agree_the_environment_was_probed(
    client, db_session, test_project,
):
    """`agent_identity` said false while `assist_session_info` and
    `assist_get_context` said true, immediately after a successful probe.

    Identity read `AgentSession.environment_probed_at`; the probe writes the
    per-workflow detail row (AssistSession / ReconSession / ExecutionSession).
    Nothing writes the unified row, so identity's answer was false for every
    workflow, forever — an agent following its own instructions would re-probe
    on every turn.
    """
    started = _start(client, test_project.id)
    headers = {"X-API-Key": started["api_key"]}
    sid = started["assist_session_id"]

    def probed_flags():
        return {
            "identity": client.get(
                "/api/v1/agent/identity", headers=headers,
            ).json()["environment_probed"],
            "session_info": client.get(
                "/api/v1/agent/assist/session", headers=headers,
            ).json()["environment_probed"],
            "context": client.get(
                "/api/v1/agent/assist/context", headers=headers,
            ).json()["session"].get("environment_probed"),
        }

    before = probed_flags()
    assert set(before.values()) == {False}, f"expected all false before: {before}"

    probe = client.post(
        f"/api/v1/agent/assist/sessions/{sid}/environment",
        headers=headers,
        json={"os_family": "linux", "shell": "bash"},
    )
    assert probe.status_code == 200, probe.text

    after = probed_flags()
    assert set(after.values()) == {True}, (
        f"tools disagree about whether the environment was probed: {after}"
    )


# ---------------------------------------------------------------------------
# 3. What MCP advertises must be able to express what the endpoint requires.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["tools_available", "tools_status"])
def test_the_probe_schema_can_express_the_tool_inventory(field):
    """The environment contract asks for a tool inventory; the MCP schema set
    `additionalProperties: false` and omitted both inventory fields, so an MCP
    agent could not send one. Every probe recorded through MCP therefore stored
    `tools_available: {}` while a curl agent could send it — the two transports
    disagreed about what a complete probe is."""
    from app.api.v1.endpoints.mcp_tools import TOOLS, _PROBE_PROPERTIES

    assert field in _PROBE_PROPERTIES

    probes = [
        (name, spec) for name, spec in TOOLS.items()
        if spec["path"].endswith("/environment")
    ]
    assert probes, "expected the per-workflow environment probe tools"
    for name, spec in probes:
        props = spec["input_schema"]["properties"]
        assert field in props, f"{name} cannot send {field}"
        assert field in spec["body_params"], (
            f"{name} advertises {field} but would not forward it"
        )


def test_the_probe_schema_accepts_every_os_family_the_model_documents():
    """The enum allowed windows/darwin/linux; EnvironmentSummary documents bsd
    and other as well, so an operator on either had to misreport their OS."""
    from app.api.v1.endpoints.mcp_tools import _PROBE_PROPERTIES

    allowed = set(_PROBE_PROPERTIES["os_family"]["enum"])
    assert {"windows", "darwin", "linux", "bsd", "other"} <= allowed


def test_an_mcp_probe_round_trips_the_tool_inventory(
    client, db_session, test_project,
):
    """End to end: send the inventory the way MCP would, read it back."""
    started = _start(client, test_project.id)
    headers = {"X-API-Key": started["api_key"]}
    sid = started["assist_session_id"]

    resp = client.post(
        f"/api/v1/agent/assist/sessions/{sid}/environment",
        headers=headers,
        json={
            "os_family": "bsd",
            "tools_available": {"nmap": True, "masscan": False},
            "tools_status": [{"name": "nmap", "status": "ok", "version": "7.94"}],
        },
    )
    assert resp.status_code == 200, resp.text
    env = resp.json()["environment"]
    assert env["tools_available"] == {"nmap": True, "masscan": False}
    assert env["os_family"] == "bsd"


def test_no_tool_schema_uses_a_top_level_union(
    ):
    """A top-level anyOf/oneOf makes some MCP hosts present the tool as an
    opaque object union instead of typed parameters, so the model has to guess
    argument names. `assist_patch_host` had one to encode "send at least one
    field"; the endpoint enforces that anyway with a 400."""
    from app.api.v1.endpoints.mcp_tools import TOOLS

    offenders = [
        name for name, spec in TOOLS.items()
        if {"anyOf", "oneOf", "allOf"} & set(spec["input_schema"])
    ]
    assert not offenders, (
        f"{offenders} advertise a top-level schema union, which degrades "
        "parameter discoverability on clients that do not flatten it. State "
        "the constraint in the description and let the endpoint enforce it."
    )


# ---------------------------------------------------------------------------
# 4. Two tools counting the same thing must be reconcilable.
# ---------------------------------------------------------------------------

def test_ingestion_issues_and_coverage_report_reconcilable_parse_errors(
    client, db_session, test_project,
):
    """Coverage counted every unresolved parse error; ingestion-issues counted
    only the ones with no failed-job row, under near-identical names. A project
    with 7 failed uploads read as "7 unresolved" in one tool and "0 unresolved,
    7 failed" in the other, and an agent reading both concluded the data was
    inconsistent. Both were right; neither said which question it answered.
    """
    from app.db import models

    for i in range(3):
        db_session.add(models.ParseError(
            project_id=test_project.id,
            filename=f"broken-{i}.xml",
            error_type="ParseError",
            error_message="unit test",
            user_message="could not parse",
            status="unresolved",
        ))
    db_session.commit()

    started = _start(client, test_project.id)
    headers = {"X-API-Key": started["api_key"]}

    issues = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers)
    assert issues.status_code == 200, issues.text
    coverage = client.get("/api/v1/agent/assist/coverage", headers=headers)
    assert coverage.status_code == 200, coverage.text

    body = issues.json()
    coverage_total = coverage.json()["data_quality"]["parse_errors_unresolved"]

    assert body["unresolved_parse_errors_total"] == coverage_total, (
        "ingestion-issues and coverage disagree on the project's unresolved "
        f"parse errors: {body['unresolved_parse_errors_total']} vs "
        f"{coverage_total}"
    )
    # And the narrower number stays a subset of the total, so a reader can see
    # the decomposition instead of guessing at it.
    assert body["unresolved_parse_errors"] <= body["unresolved_parse_errors_total"]
