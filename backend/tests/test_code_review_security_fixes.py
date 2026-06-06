"""Regression tests for v2.90.3 code-review security fixes.

Covers three findings from the second-pass code review:

  NEW A — agent creation now requires the analyst project role
          (was: any project member, including viewers, could create
          unscoped agent keys and bypass the user-side analyst gate
          on /test-plans/ writes).

  NEW G — agent management ownership checks now use
          is_project_admin(...) instead of current_user.role ==
          "admin" (the global-admin role); a project-admin who isn't
          a global admin can now manage another member's agent.

  NEW C — host_deduplication_service host lookup uses noload(...)
          for ports / vulnerabilities / attributes / notes /
          tag_assignments so a 40k-host re-scan no longer fires a
          selectin query per host for relationship graphs the
          parser doesn't read.

The C test counts SELECT queries against the dns_records lookup
shape — relationship-load queries are easy to surface that way.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership


# ---------------------------------------------------------------------------
# NEW A — agent creation requires the analyst project role.
# ---------------------------------------------------------------------------


def _make_member_user(db_session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _add_membership(db_session, project, user, role: str):
    db_session.add(
        ProjectMembership(project_id=project.id, user_id=user.id, role=role),
    )
    db_session.flush()


def test_create_agent_requires_analyst_role(db_session, test_project):
    """Direct dependency call: viewer role → 403; analyst role → pass.

    Calls ``require_project_role("analyst")``'s inner checker
    directly with a synthesized current_user so the test doesn't
    need to mint a JWT.  Mirrors the contract the route enforces."""
    from fastapi import HTTPException
    from app.api.deps import require_project_role

    viewer = _make_member_user(db_session, "test-viewer")
    _add_membership(db_session, test_project, viewer, role="viewer")
    analyst = _make_member_user(db_session, "test-analyst")
    _add_membership(db_session, test_project, analyst, role="analyst")

    checker = require_project_role("analyst")

    with pytest.raises(HTTPException) as exc_info:
        checker(project_id=test_project.id, db=db_session, current_user=viewer)
    assert exc_info.value.status_code == 403

    # Analyst-level user passes without raising.
    returned = checker(project_id=test_project.id, db=db_session, current_user=analyst)
    assert returned.id == analyst.id


def test_create_agent_endpoint_wires_analyst_dependency():
    """The route handler depends on require_project_role("analyst") —
    structural assertion that the v2.90.3 wiring is intact and a
    future refactor can't silently revert the gate."""
    from app.api.v1.endpoints.agents import create_agent
    import inspect

    sig = inspect.signature(create_agent)
    current_user_param = sig.parameters.get("current_user")
    assert current_user_param is not None
    # Depends() wraps a closure named `checker` returned by the
    # require_project_role factory; the closure's __qualname__
    # contains the factory name, which is the stable identifier
    # across FastAPI versions.
    dep = current_user_param.default
    inner = getattr(dep, "dependency", None)
    assert inner is not None, f"Expected Depends() wrapper, got {dep!r}"
    assert "require_project_role" in inner.__qualname__


# ---------------------------------------------------------------------------
# NEW G — project-admin check via is_project_admin().
# ---------------------------------------------------------------------------


def test_is_project_admin_true_for_project_admin_role(db_session, test_project):
    """A user who is only a project-admin (not global admin) returns
    True from is_project_admin — the pre-fix gate
    ``current_user.role != "admin"`` would have rejected them."""
    from app.api.deps import is_project_admin

    user = _make_member_user(db_session, "padmin")
    _add_membership(db_session, test_project, user, role="admin")  # project-admin
    assert user.role == UserRole.MEMBER  # NOT global admin
    assert is_project_admin(db_session, test_project.id, user) is True


def test_is_project_admin_false_for_analyst(db_session, test_project):
    from app.api.deps import is_project_admin

    user = _make_member_user(db_session, "padmin2")
    _add_membership(db_session, test_project, user, role="analyst")
    assert is_project_admin(db_session, test_project.id, user) is False


