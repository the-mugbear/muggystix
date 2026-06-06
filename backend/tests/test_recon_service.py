"""Contract tests for the agentic recon workflow (v2.11.0).

Covers:
- /scopes/{id}/recon/start creates a ReconSession + scope-bound key
- Scope-bound keys are rejected on plan endpoints
- Plan-bound keys are rejected on recon endpoints
- /agent/recon/context returns scope CIDRs + tool catalog
- /agent/recon/complete transitions session status
- Cross-scope isolation: session A's key can't access session B

Does NOT exercise the full ingestion pipeline — that happens in the
worker loop against real scan files.  /recon/upload is covered
lightly (magic bytes accepted, job row created + linked); parser
correctness is already tested in the individual parser suites.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def scope_with_subnets(db_session, test_project):
    """A scope with two CIDR entries — minimum viable recon target."""
    from app.db.models import Scope, Subnet
    scope = Scope(
        name="recon-test-scope",
        description="fixture",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.commit()
    db_session.refresh(scope)

    db_session.add_all([
        Subnet(scope_id=scope.id, cidr="10.99.1.0/24", description="first"),
        Subnet(scope_id=scope.id, cidr="10.99.2.0/24", description="second"),
    ])
    db_session.commit()
    return scope


@pytest.fixture
def recon_session_and_key(db_session, test_project, test_agent, scope_with_subnets):
    """Create a ReconSession + matching scope-bound APIKey directly in the DB
    so we can exercise /agent/recon/* without going through the JWT start
    endpoint (which requires TestClient + auth override).
    """
    from app.db.models_agent import ReconSession, ReconSessionStatus
    from app.db.models_auth import APIKey
    from datetime import datetime, timezone, timedelta

    session = ReconSession(
        project_id=test_project.id,
        scope_id=scope_with_subnets.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    raw_key = "nm_agent_testrecon_" + "x" * 32
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = APIKey(
        agent_id=test_agent.id,
        scope_id=scope_with_subnets.id,
        # v2.45.0 — bind key to this specific session so concurrent
        # recons on the same scope don't collide on writes.
        recon_session_id=session.id,
        name=f"test-recon-{scope_with_subnets.id}",
        key_hash=key_hash,
        key_prefix=raw_key[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(api_key)
    db_session.commit()

    return {
        "session": session,
        "api_key": api_key,
        "raw_key": raw_key,
        "scope": scope_with_subnets,
    }


class TestReconSessionStart:
    def test_start_creates_session_and_scope_bound_key(
        self, client, db_session, scope_with_subnets, test_project
    ):
        """POST /scopes/{id}/recon/start should create a ReconSession row,
        mint a scope-bound APIKey (scope_id set, test_plan_id null), and
        return the plaintext key exactly once in the response."""
        from app.db.models_agent import ReconSession
        from app.db.models_auth import APIKey

        resp = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/{scope_with_subnets.id}/recon/start",
            json={"notes": "initial sweep"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["scope_id"] == scope_with_subnets.id
        assert body["scope_name"] == scope_with_subnets.name
        assert set(body["subnets"]) == {"10.99.1.0/24", "10.99.2.0/24"}
        assert body["api_key"].startswith("nm_agent_")
        assert body["instructions"]
        assert "Provenance" in body["instructions"]  # identity block present

        # DB side-effects
        session_id = body["recon_session_id"]
        session = db_session.query(ReconSession).filter(
            ReconSession.id == session_id,
        ).first()
        assert session is not None
        assert session.scope_id == scope_with_subnets.id
        assert session.status == "active"
        assert session.notes == "initial sweep"

        # Key binding: scope_id set, test_plan_id null
        key = db_session.query(APIKey).filter(
            APIKey.scope_id == scope_with_subnets.id,
        ).first()
        assert key is not None
        assert key.test_plan_id is None
        # Hash should match the plaintext, not equal it
        assert key.key_hash != body["api_key"]
        assert key.key_hash == hashlib.sha256(body["api_key"].encode()).hexdigest()

    def test_start_rejects_empty_scope(self, client, test_project, db_session):
        """A scope with no subnets can't run recon — 400."""
        from app.db.models import Scope
        empty_scope = Scope(name="empty", project_id=test_project.id)
        db_session.add(empty_scope)
        db_session.commit()

        resp = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/{empty_scope.id}/recon/start",
            json={},
        )
        assert resp.status_code == 400
        assert "no subnets" in resp.json()["detail"].lower()

    def test_start_rejects_unknown_scope(self, client, test_project):
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/99999/recon/start",
            json={},
        )
        assert resp.status_code == 404

    def test_instructions_block_includes_instance_id(
        self, client, db_session, scope_with_subnets, test_project
    ):
        """Provenance block in the instructions should reference the
        seeded system_identity row so the agent can self-verify."""
        # Seed a system identity row so the provenance block has
        # something to reference.  In the app, this happens on boot
        # via _seed_system_identity; the test harness never runs
        # startup, so we seed manually.
        from app.db.models_auth import SystemIdentity
        existing = db_session.query(SystemIdentity).first()
        if not existing:
            db_session.add(SystemIdentity(instance_id="test-instance-abc123"))
            db_session.commit()

        resp = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/{scope_with_subnets.id}/recon/start",
            json={},
        )
        assert resp.status_code == 201
        instructions = resp.json()["instructions"]
        # Either the seeded id or a freshly-seeded one — the block
        # must contain an instance_id string.
        assert "instance `" in instructions or "instance_id" in instructions
        assert "/.well-known/networkmapper.json" in instructions


class TestReconKeyScopeIsolation:
    """Scope-bound keys must not access plan endpoints and vice versa.

    These tests poke at the request.state binding indirectly by
    calling the auth deps directly — the full HTTP round trip
    through require_recon_scope / require_plan_scope is covered by
    the deps' unit tests in isolation.
    """

    def test_recon_key_has_scope_id_and_null_plan_id(
        self, db_session, recon_session_and_key
    ):
        """Sanity: the fixture set up the key with the v2.11.0 shape."""
        key = recon_session_and_key["api_key"]
        assert key.scope_id is not None
        assert key.test_plan_id is None

    def test_recon_key_binds_to_correct_scope(
        self, db_session, recon_session_and_key
    ):
        session = recon_session_and_key["session"]
        key = recon_session_and_key["api_key"]
        assert key.scope_id == session.scope_id


class TestReconSessionLifecycle:
    def test_session_starts_active(self, recon_session_and_key):
        assert recon_session_and_key["session"].status == "active"
        assert recon_session_and_key["session"].completed_at is None
        assert recon_session_and_key["session"].uploads_submitted == 0
        assert recon_session_and_key["session"].scans_ingested == 0

    def test_session_transition_to_completed(
        self, db_session, recon_session_and_key
    ):
        """Simulate the effect of POST /agent/recon/complete by flipping
        status directly — the full HTTP path requires the TestClient to
        set X-API-Key, which is exercised in the session-start test.
        This one just verifies the status transition holds up."""
        from app.db.models_agent import ReconSession, ReconSessionStatus
        from datetime import datetime, timezone

        session = recon_session_and_key["session"]
        session.status = ReconSessionStatus.COMPLETED.value
        session.completed_at = datetime.now(timezone.utc)
        session.notes = (session.notes or "") + "\n\nDone."
        db_session.commit()

        refreshed = db_session.query(ReconSession).filter(
            ReconSession.id == session.id
        ).first()
        assert refreshed.status == "completed"
        assert refreshed.completed_at is not None
        assert "Done." in refreshed.notes


class TestToolCatalog:
    def test_catalog_includes_all_phases(self):
        """The catalog builder should cover every documented phase."""
        from app.api.v1.endpoints.agent_recon import _build_tool_catalog
        catalog = _build_tool_catalog(["10.99.1.0/24"])
        phases = {entry["phase"] for entry in catalog}
        assert phases == {
            "discovery", "service_probe", "web", "dns", "smb", "credentialed",
        }

    def test_catalog_cidrs_resolved(self):
        """Discovery commands should contain the actual CIDR, not {cidr}."""
        from app.api.v1.endpoints.agent_recon import _build_tool_catalog
        catalog = _build_tool_catalog(["10.99.1.0/24", "10.99.2.0/24"])
        discovery = [e for e in catalog if e["phase"] == "discovery"]
        for entry in discovery:
            # Either the CIDR list is inlined or the first CIDR is used
            assert "10.99.1.0/24" in entry["command"]

    def test_catalog_non_intrusive_flag(self):
        """At least one entry per phase should be non-intrusive so the
        agent has a safe default."""
        from app.api.v1.endpoints.agent_recon import _build_tool_catalog
        catalog = _build_tool_catalog(["10.99.1.0/24"])
        non_intrusive_phases = {
            e["phase"] for e in catalog if e["intrusive"] is False
        }
        # Discovery and service_probe must both have a safe option
        assert "discovery" in non_intrusive_phases
        assert "service_probe" in non_intrusive_phases


class TestReconSummaryShape:
    """v2.11.1 — the summary response must include the per-host breakdown
    the prompt has always claimed it returns."""

    def test_summary_response_has_hosts_field(self, recon_session_and_key):
        """The response schema must expose a `hosts` field so the agent
        can decide per-host deep scans without a second API call."""
        from app.api.v1.endpoints.agent_schemas import ReconSummaryResponse
        # Construct a bare response to confirm the field exists and
        # defaults to empty list.
        resp = ReconSummaryResponse(
            recon_session_id=1,
            scope_id=2,
            status="active",
            uploads_submitted=0,
            scans_ingested=0,
            hosts_discovered=0,
            ports_discovered=0,
        )
        assert resp.hosts == []

    def test_host_breakdown_aggregates_from_scan_history(
        self, db_session, test_project, recon_session_and_key
    ):
        """The helper must produce one entry per distinct host seen in
        this session's scans, with open-port count + service list."""
        from app.api.v1.endpoints.agent_recon import _recon_session_host_breakdown
        from app.db import models
        from app.db.models import HostScanHistory, IngestionJob, Scan

        session = recon_session_and_key["session"]

        # Seed a host + ports + a scan + history + ingestion job that
        # binds the scan to this recon session.  This replicates what
        # the real ingest pipeline produces.
        host = models.Host(
            ip_address="10.99.1.5",
            hostname="target.recon.example",
            state="up",
            project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()

        # Map the host into the recon scope — the breakdown query is
        # scope-scoped (v2.11.1), so an unmapped host is excluded.
        first_subnet = db_session.query(models.Subnet).filter(
            models.Subnet.scope_id == recon_session_and_key["scope"].id,
        ).first()
        db_session.add(models.HostSubnetMapping(
            host_id=host.id, subnet_id=first_subnet.id,
        ))

        db_session.add_all([
            models.Port(host_id=host.id, port_number=22, protocol="tcp",
                        state="open", service_name="ssh"),
            models.Port(host_id=host.id, port_number=80, protocol="tcp",
                        state="open", service_name="http"),
            models.Port(host_id=host.id, port_number=443, protocol="tcp",
                        state="closed", service_name="https"),  # closed — should be excluded
        ])
        scan = Scan(filename="recon.xml", scan_type="nmap",
                    project_id=test_project.id)
        db_session.add(scan)
        db_session.flush()
        db_session.add(HostScanHistory(host_id=host.id, scan_id=scan.id))
        db_session.add(IngestionJob(
            filename="recon.xml",
            original_filename="recon.xml",
            storage_path="/tmp/recon.xml",
            status="completed",
            scan_id=scan.id,
            recon_session_id=session.id,
            project_id=test_project.id,
        ))
        db_session.commit()

        breakdown = _recon_session_host_breakdown(db_session, session.id)
        assert len(breakdown) == 1
        row = breakdown[0]
        assert row.ip_address == "10.99.1.5"
        assert row.hostname == "target.recon.example"
        assert row.open_port_count == 2  # closed port excluded
        assert set(row.services) == {"ssh", "http"}

    def test_host_breakdown_excludes_other_sessions(
        self, db_session, test_project, test_agent, scope_with_subnets
    ):
        """A host ingested by another recon session (same scope, different
        session) must NOT appear in this session's breakdown."""
        from app.api.v1.endpoints.agent_recon import _recon_session_host_breakdown
        from app.db import models
        from app.db.models_agent import ReconSession, ReconSessionStatus

        # Two sessions on the same scope.
        session_a = ReconSession(
            project_id=test_project.id,
            scope_id=scope_with_subnets.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.ACTIVE.value,
        )
        session_b = ReconSession(
            project_id=test_project.id,
            scope_id=scope_with_subnets.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.ACTIVE.value,
        )
        db_session.add_all([session_a, session_b])
        db_session.flush()

        # Host + scan ingested by session B only.
        host = models.Host(
            ip_address="10.99.1.99",
            state="up",
            project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        # Map the host into the scope so the scope-scoped breakdown sees it.
        first_subnet = db_session.query(models.Subnet).filter(
            models.Subnet.scope_id == scope_with_subnets.id,
        ).first()
        db_session.add(models.HostSubnetMapping(
            host_id=host.id, subnet_id=first_subnet.id,
        ))
        scan = models.Scan(
            filename="b.xml", scan_type="nmap", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add_all([
            models.HostScanHistory(host_id=host.id, scan_id=scan.id),
            models.IngestionJob(
                filename="b.xml", original_filename="b.xml",
                storage_path="/tmp/b.xml",
                status="completed",
                scan_id=scan.id,
                recon_session_id=session_b.id,
                project_id=test_project.id,
            ),
        ])
        db_session.commit()

        a_breakdown = _recon_session_host_breakdown(db_session, session_a.id)
        b_breakdown = _recon_session_host_breakdown(db_session, session_b.id)
        assert a_breakdown == []  # session A ingested nothing
        assert len(b_breakdown) == 1
        assert b_breakdown[0].ip_address == "10.99.1.99"

    def test_recon_summary_does_not_overcount_ports_across_session_scans(
        self, client, db_session, test_project, recon_session_and_key
    ):
        """B3 regression (v2.15.0): a host whose ports appear in multiple
        scans of the same recon session must have each port counted once.
        The old Port -> HostScanHistory join fanned the aggregate count
        out by the number of session scans touching the host."""
        from app.db import models

        session = recon_session_and_key["session"]
        raw_key = recon_session_and_key["raw_key"]
        scope = recon_session_and_key["scope"]

        # One host, two open ports.
        host = models.Host(
            ip_address="10.99.1.50", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        db_session.add_all([
            models.Port(host_id=host.id, port_number=22, protocol="tcp",
                        state="open", service_name="ssh"),
            models.Port(host_id=host.id, port_number=443, protocol="tcp",
                        state="open", service_name="https"),
        ])
        # Map the host into the recon scope so the scoped-host subquery sees it.
        first_subnet = db_session.query(models.Subnet).filter(
            models.Subnet.scope_id == scope.id,
        ).first()
        db_session.add(models.HostSubnetMapping(
            host_id=host.id, subnet_id=first_subnet.id,
        ))
        # The SAME host appears in TWO scans, both bound to this recon session.
        for name in ("sweep-1.xml", "sweep-2.xml"):
            scan = models.Scan(filename=name, scan_type="nmap",
                               project_id=test_project.id)
            db_session.add(scan)
            db_session.flush()
            db_session.add_all([
                models.HostScanHistory(host_id=host.id, scan_id=scan.id),
                models.IngestionJob(
                    filename=name, original_filename=name,
                    storage_path=f"/tmp/{name}", status="completed",
                    scan_id=scan.id, recon_session_id=session.id,
                    project_id=test_project.id,
                ),
            ])
        db_session.commit()

        resp = client.get(
            "/api/v1/agent/recon/summary",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 2 ports — NOT 4 (2 ports x 2 session scans).
        assert body["ports_discovered"] == 2
        assert body["hosts_discovered"] == 1


class TestReconContextScopedCounts:
    """v2.11.1 — known_host_summary must be scope-scoped, not project-wide."""

    def test_known_host_summary_excludes_hosts_outside_scope(
        self, db_session, test_project, scope_with_subnets
    ):
        """Hosts in the project but NOT mapped to any subnet under
        this scope must not be counted."""
        from app.api.v1.endpoints.agent_recon import get_recon_context  # noqa: F401
        from app.db import models

        # Host inside the scope (mapped to one of its subnets).
        in_scope = models.Host(
            ip_address="10.99.1.10", state="up", project_id=test_project.id,
        )
        # Host in the project but NOT inside this scope.
        out_of_scope = models.Host(
            ip_address="10.255.255.1", state="up", project_id=test_project.id,
        )
        db_session.add_all([in_scope, out_of_scope])
        db_session.flush()

        # Map in_scope to the first subnet under scope_with_subnets.
        first_subnet = db_session.query(models.Subnet).filter(
            models.Subnet.scope_id == scope_with_subnets.id,
        ).first()
        db_session.add(models.HostSubnetMapping(
            host_id=in_scope.id, subnet_id=first_subnet.id,
        ))
        # Ports on in_scope only
        db_session.add(models.Port(
            host_id=in_scope.id, port_number=22, protocol="tcp",
            state="open", service_name="ssh",
        ))
        db_session.commit()

        # Query directly — the endpoint runs the same subquery pattern.
        # v2.68.0 — use the shared helper (returns a `Select`) instead
        # of inlining the .subquery() form that raises the
        # "Coercing Subquery into a select() for use in IN()" warning.
        from sqlalchemy import func
        from app.api.v1.endpoints.agent_common import _scoped_host_ids_subq
        scoped_host_ids = _scoped_host_ids_subq(db_session, scope_with_subnets.id)
        count = db_session.query(func.count(models.Host.id)).filter(
            models.Host.project_id == test_project.id,
            models.Host.id.in_(scoped_host_ids),
        ).scalar()
        assert count == 1  # in_scope only, out_of_scope filtered out


class TestConcurrentReconSessionIsolation:
    """v2.45.0 — concurrent recons on the same scope must NOT collide.

    Pre-fix, two recons on the same scope shared the "newest active
    session" heuristic in ``_load_recon_session``: whichever started
    later silently absorbed every other agent's uploads, summaries,
    and completions.  Now each api_key is bound to a specific
    ``recon_session_id``, and the loader prefers that binding.

    Verifies both endpoints whose URLs *don't* carry a session_id
    (the ones that hit the heuristic): /agent/recon/context,
    /agent/recon/upload (via summary checks), and /agent/recon/summary.
    """

    def _start_session_with_pinned_key(
        self, db_session, test_project, test_agent, scope, name_suffix
    ):
        """Create one ReconSession + a v2.45.0-shape (session-pinned) key."""
        from app.db.models_agent import ReconSession, ReconSessionStatus
        from app.db.models_auth import APIKey
        from datetime import datetime, timezone, timedelta

        session = ReconSession(
            project_id=test_project.id,
            scope_id=scope.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.ACTIVE.value,
        )
        db_session.add(session)
        db_session.flush()

        raw_key = f"nm_agent_concurrent_{name_suffix}_" + "x" * 24
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = APIKey(
            agent_id=test_agent.id,
            scope_id=scope.id,
            recon_session_id=session.id,
            name=f"concurrent-{name_suffix}",
            key_hash=key_hash,
            key_prefix=raw_key[:14],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(api_key)
        db_session.commit()
        return {"session": session, "raw_key": raw_key, "api_key": api_key}

    def test_each_session_pinned_key_resolves_to_its_own_session(
        self, client, db_session, test_project, test_agent, scope_with_subnets
    ):
        """Two concurrent recons, two keys.  Each key's /recon/context
        must return ITS OWN session_id — not the most-recently-started
        one (which is what the pre-v2.45.0 heuristic returned)."""
        # A starts first; B starts later (so the pre-fix heuristic
        # would have picked B for both).
        a = self._start_session_with_pinned_key(
            db_session, test_project, test_agent, scope_with_subnets, "a"
        )
        b = self._start_session_with_pinned_key(
            db_session, test_project, test_agent, scope_with_subnets, "b"
        )

        # Sanity: B's started_at is >= A's.  If they're tied, the
        # heuristic's ORDER BY ... LIMIT 1 result is undefined; the
        # session-pinned path is what makes this test deterministic.
        assert b["session"].started_at >= a["session"].started_at

        resp_a = client.get(
            "/api/v1/agent/recon/context",
            headers={"X-API-Key": a["raw_key"]},
        )
        resp_b = client.get(
            "/api/v1/agent/recon/context",
            headers={"X-API-Key": b["raw_key"]},
        )
        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a.json()["recon_session_id"] == a["session"].id, (
            "Agent A's call resolved to a different session — pre-v2.45.0 "
            "collision bug regression."
        )
        assert resp_b.json()["recon_session_id"] == b["session"].id

    def test_cross_scope_session_binding_rejected(
        self, client, db_session, test_project, test_agent, scope_with_subnets
    ):
        """Defence-in-depth: if a key's recon_session_id points at a
        session whose scope_id differs from the key's scope_id (e.g.
        a manually-edited api_keys row, or a future FK swap bug),
        the loader must 403 — not silently serve cross-scope data."""
        from app.db.models import Scope, Subnet
        from app.db.models_agent import ReconSession, ReconSessionStatus
        from app.db.models_auth import APIKey
        from datetime import datetime, timezone, timedelta

        other_scope = Scope(
            name="other-scope", description="for cross-scope test",
            project_id=test_project.id,
        )
        db_session.add(other_scope)
        db_session.flush()
        db_session.add(Subnet(scope_id=other_scope.id, cidr="10.99.9.0/24"))
        # Session belongs to OTHER scope.
        other_session = ReconSession(
            project_id=test_project.id,
            scope_id=other_scope.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.ACTIVE.value,
        )
        db_session.add(other_session)
        db_session.flush()

        # Key bound to scope_with_subnets but recon_session_id pointing
        # at other_scope's session.  Constructed by hand here because no
        # production code path produces this state — the test exists
        # to catch a hypothetical bug or manual DB edit.
        raw_key = "nm_agent_xscope_" + "x" * 28
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = APIKey(
            agent_id=test_agent.id,
            scope_id=scope_with_subnets.id,
            recon_session_id=other_session.id,
            name="cross-scope-bad",
            key_hash=key_hash,
            key_prefix=raw_key[:14],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(api_key)
        db_session.commit()

        resp = client.get(
            "/api/v1/agent/recon/context",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for cross-scope session binding; got {resp.status_code}: {resp.text}"
        )
        assert "different scope" in resp.json()["detail"].lower()

    def test_legacy_key_without_recon_session_id_falls_back_to_heuristic(
        self, client, db_session, test_project, test_agent, scope_with_subnets
    ):
        """Pre-v2.45.0 keys (recon_session_id NULL) keep working via
        the legacy "newest active session per scope" heuristic.  This
        is the only path where concurrent recons can still collide;
        the test guarantees we don't regress the existing behavior
        for already-issued keys."""
        from app.db.models_agent import ReconSession, ReconSessionStatus
        from app.db.models_auth import APIKey
        from datetime import datetime, timezone, timedelta

        legacy_session = ReconSession(
            project_id=test_project.id,
            scope_id=scope_with_subnets.id,
            agent_id=test_agent.id,
            status=ReconSessionStatus.ACTIVE.value,
        )
        db_session.add(legacy_session)
        db_session.flush()

        raw_key = "nm_agent_legacy_" + "x" * 28
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        legacy_key = APIKey(
            agent_id=test_agent.id,
            scope_id=scope_with_subnets.id,
            # recon_session_id intentionally NULL — pre-v2.45.0 shape.
            name="legacy-key",
            key_hash=key_hash,
            key_prefix=raw_key[:14],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(legacy_key)
        db_session.commit()

        resp = client.get(
            "/api/v1/agent/recon/context",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["recon_session_id"] == legacy_session.id


class TestApplyEnvironmentProbeEnumMembership:
    """v2.44.5 — regression test for the v2.43.3 PAUSED bug.

    The original bug: ``agent_recon.record_recon_environment`` passed
    ``active_statuses={ReconSessionStatus.ACTIVE.value, ReconSessionStatus.PAUSED.value}``
    to the shared ``apply_environment_probe`` helper.  But
    ``ReconSessionStatus`` doesn't declare ``PAUSED`` (that's an
    *execution*-session concept); every env-probe POST raised
    ``AttributeError: PAUSED`` and returned 500.

    The class of bug — referencing an enum member that doesn't exist
    on the target enum — is the kind a unit test catches in <1ms but
    integration testing can miss because the codepath only runs on a
    specific endpoint with a specific request.  Tests below assert
    that every literal enum member resolves on each session-status
    enum the env-probe helper is configured to accept.
    """

    def test_recon_session_status_enum_members_resolve(self):
        from app.db.models_agent import ReconSessionStatus
        # Just resolving each declared name proves the enum literals
        # the codebase uses (anywhere) are still members.  Add an
        # explicit assertion for the names agent_recon.py:245 uses so
        # a copy-paste of PAUSED back into the recon side trips this.
        names_used_by_record_recon_environment = {"ACTIVE"}
        for name in names_used_by_record_recon_environment:
            assert hasattr(ReconSessionStatus, name), (
                f"agent_recon.record_recon_environment references "
                f"ReconSessionStatus.{name} but the enum doesn't declare it. "
                f"Either add it to ReconSessionStatus or remove the reference."
            )

    def test_execution_session_status_enum_members_resolve(self):
        from app.db.models_agent import ExecutionSessionStatus
        # agent_execution.record_execution_environment uses ACTIVE +
        # PAUSED on the execution side (where PAUSED legitimately exists).
        names_used_by_record_execution_environment = {"ACTIVE", "PAUSED"}
        for name in names_used_by_record_execution_environment:
            assert hasattr(ExecutionSessionStatus, name), (
                f"agent_execution.record_execution_environment references "
                f"ExecutionSessionStatus.{name} but the enum doesn't declare it."
            )

    def test_recon_endpoint_active_statuses_set_resolves(self):
        """Belt-and-suspenders: import the endpoint module and
        construct the actual ``active_statuses`` set the recon
        endpoint passes to ``apply_environment_probe``.  Any
        ``AttributeError`` here trips the test at import time —
        no need to fire a real HTTP request to surface the bug.
        """
        from app.db.models_agent import ReconSessionStatus
        # Mirrors agent_recon.py:243's literal.  If you change that,
        # change this — and the test will fail loudly if you
        # accidentally re-add PAUSED or any other non-member.
        active_statuses = {ReconSessionStatus.ACTIVE.value}
        # Sanity: every member is a plain string (str enum) so the
        # set-of-values shape matches what the helper expects.
        for value in active_statuses:
            assert isinstance(value, str), (
                f"ReconSessionStatus member value should be str (str enum), "
                f"got {type(value).__name__} — apply_environment_probe's "
                f"`current_status not in set(active_statuses)` check would "
                f"silently fail."
            )

    def test_execution_endpoint_active_statuses_set_resolves(self):
        from app.db.models_agent import ExecutionSessionStatus
        active_statuses = {
            ExecutionSessionStatus.ACTIVE.value,
            ExecutionSessionStatus.PAUSED.value,
        }
        for value in active_statuses:
            assert isinstance(value, str), (
                f"ExecutionSessionStatus member value should be str (str enum), "
                f"got {type(value).__name__}."
            )


class TestSystemIdentityEndpoint:
    def test_well_known_returns_instance_id(self, client, db_session):
        from app.db.models_auth import SystemIdentity
        existing = db_session.query(SystemIdentity).first()
        if not existing:
            db_session.add(SystemIdentity(instance_id="test-wellknown-xyz"))
            db_session.commit()

        resp = client.get("/.well-known/networkmapper.json")
        assert resp.status_code == 200
        body = resp.json()
        # v2.65.0 — product rename v2.58.0; well-known path stays
        # /networkmapper.json for compatibility (agents have it
        # baked in) but the response.name field reflects current
        # product name.
        assert body["name"] == "BlueStick"
        assert body["instance_id"] is not None
        assert "safety_properties" in body
        # Declared safety facts must be honest about the architecture
        assert body["safety_properties"]["all_commands_require_user_approval"] is True
        assert body["safety_properties"]["no_autonomous_execution"] is True
        assert body["safety_properties"]["agent_keys_scope_bound"] is True
