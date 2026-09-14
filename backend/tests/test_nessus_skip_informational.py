"""v2.341.0 — skip informational Nessus findings at ingest; hide them on read.

A Nessus batch for a large estate is mostly severity-0 report items.  Each
became a ``vulnerabilities`` row with its own copy of the plugin text, while
posture / insights / risk scoring already ignore the INFO severity.  These
pin the three pieces that change that:

* the ingest path drops severity-0 rows when asked, still upserts their
  ports (a Nessus-only host must keep its open ports), and says how many it
  skipped;
* the upload routes resolve the flag from the form field, else the project's
  setting, else the deployment default — and an analyst can set the project
  choice without being a project admin;
* the host detail endpoint omits INFO rows unless asked, and always says how
  many there are.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.nessus_integration_service import NessusIntegrationService


NESSUS_XML = textwrap.dedent("""\
    <?xml version="1.0" ?>
    <NessusClientData_v2>
    <Report name="batch-7">
    <ReportHost name="10.9.9.5">
      <HostProperties>
        <tag name="host-ip">10.9.9.5</tag>
        <tag name="host-fqdn">web.example.test</tag>
      </HostProperties>
      <ReportItem port="443" svc_name="www" protocol="tcp" severity="0" pluginID="11219" pluginName="Nessus SYN scanner">
        <description>Port scanner.</description>
        <plugin_output>Port 443/tcp was found to be open</plugin_output>
      </ReportItem>
      <ReportItem port="443" svc_name="www" protocol="tcp" severity="0" pluginID="22964" pluginName="Service Detection">
        <description>Service detection.</description>
      </ReportItem>
      <ReportItem port="0" svc_name="general" protocol="tcp" severity="0" pluginID="19506" pluginName="Nessus Scan Information">
        <description>Scan info.</description>
      </ReportItem>
      <ReportItem port="443" svc_name="www" protocol="tcp" severity="2" pluginID="42873" pluginName="SSL Medium Strength Cipher Suites Supported">
        <description>Weak ciphers.</description>
        <risk_factor>Medium</risk_factor>
      </ReportItem>
      <ReportItem port="22" svc_name="ssh" protocol="tcp" severity="3" pluginID="90317" pluginName="SSH Weak Algorithms Supported">
        <description>Weak algorithms.</description>
        <risk_factor>High</risk_factor>
      </ReportItem>
    </ReportHost>
    </Report>
    </NessusClientData_v2>
