"""The tool registry — one source of truth for the tools BlueStick knows about.

Before this there were two lists that could not see each other: 61 curated
entries hardcoded in the frontend reference page, and 11 tools in the backend
recon catalogue — the only one that could gate anything. They had already
drifted (`testssl` was agent-usable with no human entry), and any auto-approval
rule built on the backend list would have rejected tools the app itself
recommends.

These tests pin the properties that make the registry usable as a policy input:
it covers everything the recon catalogue hands agents, approval and
ingestibility stay independent, and seeding never clobbers an operator's
decisions.
"""
from __future__ import annotations

from app.db.models_tools import (
    TOOL_APPROVED,
    TOOL_REFERENCE,
    TOOL_SUGGESTED,
    ToolRegistryEntry,
)
from app.services import tool_registry_service as registry


def _seed(db):
    registry.seed_registry(db)


def test_registry_covers_every_tool_the_recon_catalogue_offers(db_session):
    """The catalogue is what an agent is told it may run. A tool missing from
    the registry is a tool no approval rule can reason about — which is exactly
    how `testssl` ended up agent-usable and undocumented."""
    from app.services.recon_planning_service import build_tool_catalog

    _seed(db_session)
    known = {t.name for t in registry.list_tools(db_session)}
    offered = {entry["tool"] for entry in build_tool_catalog(["10.0.0.0/24"])}

    assert offered <= known, f"recon offers tools absent from the registry: {offered - known}"
    # And every one of them is approved — the catalogue must not offer a tool
    # the policy layer would refuse.
    approved = registry.approved_tool_names(db_session)
    assert offered <= approved, f"recon offers unapproved tools: {offered - approved}"


def test_testssl_now_has_human_knowledge(db_session):
    """The one real divergence between the two lists: agents could be told to
    run it, humans had nowhere to read what it was."""
    _seed(db_session)
    testssl = db_session.query(ToolRegistryEntry).filter_by(name="testssl").one()
    assert testssl.status == TOOL_APPROVED
    assert testssl.description and testssl.install and testssl.url
    assert testssl.category


def test_approval_and_ingestibility_are_independent(db_session):
    """A policy fact and an engineering fact. Fusing them would mean either no
    tool can be approved until someone writes a parser — stalling the vetting
    loop — or approving tools whose upload then fails."""
    _seed(db_session)
    tools = registry.list_tools(db_session)

    ingestible = {t.name for t in tools if t.ingestible}
    approved = {t.name for t in tools if t.status == TOOL_APPROVED}

    # Parsers exist for tools agents aren't offered — so ingestible does not
    # imply approved.
    assert ingestible - approved, "expected tools with parsers that agents aren't offered"

    # And the converse: a tool can be approved with no parser at all. Asserted
    # by building one rather than by finding one, because today's seed happens
    # to have a parser for every approved tool — that coincidence must not be
    # allowed to become a constraint, since execution records evidence text and
    # never ingests scanner output.
    db_session.add(
        ToolRegistryEntry(
            name="some-approved-tool-with-no-parser",
            description="Approved to run; BlueStick cannot parse its output.",
            category="General Purpose",
            status=TOOL_APPROVED,
            ingestible=False,
        )
    )
    db_session.commit()
    assert "some-approved-tool-with-no-parser" in registry.approved_tool_names(db_session)


def test_documented_tools_are_not_silently_approved(db_session):
    """The reference page is a knowledge repo — being documented must not imply
    an agent may run it. Most of the catalogue is reference-only by design."""
    _seed(db_session)
    tools = registry.list_tools(db_session)
    reference_only = [t for t in tools if t.status == TOOL_REFERENCE]

    assert len(reference_only) > len([t for t in tools if t.status == TOOL_APPROVED])
    # A reference-only tool carries no agent policy metadata to be misread.
    assert all(not (t.phases or []) for t in reference_only)


