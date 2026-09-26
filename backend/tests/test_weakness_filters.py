"""Weakness / access flags and misconfiguration checks as structured /hosts
filters (v2.423.0) — the Hosts "+ Add filter" catalog reaches what only the
query bar's has: / check: reached before, with the same answer."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.services.host_query import WEAKNESS_FLAGS, build_filtered_host_query
from app.services.host_query_dsl import _HAS_KEYWORDS
from app.services.misconfig_checks import record_misconfig


def _seed(db, project):
    scan = models.Scan(filename="s", tool_name="nmap", project_id=project.id)
    db.add(scan)
    db.flush()
    hosts = {}
    for ip, smb in (("10.9.1.1", "not_required"), ("10.9.1.2", "required"), ("10.9.1.3", None)):
        h = models.Host(project_id=project.id, ip_address=ip, state="up", smb_signing=smb)
        db.add(h)
        db.flush()
        hosts[ip] = h
    db.add(models.Port(host_id=hosts["10.9.1.3"].id, port_number=21, protocol="tcp", state="open"))
    db.flush()
    record_misconfig(db, check_id="ftp_anonymous", host_id=hosts["10.9.1.3"].id, scan_id=scan.id,
                     source=VulnerabilitySource.NMAP, port_number=21)
    db.commit()
    return hosts


def _ips(db, user, project, **kw):
    return {h.ip_address for h in build_filtered_host_query(db, user, project_id=project.id, **kw).all()}


def test_every_flag_is_a_dsl_has_keyword():
    assert set(WEAKNESS_FLAGS) <= set(_HAS_KEYWORDS)


def test_weaknesses_filter_matches_the_dsl(db_session, test_project, test_user):
    _seed(db_session, test_project)
    structured = _ips(db_session, test_user, test_project, weaknesses="smb_unsigned")
    assert structured == {"10.9.1.1"}
    assert structured == _ips(db_session, test_user, test_project, q="has:smb_unsigned")
    # OR within the group.
    assert _ips(db_session, test_user, test_project, weaknesses="smb_unsigned,cleartext") == {"10.9.1.1", "10.9.1.3"}


def test_checks_filter(db_session, test_project, test_user):
    _seed(db_session, test_project)
    assert _ips(db_session, test_user, test_project, checks="ftp_anonymous") == {"10.9.1.3"}


def test_unknown_values_are_refused(db_session, test_project, test_user):
    with pytest.raises(HTTPException):
        build_filtered_host_query(db_session, test_user, project_id=test_project.id, weaknesses="critical")
    with pytest.raises(HTTPException):
        build_filtered_host_query(db_session, test_user, project_id=test_project.id, checks="nope")


def test_filter_data_counts_each_flag_under_the_other_conditions(client, db_session, test_project):
    _seed(db_session, test_project)
    url = f"/api/v1/projects/{test_project.id}/hosts/filters/data"

    body = client.get(url).json()
    counts = {w["name"]: w["host_count"] for w in body["weaknesses"]}
    assert [w["name"] for w in body["weaknesses"]] == list(WEAKNESS_FLAGS)
    assert counts["smb_unsigned"] == 1 and counts["cleartext"] == 1 and counts["eol"] == 0
    assert all(w["label"] and w["description"] for w in body["weaknesses"])
    assert {c["id"]: c["host_count"] for c in body["checks"]} == {"ftp_anonymous": 1}

    # Ticking one flag leaves the others' counts alone (own dimension excluded)…
    body = client.get(url + "?weaknesses=smb_unsigned").json()
    assert {w["name"]: w["host_count"] for w in body["weaknesses"]}["cleartext"] == 1
    # …while another condition scopes them.
    body = client.get(url + "?checks=ftp_anonymous").json()
    assert {w["name"]: w["host_count"] for w in body["weaknesses"]}["smb_unsigned"] == 0


def test_hosts_list_accepts_the_params(client, db_session, test_project):
    _seed(db_session, test_project)
    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/?weaknesses=smb_unsigned").json()
    items = body.get("items", body.get("hosts", body))
    assert [h["ip_address"] for h in items] == ["10.9.1.1"]
    assert client.get(f"/api/v1/projects/{test_project.id}/hosts/?weaknesses=bogus").status_code == 400


def test_rows_and_detail_carry_the_flags(client, db_session, test_project):
    """The row names the flag a weakness condition matched; the inspector
    states it (an EOL OS used to read as a plain OS name)."""
    hosts = _seed(db_session, test_project)
    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/").json()
    flags = {h["ip_address"]: h["weakness_flags"] for h in body["items"]}
    assert flags == {"10.9.1.1": ["smb_unsigned"], "10.9.1.2": [], "10.9.1.3": ["cleartext"]}
    checks = {h["ip_address"]: h["check_ids"] for h in body["items"]}
    assert checks["10.9.1.3"] == ["ftp_anonymous"] and checks["10.9.1.1"] == []
    detail = client.get(f"/api/v1/projects/{test_project.id}/hosts/{hosts['10.9.1.1'].id}").json()
    assert detail["weakness_flags"] == ["smb_unsigned"]
    assert detail["weakness_labels"] == {"smb_unsigned": "SMB signing not required"}
