"""A stored session cannot go missing from the session list without someone
having decided it.

This is the guard for the bug that actually happened.  "The list of kinds of
session" was duplicated across five places — the service's kind set, its
per-kind branches, the endpoint's literal, the rollup's buckets and the
frontend union — and **assist was missing from all of them for months**.  An
operator with a live assist key saw nothing on Agent Runs, and the
per-(model, tool) rollup under-reported what a harness had done.  Nothing
failed: an absent kind produces no error, only a shorter list that looks
complete.

2.489.0 — the five copies are gone.  A session has no kind on the wire, no
per-kind branch computes anything, and there is ONE statement of which stored
``agent_sessions.workflow`` values are sessions the list has:
``agent_session_service.LISTED_WORKFLOWS``.  What this file guards now:

* every value the enum declares is either listed or named here as off the
  list, so a NEW value cannot be silently absent (and a retired one cannot
  come back half-listed);
* the list, its count, the by-id read, the per-(model, tool) rollup and
  ``sessions_with_a_page`` all agree about a row of every stored value — they
  are one filter, and this keeps them one.

(Before 2.489.0 the same guard read ``ALL_SESSION_KINDS`` and the endpoint's
``SessionKindLiteral``, and checked that ``?kind=<retired>`` was a 422.  There
is no ``kind`` parameter any more.)
"""
from app.db.models_agent import AgentSession, AgentSessionWorkflow
from app.services.agent_session_service import (
    LISTED_WORKFLOWS,
    count_agent_sessions,
    get_agent_session_row,
    list_agent_sessions,
    sessions_with_a_page,
    summarise_by_model_tool,
)

#: Enum members whose rows are on no page: what such a session did (a test
#: plan, an execution run) was removed with its tables in v2.442.0.
OFF_THE_LIST = {"plan_generation", "execution"}
#: Stored on old rows though the enum no longer declares it (recon runs went
#: in v2.433.0).
STORED_ONLY = {"recon"}


def test_every_declared_workflow_is_listed_or_deliberately_not():
    declared = {w.value for w in AgentSessionWorkflow}
    listed = set(LISTED_WORKFLOWS)
    assert listed <= declared
    assert OFF_THE_LIST <= declared, "a value left the enum — drop it from OFF_THE_LIST too"
    assert not listed & OFF_THE_LIST
    undecided = declared - listed - OFF_THE_LIST
    assert not undecided, (
        f"workflow value(s) {sorted(undecided)} are neither in LISTED_WORKFLOWS nor "
        "named here as off the list. A session stored with one would be invisible on "
        "Agent Sessions and uncounted in the model/tool rollup — with no error to notice."
    )
    # What a new session is stored as is on the list.
    assert AgentSessionWorkflow.PROJECT.value in listed


def test_every_reader_agrees_about_a_row_of_every_stored_value(
    client, db_session, test_project, test_user,
):
    ids = {}
    for workflow in sorted(set(LISTED_WORKFLOWS) | OFF_THE_LIST | STORED_ONLY):
        row = AgentSession(
            workflow=workflow, project_id=test_project.id, started_by_id=test_user.id,
            status="ended", generated_by_model="probe-model", generated_by_tool="probe-tool",
        )
        db_session.add(row)
        db_session.flush()
        ids[workflow] = row.id
    db_session.commit()
    expected = {ids[w] for w in LISTED_WORKFLOWS}

    rows = list_agent_sessions(db_session, test_project.id)
    assert {r.id for r in rows} == expected
    assert count_agent_sessions(db_session, test_project.id) == len(expected)
    assert sessions_with_a_page(db_session, ids.values()) == expected
    [probe] = summarise_by_model_tool(db_session, test_project.id)
    assert probe == {
        "generated_by_model": "probe-model", "generated_by_tool": "probe-tool",
        "total": len(expected),
    }
    for workflow, session_id in ids.items():
        row = get_agent_session_row(db_session, test_project.id, session_id)
        assert (row is not None) == (workflow in LISTED_WORKFLOWS), workflow
        status = client.get(
            f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}"
        ).status_code
        assert status == (200 if workflow in LISTED_WORKFLOWS else 404), workflow

    # And the route says the same, with no kind on any row.
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    assert body["total"] == len(expected)
    assert {r["id"] for r in body["sessions"]} == expected
    assert all("kind" not in r for r in body["sessions"])
    # A row carries no fact that depends on what it is stored as, but one:
    # Resume refuses a row not stored as ``project`` (never sent as a field).
    assert {r.id for r in rows if r.resume_supported} == {ids["project"]}
    assert all("resume_supported" not in r for r in body["sessions"])
