"""The tool registry — one catalogue of the tools BlueStick knows about.

Before this there were two lists that could not see each other: 61 curated
entries hardcoded in the frontend reference page, and 11 tools in the backend
recon catalogue.  They had already drifted (`testssl` was agent-usable with no
human entry).

v2.433.0 — the registry is a catalogue, not agent policy: the `approved`
status (the allowlist an agent could run without asking) is gone with the rest
of the "agent on rails" model.  These tests pin that the registry covers what
the recon catalogue suggests, that ingestibility is independent of being
catalogued, that fresh and upgraded installs hold the same states, and that
seeding never clobbers a curator's decisions.
"""
from __future__ import annotations

from app.db.models_tools import (
    TOOL_REFERENCE,
    TOOL_REJECTED,
    TOOL_SUGGESTED,
    ToolRegistryEntry,
)
from app.services import tool_registry_service as registry

CATALOGUE_STATES = {TOOL_REFERENCE, TOOL_SUGGESTED, TOOL_REJECTED}


def _seed(db):
    registry.seed_registry(db)


def test_a_fresh_seed_holds_only_catalogue_states(db_session):
    """Code review (v2.433.0): the checked-in seed still carried `approved`,
    so a FRESH install re-created the retired status after the migration had
    converted nothing — `list_tools(status=reference)` then left out nmap."""
    _seed(db_session)
    tools = registry.list_tools(db_session)
    assert {t.status for t in tools} <= CATALOGUE_STATES
    reference = {t.name for t in registry.list_tools(db_session, status=TOOL_REFERENCE)}
    assert {"nmap", "testssl"} <= reference


def test_testssl_has_human_knowledge(db_session):
    _seed(db_session)
    testssl = db_session.query(ToolRegistryEntry).filter_by(name="testssl").one()
    assert testssl.status == TOOL_REFERENCE
    assert testssl.description and testssl.install and testssl.url
    assert testssl.category


def test_catalogue_and_ingestibility_are_independent(db_session):
    """Whether BlueStick parses a tool's output is an engineering fact, not a
    statement about whether it belongs in the catalogue."""
    _seed(db_session)
    tools = registry.list_tools(db_session)
    assert any(t.ingestible for t in tools)
    assert any(not t.ingestible and t.status == TOOL_REFERENCE for t in tools)


def test_seeding_is_additive_and_never_overwrites_a_decision(db_session):
    """A curator's decision, or an edited description, has to survive a
    redeploy — otherwise every release silently reverts it."""
    _seed(db_session)
    entry = db_session.query(ToolRegistryEntry).filter_by(name="gobuster").one()
    entry.status = TOOL_REJECTED
    entry.description = "Locally edited description."
    db_session.commit()

    added = registry.seed_registry(db_session)  # re-seed, as a redeploy would
    assert added == 0

    db_session.refresh(entry)
    assert entry.status == TOOL_REJECTED
    assert entry.description == "Locally edited description."


def test_suggestion_lands_in_the_same_table_pending_curation(db_session):
    """Suggestions are rows, not notes in a separate store — so curating one is
    a status change, and the suggestion shows beside the catalogued tools."""
    _seed(db_session)
    entry = registry.record_suggestion(
        db_session,
        name="crackmapexec-ng",
        rationale="Used it for SMB signing checks.",
        agent_id=7,
        project_id=3,
    )

    assert entry.status == TOOL_SUGGESTED
    assert "SMB signing" in entry.suggested_rationale
    assert "crackmapexec-ng" in {t.name for t in registry.list_tools(db_session)}


def test_resuggesting_appends_demand_rather_than_duplicating(db_session):
    _seed(db_session)
    registry.record_suggestion(db_session, name="ligolo-ng", rationale="First ask.")
    registry.record_suggestion(db_session, name="ligolo-ng", rationale="Second ask, different session.")

    rows = db_session.query(ToolRegistryEntry).filter_by(name="ligolo-ng").all()
    assert len(rows) == 1
    assert "First ask." in rows[0].suggested_rationale
    assert "Second ask" in rows[0].suggested_rationale


def test_suggesting_a_catalogued_tool_does_not_downgrade_it(db_session, client):
    """An agent suggesting a tool the catalogue already has must not knock it
    out of the catalogue — and the agent is told it is already there."""
    _seed(db_session)
    entry = registry.record_suggestion(db_session, name="nmap", rationale="please add nmap")
    assert entry.status == TOOL_REFERENCE