def test_seeding_is_additive_and_never_overwrites_a_decision(db_session):
    """An operator's approval, or an edited description, has to survive a
    redeploy — otherwise every release silently reverts their vetting."""
    _seed(db_session)
    entry = db_session.query(ToolRegistryEntry).filter_by(name="gobuster").one()
    entry.status = TOOL_APPROVED
    entry.description = "Locally edited description."
    db_session.commit()

    added = registry.seed_registry(db_session)  # re-seed, as a redeploy would
    assert added == 0

    db_session.refresh(entry)
    assert entry.status == TOOL_APPROVED
    assert entry.description == "Locally edited description."


def test_suggestion_lands_in_the_same_table_pending_vetting(db_session):
    """Suggestions are rows, not notes in a separate store — so vetting is a
    status change rather than a copy between systems, and the suggested tool
    shows up beside the vetted ones, visibly unapproved."""
    _seed(db_session)
    entry = registry.record_suggestion(
        db_session,
        name="crackmapexec-ng",
        rationale="Needed for SMB signing checks the approved set doesn't cover.",
        agent_id=7,
        project_id=3,
    )

    assert entry.status == TOOL_SUGGESTED
    assert entry.name not in registry.approved_tool_names(db_session)
    assert "SMB signing" in entry.suggested_rationale
    # It is visible to the same listing the reference page reads.
    assert "crackmapexec-ng" in {t.name for t in registry.list_tools(db_session)}


def test_resuggesting_appends_demand_rather_than_duplicating(db_session):
    _seed(db_session)
    registry.record_suggestion(db_session, name="ligolo-ng", rationale="First ask.")
    registry.record_suggestion(db_session, name="ligolo-ng", rationale="Second ask, different session.")

    rows = db_session.query(ToolRegistryEntry).filter_by(name="ligolo-ng").all()
    assert len(rows) == 1
    assert "First ask." in rows[0].suggested_rationale
    assert "Second ask" in rows[0].suggested_rationale


def test_suggesting_an_approved_tool_does_not_downgrade_it(db_session):
    """An agent asking for something it already has must not knock the tool out
    of the approved set."""
    _seed(db_session)
    entry = registry.record_suggestion(db_session, name="nmap", rationale="please add nmap")
    assert entry.status == TOOL_APPROVED
    assert "nmap" in registry.approved_tool_names(db_session)


def test_registry_covers_every_tool_the_reference_page_documents(db_session):
    """The page is being migrated onto this registry in a follow-up commit.
    Until it is, the two can still drift — so pin the direction that matters:
    anything the human catalogue documents must exist in the registry, or the
    page will lose entries the moment it switches over.

    Reads the TSX the same way test_tool_command_consistency does.
    """
    import re
    import pytest

    # Same resolution as test_tool_command_consistency: honours
    # $BLUESTICK_REPO_ROOT / an ancestor / /repo, and skips under a
    # backend-only mount rather than false-failing.
    from tests.test_tool_command_consistency import _read

    tsx = _read("frontend/src/pages/ToolReference.tsx")
    if tsx is None:
        pytest.skip("frontend source not mounted — run with the repo root mounted")

    documented = set(re.findall(r"\{ name: '([^']+)'", tsx))
    assert documented, "could not parse the reference page catalogue"

    _seed(db_session)
    known = {t.name for t in registry.list_tools(db_session)}
    assert documented <= known, f"documented but not in the registry: {documented - known}"


def test_endpoint_serves_the_registry_and_filters_by_status(client, db_session):
    _seed(db_session)
    body = client.get("/api/v1/references/tools").json()
    assert body["count"] > 50
    names = {t["name"] for t in body["tools"]}
    assert {"nmap", "testssl", "sqlcmd"} <= names

    approved = client.get("/api/v1/references/tools?status=approved").json()
    assert 0 < approved["count"] < body["count"]
    assert all(t["status"] == "approved" for t in approved["tools"])
    # The agent-facing fields ride along for the approved subset.
    nmap = next(t for t in approved["tools"] if t["name"] == "nmap")
    assert nmap["phases"] and nmap["intrusive"] is not None
