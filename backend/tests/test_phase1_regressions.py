"""Regression tests for the v2.15.0 (Phase 1) correctness fixes.

Each test pins a bug found in the backend code review so it can't
silently come back:

- B1  recon upload attribution race  — create_job stamps recon_session_id
- B2  sanity-check unique-constraint 500 — (session, entry, method) key
- B4  test-plan cross-project visibility — actionable 404

(B3, the recon port-overcount fix, is covered in test_recon_service.py
where the recon host/scan/port fixtures already live.)
"""
from __future__ import annotations

import io

import pytest
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# B1 — IngestionService.create_job stamps recon_session_id in the row-creation
# transaction (previously a second commit, leaving a worker-race window).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_stamps_recon_session_id_atomically(
    db_session, test_project, test_agent, monkeypatch, tmp_path
):
    from app.core.config import settings
    from app.db import models
    from app.db.models_agent import ReconSession, ReconSessionStatus
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    # A real ReconSession to point the job at — recon_session_id is a real
    # FK, so a bogus integer is rejected by the database.
    scope = models.Scope(
        name="b1-scope", description="fixture", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    recon = ReconSession(
        project_id=test_project.id,
        scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    )
    db_session.add(recon)
    db_session.flush()

    svc = IngestionService()
    xml = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"></nmaprun>'
    upload = UploadFile(filename="recon-sweep.xml", file=io.BytesIO(xml))

    job = await svc.create_job(
        db=db_session,
        upload=upload,
        submitted_by_id=None,
        options={"project_id": test_project.id, "recon_session_id": recon.id},
    )

    # The FK must be set on the row the moment it is committed as 'queued'
    # — that is the whole point of the fix (no second-commit gap the
    # worker could slip through).
    assert job.recon_session_id == recon.id
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_create_job_without_recon_session_leaves_fk_null(
    db_session, test_project, monkeypatch, tmp_path
):
    """A normal human upload carries no recon_session_id — must stay null."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    svc = IngestionService()
    upload = UploadFile(
        filename="manual.xml",
        file=io.BytesIO(b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"></nmaprun>'),
    )
    job = await svc.create_job(
        db=db_session, upload=upload, submitted_by_id=None,
        options={"project_id": test_project.id},
    )
    assert job.recon_session_id is None


# ---------------------------------------------------------------------------
# B2 — host_sanity_checks uniqueness widened to (session, entry, method) so
# the execution workflow can record multiple verification methods per host.
# ---------------------------------------------------------------------------

@pytest.fixture
def execution_target(db_session, test_plan):
    """A persisted (ExecutionSession, TestPlanEntry, Host) graph so
    HostSanityCheck rows have real foreign keys to point at."""
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry, ExecutionSession, ExecutionSessionStatus,
    )
    host = models.Host(
        ip_address="10.0.0.5", state="up", project_id=test_plan.project_id,
    )
    db_session.add(host)
    db_session.flush()
    # proposed_tests=[] is the canonical "no work in plan" shape; the
    # /complete endpoint gates on this AND on the sanity-check
    # signal.  The two regression tests below exercise the
    # sanity-check gate; each one passes `no_tests_run_reason` in its
    # payload to satisfy the empty-tests gate, leaving the
    # sanity-check assertion the actual test point.
    entry = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host.id,
        priority="high",
        test_phase="enumeration",
        proposed_tests=[],
        rationale="regression-test fixture",
    )
    db_session.add(entry)
    session = ExecutionSession(
        test_plan_id=test_plan.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.flush()
    return {"session": session, "entry": entry, "host": host}


def test_sanity_check_allows_multiple_methods_per_entry(db_session, execution_target):
    """Two different methods for the same (session, entry) must both
    persist — the old (session, entry)-only constraint 500'd the second."""
    from app.db.models_agent import HostSanityCheck

    t = execution_target
    common = dict(
        execution_session_id=t["session"].id,
        entry_id=t["entry"].id,
        host_id=t["host"].id,
        target_ip="10.0.0.5",
        passed=True,
    )
    db_session.add(HostSanityCheck(method="network_context", **common))
    db_session.add(HostSanityCheck(method="reverse_dns", **common))
    db_session.commit()  # must NOT raise

    rows = (
        db_session.query(HostSanityCheck)
        .filter(HostSanityCheck.entry_id == t["entry"].id)
        .all()
    )
    assert {r.method for r in rows} == {"network_context", "reverse_dns"}


def test_sanity_check_still_unique_per_method(db_session, execution_target):
    """Uniqueness is still enforced — *per method*. Recording the same
    method twice for one entry collides."""
    from app.db.models_agent import HostSanityCheck

    t = execution_target
    common = dict(
        execution_session_id=t["session"].id,
        entry_id=t["entry"].id,
        host_id=t["host"].id,
        target_ip="10.0.0.6",
        passed=True,
        method="banner_grab",
    )
    db_session.add(HostSanityCheck(**common))
    db_session.commit()

    db_session.add(HostSanityCheck(**common))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# B4 — get_test_plan distinguishes "doesn't exist" from "exists in another
# project" so a plan generated under a different project context is no
# longer a silent dead end.
# ---------------------------------------------------------------------------

def _make_plan_in_project(db_session, project_id: int):
    from app.db.models_agent import TestPlan, TestPlanStatus
    plan = TestPlan(
        project_id=project_id,
        agent_id=None,
        version=1,
        title="cross-project fixture plan",
        status=TestPlanStatus.DRAFT.value,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def test_get_test_plan_in_wrong_project_returns_actionable_404(
    client, db_session, test_project
):
    from app.db.models_project import Project

    other = Project(name="other-project", slug="other-project", description="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    plan = _make_plan_in_project(db_session, other.id)

    # Ask for the plan from the WRONG project's URL scope.
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{plan.id}"
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "different project" in detail
    assert f"#{other.id}" in detail  # tells the user where it actually lives


def test_get_test_plan_truly_missing_returns_plain_404(client, test_project):
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/999999"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Test plan not found"


def test_get_test_plan_in_correct_project_succeeds(client, db_session, test_project):
    plan = _make_plan_in_project(db_session, test_project.id)
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{plan.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == plan.id
    assert body["project_id"] == test_project.id  # new field exposed by B4


# ---------------------------------------------------------------------------
# Batch B regressions — sanity-check enforcement on /complete, byte-cap
# truncation, brief-mode policy parity, SBOM cache invalidation.
# ---------------------------------------------------------------------------

def test_evaluate_host_policy_high_value_port_medium_vuln():
    """The brief-mode bug (#1 from the v2.21.0 review): a host with a
    medium vuln + a single service + a high-value port (SMB, RDP, ...)
    qualifies for inclusion.  Brief mode used to pass an empty port set
    and got False here; fixed version passes the real set and gets True."""
    from app.api.v1.endpoints.agent_test_plans import _evaluate_host_policy
    vc = {"critical": 0, "high": 0, "medium": 1, "low": 0}
    svcs = ["smb"]  # single service — only the high-value port can save it
    assert _evaluate_host_policy(vc, svcs, {445}) is True       # post-fix
    assert _evaluate_host_policy(vc, svcs, set()) is False      # pre-fix brief mode
    # Sanity: a critical vuln always qualifies regardless of ports.
    assert _evaluate_host_policy({"critical": 1, "high": 0, "medium": 0, "low": 0}, [], set()) is True


def test_truncate_to_byte_cap_respects_multibyte():
    """#3 from the review: pre-fix code measured bytes but sliced by chars,
    so a multi-byte string with byte length above the cap still wrote past
    it after re-encoding.  Result must always fit in ``cap`` bytes."""
    from app.api.v1.endpoints.agent_execution import _truncate_to_byte_cap

    # "✓" is 3 UTF-8 bytes.  60_000 chars × 3 = 180_000 bytes > 100_000 cap.
    text = "✓" * 60_000
    cap = 100_000
    out = _truncate_to_byte_cap(text, cap)
    assert len(out.encode("utf-8")) <= cap
    assert out.endswith("--- OUTPUT TRUNCATED ---")

    # Under-cap input is returned unchanged.
    short = "abc"
    assert _truncate_to_byte_cap(short, cap) == short

    # Empty input is a no-op.
    assert _truncate_to_byte_cap("", cap) == ""


def test_sbom_cache_invalidates_on_app_version_change(monkeypatch, tmp_path):
    """#4: the cache used to key only on manifest mtimes, so an
    ``app_version`` bump that didn't touch requirements.txt /
    package-lock.json kept serving the stale envelope."""
    from app.services import sbom_service

    # Isolate from any cache the suite has primed and from the real
    # manifest paths (so we don't depend on whether package-lock is
    # mounted into the test container).
    monkeypatch.setattr(sbom_service, "_BACKEND_REQUIREMENTS_PATH", tmp_path / "requirements.txt")
    monkeypatch.setattr(sbom_service, "_FRONTEND_LOCK_PATH", tmp_path / "package-lock.json")
    monkeypatch.setattr(sbom_service, "_cache", None)
    monkeypatch.setattr(sbom_service, "_cache_key", None)

    a = sbom_service.get_sbom("a.b.c")
    assert a["app_version"] == "a.b.c"
    b = sbom_service.get_sbom("x.y.z")
    assert b["app_version"] == "x.y.z"


# ---------------------------------------------------------------------------
# #2 — completion now requires either a passing sanity check OR an explicit
# override_reason.  Visibility-only mode (v2.17.1) just annotated the
# omission; v2.22.0 enforces the audit-trail invariant.
# ---------------------------------------------------------------------------

@pytest.fixture
def plan_agent_key(db_session, test_agent, test_plan):
    """Mint a plan-scoped APIKey so the agent endpoints accept us."""
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.db.models_auth import APIKey
    raw = "nm_agent_complete_test_" + "z" * 24
    api_key = APIKey(
        agent_id=test_agent.id,
        test_plan_id=test_plan.id,
        name=f"plan-{test_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(api_key)
    db_session.commit()
    return {"raw": raw, "row": api_key}


def test_planning_context_with_candidates_returns_sample_host(client, db_session, test_project, test_plan, plan_agent_key):
    """Regression: GET /agent/test-plans/{id}/context 500'd when the plan
    had >=1 candidate host — the entry-template builder read
    ``candidates[0].host_id``, but CandidateHost's field is ``id``.  With
    a candidate present (a host with an open port) the endpoint must 200
    and the sample host id must be the real host id."""
    from app.db import models
    host = models.Host(project_id=test_project.id, ip_address="10.6.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    # Zero-port hosts are excluded from candidates by default — give it an open port.
    db_session.add(models.Port(host_id=host.id, port_number=445, protocol="tcp", state="open"))
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/context",
        headers={"X-API-Key": plan_agent_key["raw"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entry_template"]["host_id"] == host.id


def test_archive_plan_abandons_non_terminal(client, test_project, test_plan):
    """A non-terminal (approved) plan can be abandoned → ARCHIVED; a second
    abandon of the now-terminal plan is rejected with 400."""
    base = f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}/archive"
    resp = client.post(base, json={"reason": "client descoped this segment"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "archived"
    # Already terminal → 400.
    assert client.post(base, json={}).status_code == 400


def test_complete_rejects_without_sanity_check_or_override(client, execution_target, plan_agent_key, test_plan):
    """No passing HostSanityCheck and no override_reason → 400."""
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{execution_target['entry'].id}/complete",
        headers={"X-API-Key": plan_agent_key["raw"]},
        json={"findings_summary": "no findings", "overall_status": "completed"},
    )
    assert resp.status_code == 400, resp.text
    assert "sanity" in resp.json()["detail"].lower()


def test_complete_accepts_with_override_reason(client, execution_target, plan_agent_key, test_plan):
    """No passing sanity check but an explicit override_reason → accepted,
    and the reason is echoed in the response for the audit trail."""
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{execution_target['entry'].id}/complete",
        headers={"X-API-Key": plan_agent_key["raw"]},
        json={
            "findings_summary": "host offline",
            "overall_status": "completed",
            "override_reason": "target stopped responding before verification banner-grab",
            # v2.65.0 — proposed_tests=[] gate now active; pass an
            # explicit reason so the test exercises only the
            # sanity-check override path it was written to verify.
            "no_tests_run_reason": "fixture entry has no proposed tests",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sanity_check_missing"] is True
    assert "target stopped responding" in body["override_reason"]


def test_complete_accepts_with_passing_sanity_check(client, execution_target, plan_agent_key, test_plan, db_session):
    """A passing sanity check on the entry → completion succeeds without
    needing an override."""
    from app.db.models_agent import HostSanityCheck
    db_session.add(HostSanityCheck(
        execution_session_id=execution_target["session"].id,
        entry_id=execution_target["entry"].id,
        host_id=execution_target["host"].id,
        method="banner_grab",
        target_ip="10.0.0.5",
        passed=True,
    ))
    db_session.commit()
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{execution_target['entry'].id}/complete",
        headers={"X-API-Key": plan_agent_key["raw"]},
        json={
            "findings_summary": "host verified, no findings",
            "overall_status": "completed",
            # See sibling test_complete_accepts_with_override_reason —
            # fixture entry's proposed_tests=[] needs the empty-tests
            # gate satisfied so the sanity-check assertions are the
            # actual test point.
            "no_tests_run_reason": "fixture entry has no proposed tests",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sanity_check_missing"] is False
    assert body["sanity_checks_passed"] == 1


# ---------------------------------------------------------------------------
# v2.23.0 — environment probe.  Recon and execution sessions each carry a
# per-session, per-user probe blob plus four audit columns.  Verify the
# write endpoints persist correctly and the read endpoints echo back the
# stored data.  Verify the two sessions are isolated (a user posting to
# their session does not poison another user's session on the same plan).
# ---------------------------------------------------------------------------

@pytest.fixture
def recon_session(db_session, test_project, test_agent):
    """An active ReconSession with its own scope, ready for the probe test."""
    from app.db import models
    from app.db.models_agent import ReconSession, ReconSessionStatus
    scope = models.Scope(
        name="env-probe-scope", description="fixture", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    session = ReconSession(
        project_id=test_project.id,
        scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return {"scope": scope, "session": session}


@pytest.fixture
def recon_agent_key(db_session, test_agent, recon_session):
    """A scope-bound APIKey for the recon session above."""
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.db.models_auth import APIKey
    raw = "nm_agent_recon_envprobe_" + "k" * 24
    api_key = APIKey(
        agent_id=test_agent.id,
        scope_id=recon_session["scope"].id,
        name=f"recon-{recon_session['scope'].id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(api_key)
    db_session.commit()
    return raw


@pytest.fixture
def execution_session_with_key(db_session, test_agent, test_plan):
    """An active ExecutionSession + plan-scoped APIKey pair."""
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    from app.db.models_auth import APIKey
    session = ExecutionSession(
        test_plan_id=test_plan.id,
        agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.flush()
    raw = "nm_agent_exec_envprobe_" + "e" * 24
    api_key = APIKey(
        agent_id=test_agent.id,
        test_plan_id=test_plan.id,
        name=f"plan-{test_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(api_key)
    db_session.commit()
    db_session.refresh(session)
    return {"session": session, "key": raw}


_PROBE_BODY = {
    "os_family": "linux",
    "os_release": "Kali rolling",
    "arch": "x86_64",
    "shell": "bash",
    "python": "/usr/bin/python3",
    "python_version": "Python 3.11.4",
    "wsl_available": False,
    "tools_available": {"nmap": True, "masscan": True, "httpx": True, "dig": True},
    "notes": "fixture probe",
}


def test_execution_environment_persists_and_is_echoed(
    client, execution_session_with_key, test_plan, db_session,
):
    """Round-trip: POST probe → read /execution-context → see the probe in `environment`."""
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    # Make the plan executable (the context endpoint demands approved/in_progress).
    test_plan.status = "approved"
    db_session.commit()

    resp = client.post(
        f"/api/v1/agent/execution-sessions/{es.id}/environment",
        headers={"X-API-Key": key, "X-Plan-Id": str(test_plan.id)},  # plan_id only via path on get; key is what matters
        json=_PROBE_BODY,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == es.id
    assert body["session_type"] == "execution"
    assert body["probed_at"] is not None
    assert body["environment"]["os_family"] == "linux"
    assert body["environment"]["tools_available"]["nmap"] is True

    # Probe row is on the session record itself.
    db_session.refresh(es)
    assert es.environment is not None
    assert es.environment_probed_at is not None
    assert es.environment_probed_by_user_id is not None  # populated from agent.owner_id
    # ExecutionContextResponse should now echo it back.
    ctx = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["environment"]["os_family"] == "linux"


def test_recon_environment_persists_and_is_echoed(
    client, recon_session, recon_agent_key, db_session,
):
    """Same round-trip for the recon workflow."""
    rs = recon_session["session"]

    resp = client.post(
        f"/api/v1/agent/recon/sessions/{rs.id}/environment",
        headers={"X-API-Key": recon_agent_key},
        json={**_PROBE_BODY, "os_family": "windows", "powershell_execution_policy": "RemoteSigned"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_type"] == "recon"
    assert body["environment"]["powershell_execution_policy"] == "RemoteSigned"

    db_session.refresh(rs)
    assert rs.environment_probed_by_user_id is not None  # audit chain stamped

    ctx = client.get(
        "/api/v1/agent/recon/context",
        headers={"X-API-Key": recon_agent_key},
    )
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["environment"]["os_family"] == "windows"


def test_execution_probe_rejects_other_users_session(
    client, db_session, test_project, execution_session_with_key,
):
    """Another agent's plan-scoped key must not be able to overwrite this
    session's environment.  The scoped key chain (key → plan → agent →
    owner) is enforced by require_plan_scope + the agent_id check in the
    handler."""
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.db.models_auth import APIKey, User
    from app.db.models_agent import Agent, TestPlan, TestPlanStatus
    es = execution_session_with_key["session"]

    # Build a second user + agent + plan + plan-scoped key.  Two separate
    # users on the same project can both work, but they cannot reach each
    # other's sessions.
    # Explicit id sidesteps the conftest test_user fixture (id=1), which
    # leaves the users-id sequence pointing at 1 on Postgres.
    intruder = User(
        id=9999, username="intruder", email="i@example.com",
        hashed_password="$2b$12$dummy", role="analyst", is_active=True,
        must_change_password=False,
    )
    db_session.add(intruder)
    db_session.flush()
    intruder_agent = Agent(
        name="intruder-agent", project_id=test_project.id,
        owner_id=intruder.id, description="fixture", is_active=True,
    )
    db_session.add(intruder_agent)
    db_session.flush()
    # Version 1 is already taken by the test_plan fixture, so use 2.
    intruder_plan = TestPlan(
        project_id=test_project.id, agent_id=intruder_agent.id,
        version=2, title="intruder's plan", status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(intruder_plan)
    db_session.flush()
    raw = "nm_agent_intruder_" + "x" * 28
    api_key = APIKey(
        agent_id=intruder_agent.id,
        test_plan_id=intruder_plan.id,
        name=f"plan-{intruder_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(api_key)
    db_session.commit()

    # Intruder's key is scoped to intruder_plan.  Try to write to the
    # victim's execution session id — require_plan_scope sees plan_id
    # in the path mismatch is N/A here (the path is the session id, not
    # a plan id), so the in-handler check (session.test_plan.agent_id !=
    # caller.agent.id) is the one that must catch this.  403 expected.
    resp = client.post(
        f"/api/v1/agent/execution-sessions/{es.id}/environment",
        headers={"X-API-Key": raw},
        json=_PROBE_BODY,
    )
    # Either 403 (handler-side check) or 404 (the scoped_plan_id !=
    # session.test_plan_id catches it before the handler runs) is
    # acceptable.  What matters is "not 200" and "no row written".
    assert resp.status_code in (403, 404), resp.text
    db_session.refresh(es)
    assert es.environment is None  # victim row untouched


def test_environment_summary_accepts_extras():
    """The schema declares extra='allow' so an agent can surface
    observed facts beyond the fixed shape without a schema bump."""
    from app.api.v1.endpoints.agent_schemas import EnvironmentSummary
    env = EnvironmentSummary(
        os_family="linux",
        tools_available={"nmap": True},
        # Extras: kernel version, observed AV agent — not in the model.
        kernel="6.5.0-kali",
        observed_av="none",
    )
    dumped = env.model_dump()
    assert dumped["kernel"] == "6.5.0-kali"
    assert dumped["observed_av"] == "none"


def test_prompt_version_bumped_for_environment_probe():
    """PROMPT_VERSION must reflect the new probe contract so agents
    using older prompts can tell they're on the wrong version."""
    from app.services.agent_prompt_service import PROMPT_VERSION
    # v2.23.0 raised the floor to 1.10.0 — anything lower is stale.
    major, minor, _ = PROMPT_VERSION.split(".")
    assert (int(major), int(minor)) >= (1, 10), PROMPT_VERSION


# ---------------------------------------------------------------------------
# v2.24.0 — agent API call log.  Middleware writes one row per inbound
# /agent/* request that authenticated as an agent; the human-facing
# /projects/{id}/test-plans/{plan_id}/api-activity endpoint serves them
# back so a user can audit "did the agent query the right hosts?".
# ---------------------------------------------------------------------------

def test_middleware_helpers_extract_host_ids_and_target_ips():
    """The cheap reference-extraction helpers are the heart of the
    "did the agent query the right hosts?" query.  Pin their behaviour."""
    from app.services.agent_api_log_service import (
        _coerce_int_list, _extract_target_ips, _collect_referenced_ids,
        _strip_sensitive_fields, _summarise_body,
    )

    # Comma-separated string of ids — both the path-param and query-
    # string call patterns rely on this.
    assert _coerce_int_list("1,2,3") == [1, 2, 3]
    assert _coerce_int_list("1, 2, 2, 3") == [1, 2, 3]
    assert _coerce_int_list(42) == [42]
    assert _coerce_int_list([1, "2", "x"]) == [1, 2]
    assert _coerce_int_list(None) == []

    # IP extraction from arbitrary nested input.
    assert _extract_target_ips({"target_ip": "10.0.0.5"}) == ["10.0.0.5"]
    assert _extract_target_ips({"a": ["10.0.0.5", "10.0.0.5", "10.0.0.6"]}) == [
        "10.0.0.5", "10.0.0.6",
    ]
    # Random strings don't get false-positive'd.
    assert "not-an-ip" not in _extract_target_ips({"x": "not-an-ip"})

    # Aggregated path + query + body extraction.
    host_ids, entry_ids, ips = _collect_referenced_ids(
        path_params={"entry_id": "7"},
        query_params={"host_ids": "1,2,3"},
        body_json={"target_ip": "10.0.0.5", "host_id": 4},
    )
    assert sorted(host_ids) == [1, 2, 3, 4]
    assert entry_ids == [7]
    assert ips == ["10.0.0.5"]

    # Sensitive fields are stripped from captured bodies (defence in
    # depth — agents never put their key in the body, but we strip
    # anyway).
    stripped = _strip_sensitive_fields({"api_key": "secret", "host_id": 5})
    assert stripped == {"api_key": "***", "host_id": 5}

    # Body summarisation honours the cap and the content type.
    big = b"x" * (10 * 1024)
    summarised = _summarise_body(big, "application/json")
    assert summarised["_truncated"] is True
    assert summarised["_size"] == len(big)
    # Multipart bodies skip payload capture entirely (file uploads).
    summarised = _summarise_body(b"x" * 10, "multipart/form-data; boundary=abc")
    assert summarised["_multipart"] is True


def test_middleware_records_agent_request_against_plan(
    client, execution_session_with_key, test_plan, db_session, test_project,
):
    """End-to-end: an agent calls /agent/test-plans/{id}/execution-context
    with an API key, the middleware writes one row, the human-facing
    list endpoint returns it.  Verifies wiring + the cross-cut: a real
    request stamps agent_id, test_plan_id, execution_session_id,
    project_id, response status, method, path, duration."""
    from app.db.models_agent import AgentApiCall
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    # Approve the plan so /execution-context is willing to serve.
    test_plan.status = "approved"
    db_session.commit()

    # An agent-side call goes through the middleware.
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key, "User-Agent": "fixture-agent/1.0"},
    )
    assert resp.status_code == 200, resp.text

    # Middleware wrote one row.
    rows = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.test_plan_id == test_plan.id)
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.method == "GET"
    assert row.path.endswith(f"/agent/test-plans/{test_plan.id}/execution-context")
    assert row.status_code == 200
    assert row.test_plan_id == test_plan.id
    assert row.execution_session_id == es.id
    assert row.project_id == test_plan.project_id
    assert row.user_agent == "fixture-agent/1.0"
    assert row.duration_ms is not None
    # Path-template captured for grouping.
    assert "{plan_id}" in (row.path_template or "")

    # Human-facing endpoint surfaces it.
    list_resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}/api-activity"
    )
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    assert body["total"] >= 1
    assert any(
        item["path_template"] and "{plan_id}" in item["path_template"]
        for item in body["items"]
    )


def test_middleware_captures_mutation_body_and_references_hosts(
    client, execution_session_with_key, test_plan, db_session, test_project,
):
    """A POST (mutation) gets its body summarised and any host_id / IP
    references parsed out so the host-filter on the activity endpoint
    works."""
    from app.db.models_agent import AgentApiCall
    from app.db import models
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    # An entry + host so the sanity-check endpoint accepts the call.
    host = models.Host(
        ip_address="10.0.0.42", state="up", project_id=test_plan.project_id,
    )
    db_session.add(host)
    db_session.flush()
    from app.db.models_agent import TestPlanEntry
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration", proposed_tests=[], rationale="fixture",
    )
    db_session.add(entry)
    test_plan.status = "approved"
    db_session.commit()
    db_session.refresh(entry)

    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{entry.id}/sanity-check",
        headers={"X-API-Key": key},
        json={
            "method": "banner_grab",
            "target_ip": "10.0.0.42",
            "passed": True,
            "details": "ok",
        },
    )
    assert resp.status_code == 201, resp.text

    row = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.method == "POST",
                AgentApiCall.test_plan_id == test_plan.id)
        .order_by(AgentApiCall.created_at.desc())
        .first()
    )
    assert row is not None
    # Body summary captured (mutation body, under the cap, JSON parsed).
    assert row.request_body_summary["method"] == "banner_grab"
    assert row.request_body_summary["target_ip"] == "10.0.0.42"
    # Host-reference index populated from path + body.
    assert row.referenced_entry_ids == [entry.id]
    assert "10.0.0.42" in (row.referenced_target_ips or [])

    # Host-id filter on the activity endpoint returns this row.
    filtered = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}/api-activity",
        params={"target_ip": "10.0.0.42"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 1
    assert all(
        "10.0.0.42" in (item.get("referenced_target_ips") or [])
        for item in filtered.json()["items"]
    )


def test_api_activity_endpoints_enforce_project_membership(
    client, test_plan, test_project, db_session,
):
    """Cross-tenant IDOR regression: the per-plan and per-recon-session
    api-activity list endpoints must authorise via project membership
    (get_current_project), not merely authenticate.  A non-admin user who
    is not a member of the project must get 403; previously they could read
    another tenant's agent audit log (target IPs, request bodies) using the
    path project_id alone.  A member of the same project still gets 200.
    """
    from datetime import datetime, timezone
    from app.main import app
    from app.api.v1.endpoints.auth import get_current_user
    from app.db.models_auth import User, UserRole
    from app.db.models_project import ProjectMembership

    outsider = User(
        # Explicit high id: test_user hardcodes id=1 without advancing the
        # Postgres sequence, so an id-less insert would collide on users_pkey.
        id=4242,
        username="idor-outsider",
        email="idor-outsider@example.com",
        full_name="Outsider",
        hashed_password="x",
        role=UserRole.MEMBER,  # not admin → no membership bypass
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(outsider)
    db_session.commit()
    db_session.refresh(outsider)

    app.dependency_overrides[get_current_user] = lambda: outsider

    plan_url = (
        f"/api/v1/projects/{test_project.id}"
        f"/test-plans/{test_plan.id}/api-activity"
    )
    recon_url = (
        f"/api/v1/projects/{test_project.id}/recon-sessions/1/api-activity"
    )

    # Non-member → 403 on both endpoints.
    assert client.get(plan_url).status_code == 403
    assert client.get(recon_url).status_code == 403

    # Grant membership → the same user now passes the authz gate (200).
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=outsider.id, role="viewer",
    ))
    db_session.commit()
    assert client.get(plan_url).status_code == 200


def test_api_activity_owner_attribution_and_mine_filter(
    client, test_plan, test_project, test_agent, db_session,
):
    """Each activity row carries owner/agent attribution (joined from
    Agent.owner), and ?mine=true restricts to the current user's own
    agents — so one operator's calls aren't lost in a project-wide
    firehose.  The client fixture authenticates as test-admin, who owns
    test_agent; a second agent owned by another user must be excluded
    under mine=true but visible (attributed) in the default 'all' view."""
    from datetime import datetime, timezone
    from app.db.models_agent import Agent, AgentApiCall
    from app.db.models_auth import User, UserRole

    other = User(
        id=7777, username="other-owner", email="other-owner@example.com",
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
        is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.flush()
    other_agent = Agent(
        name="other-agent", project_id=test_project.id,
        owner_id=other.id, is_active=True,
    )
    db_session.add(other_agent)
    db_session.flush()

    for ag in (test_agent, other_agent):
        db_session.add(AgentApiCall(
            agent_id=ag.id, project_id=test_project.id,
            test_plan_id=test_plan.id, method="GET", path="/x",
            status_code=200, duration_ms=1,
            created_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    url = (
        f"/api/v1/projects/{test_project.id}"
        f"/test-plans/{test_plan.id}/api-activity"
    )

    # Default 'all' view: both rows, each attributed to its owner + agent.
    allr = client.get(url).json()
    assert allr["total"] == 2
    assert {i["owner_username"] for i in allr["items"]} == {"test-admin", "other-owner"}
    assert all(i["agent_name"] for i in allr["items"])

    # mine=true (caller is test-admin): only test_agent's row.
    mine = client.get(url, params={"mine": "true"}).json()
    assert mine["total"] == 1
    assert mine["items"][0]["owner_username"] == "test-admin"


def test_middleware_skips_unauthenticated_agent_requests(
    client, test_plan, db_session,
):
    """A bad API key gets a 401 — and crucially produces NO log row.
    The audit log must only reflect successful agent attribution."""
    from app.db.models_agent import AgentApiCall
    before = db_session.query(AgentApiCall).count()
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": "nm_agent_definitely_not_real_key"},
    )
    assert resp.status_code == 401
    db_session.expire_all()
    assert db_session.query(AgentApiCall).count() == before


def test_purge_older_than_drops_old_rows(db_session, execution_session_with_key, test_plan):
    """The retention helper deletes rows older than the cutoff and
    returns the count.  Manually-aged rows simulate days-old activity
    so the test runs in milliseconds."""
    from datetime import datetime, timedelta, timezone
    from app.db.models_agent import AgentApiCall
    from app.services.agent_api_log_service import purge_older_than

    old = datetime.now(timezone.utc) - timedelta(days=120)
    new = datetime.now(timezone.utc) - timedelta(days=5)
    for ts in (old, old, new):
        db_session.add(AgentApiCall(
            agent_id=execution_session_with_key["session"].agent_id,
            project_id=test_plan.project_id,
            test_plan_id=test_plan.id,
            method="GET", path="/api/v1/agent/test-plans/x",
            status_code=200, duration_ms=10,
            created_at=ts,
        ))
    db_session.commit()

    deleted = purge_older_than(db_session, days=90)
    assert deleted == 2
    remaining = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.test_plan_id == test_plan.id)
        .count()
    )
    assert remaining == 1  # the 5-day-old row survives


# ---------------------------------------------------------------------------
# v2.25.0 — review-driven hardening pass.  Each test pins a specific
# finding from the cross-functional code review so it can't drift back.
# ---------------------------------------------------------------------------

def test_execution_env_probe_rejects_recon_scoped_key(
    client, db_session, test_project, test_agent, test_plan,
):
    """Critical #1: a recon-scoped key (scoped_scope_id set,
    scoped_plan_id null) used to bypass the plan check in the execution
    environment-probe handler and could write into the wrong workflow.
    Now explicitly rejected with 403."""
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.db import models
    from app.db.models_auth import APIKey
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        ReconSession, ReconSessionStatus,
    )

    # The victim execution session (under the same agent).
    es = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(es)
    db_session.flush()

    # A scope + active recon session for the same agent.
    scope = models.Scope(name="auth-hole-scope", description="fixture",
                        project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    rs = ReconSession(project_id=test_project.id, scope_id=scope.id,
                     agent_id=test_agent.id,
                     status=ReconSessionStatus.ACTIVE.value)
    db_session.add(rs)
    db_session.flush()

    # A *recon-scoped* API key — scope_id set, test_plan_id null.
    raw = "nm_agent_recon_auth_hole_" + "r" * 24
    db_session.add(APIKey(
        agent_id=test_agent.id, scope_id=scope.id,
        name=f"recon-{scope.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()

    # Attempt to write the victim session's environment with the
    # recon-scoped key.  Must be refused.
    resp = client.post(
        f"/api/v1/agent/execution-sessions/{es.id}/environment",
        headers={"X-API-Key": raw},
        json={"os_family": "linux", "tools_available": {"nmap": True}},
    )
    assert resp.status_code == 403, resp.text
    assert "reconnaissance" in resp.json()["detail"].lower()
    db_session.refresh(es)
    assert es.environment is None  # victim untouched


def test_complete_rejects_unknown_overall_status(
    client, db_session, test_plan, execution_session_with_key,
):
    """Critical #2: overall_status was `str = "completed"` so a typo or
    arbitrary value would land in the DB and disappear from progress
    queries that only recognise canonical terminal states.  Now a
    strict enum; unknown values → 422."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry, HostSanityCheck
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.99", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(test_plan_id=test_plan.id, host_id=host.id,
                         priority="high", test_phase="enumeration",
                         proposed_tests=[], rationale="fixture")
    db_session.add(entry)
    db_session.flush()  # populate entry.id before referencing it
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="banner_grab", target_ip="10.0.0.99", passed=True,
    ))
    test_plan.status = "approved"
    db_session.commit()

    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{entry.id}/complete",
        headers={"X-API-Key": key},
        json={"overall_status": "definitely-not-a-real-status",
              "findings_summary": "test"},
    )
    assert resp.status_code == 422, resp.text


def test_complete_rejects_when_proposed_tests_have_zero_results(
    client, db_session, test_plan, execution_session_with_key,
):
    """Critical #3: completion accepted an entry with proposed tests
    but zero TestExecutionResult rows — silently dropping the
    documented per-test workflow.  Now refused unless the caller
    passes ``no_tests_run_reason``."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry, HostSanityCheck
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.77", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    # An entry WITH proposed tests but no results recorded.
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[{"tool": "nmap", "description": "service scan",
                        "command": "nmap -sV {ip}"}],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.flush()  # populate entry.id before referencing it
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="banner_grab", target_ip="10.0.0.77", passed=True,
    ))
    test_plan.status = "approved"
    db_session.commit()

    # Without the override → 400.
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{entry.id}/complete",
        headers={"X-API-Key": key},
        json={"findings_summary": "no tests run"},
    )
    assert resp.status_code == 400, resp.text
    assert "no_tests_run_reason" in resp.json()["detail"]

    # With the override → 200, reason echoed in the audit body.
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{entry.id}/complete",
        headers={"X-API-Key": key},
        json={"findings_summary": "host went offline before any test ran",
              "no_tests_run_reason": "target stopped responding mid-run"},
    )
    assert resp.status_code == 200, resp.text
    assert "stopped responding" in resp.json()["no_tests_run_reason"]


def test_execution_context_sanity_check_uses_any_passed(
    client, db_session, test_plan, execution_session_with_key,
):
    """Critical #4: with multiple sanity-check rows per entry (different
    methods), the old dict comprehension overwrote nondeterministically.
    A host with one passing + one failing check now consistently reports
    sanity_check_passed=True under the "any passed" rule."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry, HostSanityCheck
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.33", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(test_plan_id=test_plan.id, host_id=host.id,
                         priority="high", test_phase="enumeration",
                         proposed_tests=[], rationale="fixture")
    db_session.add(entry)
    db_session.flush()  # populate entry.id before referencing it
    # Two checks, different methods, mixed pass/fail.  Any-passed rule
    # → True; all-passed rule would be False; last-row-wins would be
    # nondeterministic.
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="banner_grab", target_ip="10.0.0.33", passed=True,
    ))
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="reverse_dns", target_ip="10.0.0.33", passed=False,
    ))
    test_plan.status = "approved"
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text
    host_block = next(
        h for h in resp.json()["hosts"] if h["entry_id"] == entry.id
    )
    assert host_block["sanity_check_passed"] is True


def test_execution_context_coerces_string_proposed_tests(
    client, db_session, test_plan, execution_session_with_key,
):
    """Critical #5: proposed_tests historically accepted Union[str,
    ProposedTest], but the renderer dropped every non-dict item.  Old
    plans with bare-string tests appeared to have zero executable tests.
    Now strings are coerced to a structured shape with the index
    preserved."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.55", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    # Mixed-shape proposed_tests: one bare string (legacy), one
    # structured dict (current contract).  Both must appear in the
    # execution context with correct test_index values.
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[
            "nmap -sV {ip}",   # legacy bare-string form
            {"tool": "nmap", "description": "deep scan",
             "command": "nmap -p- {ip}"},
        ],
        rationale="fixture",
    )
    db_session.add(entry)
    test_plan.status = "approved"
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text
    host_block = next(
        h for h in resp.json()["hosts"] if h["entry_id"] == entry.id
    )
    assert len(host_block["tests"]) == 2
    # The legacy string came through as a coerced dict at index 0,
    # with the {ip} placeholder resolved.
    assert host_block["tests"][0]["test_index"] == 0
    assert host_block["tests"][0]["command"] == "nmap -sV 10.0.0.55"
    # The structured dict kept its tool field at index 1.
    assert host_block["tests"][1]["test_index"] == 1
    assert host_block["tests"][1]["tool"] == "nmap"


def test_execution_progress_aggregates_full_status_enum(
    client, db_session, test_plan, execution_session_with_key,
):
    """Optimisation #7: ``tests_pending`` was ``total - executed -
    skipped``, which silently counted FAILED / NOT_APPLICABLE /
    PENDING_APPROVAL as still pending.  Now derived from
    "proposed - any-row-exists" so a future enum addition doesn't
    silently rejoin the pending bucket."""
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry, TestExecutionResult, TestExecutionStatus,
    )
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.22", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[
            {"tool": "nmap", "description": "t1"},
            {"tool": "nmap", "description": "t2"},
            {"tool": "nmap", "description": "t3"},
            {"tool": "nmap", "description": "t4"},
        ],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.flush()
    for idx, status in enumerate([
        TestExecutionStatus.EXECUTED.value,
        TestExecutionStatus.FAILED.value,
        TestExecutionStatus.NOT_APPLICABLE.value,
    ]):
        db_session.add(TestExecutionResult(
            execution_session_id=es.id, entry_id=entry.id,
            test_index=idx, status=status, is_finding=False,
        ))
    test_plan.status = "approved"
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-progress",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_tests"] == 4
    assert body["tests_executed"] == 1
    assert body["tests_failed"] == 1
    assert body["tests_not_applicable"] == 1
    # Only the one test with no row at all is genuinely pending.
    assert body["tests_pending"] == 1


def test_analyze_scope_size_collapses_empty_into_small():
    """Nit #9: ``size_bucket`` previously emitted ``"tiny"`` for empty
    scopes — undocumented and leaked through to clients consuming the
    field.  Now empty scopes return ``"small"`` so the documented
    enum (small | medium | large) is exhaustive."""
    from app.api.v1.endpoints.agent_recon import _analyze_scope_size
    result = _analyze_scope_size([])
    assert result["size_bucket"] == "small"
    assert result["size_bucket"] in {"small", "medium", "large"}


# ---------------------------------------------------------------------------
# v2.26.0 — execution-path state-machine + auth-path infrastructure cleanup.
# 1. Rate limiter now reads from agent_api_calls (global across workers).
# 2. Activity stamping debounced to once per 60s.
# 3. Completion routes through TestPlanService.update_entry so history is
#    recorded and the lifecycle-timestamp path is shared with every other
#    entry update.
# ---------------------------------------------------------------------------

def test_rate_limit_uses_agent_api_call_log(
    client, db_session, test_plan, execution_session_with_key, test_agent,
):
    """The new limiter counts rows in agent_api_calls within the last 60s.
    Pre-populating that table to the limit causes the next request to 429."""
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import AgentApiCall
    key = execution_session_with_key["key"]
    test_plan.status = "approved"
    db_session.commit()

    # Bring the agent down to a tiny limit so we don't have to pre-load a
    # realistic number of rows.
    test_agent.rate_limit_rpm = 3
    db_session.commit()

    # Three rows within the 60s window → at the limit.
    now = datetime.now(timezone.utc)
    for i in range(3):
        db_session.add(AgentApiCall(
            agent_id=test_agent.id, project_id=test_plan.project_id,
            test_plan_id=test_plan.id,
            method="GET", path="/api/v1/agent/test-plans/x",
            status_code=200, duration_ms=10,
            created_at=now - timedelta(seconds=5),
        ))
    db_session.commit()

    # The 4th request hits the limiter (count == limit before the new
    # request is logged) → 429.
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 429, resp.text


def test_rate_limit_ignores_rows_outside_window(
    client, db_session, test_plan, execution_session_with_key, test_agent,
):
    """Rows older than the 60s window must not count toward the limit —
    otherwise the limit would only ever climb."""
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import AgentApiCall
    key = execution_session_with_key["key"]
    test_plan.status = "approved"
    test_agent.rate_limit_rpm = 3
    db_session.commit()

    # Three rows OUTSIDE the window — must not count.
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    for i in range(3):
        db_session.add(AgentApiCall(
            agent_id=test_agent.id, project_id=test_plan.project_id,
            test_plan_id=test_plan.id,
            method="GET", path="/api/v1/agent/test-plans/x",
            status_code=200, duration_ms=10,
            created_at=old,
        ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text


def test_rate_limit_inprocess_counter_without_db_rows(test_agent, db_session):
    """The in-process sliding-window counter must enforce the limit even
    when the agent_api_calls DB count is 0.  The audit rows the DB count
    reads are written by a *post-response* BackgroundTask, so under a burst
    on one worker the DB count lags (or reads 0 if the writer is failing)
    and the limiter would fail open exactly under load.  check_agent_rate_limit
    takes max(db_count, in-process count); this drives the in-process branch
    directly with an empty audit table.  (The autouse _reset_agent_rate_limit_state
    fixture clears the module deque between tests.)"""
    from fastapi import HTTPException
    from app.api.deps import check_agent_rate_limit

    test_agent.rate_limit_rpm = 3
    db_session.commit()

    # No AgentApiCall rows exist → db_count stays 0; only the synchronous
    # in-process deque accumulates.  First 3 pass, 4th trips 429.
    for _ in range(3):
        assert check_agent_rate_limit(agent=test_agent, db=db_session) is test_agent
    with pytest.raises(HTTPException) as exc:
        check_agent_rate_limit(agent=test_agent, db=db_session)
    assert exc.value.status_code == 429


def test_audit_log_redacts_value_shaped_secrets():
    """The by-value redaction path (_redact_secret_values) scrubs secrets
    that hide under a non-sensitive key — an agent key pasted into a recon
    command, a JWT, or a Bearer token in a free-text body.  The by-key
    stripper (_strip_sensitive_fields) cannot catch these, and the
    agent_api_calls table is surfaced to every project viewer, so a
    regression here would leak credentials to lower-privileged users."""
    from app.services.agent_api_log_service import (
        _redact_secret_values, _strip_sensitive_fields,
    )

    cmd = "curl -H 'X-API-Key: nm_agent_REALSECRETtoken123' https://h/x"
    assert "nm_agent_REALSECRETtoken123" not in _redact_secret_values(cmd)
    assert "***" in _redact_secret_values(cmd)

    jwt = "eyJhbGc.eyJzdWIiOiIxIn0.sigPART_abc-123"
    assert jwt not in _redact_secret_values(f"token={jwt}")

    assert "abc.def-TOKEN" not in _redact_secret_values(
        "Authorization: Bearer abc.def-TOKEN"
    )

    # And via the nested body walker: a secret under a benign key name
    # ("command") is still scrubbed because the walker redacts string values.
    cleaned = _strip_sensitive_fields({"command": cmd, "host_id": 5})
    assert "nm_agent_REALSECRETtoken123" not in cleaned["command"]
    assert cleaned["host_id"] == 5


def test_activity_stamp_debounced(
    client, db_session, test_plan, execution_session_with_key, test_agent,
):
    """v2.26.0 — last_used / last_activity_at only updates when the
    persisted value is older than the debounce window.  A request that
    follows a recent one must NOT advance the timestamp."""
    from datetime import datetime, timezone, timedelta
    from app.db.models_auth import APIKey
    from app.db.models_agent import Agent
    key = execution_session_with_key["key"]
    test_plan.status = "approved"
    db_session.commit()

    # Stamp both as "just used now" — well inside the debounce window.
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    api_key_row = db_session.query(APIKey).filter(
        APIKey.test_plan_id == test_plan.id
    ).first()
    api_key_row.last_used = fresh
    test_agent.last_activity_at = fresh
    db_session.commit()
    captured_key_ts = api_key_row.last_used
    captured_agent_ts = test_agent.last_activity_at

    # Drive a request that should NOT advance either timestamp.
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(api_key_row)
    db_session.refresh(test_agent)
    assert api_key_row.last_used == captured_key_ts, (
        "api_key.last_used must NOT be re-stamped within the debounce window"
    )
    assert test_agent.last_activity_at == captured_agent_ts, (
        "agent.last_activity_at must NOT be re-stamped within the debounce window"
    )


def test_activity_stamp_writes_when_stale(
    client, db_session, test_plan, execution_session_with_key, test_agent,
):
    """Conversely, a stale (or null) timestamp must be advanced on the
    next request — otherwise the value is never written at all."""
    from datetime import datetime, timezone, timedelta
    from app.db.models_auth import APIKey
    key = execution_session_with_key["key"]
    test_plan.status = "approved"
    db_session.commit()

    stale = datetime.now(timezone.utc) - timedelta(seconds=300)
    api_key_row = db_session.query(APIKey).filter(
        APIKey.test_plan_id == test_plan.id
    ).first()
    api_key_row.last_used = stale
    test_agent.last_activity_at = stale
    db_session.commit()

    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(api_key_row)
    db_session.refresh(test_agent)
    assert api_key_row.last_used > stale, (
        "api_key.last_used must be advanced once the persisted value is stale"
    )
    assert test_agent.last_activity_at > stale, (
        "agent.last_activity_at must be advanced once the persisted value is stale"
    )


def test_completion_records_history_via_service(
    client, db_session, test_plan, execution_session_with_key,
):
    """v2.26.0 — completion now routes through TestPlanService.update_entry,
    which writes a TestPlanHistory row for each changed field.  Previously
    the route handler wrote entry.status / .findings / .results_data
    inline and skipped history entirely, making "what closed this entry?"
    untraceable in the audit log."""
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry, TestExecutionResult, TestExecutionStatus,
        HostSanityCheck, TestPlanHistory,
    )
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]

    host = models.Host(ip_address="10.0.0.66", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[{"tool": "nmap", "description": "service scan"}],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.flush()
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="banner_grab", target_ip="10.0.0.66", passed=True,
    ))
    db_session.add(TestExecutionResult(
        execution_session_id=es.id, entry_id=entry.id,
        test_index=0, status=TestExecutionStatus.EXECUTED.value,
        is_finding=False,
    ))
    test_plan.status = "approved"
    db_session.commit()
    initial_completed_at = entry.completed_at

    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{entry.id}/complete",
        headers={"X-API-Key": key},
        json={"findings_summary": "all clear",
              "overall_status": "completed"},
    )
    assert resp.status_code == 200, resp.text

    # History rows recorded for the status + findings + results_data
    # transitions.  Pre-fix the audit log was silent on completion.
    history = (
        db_session.query(TestPlanHistory)
        .filter(TestPlanHistory.entry_id == entry.id)
        .all()
    )
    fields_changed = {h.field_changed for h in history if h.field_changed}
    assert "status" in fields_changed
    actions = {h.action for h in history}
    assert "status_changed" in actions

    # completed_at is now set via the service's lifecycle path.
    db_session.refresh(entry)
    assert entry.completed_at is not None
    assert entry.completed_at != initial_completed_at


# ---------------------------------------------------------------------------
# v2.27.0 — backend monolith extractions.  These tests pin the public
# surface of the new service / parser modules so a future code-move
# can't quietly drop one of the functions or change a signature.
# ---------------------------------------------------------------------------

def test_v2_27_0_host_query_module_surface():
    """host_query.py exposes the helpers the route file imports as
    aliases (build_filtered_host_query, apply_host_sorting,
    parse_subnets, make_correlated_subquery, escape_like).  Also
    exposes the SERVICE_PORT_MAPPINGS dict consumed by both the
    search path and the structured ``services=`` filter."""
    from app.services import host_query
    assert callable(host_query.build_filtered_host_query)
    assert callable(host_query.apply_host_sorting)
    assert callable(host_query.parse_subnets)
    assert callable(host_query.make_correlated_subquery)
    assert callable(host_query.escape_like)
    assert isinstance(host_query.SERVICE_PORT_MAPPINGS, dict)
    # The escape helper does NOT mangle plain values, and it DOES
    # escape SQL LIKE wildcards.
    assert host_query.escape_like("simple") == "simple"
    assert host_query.escape_like("100%") == "100\\%"
    assert host_query.escape_like("a_b") == "a\\_b"


def test_v2_27_0_host_serialization_module_surface():
    """host_serialization.py exposes the dict-builders the route file
    imports as aliases."""
    from app.services import host_serialization
    assert callable(host_serialization.build_vuln_summary)
    assert callable(host_serialization.serialize_host_base)
    assert callable(host_serialization.serialize_host_detail)
    assert callable(host_serialization.serialize_vulnerability)
    assert callable(host_serialization.vulnerability_sort_key)
    assert host_serialization.SEVERITY_ORDER["critical"] == 0
    assert host_serialization.SEVERITY_ORDER["unknown"] == 5


def test_v2_27_0_recon_planning_module_surface():
    """recon_planning_service.py exposes the four planning helpers
    extracted from agent_recon.py.  Empty scope still buckets as
    small (the v2.25.0 nit fix is preserved)."""
    from app.services import recon_planning_service as p
    assert callable(p.analyze_scope_size)
    assert callable(p.masscan_rate_for_bucket)
    assert callable(p.build_tool_catalog)
    assert callable(p.build_recommended_sequence)
    assert p.analyze_scope_size([])["size_bucket"] == "small"
    assert p.masscan_rate_for_bucket("medium") == 2500


def test_v2_27_0_recon_summary_module_surface():
    from app.services import recon_summary_service as s
    assert callable(s.recon_session_host_breakdown)
    assert callable(s.web_targets_from_hosts)
    assert callable(s.build_known_hosts_probe)
    assert s.web_targets_from_hosts([]) == []


def test_v2_27_0_content_detection_module_surface():
    """parsers/content_detection.py exposes the looks_like_*
    predicates + is_nessus_sample, all as module-level functions
    (previously bound to IngestionService).  Pin the surface so an
    accidental rename or deletion is caught.

    v2.65.0 — added `looks_like_dns_csv` to the expected set; the
    DNS-CSV detector landed sometime between v2.27.0 and now and the
    test wasn't bumped, so this pin was reading as "test broken"
    rather than "drift caught".
    """
    from app.parsers import content_detection
    public = sorted(
        x for x in dir(content_detection)
        if x.startswith("looks_like_") or x == "is_nessus_sample"
    )
    assert public == [
        "is_nessus_sample",
        "looks_like_amass",
        "looks_like_bloodhound",
        "looks_like_dirbuster",
        "looks_like_dns_csv",
        "looks_like_dnsx",  # added v2.88.0 — dnsx JSON/JSONL (closes #44)
        "looks_like_eyewitness_json",
        "looks_like_gnmap",
        "looks_like_masscan_json",
        "looks_like_masscan_list",
        "looks_like_masscan_xml",
        "looks_like_naabu",
        "looks_like_netexec",
        "looks_like_nikto",
        "looks_like_nmap_xml",  # added v2.45.1 — structural root-element check
        "looks_like_openvas",
        "looks_like_rustscan",
        "looks_like_smbmap",
    ]
    assert content_detection.looks_like_gnmap(b"# Nmap 7.94 scan initiated\nHost: 10.0.0.1")


# v2.45.1 — nmap XML mis-detected as OpenVAS when NSE captures
# openvas/greenbone strings (e.g. http-title or ssl-cert script
# output from a Greenbone-hosted scanner being scanned BY nmap).
# Operator hit this on a real engagement and had to sanitize their
# own scan output as a workaround.  Fix: detect by XML root element,
# not free-text body keywords.

# v2.49.5 — the v2.45.1 fixture used a contrived prolog order
# (decl -> DOCTYPE -> PI -> root, no comment) that happened to
# satisfy the original single-shot prolog regex.  Real ``nmap -oX``
# output is decl -> PI -> COMMENT -> root, and the comment broke
# the regex so detection fell through to the keyword fallback and
# mis-fired on greenbone/openvas substrings in NSE script output.
# The fixture now mirrors real nmap output so a future regex
# regression can't pass this test without also passing real files.
_NMAP_XML_WITH_OPENVAS_NSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<!-- Nmap 7.94SVN scan initiated Mon May 22 11:23:45 2026 as: nmap -sV -sC -oX scan.xml 10.32.56.11 -->
<nmaprun scanner="nmap" args="nmap -sV -sC 10.32.56.11" start="1715000000" version="7.94SVN">
  <host>
    <address addr="10.32.56.11" addrtype="ipv4"/>
    <ports>
      <port protocol="tcp" portid="443">
        <state state="open" reason="syn-ack"/>
        <service name="https" tunnel="ssl"/>
        <script id="http-title" output="OPENVAS Scan Report"/>
        <script id="ssl-cert" output="Subject: commonName=greenbone-host/organizationName=Greenbone AG"/>
      </port>
    </ports>
  </host>
</nmaprun>"""

_GENUINE_OPENVAS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report id="abc-123" extension="xml" content_type="text/xml" format_id="abc">
  <results count="3" filtered="2">
    <result id="r1">
      <name>Test finding</name>
      <threat>Medium</threat>
    </result>
  </results>
</report>"""

_LEGACY_OPENVAS_XML = b"""<?xml version="1.0"?>
<openvas-results>
  <result host="10.0.0.1">stuff</result>
</openvas-results>"""


def test_nmap_xml_with_openvas_in_nse_output_is_not_misdetected():
    """The bug: nmap XML mentioning openvas/greenbone in NSE script
    output got mis-routed to OpenVASParser.  After v2.45.1, the
    structural root-element check (``<nmaprun>``) wins over keyword
    matching.
    """
    from app.parsers import content_detection
    assert content_detection.looks_like_nmap_xml(_NMAP_XML_WITH_OPENVAS_NSE)
    assert not content_detection.looks_like_openvas(
        _NMAP_XML_WITH_OPENVAS_NSE, "nmap-output.xml"
    ), (
        "Regression: nmap XML with NSE-captured openvas/greenbone strings "
        "is being mis-detected as OpenVAS.  The root element is <nmaprun> "
        "— route to NmapXMLParser, not OpenVASParser."
    )


def test_genuine_openvas_xml_still_detected():
    """Defence-in-depth: tightening looks_like_openvas didn't break
    real OpenVAS XML detection."""
    from app.parsers import content_detection
    assert content_detection.looks_like_openvas(
        _GENUINE_OPENVAS_XML, "scan.xml"
    )
    assert not content_detection.looks_like_nmap_xml(_GENUINE_OPENVAS_XML)


def test_legacy_openvas_xml_still_detected():
    """Older OpenVAS exports use <openvas-results> as the root; the
    structural check covers them too."""
    from app.parsers import content_detection
    assert content_detection.looks_like_openvas(
        _LEGACY_OPENVAS_XML, "old-scan.xml"
    )


def test_v2_45_2_plan_generation_status_mapping():
    """Activity-timeline plan_generation rows show GENERATION-bounded
    status, not the plan's full lifecycle.  Pre-fix a plan that had
    moved to execution still showed plan_generation = 'in_progress',
    confusing operators into thinking the agent never finished.
    """
    from app.services.agent_session_service import _plan_generation_status
    # Agent still filling entries — in-flight.
    assert _plan_generation_status("draft") == "in_progress"
    # Agent submitted; awaiting human review.
    assert _plan_generation_status("proposed") == "submitted"
    # Post-generation states all collapse to "completed" from the
    # plan-generation timeline's perspective — execution is tracked
    # separately by its own session row.
    assert _plan_generation_status("approved") == "completed"
    assert _plan_generation_status("in_progress") == "completed"
    assert _plan_generation_status("completed") == "completed"
    # Terminal rejection / archival pass through under their own labels.
    assert _plan_generation_status("rejected") == "rejected"
    assert _plan_generation_status("archived") == "archived"
    # Unknown enum values pass through unchanged so a future
    # TestPlanStatus addition doesn't silently become "in_progress".
    assert _plan_generation_status("brand_new_state") == "brand_new_state"


def test_v2_45_2_execution_complete_endpoint_exists():
    """The agent surface must expose a /complete endpoint for
    execution sessions.  Pre-fix the model had ExecutionSessionStatus.
    COMPLETED but no code path wrote it — sessions stayed ACTIVE
    forever after the agent submitted the last entry's results."""
    from app.api.v1.endpoints import agent_execution
    # Walk the router's registered routes; complete_execution_session
    # must be registered against the POST path.
    paths = {
        (route.path, frozenset(route.methods or set()))
        for route in agent_execution.router.routes
    }
    assert (
        "/execution-sessions/{session_id}/complete",
        frozenset({"POST"}),
    ) in paths, (
        f"POST /execution-sessions/{{session_id}}/complete is missing "
        f"from the agent_execution router.  Routes registered: "
        f"{sorted(p for p, m in paths)}"
    )


def test_v2_45_2_execution_prompt_includes_session_complete_step():
    """The execution agent prompt must instruct agents to call the new
    /complete endpoint.  Pre-fix, sessions stayed ACTIVE because no
    step in the prompt told the agent to close them."""
    from app.services.agent_prompt_service import build_execution_instructions
    prompt = build_execution_instructions(
        request=None,
        plan_id=42,
        plan_title="test",
        session_id=7,
        entry_count=3,
        raw_api_key="nm_agent_xxx",
        user_label="tester",
        user_id=1,
    )
    assert "/agent/execution-sessions/7/complete" in prompt, (
        "Execution prompt must include the explicit /complete call. "
        "Pre-v2.45.2 sessions stayed `active` indefinitely after the "
        "agent submitted the last entry's results."
    )
    assert "overall_status" in prompt, (
        "Prompt must surface the completed/failed distinction so the "
        "agent picks the right terminal status."
    )


def test_xml_root_element_skips_real_nmap_prolog_with_comment():
    """v2.49.5 regression: the prolog skipper must handle the
    decl -> PI -> COMMENT -> root order that real ``nmap -oX``
    emits.  The v2.45.1 single-shot regex didn't, so detection fell
    through to the keyword fallback and re-opened the
    nmap-as-openvas bug.

    This test pins the structural behavior directly (independent of
    the higher-level detectors) so a future regex regression that
    drops comment-skipping cannot pass.
    """
    from app.parsers.content_detection import _xml_root_element
    real_nmap_prolog = b"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<!-- Nmap 7.94 scan initiated -->
<nmaprun scanner="nmap"></nmaprun>"""
    assert _xml_root_element(real_nmap_prolog) == "nmaprun", (
        "Prolog skipper must consume the <!-- Nmap ... --> comment "
        "that real ``nmap -oX`` writes before <nmaprun>."
    )

    # Defence-in-depth: also exercise BOM, multiple PIs, multiple
    # comments, and the legacy DOCTYPE-only prolog.
    bom_then_nmap = b"\xef\xbb\xbf<?xml version='1.0'?><nmaprun/>"
    assert _xml_root_element(bom_then_nmap) == "nmaprun", "BOM must be stripped before prolog matching."

    multi_prolog = b"""<?xml version="1.0"?>
<?xml-stylesheet href="a.xsl" type="text/xsl"?>
<?xml-stylesheet href="b.xsl" type="text/xsl"?>
<!-- comment 1 -->
<!-- comment 2 -->
<nmaprun/>"""
    assert _xml_root_element(multi_prolog) == "nmaprun", (
        "Prolog skipper must accept any number of PIs and comments in any order."
    )

    legacy_doctype = b"""<?xml version="1.0"?>
<!DOCTYPE nmaprun>
<nmaprun/>"""
    assert _xml_root_element(legacy_doctype) == "nmaprun"


def test_openvas_filename_still_wins_for_explicit_routing():
    """Operators who name files with explicit vendor keywords keep
    explicit routing — even when the body would otherwise not match
    a known XML shape."""
    from app.parsers import content_detection
    nondescript = b"<?xml version='1.0'?><something/>"
    assert content_detection.looks_like_openvas(nondescript, "openvas-scan.xml")
    assert content_detection.looks_like_openvas(nondescript, "greenbone-export.xml")
    assert content_detection.looks_like_openvas(nondescript, "gvm-report.xml")


def test_ingestion_dispatcher_puts_nmap_first_for_nmap_root():
    """Dispatcher integration: when the XML root is <nmaprun>,
    NmapXMLParser is the FIRST parser attempted regardless of
    keyword content elsewhere in the file."""
    # Stubbed job with the bug-trigger file content.
    from unittest.mock import MagicMock
    from app.services.ingestion_service import IngestionService
    job = MagicMock(original_filename="some-scan.xml")

    svc = IngestionService.__new__(IngestionService)  # bypass __init__
    attempts = list(svc._build_parsing_attempts(job, _NMAP_XML_WITH_OPENVAS_NSE))
    # First entry must be nmap_xml — pre-fix it was openvas_xml.
    assert attempts[0][0] == "nmap_xml", (
        f"Expected nmap_xml first; got {[a[0] for a in attempts]}.  "
        f"Regression: dispatcher is still putting OpenVASParser ahead "
        f"of NmapXMLParser when NSE script output mentions openvas/greenbone."
    )


# ---------------------------------------------------------------------------
# v2.28.0 — execution results panel.  Three new surfaces: per-entry
# execution-results endpoint, latest_execution_session on TestPlanDetail,
# and test_plan_id filter on /feedback.
# ---------------------------------------------------------------------------

def test_entry_execution_results_empty_for_unrun_plan(
    client, db_session, test_plan, test_project,
):
    """An entry with no execution session must return the stable empty
    shape — entry_id, null session_id/status, empty arrays.  The UI
    relies on this so it can render "no results yet" without branching."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry
    host = models.Host(ip_address="10.0.0.11", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration", proposed_tests=[], rationale="fixture",
    )
    db_session.add(entry)
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
        f"/entries/{entry.id}/execution-results"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entry_id"] == entry.id
    assert body["execution_session_id"] is None
    assert body["tests"] == []
    assert body["sanity_checks"] == []


def test_entry_execution_results_returns_rows_for_active_session(
    client, db_session, test_plan, test_project, execution_session_with_key,
):
    """When an active execution session exists with recorded results,
    the endpoint returns them ordered by test_index + checked_at, with
    every column the UI renders (command_run, raw_output, severity,
    is_finding, executed_at)."""
    from datetime import datetime, timezone
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry, TestExecutionResult, TestExecutionStatus,
        HostSanityCheck,
    )
    es = execution_session_with_key["session"]
    host = models.Host(ip_address="10.0.0.12", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[{"tool": "nmap", "description": "t1"}],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.flush()
    db_session.add(HostSanityCheck(
        execution_session_id=es.id, entry_id=entry.id, host_id=host.id,
        method="banner_grab", target_ip="10.0.0.12", passed=True,
        details="banner matched expected",
    ))
    # Two tests, reverse order to verify the endpoint sorts by test_index.
    for idx in (1, 0):
        db_session.add(TestExecutionResult(
            execution_session_id=es.id, entry_id=entry.id,
            test_index=idx, status=TestExecutionStatus.EXECUTED.value,
            command_run=f"nmap test{idx}",
            raw_output=f"output {idx}",
            severity="medium", is_finding=(idx == 1),
            executed_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
        f"/entries/{entry.id}/execution-results"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["execution_session_id"] == es.id
    assert body["execution_session_status"] == "active"
    assert len(body["sanity_checks"]) == 1
    assert body["sanity_checks"][0]["method"] == "banner_grab"
    assert body["sanity_checks"][0]["passed"] is True
    assert [t["test_index"] for t in body["tests"]] == [0, 1]
    assert body["tests"][0]["command_run"] == "nmap test0"
    assert body["tests"][1]["is_finding"] is True


def test_plan_detail_carries_latest_execution_session(
    client, db_session, test_plan, test_project, execution_session_with_key,
):
    """TestPlanDetail now includes latest_execution_session for the
    UI's session-summary card.  An active session must surface with
    environment-probe metadata when one's been recorded."""
    from datetime import datetime, timezone
    es = execution_session_with_key["session"]
    es.environment = {"os_family": "linux", "shell": "bash"}
    es.environment_probed_at = datetime.now(timezone.utc)
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["latest_execution_session"] is not None
    assert body["latest_execution_session"]["id"] == es.id
    assert body["latest_execution_session"]["status"] == "active"
    assert body["latest_execution_session"]["environment_os_family"] == "linux"
    assert body["latest_execution_session"]["environment_shell"] == "bash"


def test_feedback_test_plan_id_filter(client, db_session, test_plan, test_project, test_agent):
    """Admin /feedback?test_plan_id=N filters down to feedback rows
    attributed to that plan — the deep-link from TestPlanDetail relies
    on this so opening it from a plan only shows that plan's
    feedback."""
    from app.db.models_agent import AgentFeedback, AgentFeedbackStatus

    # Two feedback rows: one attributed to test_plan, one to a different
    # plan_id so we can be sure the filter is doing something.
    for plan_id_value, note in ((test_plan.id, "this plan"), (99999, "other plan")):
        db_session.add(AgentFeedback(
            project_id=test_project.id,
            agent_id=test_agent.id,
            test_plan_id=plan_id_value if plan_id_value == test_plan.id else None,
            source="in_session_execution",
            prompt_version="1.10.0",
            overall_rating=4,
            friction_notes=note,
            status=AgentFeedbackStatus.NEW.value,
        ))
    db_session.commit()

    # Unfiltered should return at least both rows.
    resp = client.get("/api/v1/feedback/")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) >= 2

    # Filtered to this plan: only the row we attributed.
    resp = client.get(f"/api/v1/feedback/?test_plan_id={test_plan.id}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["test_plan_id"] == test_plan.id
    assert rows[0]["friction_notes"] == "this plan"


# ---------------------------------------------------------------------------
# v2.28.0 — multi-execution comparison surface.  A plan can be executed
# many times (different users, agents, models); these tests pin the
# session listing endpoint, the session-id query param on
# execution-results, and the agent-attribution columns.
# ---------------------------------------------------------------------------

def test_execution_session_list_orders_active_first(
    client, db_session, test_plan, test_project, test_agent,
):
    """The list endpoint returns active sessions first, then most-
    recent started.  Picker UI relies on this ordering."""
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    # Three sessions: one paused (older), one paused (newer), one active.
    base = datetime.now(timezone.utc)
    for offset, status in (
        (-3600, ExecutionSessionStatus.PAUSED.value),
        (-1800, ExecutionSessionStatus.PAUSED.value),
        (-60, ExecutionSessionStatus.ACTIVE.value),
    ):
        db_session.add(ExecutionSession(
            test_plan_id=test_plan.id, agent_id=test_agent.id,
            status=status, started_at=base + timedelta(seconds=offset),
        ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}/execution-sessions"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    statuses = [s["status"] for s in body["sessions"]]
    # Active first, then PAUSED in newest-started order.
    assert statuses[0] == "active"
    started = [s["started_at"] for s in body["sessions"][1:]]
    assert started == sorted(started, reverse=True)


def test_entry_execution_results_session_id_param(
    client, db_session, test_plan, test_project, test_agent,
):
    """``session_id`` query param picks a specific session's results.
    Two sessions on the same plan, each with distinct results — the
    endpoint must return the right one based on the param.  Validates
    the cross-execution comparison path."""
    from app.db import models
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        TestPlanEntry, TestExecutionResult, TestExecutionStatus,
    )
    host = models.Host(ip_address="10.0.0.55", state="up",
                      project_id=test_plan.project_id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id, priority="high",
        test_phase="enumeration",
        proposed_tests=[{"tool": "nmap", "description": "t1"}],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.flush()

    # Two sessions, attributed to different models.
    s1 = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.PAUSED.value,
        generated_by_model="claude-opus-4-7", generated_by_tool="claude-code",
    )
    s2 = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        generated_by_model="gpt-5-codex", generated_by_tool="codex",
    )
    db_session.add_all([s1, s2])
    db_session.flush()

    db_session.add(TestExecutionResult(
        execution_session_id=s1.id, entry_id=entry.id, test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
        command_run="run from claude", is_finding=False,
    ))
    db_session.add(TestExecutionResult(
        execution_session_id=s2.id, entry_id=entry.id, test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
        command_run="run from codex", is_finding=True, severity="high",
    ))
    db_session.commit()

    # No session_id → defaults to the active session (s2).
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
        f"/entries/{entry.id}/execution-results"
    )
    assert resp.status_code == 200
    assert resp.json()["execution_session_id"] == s2.id
    assert resp.json()["tests"][0]["command_run"] == "run from codex"

    # Explicit session_id=s1 → the older paused session.
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
        f"/entries/{entry.id}/execution-results?session_id={s1.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_session_id"] == s1.id
    assert body["tests"][0]["command_run"] == "run from claude"

    # Wrong-plan session_id → 404 with a clear message.
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}"
        f"/entries/{entry.id}/execution-results?session_id=99999"
    )
    assert resp.status_code == 404
    assert "different plan" in resp.json()["detail"] or "not found" in resp.json()["detail"].lower()


