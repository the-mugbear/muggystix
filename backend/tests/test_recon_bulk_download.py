"""The recon agent surface must not require a 31 MB response (v2.241.0).

Measured before this change, one recon session holding 40,000 hosts × 3 open
ports produced a ``/agent/recon/summary`` body of 31.4 MB — ``hosts[]``
18.9 MB, ``web_targets[]`` 12.0 MB, ``live_hosts_file_content`` 0.5 MB.
That is ~7.8M tokens: it fits in no context window, so an agent calling
summary on a large session lost the run no matter how carefully it behaved.

AGENTS.md "handled" this by telling the agent not to "read or echo it
whole" — advice that cannot be followed, because receiving a tool result is
what puts it in context. So the response is now capped server-side, and the
complete data moved to streaming download endpoints an agent redirects to a
file and parses with jq/grep.

These tests pin the three properties that make that safe:
  1. the summary is bounded and *says* it is bounded (totals + flags),
  2. the downloads are complete and carry the same scope bounding,
  3. the target file is emptied rather than shortened past the cap — a
     trimmed ``-iL`` list would silently under-scan a scope.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models import HostScanHistory, IngestionJob, Scan


@pytest.fixture
def scope_with_subnets(db_session, test_project):
    from app.db.models import Scope, Subnet
    scope = Scope(name="dl-scope", description="fixture",
                  project_id=test_project.id)
    db_session.add(scope)
    db_session.commit()
    db_session.add(Subnet(scope_id=scope.id, cidr="10.99.1.0/24"))
    db_session.commit()
    return scope


@pytest.fixture
def recon_session_and_key(db_session, test_project, test_agent, scope_with_subnets):
    from app.db.models_agent import (
        AgentSessionWorkflow, ReconSession, ReconSessionStatus,
    )
    from app.db.models_auth import APIKey
    from app.services.agent_session_service import create_agent_session

    base = create_agent_session(
        db_session, workflow=AgentSessionWorkflow.RECON.value,
        project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None, scope_id=scope_with_subnets.id,
    )
    session = ReconSession(
        project_id=test_project.id, scope_id=scope_with_subnets.id,
        agent_id=test_agent.id, status=ReconSessionStatus.ACTIVE.value,
        agent_session_id=base.id,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    raw_key = "nm_agent_dlrecon_" + "x" * 32
    db_session.add(APIKey(
        agent_id=test_agent.id, agent_session_id=base.id, name="dl-recon",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        key_prefix=raw_key[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return {"session": session, "raw_key": raw_key, "scope": scope_with_subnets}


def _seed_hosts(db_session, project, scope, ips, *, session=None, ports=(22, 80)):
    """Seed hosts wired the way the ingest pipeline wires them."""
    subnet = (
        db_session.query(models.Subnet)
        .filter(models.Subnet.scope_id == scope.id).first()
    )
    scan = Scan(filename="recon.xml", scan_type="nmap", project_id=project.id)
    db_session.add(scan)
    db_session.flush()

    for ip in ips:
        host = models.Host(ip_address=ip, state="up", project_id=project.id)
        db_session.add(host)
        db_session.flush()
        db_session.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
        for p in ports:
            db_session.add(models.Port(
                host_id=host.id, port_number=p, protocol="tcp", state="open",
                service_name={22: "ssh", 80: "http", 443: "https"}.get(p, "x"),
            ))
        db_session.add(HostScanHistory(host_id=host.id, scan_id=scan.id))

    if session is not None:
        db_session.add(IngestionJob(
            filename="recon.xml", original_filename="recon.xml",
            storage_path="/tmp/recon.xml", status="completed",
            scan_id=scan.id, recon_session_id=session.id,
            project_id=project.id,
        ))
    db_session.commit()
    return scan


def _headers(bundle):
    return {"X-API-Key": bundle["raw_key"]}


# ---------------------------------------------------------------------------
# The summary is bounded, and admits it
# ---------------------------------------------------------------------------

def test_summary_caps_hosts_and_reports_the_true_total(
    client, db_session, test_project, recon_session_and_key,
):
    from app.api.v1.endpoints.agent_recon import _SUMMARY_HOST_CAP

    n = _SUMMARY_HOST_CAP + 15
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        [f"10.99.1.{i}" for i in range(1, n + 1)],
        session=recon_session_and_key["session"],
    )

    body = client.get(
        "/api/v1/agent/recon/summary", headers=_headers(recon_session_and_key),
    ).json()

    assert len(body["hosts"]) == _SUMMARY_HOST_CAP
    # The cap must not become the number the agent reports as progress.
    assert body["hosts_total"] == n
    assert body["hosts_truncated"] is True
    assert body["web_targets_truncated"] is True


def test_small_session_is_untruncated_and_keeps_the_inline_target_file(
    client, db_session, test_project, recon_session_and_key,
):
    """The common case must be unchanged — no new round trip for small runs."""
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        ["10.99.1.7", "10.99.1.8"],
        session=recon_session_and_key["session"],
    )

    body = client.get(
        "/api/v1/agent/recon/summary", headers=_headers(recon_session_and_key),
    ).json()

    assert body["hosts_total"] == 2
    assert body["hosts_truncated"] is False
    assert body["live_hosts_file_truncated"] is False
    assert body["live_hosts_file_content"] == "10.99.1.7\n10.99.1.8\n"


def test_oversize_target_file_is_emptied_not_shortened(
    client, db_session, test_project, recon_session_and_key, monkeypatch,
):
    """The dangerous truncation.

    ``live_hosts_file_content`` gets piped into ``nmap -iL``. A shortened
    list scans part of the scope while the agent reports full coverage —
    silently wrong. Emptying it makes the next tool fail loudly instead,
    and the complete list is one download away.
    """
    monkeypatch.setattr(
        "app.api.v1.endpoints.agent_recon._INLINE_FILE_HOST_CAP", 3,
    )
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        [f"10.99.1.{i}" for i in range(1, 6)],
        session=recon_session_and_key["session"],
    )

    body = client.get(
        "/api/v1/agent/recon/summary", headers=_headers(recon_session_and_key),
    ).json()

    assert body["live_hosts_file_truncated"] is True
    assert body["live_hosts_file_content"] == "", (
        "a partial -iL target file is worse than none: it under-scans the "
        "scope while the run reports success"
    )
    assert body["downloads"]["live_hosts"]["url"].endswith("/recon/live-hosts.txt")


def test_summary_points_at_the_downloads(
    client, db_session, test_project, recon_session_and_key,
):
    body = client.get(
        "/api/v1/agent/recon/summary", headers=_headers(recon_session_and_key),
    ).json()
    downloads = body["downloads"]
    assert set(downloads) == {"hosts_ndjson", "live_hosts", "web_targets"}
    for entry in downloads.values():
        # The curl string is the point — it has to write to a file, not
        # print the body into the agent's transcript.
        assert "-o " in entry["curl"], entry


# ---------------------------------------------------------------------------
# The downloads are complete
# ---------------------------------------------------------------------------

def test_hosts_ndjson_streams_every_host_one_per_line(
    client, db_session, test_project, recon_session_and_key,
):
    from app.api.v1.endpoints.agent_recon import _SUMMARY_HOST_CAP

    n = _SUMMARY_HOST_CAP + 10
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        [f"10.99.1.{i}" for i in range(1, n + 1)],
        session=recon_session_and_key["session"],
    )

    resp = client.get(
        "/api/v1/agent/recon/hosts.ndjson", headers=_headers(recon_session_and_key),
    )
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == n, "the download must NOT inherit the summary cap"

    first = json.loads(lines[0])
    assert {"host_id", "ip_address", "open_ports", "services"} <= set(first)
    assert first["open_ports"], "per-port detail must survive the streaming path"


def test_live_hosts_download_is_a_usable_target_file(
    client, db_session, test_project, recon_session_and_key,
):
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        ["10.99.1.3", "10.99.1.1", "10.99.1.2"],
        session=recon_session_and_key["session"],
    )
    resp = client.get(
        "/api/v1/agent/recon/live-hosts.txt", headers=_headers(recon_session_and_key),
    )
    assert resp.status_code == 200
    assert resp.text == "10.99.1.1\n10.99.1.2\n10.99.1.3\n"


def test_web_targets_download_yields_urls(
    client, db_session, test_project, recon_session_and_key,
):
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        ["10.99.1.4"], session=recon_session_and_key["session"],
        ports=(80, 443),
    )
    resp = client.get(
        "/api/v1/agent/recon/web-targets.txt", headers=_headers(recon_session_and_key),
    )
    assert resp.status_code == 200
    assert set(resp.text.split()) == {"http://10.99.1.4/", "https://10.99.1.4/"}


def test_downloads_keep_the_scope_bounding(
    client, db_session, test_project, test_agent, recon_session_and_key,
):
    """A streamed body must not become a way around scope isolation.

    The summary breakdown is bounded by HostSubnetMapping (v2.13.1); the
    download shares that query, and this pins it so a future refactor of
    the streaming path can't quietly widen the blast radius.
    """
    from app.db.models import Scope, Subnet

    other = Scope(name="other-scope", project_id=test_project.id)
    db_session.add(other)
    db_session.commit()
    db_session.add(Subnet(scope_id=other.id, cidr="10.55.0.0/24"))
    db_session.commit()
    _seed_hosts(
        db_session, test_project, other, ["10.55.0.9"],
        session=recon_session_and_key["session"],
    )
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"], ["10.99.1.9"],
        session=recon_session_and_key["session"],
    )

    resp = client.get(
        "/api/v1/agent/recon/live-hosts.txt", headers=_headers(recon_session_and_key),
    )
    assert "10.99.1.9" in resp.text
    assert "10.55.0.9" not in resp.text, "out-of-scope host leaked into the download"


def test_downloads_reject_a_key_without_recon_scope(client, db_session):
    resp = client.get(
        "/api/v1/agent/recon/hosts.ndjson", headers={"X-API-Key": "nm_agent_bogus"},
    )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Ordering — moved from Python into SQL, and must not be able to 500
# ---------------------------------------------------------------------------

def test_hosts_sort_numerically_not_lexicographically(
    client, db_session, test_project, recon_session_and_key,
):
    """Ordering moved into SQL; it still has to be IP order, not string
    order (which puts .100 before .2 because '1' < '2' at index 8)."""
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        ["10.99.1.100", "10.99.1.2", "10.99.1.20"],
        session=recon_session_and_key["session"],
    )
    resp = client.get(
        "/api/v1/agent/recon/live-hosts.txt", headers=_headers(recon_session_and_key),
    )
    assert resp.text.split() == ["10.99.1.2", "10.99.1.20", "10.99.1.100"]


def test_an_unparseable_ip_address_does_not_take_down_the_endpoint(
    client, db_session, test_project, recon_session_and_key,
):
    """The reason the SQL ordering is guarded by pg_input_is_valid.

    The Python sort this replaced had an explicit non-IP fallback, because
    this column has carried non-addresses (v2.13.1: ``localhost`` strings
    from httpx TLS-SAN expansion). A bare ``ip_address::inet`` would raise
    on such a row and 500 the whole request, turning one bad parse into a
    dead recon session.
    """
    _seed_hosts(
        db_session, test_project, recon_session_and_key["scope"],
        ["10.99.1.5"], session=recon_session_and_key["session"],
    )
    subnet = (
        db_session.query(models.Subnet)
        .filter(models.Subnet.scope_id == recon_session_and_key["scope"].id)
        .first()
    )
    bad = models.Host(ip_address="localhost", state="up", project_id=test_project.id)
    db_session.add(bad)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=bad.id, subnet_id=subnet.id))
    scan = (
        db_session.query(Scan).filter(Scan.project_id == test_project.id).first()
    )
    db_session.add(HostScanHistory(host_id=bad.id, scan_id=scan.id))
    db_session.commit()

    resp = client.get(
        "/api/v1/agent/recon/live-hosts.txt", headers=_headers(recon_session_and_key),
    )
    assert resp.status_code == 200, resp.text
    lines = resp.text.split()
    assert "10.99.1.5" in lines and "localhost" in lines
    # Real addresses sort ahead of the unparseable bucket.
    assert lines.index("10.99.1.5") < lines.index("localhost")
