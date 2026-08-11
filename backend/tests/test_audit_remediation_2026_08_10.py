"""Regression tests for the 2026-08-10 audit remediation.

Two behaviours the audit found broken or newly added:

1. **Cancel/timeout must stop the nmap and gnmap parsers.** The cancel/timeout
   signal arrives as ``ParseFailure`` (a ``RuntimeError`` subclass) from
   ``report_progress``; the per-host ``except Exception`` used to swallow it, so
   a cancelled or timed-out parse ran to completion anyway. These pin that the
   signal now propagates.

2. **AI Assist host-edit (``write:host``)** — the new ``PATCH /agent/hosts/<id>``
   is gated by the capability AND the assigned-hosts row scope.
"""
from datetime import datetime, timezone

import pytest

from app.parsers.nmap_parser import NmapXMLParser
from app.parsers.gnmap_parser import GnmapParser
from app.services.ingestion_service import ParseFailure


# ---------------------------------------------------------------------------
# 1. Parser cancellation propagation
# ---------------------------------------------------------------------------

def _many_host_nmap_xml(n: int) -> str:
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nmaprun scanner="nmap" args="nmap -sV" start="1700000000" version="7.94">\n'
        '<scaninfo type="syn" protocol="tcp" numservices="1" services="1"/>\n'
    )
    hosts = "".join(
        f'<host><status state="up" reason="syn-ack"/>'
        f'<address addr="10.80.{i // 256}.{i % 256}" addrtype="ipv4"/>'
        f'<ports><port protocol="tcp" portid="22">'
        f'<state state="open" reason="syn-ack"/><service name="ssh"/>'
        f'</port></ports></host>\n'
        for i in range(n)
    )
    tail = '<runstats><finished time="1700000010"/></runstats>\n</nmaprun>\n'
    return head + hosts + tail


def _many_host_gnmap(n: int) -> str:
    return "".join(
        f"Host: 10.81.{i // 256}.{i % 256} ()\tPorts: 22/open/tcp//ssh///\t"
        f"Ignored State: closed (0)\n"
        for i in range(n)
    )


def test_nmap_parser_propagates_cancel_signal(
    db_session, tmp_path, test_project, monkeypatch
):
    """report_progress raises ParseFailure at the 100-host checkpoint; the
    per-host handler must NOT swallow it as a malformed-host warning."""
    import app.services.ingestion_service as ingestion

    def _boom(progress):
        raise ParseFailure("Job cancelled", user_message="Cancelled by user")

    monkeypatch.setattr(ingestion, "report_progress", _boom)

    path = tmp_path / "many.xml"
    path.write_text(_many_host_nmap_xml(150))
    parser = NmapXMLParser(db_session)
    with pytest.raises(ParseFailure):
        parser.parse_file(str(path), "many.xml", project_id=test_project.id)


def test_gnmap_parser_propagates_cancel_signal(
    db_session, tmp_path, test_project, monkeypatch
):
    import app.services.ingestion_service as ingestion

    def _boom(progress):
        raise ParseFailure("Job cancelled", user_message="Cancelled by user")

    monkeypatch.setattr(ingestion, "report_progress", _boom)

    path = tmp_path / "many.gnmap"
    path.write_text(_many_host_gnmap(150))
    parser = GnmapParser(db_session)
    with pytest.raises(ParseFailure):
        parser.parse_file(str(path), "many.gnmap", project_id=test_project.id)


# ---------------------------------------------------------------------------
# 2. Assist host-edit capability + row scope
# ---------------------------------------------------------------------------

def _make_host(db_session, project_id, ip="10.90.0.1"):
    from app.db.models import Host

    host = Host(
        project_id=project_id,
        ip_address=ip,
        hostname="old-name",
        os_name="Windows XP",
        state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


def _assign_host_to(db_session, host_id, user_id):
    from app.db.models import HostFollow, FollowStatus

    db_session.add(
        HostFollow(
            host_id=host_id,
            user_id=user_id,
            status=FollowStatus.IN_REVIEW,
            assigned_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()


def _start(client, project_id, *, can_write=False):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": "edit-host test", "can_write_assigned": can_write},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_readonly_assist_key_cannot_edit_host(client, test_project, db_session):
    host = _make_host(db_session, test_project.id)
    body = _start(client, test_project.id, can_write=False)
    resp = client.patch(
        f"/api/v1/agent/hosts/{host.id}",
        headers={"X-API-Key": body["api_key"]},
        json={"hostname": "hacked"},
    )
    assert resp.status_code == 403, resp.text


def test_granted_assist_key_edits_assigned_host(
    client, test_project, db_session, test_user
):
    host = _make_host(db_session, test_project.id)
    _assign_host_to(db_session, host.id, test_user.id)
    body = _start(client, test_project.id, can_write=True)

    resp = client.patch(
        f"/api/v1/agent/hosts/{host.id}",
        headers={"X-API-Key": body["api_key"]},
        json={"hostname": "corrected.example.com", "os_name": "Windows Server 2022"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["changed"]) == {"hostname", "os_name"}
    assert data["hostname"] == "corrected.example.com"

    from app.db.models import Host

    db_session.expire_all()
    refreshed = db_session.query(Host).filter(Host.id == host.id).first()
    assert refreshed.hostname == "corrected.example.com"
    assert refreshed.os_name == "Windows Server 2022"


def test_granted_assist_key_cannot_edit_unassigned_host(
    client, test_project, db_session
):
    """The grant is narrowed to assigned hosts — an unassigned host 403s even
    with write:host granted."""
    host = _make_host(db_session, test_project.id, ip="10.90.0.2")
    body = _start(client, test_project.id, can_write=True)
    resp = client.patch(
        f"/api/v1/agent/hosts/{host.id}",
        headers={"X-API-Key": body["api_key"]},
        json={"hostname": "nope"},
    )
    assert resp.status_code == 403, resp.text