def test_reference_page_no_longer_carries_its_own_catalogue(db_session):
    """The page renders this registry (v5.167.0). Re-introducing a hardcoded
    array there would recreate exactly the drift this table exists to end — a
    human list the policy layer cannot see — so guard the direction rather than
    trusting the migration to stay done.

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

    hardcoded = set(re.findall(r"\{ name: '([^']+)'", tsx))
    assert not hardcoded, f"reference page has re-grown a local tool list: {sorted(hardcoded)}"
    assert "getToolRegistry" in tsx, "reference page no longer reads the registry endpoint"

    # And the endpoint it reads still returns the curated set.
    _seed(db_session)
    assert len(registry.list_tools(db_session)) > 50


def test_vetting_a_suggestion_is_a_status_change(client, db_session):
    """The other half of `suggest_tool`. Without this the suggestions pile up in
    a table with no way to act on one short of a SQL prompt — which is the same
    as not capturing them."""
    _seed(db_session)
    registry.record_suggestion(
        db_session, name="ligolo-ng", rationale="Needed for pivoting."
    )

    resp = client.patch(
        "/api/v1/references/tools/ligolo-ng",
        json={
            "status": TOOL_REFERENCE,
            # Cataloguing usually means writing real prose: the suggested row's
            # description is the agent's rationale, which reads badly as
            # documentation on a page humans use to learn about tools.
            "description": "Reverse tunneling / pivoting tool for reaching segmented networks.",
            "category": "Remote Access",
            "install": "go install github.com/nicocha30/ligolo-ng/cmd/agent@latest",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == TOOL_REFERENCE

    db_session.expire_all()
    row = db_session.query(ToolRegistryEntry).filter_by(name="ligolo-ng").one()
    assert row.description.startswith("Reverse tunneling")
    assert row.category == "Remote Access"


def test_declining_keeps_the_row(client, db_session):
    """A declined tool stays in the table so the next agent that asks gets the
    same answer instead of re-opening a decision someone already made."""
    _seed(db_session)
    registry.record_suggestion(db_session, name="metasploit", rationale="Exploitation.")
    resp = client.patch(
        "/api/v1/references/tools/metasploit", json={"status": "rejected"}
    )
    assert resp.status_code == 200

    db_session.expire_all()
    row = db_session.query(ToolRegistryEntry).filter_by(name="metasploit").one()
    assert row.status == "rejected"
    # Still listed — the page shows it as declined rather than pretending the
    # ask never happened.
    assert "metasploit" in {t.name for t in registry.list_tools(db_session)}


def test_suggested_is_not_a_status_an_operator_can_set(client, db_session):
    """`suggested` means "an agent asked for this". Letting the UI write it
    would put a row in the review queue that nobody asked for."""
    _seed(db_session)
    resp = client.patch("/api/v1/references/tools/nmap", json={"status": "suggested"})
    assert resp.status_code == 422
    assert "suggested" in resp.json()["detail"]


def test_vetting_an_unknown_tool_is_a_404(client, db_session):
    _seed(db_session)
    resp = client.patch("/api/v1/references/tools/not-a-tool", json={"status": "reference"})
    assert resp.status_code == 404


def test_vetting_is_gated_on_the_admin_role():
    """The catalogue is one deployment-wide list, curated by global admins.

    Asserted structurally because the ``client`` fixture authenticates as an
    admin, so an HTTP call can only ever show the allowed path.
    """
    from app.api.v1.endpoints import references
    from app.db.models_auth import UserRole

    route = next(
        r for r in references.router.routes
        if getattr(r, "path", None) == "/references/tools/{name}"
    )
    gates = [
        d.call
        for d in route.dependant.dependencies
        if getattr(d.call, "__closure__", None)
    ]
    enforced = [
        cell.cell_contents for gate in gates for cell in (gate.__closure__ or ())
    ]
    assert UserRole.ADMIN in enforced, "the vetting route must require the admin role"

    # And the read path stays open — it is documentation.
    listing = next(
        r for r in references.router.routes
        if getattr(r, "path", None) == "/references/tools"
    )
    assert not [
        d for d in listing.dependant.dependencies
        if getattr(d.call, "__closure__", None)
    ]


def test_ingestibility_is_not_operator_editable(client, db_session):
    """It records whether a parser exists in this codebase, not a decision an
    operator makes — editing it would let the UI promise an upload that nothing
    can read."""
    _seed(db_session)
    # A tool with no parser — the case where flipping the flag would be a lie.
    target = next(
        t for t in registry.list_tools(db_session) if not t.ingestible
    ).name

    resp = client.patch(
        f"/api/v1/references/tools/{target}",
        json={"ingestible": True, "status": "rejected"},
    )
    # Unknown fields are ignored by the schema rather than rejected, so assert
    # on the effect: the status change lands, the flag does not.
    assert resp.status_code == 200
    db_session.expire_all()
    row = db_session.query(ToolRegistryEntry).filter_by(name=target).one()
    assert row.status == TOOL_REJECTED
    assert row.ingestible is False


def test_endpoint_serves_the_registry_and_filters_by_status(client, db_session):
    _seed(db_session)
    body = client.get("/api/v1/references/tools").json()
    assert body["count"] > 50
    names = {t["name"] for t in body["tools"]}
    assert {"nmap", "testssl", "sqlcmd"} <= names

    reference = client.get("/api/v1/references/tools?status=reference").json()
    assert 0 < reference["count"] <= body["count"]
    assert all(t["status"] == "reference" for t in reference["tools"])
    nmap = next(t for t in reference["tools"] if t["name"] == "nmap")
    assert nmap["phases"] and nmap["intrusive"] is not None