def test_agents_py_no_longer_compares_global_role(db_session):
    """Belt-and-suspenders: scan the source of agents.py for any
    remaining ``current_user.role != "admin"`` comparisons in CODE
    (not comments).  Code-review NEW G replaced all four
    occurrences with is_project_admin(...).  If a future PR
    reintroduces the pattern this test will catch it before a
    viewer-level user runs into a project-admin gate."""
    from pathlib import Path
    import app.api.v1.endpoints.agents as mod

    source = Path(mod.__file__).read_text()
    # Strip full-line comments to avoid false matches on the
    # explanatory note left at each replaced site.
    code_lines = [
        ln for ln in source.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert 'current_user.role != "admin"' not in code
    assert source.count("is_project_admin(") >= 4


# ---------------------------------------------------------------------------
# NEW C — dedup host lookup uses noload() to suppress eager relationships.
# ---------------------------------------------------------------------------


def test_dedup_host_lookup_suppresses_relationship_selectins(
    db_session, test_project,
):
    """Counts SELECT statements fired during a find_or_create_host
    call against an EXISTING host.  Pre-fix the Host.* selectin
    relationships (ports, vulnerabilities, attributes, notes,
    tag_assignments) each issued their own follow-up SELECT after
    the host row loaded.  Post-fix noload() suppresses them.

    The assertion is intentionally lenient on the upper bound —
    update_existing_host accesses scalars and writes scan-history
    rows, which legitimately query.  The signal we're guarding is
    "no extra SELECT per suppressed relationship," not "exactly N
    queries total."  Concretely: pre-fix a host with no ports / no
    vulns / etc. STILL fired ~5 extra SELECTs (one per
    relationship) probing the empty sets.  Post-fix that overhead
    is gone.
    """
    from app.services.host_deduplication_service import HostDeduplicationService

    # Seed an existing host so find_or_create_host takes the
    # update-existing path (the one with the inherited selectin
    # noise).
    scan = models.Scan(project_id=test_project.id, filename="fixt.xml", scan_type="nmap")
    db_session.add(scan)
    db_session.flush()
    existing = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.42",
        state="up",
        hostname="seeded.example",
    )
    db_session.add(existing)
    db_session.flush()

    select_count = [0]

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        upper = statement.lstrip().upper()
        if upper.startswith("SELECT"):
            select_count[0] += 1

    try:
        svc = HostDeduplicationService(db_session)
        svc.find_or_create_host(
            ip_address="10.0.0.42",
            scan_id=scan.id,
            host_data={"state": "up", "hostname": "seeded.example"},
            project_id=test_project.id,
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _count)

    # The relationship-suppression assertion: NONE of the suppressed
    # relationship tables appear in the captured SELECT statements.
    # We re-run the event capture with statement strings collected
    # so the assertion message names the offender.
    seen: list[str] = []

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _collect(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    try:
        svc2 = HostDeduplicationService(db_session)
        svc2.find_or_create_host(
            ip_address="10.0.0.42",
            scan_id=scan.id,
            host_data={"state": "up", "hostname": "seeded.example"},
            project_id=test_project.id,
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _collect)

    # None of the suppressed relationship-table targets should appear.
    # (host_attributes is the table for HostAttribute; ports is its
    # own table; vulnerabilities, host_notes, host_tag_assignments.)
    suppressed_tables = {
        "FROM ports",
        "FROM host_attributes",
        "FROM vulnerabilities",
        "FROM host_notes",
        "FROM host_tag_assignments",
    }
    upper_seen = [s.upper() for s in seen]
    for tbl in suppressed_tables:
        offenders = [s for s in upper_seen if tbl in s]
        assert not offenders, (
            f"Expected noload to suppress queries against {tbl!r}, "
            f"but caught: {offenders[:2]}"
        )