def test_session_summary_carries_agent_attribution(
    client, db_session, test_plan, test_project, test_agent,
):
    """The session listing surfaces generated_by_model / _tool /
    prompt_version on each row so the picker can label runs by
    "claude-opus-4-7 (claude-code)" vs "gpt-5-codex (codex)"."""
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    db_session.add(ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        generated_by_model="claude-opus-4-7",
        generated_by_tool="claude-code",
        prompt_version="1.12.0",
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{test_plan.id}/execution-sessions"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    s = body["sessions"][0]
    assert s["generated_by_model"] == "claude-opus-4-7"
    assert s["generated_by_tool"] == "claude-code"
    assert s["prompt_version"] == "1.12.0"


def test_environment_probe_stamps_session_attribution(
    client, db_session, test_plan, execution_session_with_key,
):
    """POSTing the environment probe with agent_model / agent_tool /
    agent_prompt_version persists them to the execution_sessions
    row's dedicated columns (not into the JSON blob)."""
    es = execution_session_with_key["session"]
    key = execution_session_with_key["key"]
    resp = client.post(
        f"/api/v1/agent/execution-sessions/{es.id}/environment",
        headers={"X-API-Key": key},
        json={
            "os_family": "linux",
            "tools_available": {"nmap": True},
            "agent_model": "claude-opus-4-7",
            "agent_tool": "claude-code",
            "agent_prompt_version": "1.12.0",
        },
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(es)
    assert es.generated_by_model == "claude-opus-4-7"
    assert es.generated_by_tool == "claude-code"
    assert es.prompt_version == "1.12.0"
    # Attribution lives in the columns, not the JSON.
    assert "agent_model" not in (es.environment or {})


# ---------------------------------------------------------------------------
# v2.28.1 — Nessus upload regression.  The v2.22.0 parse-stats plumbing
# initialised ``parse_stats`` only in the generic-parser branch of
# _execute_parser; the Nessus branch fell through to the shared
# return block and crashed with UnboundLocalError.  Pin the fix here.
# ---------------------------------------------------------------------------

def test_execute_parser_nessus_path_initialises_parse_stats(db_session, test_project):
    """The Nessus branch of _execute_parser must not crash with
    UnboundLocalError.  Mock NessusIntegrationService.process_nessus_file
    so we exercise the branch without needing a fixture .nessus file."""
    from unittest.mock import patch
    from app.services.ingestion_service import IngestionService
    from app.services.nessus_integration_service import NessusIntegrationService
    from app.db.models import IngestionJob

    svc = IngestionService()
    job = IngestionJob(
        filename="scan.nessus",
        original_filename="scan.nessus",
        storage_path="/tmp/does-not-matter.nessus",
        status="processing",
        options={"project_id": test_project.id},
        project_id=test_project.id,
    )
    db_session.add(job)
    db_session.commit()

    def _fake_process_nessus_file(self, storage_path, filename, project_id=None):
        return {
            "success": True,
            "scan_id": 42,
            "message": "Nessus processed (mocked)",
        }

    with patch.object(
        NessusIntegrationService, "process_nessus_file",
        new=_fake_process_nessus_file,
    ):
        result = svc._execute_parser(
            db=db_session,
            job=job,
            parser_class=NessusIntegrationService,
            description="Nessus XML",
        )

    # Pre-fix: this would have raised UnboundLocalError before reaching
    # the assertion.  Post-fix: parse_stats defaults to an empty dict,
    # so the call returns the canonical shape with the two ingest-
    # quality columns at their zero floor.
    assert result["scan_id"] == 42
    assert result["tool_name"] == "Nessus"
    assert result["skipped_count"] == 0
    assert result["parser_warnings"] is None


# ---------------------------------------------------------------------------
# v2.28.2 — looks_like_httpx must accept bytes.  Pre-fix it was typed
# str but called with bytes from IngestionService._read_sample; every
# non-httpx .json upload crashed with TypeError on `startswith("{")`
# during dispatch, taking down amass/bloodhound/ffuf/naabu/feroxbuster/
# eyewitness/masscan/nikto/netexec/smbmap JSON uploads.
# ---------------------------------------------------------------------------

def test_looks_like_httpx_accepts_bytes():
    """The detector must work on bytes (production caller) without
    TypeError, and must STILL detect real httpx output by signature."""
    from app.parsers.httpx_parser import looks_like_httpx

    real_httpx = (
        b'{"timestamp":"2026-05-15T00:00:00Z","url":"https://10.0.1.5/",'
        b'"port":"443","status_code":200,"tech":["Nginx"],'
        b'"webserver":"nginx/1.18.0"}'
    )
    assert looks_like_httpx(real_httpx, "scan.jsonl") is True

    # Bytes input that's NOT httpx (an eyewitness JSON sample) must
    # return False, not crash.  Pre-fix this is where the production
    # bug fired.
    non_httpx_json = b'{"results": [{"url": "https://x/", "screenshot": "a.png"}]}'
    assert looks_like_httpx(non_httpx_json, "eyewitness_sample.json") is False


def test_looks_like_httpx_handles_arbitrary_non_httpx_bytes_without_crashing():
    """The bulk-upload regression: every non-httpx .json file was
    routed through this sniffer first.  Any TypeError here cascades
    into a parse failure for files that aren't even meant for httpx.
    Spot-check the formats seen failing in the user's bulk upload."""
    from app.parsers.httpx_parser import looks_like_httpx

    samples = {
        "amass_sample.json": b'[{"name":"a.example.com","domain":"example.com"}]',
        "bloodhound_sample.json": b'{"computers": [{"Name": "WS01"}]}',
        "ffuf_sample.json": b'{"results":[{"url":"http://x/","status":200}]}',
        "naabu_sample.json": b'{"ip":"10.0.1.5","port":443,"host":"x"}',
        "masscan_sample.json": b'{"ip":"10.0.1.5","ports":[{"port":443}]}',
        "nikto_sample.json": b'{"vulnerabilities":[{"id":"X","msg":"y"}]}',
    }
    for fname, blob in samples.items():
        # Must return a bool, NEVER raise.
        result = looks_like_httpx(blob, fname)
        assert isinstance(result, bool), f"non-bool from {fname!r}: {result!r}"


def test_looks_like_httpx_still_accepts_str_for_back_compat():
    """The old type annotation was ``str``; we accept both so any
    test or caller still passing str continues to work."""
    from app.parsers.httpx_parser import looks_like_httpx
    text_sample = (
        '{"url":"https://10.0.1.5/","status_code":200,"tech":["Nginx"],'
        '"webserver":"nginx"}'
    )
    assert looks_like_httpx(text_sample, "scan.jsonl") is True


# ---------------------------------------------------------------------------
# v2.30.0 — symmetric attribution + unified agent_sessions timeline.
# Backend prep for the v3 UI overhaul.
# ---------------------------------------------------------------------------

def test_recon_probe_stamps_session_attribution(
    client, db_session, recon_session, recon_agent_key,
):
    """POSTing the recon environment probe with agent_model /
    agent_tool / agent_prompt_version persists them to the
    recon_sessions row's dedicated columns (v2.30.0), mirroring the
    v2.28.0 behaviour on execution_sessions."""
    rs = recon_session["session"]
    resp = client.post(
        f"/api/v1/agent/recon/sessions/{rs.id}/environment",
        headers={"X-API-Key": recon_agent_key},
        json={
            "os_family": "linux",
            "tools_available": {"nmap": True, "masscan": True},
            "agent_model": "gpt-5-codex",
            "agent_tool": "codex",
            "agent_prompt_version": "1.13.0",
        },
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(rs)
    assert rs.generated_by_model == "gpt-5-codex"
    assert rs.generated_by_tool == "codex"
    assert rs.prompt_version == "1.13.0"
    # Attribution lives in the columns, not in the JSON blob.
    assert "agent_model" not in (rs.environment or {})


def test_agent_sessions_unified_timeline(
    client, db_session, test_project, test_agent, test_plan,
):
    """The unified /agent-sessions endpoint UNION-ALLs recon, plan-
    generation, and execution into one timeline ordered newest-first.
    Drives the v3 Project Activity surface."""
    from datetime import datetime, timezone, timedelta
    from app.db import models
    from app.db.models_agent import (
        ReconSession, ReconSessionStatus,
        ExecutionSession, ExecutionSessionStatus,
    )

    # Build one of each: a recon session (oldest), the existing
    # test_plan (middle), an execution session (newest).
    scope = models.Scope(
        name="unified-tl-scope", description="fixture",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    now = datetime.now(timezone.utc)
    rs = ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
        started_at=now - timedelta(hours=3),
        generated_by_model="claude-opus-4-7",
        generated_by_tool="claude-code",
    )
    db_session.add(rs)
    es = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        started_at=now - timedelta(hours=1),
        generated_by_model="gpt-5-codex",
        generated_by_tool="codex",
    )
    db_session.add(es)
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Should include all three kinds.
    kinds = [s["kind"] for s in body["sessions"]]
    assert "recon" in kinds
    assert "plan_generation" in kinds  # test_plan from the fixture
    assert "execution" in kinds
    # Attribution surfaces correctly for both recon (v2.30.0) and
    # execution (v2.28.0).
    recon_row = next(s for s in body["sessions"] if s["kind"] == "recon")
    exec_row = next(s for s in body["sessions"] if s["kind"] == "execution")
    assert recon_row["generated_by_model"] == "claude-opus-4-7"
    assert recon_row["generated_by_tool"] == "claude-code"
    assert exec_row["generated_by_model"] == "gpt-5-codex"
    assert exec_row["generated_by_tool"] == "codex"
    # Newest-first ordering: execution (1h ago) → plan (created in
    # fixture) → recon (3h ago).  Just assert recon is after exec.
    exec_idx = kinds.index("execution")
    recon_idx = kinds.index("recon")
    assert exec_idx < recon_idx


def test_agent_sessions_filter_by_model(
    client, db_session, test_project, test_agent, test_plan,
):
    """``?model=...`` narrows to sessions attributed to one model.
    Critical for the v3 "compare runs by model" workflow."""
    from datetime import datetime, timezone, timedelta
    from app.db import models
    from app.db.models_agent import (
        ReconSession, ReconSessionStatus,
        ExecutionSession, ExecutionSessionStatus,
    )
    scope = models.Scope(
        name="filter-scope", description="fixture",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    now = datetime.now(timezone.utc)
    # Two recon sessions, two different models.
    for model_id, hours_ago in (("claude-opus-4-7", 2), ("gpt-5-codex", 1)):
        db_session.add(ReconSession(
            project_id=test_project.id, scope_id=scope.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.COMPLETED.value,
            started_at=now - timedelta(hours=hours_ago),
            generated_by_model=model_id,
            generated_by_tool="claude-code" if "claude" in model_id else "codex",
        ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions"
        "?model=gpt-5-codex"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the codex recon session should appear.  (The fixture
    # test_plan has no attribution; it falls out of this filter.)
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["generated_by_model"] == "gpt-5-codex"
    assert body["sessions"][0]["kind"] == "recon"


def test_agent_sessions_by_model_tool_summary(
    client, db_session, test_project, test_agent, test_plan,
):
    """The summary endpoint groups by (model, tool) and counts kinds.
    Drives the v3 "compare models on this project" rollup card."""
    from datetime import datetime, timezone, timedelta
    from app.db import models
    from app.db.models_agent import (
        ReconSession, ReconSessionStatus,
        ExecutionSession, ExecutionSessionStatus,
    )
    scope = models.Scope(
        name="summary-scope", description="fixture",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    now = datetime.now(timezone.utc)
    # Two claude-opus sessions, one codex session.
    for spec in [
        ("claude-opus-4-7", "claude-code", "recon"),
        ("claude-opus-4-7", "claude-code", "execution"),
        ("gpt-5-codex", "codex", "recon"),
    ]:
        model_id, tool_id, kind = spec
        if kind == "recon":
            db_session.add(ReconSession(
                project_id=test_project.id, scope_id=scope.id,
                agent_id=test_agent.id,
                status=ReconSessionStatus.COMPLETED.value,
                started_at=now - timedelta(hours=2),
                generated_by_model=model_id,
                generated_by_tool=tool_id,
            ))
        else:
            db_session.add(ExecutionSession(
                test_plan_id=test_plan.id, agent_id=test_agent.id,
                status=ExecutionSessionStatus.PAUSED.value,
                started_at=now - timedelta(hours=1),
                generated_by_model=model_id,
                generated_by_tool=tool_id,
            ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool"
    )
    assert resp.status_code == 200, resp.text
    summary = {
        (r["generated_by_model"], r["generated_by_tool"]): r
        for r in resp.json()["summary"]
    }
    claude = summary[("claude-opus-4-7", "claude-code")]
    assert claude["recon"] == 1
    assert claude["execution"] == 1
    assert claude["total"] == 2
    codex = summary[("gpt-5-codex", "codex")]
    assert codex["recon"] == 1
    assert codex["total"] == 1


# ---------------------------------------------------------------------------
# v3 alpha.3 — typed source-provenance on TestPlan + status filter on
# /agent-sessions + project coverage endpoint.
# ---------------------------------------------------------------------------

def test_generate_plan_stamps_recon_session_source(
    client, db_session, test_project, test_agent,
):
    """Plans created with source_kind='recon_session' persist the FK
    and the API echoes both back on the detail response."""
    from app.db import models
    from app.db.models_agent import ReconSession, ReconSessionStatus

    scope = models.Scope(
        name="prov-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    recon = ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.COMPLETED.value,
    )
    db_session.add(recon)
    db_session.commit()
    db_session.refresh(recon)

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={
            "title": "prov plan",
            "source_kind": "recon_session",
            "source_recon_session_id": recon.id,
        },
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["plan_id"]

    detail = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{plan_id}"
    ).json()
    assert detail["source_kind"] == "recon_session"
    assert detail["source_recon_session_id"] == recon.id
    assert detail["source_host_ids"] is None
    assert detail["source_plan_id"] is None


def test_generate_plan_manual_hosts_requires_payload(client, test_project):
    """source_kind='manual_hosts' without source_host_ids → 422."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={"title": "missing payload", "source_kind": "manual_hosts"},
    )
    assert resp.status_code == 422
    assert "source_host_ids" in resp.json()["detail"]


def test_generate_plan_mutually_exclusive_payloads(client, test_project):
    """Setting both source_recon_session_id and source_host_ids → 422."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={
            "title": "conflicting",
            "source_kind": "recon_session",
            "source_recon_session_id": 1,
            "source_host_ids": [1, 2],
        },
    )
    assert resp.status_code == 422
    assert "mutually exclusive" in resp.json()["detail"]


def test_generate_plan_inferred_filter_set(client, test_project):
    """Omitting source_kind but passing filter_criteria → server infers
    'filter_set' so legacy clients get free provenance."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={
            "title": "inferred",
            "filter_criteria": {"min_severity": "high"},
        },
    )
    assert resp.status_code == 201
    plan_id = resp.json()["plan_id"]
    detail = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{plan_id}"
    ).json()
    assert detail["source_kind"] == "filter_set"


def test_generate_plan_bare_request_lands_as_unspecified(client, test_project):
    """A request with neither source_kind nor filter_criteria lands as
    'unspecified' — pre-alpha.3 callers keep working."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={"title": "bare"},
    )
    assert resp.status_code == 201
    plan_id = resp.json()["plan_id"]
    detail = client.get(
        f"/api/v1/projects/{test_project.id}/test-plans/{plan_id}"
    ).json()
    assert detail["source_kind"] == "unspecified"


def test_agent_sessions_status_filter_narrows_to_active(
    client, db_session, test_project, test_agent, test_plan,
):
    """v3 alpha.3 — ``?status=active`` returns only active sessions
    across kinds.  Drives the in-flight-runs banner."""
    from app.db import models
    from app.db.models_agent import (
        ReconSession, ReconSessionStatus,
        ExecutionSession, ExecutionSessionStatus,
    )

    scope = models.Scope(
        name="status-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    db_session.add(ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    ))
    db_session.add(ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.COMPLETED.value,
    ))
    db_session.add(ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    ))
    db_session.add(ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?status=active"
    )
    assert resp.status_code == 200, resp.text
    sessions = resp.json()["sessions"]
    # Every returned row must be active, across all kinds.
    assert all(s["status"] == "active" for s in sessions)
    kinds = {s["kind"] for s in sessions}
    assert "recon" in kinds
    assert "execution" in kinds


def test_coverage_summary_counts_hosts_by_pipeline_stage(
    client, db_session, test_project, test_plan,
):
    """Project coverage reports per-stage host counts and gap counts.

    The gap counts (hosts_no_plan, hosts_no_execution) are sticky
    fields — the UI relies on them being non-negative and consistent
    with the totals.
    """
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry, TestExecutionResult, TestExecutionStatus,
    )

    # Three hosts.  One has a plan entry, one has a plan entry +
    # execution result, one has neither (universe baseline).
    hosts = []
    for i, ip in enumerate(["10.0.1.10", "10.0.1.11", "10.0.1.12"]):
        h = models.Host(
            ip_address=ip, state="up", project_id=test_project.id,
        )
        db_session.add(h)
        hosts.append(h)
    db_session.flush()

    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    entry1 = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=hosts[0].id,
        priority="high", test_phase="enumeration",
        proposed_tests=[], rationale="cov-entry-1",
    )
    entry2 = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=hosts[1].id,
        priority="medium", test_phase="enumeration",
        proposed_tests=[], rationale="cov-entry-2",
    )
    db_session.add(entry1)
    db_session.add(entry2)
    es = ExecutionSession(
        test_plan_id=test_plan.id,
        status=ExecutionSessionStatus.COMPLETED.value,
    )
    db_session.add(es)
    db_session.flush()

    # Only entry2's host gets an execution result.
    db_session.add(TestExecutionResult(
        execution_session_id=es.id,
        entry_id=entry2.id, test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_hosts"] == 3
    assert body["hosts_with_plan_entry"] == 2
    assert body["hosts_with_execution_result"] == 1
    assert body["hosts_no_plan"] == 1
    assert body["hosts_no_execution"] == 2


def test_coverage_summary_reports_scope_breakdown(
    client, db_session, test_project,
):
    """The coverage endpoint includes a per-scope row with
    discovered-vs-scoped counts so the v3 Operations page can render
    the scope-by-scope coverage list directly."""
    from app.db import models

    scope = models.Scope(
        name="cov-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(
        scope_id=scope.id, cidr="10.99.0.0/30", description="four-IP range",
    )
    db_session.add(subnet)
    db_session.flush()

    # Two of the four IPs in the /30 are discovered.
    h1 = models.Host(
        ip_address="10.99.0.1", state="up", project_id=test_project.id,
    )
    h2 = models.Host(
        ip_address="10.99.0.2", state="up", project_id=test_project.id,
    )
    db_session.add(h1)
    db_session.add(h2)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=h1.id, subnet_id=subnet.id))
    db_session.add(models.HostSubnetMapping(host_id=h2.id, subnet_id=subnet.id))
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_scopes"] == 1
    sc = body["scopes"][0]
    assert sc["scope_name"] == "cov-scope"
    assert sc["total_scoped_ips"] == 4  # /30 → 4 addresses
    assert sc["discovered_in_scope"] == 2
    assert sc["coverage_percent"] == 50.0


