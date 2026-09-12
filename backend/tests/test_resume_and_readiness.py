"""Targeted tests for the workflow-resume + host-readiness paths.

These cover the load-bearing behaviors introduced by Plan B (workflow
resume) and Plan C (host readiness):

* ``_mint_plan_agent_key`` REVOKES every prior active key for the plan
  before minting a new one (the Critical-1 fix that closes the
  two-agents-one-session hole — see the helper's docstring).
* ``build_tool_readiness`` correctly cross-references the agent tool
  catalog against a probe (tools_status path, tools_available
  fallback, no-probe path).

The endpoint-level resume and tool-readiness HTTP paths share the same
helpers but require project-role auth wiring that the existing test
fixtures do not stand up; the helpers below are where the load-bearing
logic actually lives.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models_auth import APIKey
from app.services.agent_session_service import create_agent_session, mint_session_key
from app.services.recon_planning_service import build_tool_readiness


# ---------------------------------------------------------------------------
# One live key per SESSION (v2.337.0 — keys bind to a session, not a plan).
# ---------------------------------------------------------------------------


def test_mint_session_key_revokes_prior_active_key_for_the_session(
    db_session, test_project, test_agent
):
    """A prior active key on the same session is revoked when a new key is
    minted — one live key per session, so a resumed session's orphaned key
    cannot keep writing beside the new one."""
    session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    prior = APIKey(
        agent_id=test_agent.id,
        agent_session_id=session.id,
        name="prior",
        key_hash=hashlib.sha256(b"prior").hexdigest(),
        key_prefix="nm_agent_prio",
        is_active=True,
    )
    db_session.add(prior)
    db_session.commit()

    raw_key = mint_session_key(db_session, agent=test_agent, session=session)
    db_session.commit()
    db_session.refresh(prior)

    assert prior.is_active is False, "prior session key must be revoked on re-mint"
    assert raw_key.startswith("nm_agent_")
    active = (
        db_session.query(APIKey)
        .filter(APIKey.agent_session_id == session.id, APIKey.is_active.is_(True))
        .all()
    )
    assert len(active) == 1
    assert active[0].key_hash == hashlib.sha256(raw_key.encode()).hexdigest()


def test_mint_session_key_leaves_other_sessions_keys_alone(
    db_session, test_project, test_agent
):
    """The revoke is scoped to one session — a second concurrent session's
    key is untouched, so two operators stay isolated."""
    other = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    other_key = APIKey(
        agent_id=test_agent.id,
        agent_session_id=other.id,
        name="other-session-key",
        key_hash=hashlib.sha256(b"other").hexdigest(),
        key_prefix="nm_agent_othe",
        is_active=True,
    )
    db_session.add(other_key)
    db_session.commit()

    mine = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    mint_session_key(db_session, agent=test_agent, session=mine)
    db_session.commit()
    db_session.refresh(other_key)

    assert other_key.is_active is True, "minting for one session must not revoke another's key"


# ---------------------------------------------------------------------------
# Plan C — build_tool_readiness
# ---------------------------------------------------------------------------


def test_build_tool_readiness_no_probe_marks_all_unknown():
    result = build_tool_readiness(probe=None)
    assert result["has_probe"] is False
    assert result["os_family"] is None
    assert result["summary"]["total"] > 0
    assert result["summary"]["unknown"] == result["summary"]["total"]
    assert all(t["status"] == "unknown" for t in result["tools"])
    assert all(isinstance(t["install_hints"], dict) for t in result["tools"])


def test_build_tool_readiness_uses_tools_status_when_present():
    probe = {
        "os_family": "linux",
        "shell": "bash",
        "tools_status": [
            {"name": "nmap", "status": "ok", "path": "/usr/bin/nmap"},
            {"name": "masscan", "status": "missing"},
            {
                "name": "httpx",
                "status": "warn",
                "issue": "Python httpx shadows ProjectDiscovery httpx",
            },
        ],
    }
    result = build_tool_readiness(probe=probe)

    assert result["has_probe"] is True
    assert result["os_family"] == "linux"
    assert result["preferred_provider"] == "apt"

    by_name = {t["tool"]: t for t in result["tools"]}
    assert by_name["nmap"]["status"] == "installed"
    assert by_name["nmap"]["path"] == "/usr/bin/nmap"
    assert by_name["masscan"]["status"] == "missing"
    assert by_name["httpx"]["status"] == "warn"
    assert "Python httpx shadows" in (by_name["httpx"]["issue"] or "")


def test_build_tool_readiness_falls_back_to_tools_available():
    """tools_status is authoritative; tools_available is the simpler
    fallback when the agent has not run the preflight script yet."""
    probe = {
        "os_family": "darwin",
        "tools_available": {"nmap": True, "masscan": False},
    }
    result = build_tool_readiness(probe=probe)

    assert result["preferred_provider"] == "brew"
    by_name = {t["tool"]: t for t in result["tools"]}
    assert by_name["nmap"]["status"] == "installed"
    assert by_name["masscan"]["status"] == "missing"


def test_build_tool_readiness_dict_form_tools_status_also_accepted():
    """tools_status accepts the dict-keyed shape some agents emit, not
    just the documented list shape (parity with _env_tool_unavailable)."""
    probe = {
        "os_family": "linux",
        "tools_status": {
            "nmap": {"status": "ok", "path": "/usr/bin/nmap"},
            "masscan": {"status": "missing"},
        },
    }
    result = build_tool_readiness(probe=probe)
    by_name = {t["tool"]: t for t in result["tools"]}
    assert by_name["nmap"]["status"] == "installed"
    assert by_name["masscan"]["status"] == "missing"


def test_build_tool_readiness_summary_counts_match_per_tool_statuses():
    probe = {
        "os_family": "linux",
        "tools_status": [
            {"name": "nmap", "status": "ok"},
            {"name": "masscan", "status": "missing"},
            {"name": "httpx", "status": "warn"},
        ],
    }
    result = build_tool_readiness(probe=probe)
    statuses = [t["status"] for t in result["tools"]]
    assert result["summary"]["installed"] == statuses.count("installed")
    assert result["summary"]["missing"] == statuses.count("missing")
    assert result["summary"]["warn"] == statuses.count("warn")
    assert result["summary"]["unknown"] == statuses.count("unknown")
    assert (
        result["summary"]["installed"]
        + result["summary"]["missing"]
        + result["summary"]["warn"]
        + result["summary"]["unknown"]
        == result["summary"]["total"]
    )