""")


def _write(tmp_path: Path) -> str:
    p = tmp_path / "batch7.nessus"
    p.write_text(NESSUS_XML)
    return str(p)


def _host(db, project_id: int):
    # Takes the id, not the row: the Nessus service expunges the session as
    # it commits, which detaches the fixture's Project instance.
    return (
        db.query(models.Host)
        .filter(models.Host.project_id == project_id, models.Host.ip_address == "10.9.9.5")
        .first()
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_skip_drops_info_rows_but_keeps_ports_and_reports_the_count(db_session, test_project, tmp_path):
    pid = test_project.id
    result = NessusIntegrationService(db_session).process_nessus_file(
        _write(tmp_path), project_id=pid, skip_informational=True,
    )
    assert result["success"], result
    assert result["vulnerabilities_found"] == 2
    assert result["informational_skipped"] == 3
    assert "3 informational skipped" in result["message"]

    host = _host(db_session, pid)
    assert host is not None
    sevs = sorted(
        v.severity.value for v in
        db_session.query(Vulnerability).filter(Vulnerability.host_id == host.id).all()
    )
    assert sevs == ["high", "medium"]
    # Ports came from the informational items too: 443 was seen by the SYN
    # scanner and service detection before any real finding named it, and a
    # Nessus-only host must not lose its open ports to the switch.
    ports = sorted(p.port_number for p in host.ports)
    assert ports == [22, 443]


def test_default_keeps_info_rows(db_session, test_project, tmp_path):
    pid = test_project.id
    result = NessusIntegrationService(db_session).process_nessus_file(
        _write(tmp_path), project_id=pid,
    )
    assert result["success"], result
    assert result["vulnerabilities_found"] == 5
    assert result["informational_skipped"] == 0
    assert "informational skipped" not in result["message"]
    host = _host(db_session, pid)
    info = (
        db_session.query(Vulnerability)
        .filter(Vulnerability.host_id == host.id, Vulnerability.severity == VulnerabilitySeverity.INFO)
        .count()
    )
    assert info == 3


# ---------------------------------------------------------------------------
# Resolution: form field > project setting > deployment default
# ---------------------------------------------------------------------------

def test_project_setting_and_upload_override(client, db_session, test_project, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.db.models import IngestionJob

    # Deployment default off, project undecided → uploads keep info rows.
    monkeypatch.setattr(settings, "NESSUS_SKIP_INFORMATIONAL_DEFAULT", False)
    r = client.get(f"/api/v1/projects/{test_project.id}")
    assert r.status_code == 200, r.text
    assert r.json()["skip_informational_findings"] is None
    assert r.json()["skip_informational_effective"] is False

    # Deployment default on, project undecided → effective follows it.
    monkeypatch.setattr(settings, "NESSUS_SKIP_INFORMATIONAL_DEFAULT", True)
    assert client.get(f"/api/v1/projects/{test_project.id}").json()["skip_informational_effective"] is True

    # The project chooses (the switch beside the drop zone) — explicit false
    # beats a deployment default of true.
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/ingest-settings",
        json={"skip_informational_findings": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skip_informational_findings"] is False
    assert r.json()["skip_informational_effective"] is False

    # An upload with no form field carries the project's effective value.
    files = {"file": ("batch7.nessus", NESSUS_XML.encode(), "application/xml")}
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/", files=files)
    assert r.status_code in (200, 201, 202), r.text
    job = db_session.query(IngestionJob).order_by(IngestionJob.id.desc()).first()
    assert job.options["skip_informational"] is False

    # A per-upload form field overrides the project's choice.
    files = {"file": ("batch7-again.nessus", NESSUS_XML.replace("batch-7", "batch-8").encode(), "application/xml")}
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/",
        files=files, data={"skip_informational": "true"},
    )
    assert r.status_code in (200, 201, 202), r.text
    job = db_session.query(IngestionJob).order_by(IngestionJob.id.desc()).first()
    assert job.options["skip_informational"] is True

    # null clears the project's choice; the deployment default applies again.
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/ingest-settings",
        json={"skip_informational_findings": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skip_informational_findings"] is None
    assert r.json()["skip_informational_effective"] is True


def test_ingest_setting_is_analyst_writable_not_viewer(client, db_session, test_project, test_user):
    """The people who upload scans may decide what an upload keeps; a viewer
    may not.  (The test client is a global admin, so demote it per call.)"""
    from app.db.models_auth import UserRole
    from app.db.models_project import ProjectMembership

    test_user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="viewer"))
    db_session.commit()
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/ingest-settings",
        json={"skip_informational_findings": True},
    )
    assert r.status_code == 403, r.text

    db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == test_project.id, ProjectMembership.user_id == test_user.id,
    ).update({"role": "analyst"})
    db_session.commit()
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/ingest-settings",
        json={"skip_informational_findings": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skip_informational_findings"] is True


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def test_host_detail_hides_info_rows_unless_asked(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.9.9.7", state="up")
    db_session.add(host)
    db_session.flush()
    for i, sev in enumerate([VulnerabilitySeverity.INFO] * 3 + [VulnerabilitySeverity.HIGH]):
        db_session.add(Vulnerability(
            title=f"v{i}", severity=sev, source=VulnerabilitySource.NESSUS,
            host_id=host.id, plugin_id=str(1000 + i),
        ))
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["severity"] for v in body["vulnerabilities"]] == ["high"]
    assert body["informational_count"] == 3
    assert body["informational_included"] is False
    # The summary still counts everything — the at-a-glance line reads it.
    assert body["vulnerability_summary"]["info"] == 3
    assert body["vulnerability_summary"]["total_vulnerabilities"] == 4

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}", params={"include_info": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(v["severity"] for v in body["vulnerabilities"]) == ["high", "info", "info", "info"]
    assert body["informational_included"] is True


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------

def test_truncated_import_still_reports_the_skipped_count(db_session, test_project, tmp_path):
    """A file cut off after a complete host has committed that host with the
    switch applied; the failure payload must say how many informational items
    were skipped, the same way a clean import does."""
    pid = test_project.id
    cut = NESSUS_XML.index("</ReportHost>") + len("</ReportHost>")
    truncated = NESSUS_XML[:cut] + '\n<ReportHost name="10.9.9.6"><HostProperties><tag name="host-ip">10.9.9.6</tag>'
    p = tmp_path / "cut.nessus"
    p.write_text(truncated)

    result = NessusIntegrationService(db_session).process_nessus_file(
        str(p), project_id=pid, skip_informational=True,
    )
    assert result["success"] is False, result
    assert result["error"] == "Nessus XML was truncated mid-parse"
    assert result["hosts_processed"] == 1
    assert result["informational_skipped"] == 3
    assert "(3 informational skipped)" in result["message"]
    # The complete host landed with the switch applied.
    host = _host(db_session, pid)
    assert host is not None
    assert sorted(p_.port_number for p_ in host.ports) == [22, 443]
    assert (
        db_session.query(Vulnerability)
        .filter(Vulnerability.host_id == host.id, Vulnerability.severity == VulnerabilitySeverity.INFO)
        .count()
    ) == 0


def test_agent_upload_resolves_the_switch_like_the_operator_upload(client, db_session, test_project, monkeypatch):
    """Both upload routes go through resolve_skip_informational: form value >
    project choice > deployment default, identically."""
    from app.core.config import settings
    from app.db import models as m
    from app.db.models import IngestionJob

    # A unified session with a recon phase open, so /agent/recon/upload accepts.
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "t"})
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    scope = m.Scope(project_id=test_project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    db_session.add(m.Subnet(scope_id=scope.id, cidr="10.9.0.0/16"))
    db_session.commit()
    r = client.post("/api/v1/agent/recon/start", headers={"X-API-Key": key}, json={"scope_id": scope.id})
    assert r.status_code == 201, r.text

    def latest_job_option():
        job = db_session.query(IngestionJob).order_by(IngestionJob.id.desc()).first()
        return job.options["skip_informational"]

    def agent_upload(name: str, **form):
        files = {"file": (name, NESSUS_XML.replace("batch-7", name).encode(), "application/xml")}
        r = client.post("/api/v1/agent/recon/upload", headers={"X-API-Key": key}, files=files, data=form)
        assert r.status_code == 201, r.text

    # deployment default on, project undecided → True
    monkeypatch.setattr(settings, "NESSUS_SKIP_INFORMATIONAL_DEFAULT", True)
    agent_upload("a1.nessus")
    assert latest_job_option() is True
    # project says False → False, even with the default on
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/ingest-settings",
        json={"skip_informational_findings": False},
    )
    assert r.status_code == 200, r.text
    agent_upload("a2.nessus")
    assert latest_job_option() is False
    # form field beats the project
    agent_upload("a3.nessus", skip_informational="true")
    assert latest_job_option() is True