# ---------------------------------------------------------------------------
# v3 alpha.6 — JWT-facing recon-session detail endpoint
# ---------------------------------------------------------------------------

def test_recon_session_detail_bundles_summary_uploads_and_plans(
    client, db_session, test_project, test_agent,
):
    """``GET /projects/{id}/recon-sessions/{session_id}`` returns the
    full bundle the v3 Recon Run Detail page consumes — summary +
    upload rows + plans whose ``source_recon_session_id`` matches."""
    from app.db import models
    from app.db.models_agent import (
        ReconSession, ReconSessionStatus,
        TestPlan, TestPlanStatus, TestPlanSourceKind,
    )

    scope = models.Scope(
        name="recon-detail-scope", description="x",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    rs = ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.COMPLETED.value,
        uploads_submitted=2,
        scans_ingested=2,
        hosts_discovered=5,
        generated_by_model="claude-opus-4-7",
        generated_by_tool="claude-code",
    )
    db_session.add(rs)
    db_session.flush()

    # Two ingestion jobs tagged with this session — one completed,
    # one failed.  The detail endpoint must surface both.
    db_session.add(models.IngestionJob(
        filename="nmap-output.xml",
        original_filename="nmap-output.xml",
        storage_path="/tmp/fixture/nmap-output.xml",
        status="completed",
        recon_session_id=rs.id,
        skipped_count=0,
    ))
    db_session.add(models.IngestionJob(
        filename="masscan-junk.json",
        original_filename="masscan-junk.json",
        storage_path="/tmp/fixture/masscan-junk.json",
        status="failed",
        recon_session_id=rs.id,
        last_error="masscan parser rejected an empty body",
    ))

    # A plan with source_recon_session_id pointed at this run —
    # should appear in the plans_generated list.
    plan = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id,
        version=1,
        title="downstream plan",
        status=TestPlanStatus.PROPOSED.value,
        source_kind=TestPlanSourceKind.RECON_SESSION.value,
        source_recon_session_id=rs.id,
    )
    db_session.add(plan)
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{rs.id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Summary section
    assert body["summary"]["id"] == rs.id
    assert body["summary"]["status"] == "completed"
    assert body["summary"]["generated_by_model"] == "claude-opus-4-7"
    assert body["summary"]["uploads_submitted"] == 2

    # Uploads section — both jobs surface with their statuses.
    upload_files = {u["filename"]: u for u in body["uploads"]}
    assert "nmap-output.xml" in upload_files
    assert "masscan-junk.json" in upload_files
    assert upload_files["masscan-junk.json"]["status"] == "failed"
    assert upload_files["masscan-junk.json"]["last_error"]

    # Plans-generated section — the alpha.3 FK is the join key.
    assert len(body["plans_generated"]) == 1
    assert body["plans_generated"][0]["plan_id"] == plan.id
    assert body["plans_generated"][0]["title"] == "downstream plan"


def test_recon_session_detail_returns_actionable_404_for_cross_project(
    client, db_session, test_project, test_agent,
):
    """A recon session under project B must 404 with an actionable
    detail when queried from project A's URL scope — same pattern as
    get_test_plan."""
    from app.db import models
    from app.db.models_agent import ReconSession, ReconSessionStatus
    from app.db.models_project import Project

    other = Project(name="other-recon-project", slug="other-recon", description="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    scope = models.Scope(name="other-scope", description="x", project_id=other.id)
    db_session.add(scope)
    db_session.flush()
    rs = ReconSession(
        project_id=other.id, scope_id=scope.id, agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    )
    db_session.add(rs)
    db_session.commit()
    db_session.refresh(rs)

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{rs.id}"
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "different project" in detail
    assert f"#{other.id}" in detail


def test_recon_session_list_filters_by_status(
    client, db_session, test_project, test_agent,
):
    """``?status=active`` narrows the list to in-flight sessions —
    drives the Operations Active Runs link-through (when a future
    alpha shows multiple runs per scope)."""
    from app.db import models
    from app.db.models_agent import ReconSession, ReconSessionStatus

    scope = models.Scope(
        name="list-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    db_session.add(ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    ))
    db_session.add(ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.COMPLETED.value,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/?status=active"
    )
    assert resp.status_code == 200, resp.text
    # v2.86.10 — list endpoints return a Paginated envelope {items,total,…}.
    rows = resp.json()["items"]
    assert all(r["status"] == "active" for r in rows)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# v3 alpha.7 — JWT-facing execution-session lookup by id
# ---------------------------------------------------------------------------

def test_execution_session_lookup_returns_bundle(
    client, db_session, test_project, test_agent, test_plan,
):
    """``GET /projects/{id}/execution-sessions/{session_id}`` returns
    the same payload shape as the plan-scoped all-entry-results endpoint
    but addressable without knowing the plan id — drives the v3 alpha.7
    /executions/:sessionId permalink page."""
    from app.db import models
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        TestPlanEntry,
    )

    host = models.Host(ip_address="10.0.2.5", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id,
        priority="high", test_phase="enumeration",
        proposed_tests=[], rationale="alpha.7 fixture",
    )
    db_session.add(entry)
    es = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        generated_by_model="claude-opus-4-7",
        generated_by_tool="claude-code",
    )
    db_session.add(es)
    db_session.commit()
    db_session.refresh(es)

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/{es.id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Top-level matches the AllEntryResultsResponse shape.
    assert body["plan_id"] == test_plan.id
    assert body["execution_session_id"] == es.id
    assert body["execution_session_status"] == "active"
    assert body["generated_by_model"] == "claude-opus-4-7"
    # Entries surface even with no results yet.
    assert len(body["entries"]) == 1
    assert body["entries"][0]["entry_id"] == entry.id
    assert body["entries"][0]["host_ip"] == "10.0.2.5"


def test_execution_session_lookup_404_for_cross_project(
    client, db_session, test_project, test_agent,
):
    """A session belonging to a plan in another project must 404 with
    the actionable cross-project detail."""
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        TestPlan, TestPlanStatus,
    )
    from app.db.models_project import Project

    other = Project(name="other-exec", slug="other-exec", description="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    other_plan = TestPlan(
        project_id=other.id, agent_id=test_agent.id,
        version=1, title="other plan",
        status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(other_plan)
    db_session.flush()
    es = ExecutionSession(
        test_plan_id=other_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
    )
    db_session.add(es)
    db_session.commit()
    db_session.refresh(es)

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/{es.id}"
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "different project" in detail
    assert f"#{other.id}" in detail


def test_execution_session_lookup_404_for_missing(client, test_project):
    """A session id that doesn't exist anywhere returns a plain 404."""
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/999999"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Execution session not found"


# ---------------------------------------------------------------------------
# v3 alpha.9 — host workflow lineage
# ---------------------------------------------------------------------------

def test_host_lineage_returns_recons_plans_and_executions(
    client, db_session, test_project, test_agent, test_plan,
):
    """``GET /projects/{id}/hosts/{host_id}/lineage`` returns the three
    cross-workflow sections so HostDetail can render a single-query
    'what's been done to this host' panel."""
    from app.db import models
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        ReconSession, ReconSessionStatus,
        TestExecutionResult, TestExecutionStatus,
        TestPlanEntry,
    )

    # Build the lineage graph for one host:
    #   - recon session #1 discovered it (HostScanHistory → Scan →
    #     IngestionJob.recon_session_id)
    #   - test plan entry references it
    #   - execution session has a result against that entry
    host = models.Host(
        ip_address="10.0.3.5", state="up", project_id=test_project.id,
    )
    db_session.add(host)
    db_session.flush()

    scope = models.Scope(
        name="lineage-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    rs = ReconSession(
        project_id=test_project.id, scope_id=scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.COMPLETED.value,
        generated_by_model="claude-opus-4-7",
    )
    db_session.add(rs)
    db_session.flush()

    # Scan + ingestion job tied to that recon session; HostScanHistory
    # links the host to the scan.
    scan = models.Scan(
        filename="lineage.xml", scan_type="nmap",
        project_id=test_project.id,
    )
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.IngestionJob(
        filename="lineage.xml",
        original_filename="lineage.xml",
        storage_path="/tmp/fixture/lineage.xml",
        status="completed",
        recon_session_id=rs.id,
        scan_id=scan.id,
    ))
    db_session.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id,
    ))

    # Plan entry against this host.
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id,
        priority="high", test_phase="enumeration",
        proposed_tests=[], rationale="lineage fixture",
    )
    db_session.add(entry)
    db_session.flush()

    # Execution session with one result + one finding for that entry.
    es = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
        generated_by_model="gpt-5-codex",
    )
    db_session.add(es)
    db_session.flush()
    db_session.add(TestExecutionResult(
        execution_session_id=es.id, entry_id=entry.id, test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
        is_finding=True, severity="critical",
        findings_summary="SQL injection",
    ))
    db_session.add(TestExecutionResult(
        execution_session_id=es.id, entry_id=entry.id, test_index=1,
        status=TestExecutionStatus.EXECUTED.value,
        is_finding=False,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/lineage"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["host_id"] == host.id
    assert body["ip_address"] == "10.0.3.5"

    # Recon row surfaces with attribution and scope name resolution.
    assert len(body["recon_sessions"]) == 1
    rrow = body["recon_sessions"][0]
    assert rrow["session_id"] == rs.id
    assert rrow["scope_name"] == "lineage-scope"
    assert rrow["generated_by_model"] == "claude-opus-4-7"

    # Plan-entry row carries entry + plan attribution.
    assert len(body["plan_entries"]) == 1
    prow = body["plan_entries"][0]
    assert prow["plan_id"] == test_plan.id
    assert prow["entry_id"] == entry.id

    # Execution row with the per-host counts (2 tests, 1 finding).
    assert len(body["execution_sessions"]) == 1
    erow = body["execution_sessions"][0]
    assert erow["execution_session_id"] == es.id
    assert erow["plan_id"] == test_plan.id
    assert erow["test_count"] == 2
    assert erow["finding_count"] == 1


def test_host_lineage_404_for_missing_host(client, test_project):
    """A host id that doesn't exist returns 404."""
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/999999/lineage"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# v3 alpha.12 — execution-session list endpoint
# ---------------------------------------------------------------------------

def test_execution_session_list_returns_per_project_rows(
    client, db_session, test_project, test_agent, test_plan,
):
    """``GET /projects/{id}/execution-sessions/`` returns project-wide
    rows with per-session result + finding counts so the v3 alpha.12
    list page can render without N+1."""
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
        TestExecutionResult, TestExecutionStatus,
        TestPlanEntry,
    )
    from app.db import models

    # Plan + entry + two sessions: one with results (one a finding,
    # one not), one with no results.
    host = models.Host(ip_address="10.0.4.5", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=test_plan.id, host_id=host.id,
        priority="high", test_phase="enumeration",
        proposed_tests=[], rationale="alpha.12 fixture",
    )
    db_session.add(entry)
    es1 = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
        generated_by_model="claude-opus-4-7",
    )
    es2 = ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        generated_by_model="gpt-5-codex",
    )
    db_session.add(es1)
    db_session.add(es2)
    db_session.flush()
    db_session.add(TestExecutionResult(
        execution_session_id=es1.id, entry_id=entry.id, test_index=0,
        status=TestExecutionStatus.EXECUTED.value, is_finding=True,
    ))
    db_session.add(TestExecutionResult(
        execution_session_id=es1.id, entry_id=entry.id, test_index=1,
        status=TestExecutionStatus.EXECUTED.value, is_finding=False,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/"
    )
    assert resp.status_code == 200, resp.text
    # v2.86.10 — list endpoints return a Paginated envelope {items,total,…}.
    rows = resp.json()["items"]
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id[es1.id]["result_count"] == 2
    assert by_id[es1.id]["finding_count"] == 1
    assert by_id[es1.id]["generated_by_model"] == "claude-opus-4-7"
    assert by_id[es1.id]["plan_title"] == "contract test plan"
    # Empty session still surfaces, with zero counts.
    assert by_id[es2.id]["result_count"] == 0
    assert by_id[es2.id]["finding_count"] == 0


def test_execution_session_list_filters_by_status_and_plan(
    client, db_session, test_project, test_agent, test_plan,
):
    """The filters apply independently and AND together — drives the
    alpha.12 filter chip strip."""
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionStatus,
    )
    db_session.add(ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    ))
    db_session.add(ExecutionSession(
        test_plan_id=test_plan.id, agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/?status=active"
    )
    assert resp.status_code == 200
    # v2.86.10 — list endpoints return a Paginated envelope {items,total,…}.
    rows = resp.json()["items"]
    assert all(r["status"] == "active" for r in rows)
    assert len(rows) == 1

    # ?test_plan_id= filter — same plan, both rows back.
    resp2 = client.get(
        f"/api/v1/projects/{test_project.id}/execution-sessions/?test_plan_id={test_plan.id}"
    )
    assert resp2.status_code == 200
    assert all(r["test_plan_id"] == test_plan.id for r in resp2.json()["items"])
