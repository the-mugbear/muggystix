"""End-to-end tests for the systemic-weakness `has:` DSL family.

These are the drill-down targets for Systemic / Subnet Insights: a blind-spot
row ("SMB signing disabled — N hosts") links to `/hosts?q=has:smb_unsigned`, so
the predicate MUST resolve the same hosts the insight counts.  The judgments are
shared with the insight views via ``host_condition_sets``; here we pin the
DSL-facing behaviour and the two correctness nuances that bite:

  * NOT-of-a-condition includes hosts whose posture is unknown (NULL), not just
    the explicitly-clean ones (the NOT-IN/NULL footgun);
  * cert / weak-auth use latest-observation-wins, so a fixed weakness drops out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_confidence import NetexecResult
from app.services import host_condition_sets as HC


def _host(db, project_id, ip, **kw):
    h = models.Host(project_id=project_id, ip_address=ip, state="up", **kw)
    db.add(h)
    db.flush()
    return h


def _seed(db, project_id):
    scan = models.Scan(project_id=project_id, filename="c.xml", scan_type="nmap")
    scan2 = models.Scan(project_id=project_id, filename="c2.xml", scan_type="nmap")
    db.add_all([scan, scan2])
    db.flush()
    now = datetime.now(timezone.utc)

    eol = _host(db, project_id, "10.0.0.1", os_name="Windows XP Professional")
    smb = _host(db, project_id, "10.0.0.2", os_name="Ubuntu", smb_signing="disabled")
    smb_ok = _host(db, project_id, "10.0.0.3", os_name="Ubuntu", smb_signing="enabled")
    smb_null = _host(db, project_id, "10.0.0.4", os_name="Ubuntu")  # smb_signing NULL
    clear = _host(db, project_id, "10.0.0.5", os_name="Ubuntu")
    cert = _host(db, project_id, "10.0.0.6", os_name="Ubuntu")
    weak = _host(db, project_id, "10.0.0.7", os_name="Ubuntu")
    fixed = _host(db, project_id, "10.0.0.8", os_name="Ubuntu")  # weakness re-observed clean

    # Cleartext: open telnet (23) matches; a closed one and a non-cleartext open
    # port must NOT match.
    db.add(models.Port(host_id=clear.id, port_number=23, protocol="tcp", state="open"))
    db.add(models.Port(host_id=smb_ok.id, port_number=23, protocol="tcp", state="closed"))
    db.add(models.Port(host_id=clear.id, port_number=443, protocol="tcp", state="open"))

    # Cert: expired cert on `cert`.
    db.add(models.WebInterface(
        project_id=project_id, host_id=cert.id, scan_id=scan.id, port=443,
        url="https://10.0.0.6/", source="httpx",
        cert_not_after=now - timedelta(days=30), last_seen=now,
    ))
    # Latest-wins: `fixed` had an expired cert, then a newer clean observation
    # on the SAME url (a later scan) → must NOT count as a cert issue.
    db.add(models.WebInterface(
        project_id=project_id, host_id=fixed.id, scan_id=scan.id, port=443,
        url="https://10.0.0.8/", source="httpx",
        cert_not_after=now - timedelta(days=30), last_seen=now - timedelta(days=2),
    ))
    db.add(models.WebInterface(
        project_id=project_id, host_id=fixed.id, scan_id=scan2.id, port=443,
        url="https://10.0.0.8/", source="httpx",
        cert_not_after=now + timedelta(days=365), last_seen=now,
    ))

    # Weak auth: explicit guest login succeeds on `weak`; an unknown-username
    # success on `clear` must NOT count (only explicit weak identities do).
    db.add(NetexecResult(
        scan_id=scan.id, host_id=weak.id, protocol="smb", port=445,
        auth_success=True, username="guest", discovered_at=now,
    ))
    db.add(NetexecResult(
        scan_id=scan.id, host_id=clear.id, protocol="smb", port=445,
        auth_success=True, username=None, discovered_at=now,
    ))
    db.flush()
    return {
        "eol": eol, "smb": smb, "smb_ok": smb_ok, "smb_null": smb_null,
        "clear": clear, "cert": cert, "weak": weak, "fixed": fixed,
    }


def _ips(resp):
    assert resp.status_code == 200, resp.text
    return {item["ip_address"] for item in resp.json()["items"]}


def _q(client, project_id, q):
    return client.get(f"/api/v1/projects/{project_id}/hosts/", params={"q": q})


def test_each_condition_keyword_matches_its_hosts(client, db_session, test_project):
    _seed(db_session, test_project.id)
    pid = test_project.id
    assert _ips(_q(client, pid, "has:eol")) == {"10.0.0.1"}
    assert _ips(_q(client, pid, "has:smb_unsigned")) == {"10.0.0.2"}
    assert _ips(_q(client, pid, "has:cleartext")) == {"10.0.0.5"}
    assert _ips(_q(client, pid, "has:cert_issue")) == {"10.0.0.6"}
    assert _ips(_q(client, pid, "has:weak_auth")) == {"10.0.0.7"}


def test_not_smb_unsigned_includes_unknown_posture(client, db_session, test_project):
    """NOT has:smb_unsigned must keep hosts whose signing posture is NULL —
    they are 'not known-unsigned', not excluded by the NOT-IN/NULL footgun."""
    s = _seed(db_session, test_project.id)
    got = _ips(_q(client, test_project.id, "NOT has:smb_unsigned"))
    assert "10.0.0.2" not in got                 # the unsigned host is excluded
    assert "10.0.0.4" in got                     # NULL-posture host is kept
    assert s["smb"].ip_address == "10.0.0.2"


def test_cert_issue_is_latest_observation_wins(client, db_session, test_project):
    """A host whose newest cert observation is clean must not match, even though
    an older observation was expired."""
    _seed(db_session, test_project.id)
    assert "10.0.0.8" not in _ips(_q(client, test_project.id, "has:cert_issue"))


def test_condition_keywords_compose(client, db_session, test_project):
    _seed(db_session, test_project.id)
    # No host is both EOL and unsigned in the fixture.
    assert _ips(_q(client, test_project.id, "has:eol AND has:smb_unsigned")) == set()
    assert _ips(_q(client, test_project.id, "has:eol OR has:cleartext")) == {"10.0.0.1", "10.0.0.5"}


def test_condition_sets_helpers_match_drilldown(db_session, test_project):
    """The host_condition_sets helpers (which the systemic view also uses) and
    the DSL drill-down agree by construction — pin the id-sets directly."""
    s = _seed(db_session, test_project.id)
    pid = test_project.id
    assert HC.eol_os_host_ids(db_session, pid) == {s["eol"].id}
    assert HC.smb_unsigned_host_ids(db_session, pid) == {s["smb"].id}
    assert HC.cleartext_host_ids(db_session, pid) == {s["clear"].id}
    assert HC.cert_issue_host_ids(db_session, pid) == {s["cert"].id}
    assert HC.weak_auth_host_ids(db_session, pid) == {s["weak"].id}
