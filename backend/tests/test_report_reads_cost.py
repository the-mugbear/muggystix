"""R2 (2026-10-22) — a draft's read does not build an object per endpoint.

A draft's dataset took its endpoints as entities: 1,000 findings × 200
endpoints spent 5.7 of a draft read's 7.0 s building FindingHost / Host /
DNSName objects (the SQL was 0.1 s).  They are columns now (1.6 s); the
dataset, the reported state and the summary are compared below with the
entity form, written out, on a project built to reach every branch (false
positives, a finding whose every endpoint is one, a finding with none, names,
ports, test results on listed and unlisted hosts).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import load_only, noload, selectinload

from app.db import models
from app.db.models import DNSName, Host
from app.db.models_findings import Finding, FindingHost, FindingHostStatus
from app.db.models_proposals import EvidenceRecord
from app.db.models_reports import Report, ReportKind, ReportStatus
from app.services import client_report_service as crs
from app.services.client_report_service import ClientReportService

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _old_included(self, project_id):
    """``_included`` as it stood before R2: entities, endpoints by selectin."""
    findings = (
        self.db.query(Finding)
        .options(
            selectinload(Finding.hosts).selectinload(FindingHost.host).options(
                load_only(Host.id, Host.ip_address, Host.hostname),
                noload(Host.ports), noload(Host.vulnerabilities),
                noload(Host.notes), noload(Host.tag_assignments),
            ),
            selectinload(Finding.hosts).selectinload(FindingHost.name),
            noload(Finding.vulnerabilities),
        )
        .filter(Finding.project_id == project_id, Finding.status.in_(crs.INCLUDED_STATUSES))
        .all()
    )
    out = []
    for f in findings:
        live = sorted(
            (fh for fh in f.hosts if fh.host_status != FindingHostStatus.FALSE_POSITIVE.value),
            key=crs._endpoint_order,
        )
        if f.hosts and not live:
            continue
        f._report_endpoints = live
        f._has_endpoints = bool(f.hosts)
        out.append(f)
    out.sort(key=crs._sort_key)
    return out


def _estate(db, project):
    pid = project.id
    hosts = []
    for n, ip in enumerate(["10.0.0.10", "10.0.0.2", "10.0.0.9", "192.168.1.1", "fe80::1", "10.0.0.3"]):
        host = Host(project_id=pid, ip_address=ip, state="up", hostname=f"h{n}.corp" if n % 2 else None)
        db.add(host)
        hosts.append(host)
    db.flush()
    port = models.Port(host_id=hosts[0].id, port_number=443, protocol="tcp", state="open")
    db.add(port)
    names = [DNSName(project_id=pid, fqdn=f"www{n}.example.com") for n in range(2)]
    db.add_all(names)
    db.flush()

    def finding(title, severity, status, endpoints):
        f = Finding(project_id=pid, title=title, severity=severity, status=status, source="manual",
                    description="What it is.", impact="Why it matters.", recommendation="Fix it.",
                    cvss_score=7.5 if severity == "high" else None)
        db.add(f)
        db.flush()
        for host, state, name, with_port in endpoints:
            db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=state,
                               name_id=name.id if name is not None else None,
                               port_id=port.id if with_port else None))
        db.flush()
        return f

    open_, fp, fixed = "open", FindingHostStatus.FALSE_POSITIVE.value, FindingHostStatus.REMEDIATED.value
    mixed = finding("Mixed endpoints", "high", "confirmed", [
        (hosts[0], open_, None, True), (hosts[0], open_, names[0], True), (hosts[1], fixed, None, False),
        (hosts[2], fp, None, False), (hosts[3], open_, names[1], False), (hosts[4], open_, None, False),
        (hosts[5], open_, None, False),
    ])
    finding("Every endpoint a false positive", "critical", "confirmed", [(hosts[1], fp, None, False)])
    loose = finding("No endpoints", "medium", "accepted_risk", [])
    finding("Remediated", "low", "remediated", [(hosts[2], fixed, names[0], False)])
    finding("Still open", "high", "open", [(hosts[0], open_, None, False)])
    for n, (f, host) in enumerate([(mixed, hosts[0]), (mixed, hosts[2]), (mixed, hosts[3]), (loose, hosts[4])]):
        db.add(EvidenceRecord(
            project_id=pid, host_id=host.id, finding_id=f.id, tool="nmap", command=f"nmap {host.ip_address}",
            outcome="finding", summary="Vulnerable.", raw_output_preview="VULNERABLE", raw_output_bytes=10,
            executed_at=T0 + timedelta(minutes=n), created_at=T0,
        ))
    report = Report(project_id=pid, kind=ReportKind.FULL, status=ReportStatus.DRAFT, title="Draft",
                    template="pentest", settings={})
    db.add(report)
    db.commit()
    return report


def _build(db, report, included, monkeypatch):
    monkeypatch.setattr(ClientReportService, "_included", included)
    db.expire_all()
    dataset, reported, summary = ClientReportService(db).build(db.get(Report, report.id))
    for out in (dataset,):
        out["report"].pop("date", None)
    return dataset, reported, summary


def test_a_drafts_dataset_reported_state_and_summary_are_what_the_entity_form_built(
    db_session, test_project, monkeypatch,
):
    report = _estate(db_session, test_project)
    new_included = ClientReportService._included
    old = _build(db_session, report, _old_included, monkeypatch)
    new = _build(db_session, report, new_included, monkeypatch)
    assert new == old
    dataset, reported, summary = new
    # The fixture reaches the branches it is for.
    titles = [f["title"] for f in dataset["findings"]]
    assert "Every endpoint a false positive" not in titles and "Still open" not in titles
    assert {"Mixed endpoints", "No endpoints", "Remediated"} <= set(titles)
    mixed = next(f for f in dataset["findings"] if f["title"] == "Mixed endpoints")
    assert [e["address"] for e in mixed["affected"]] == [
        "10.0.0.2", "10.0.0.3", "10.0.0.10", "10.0.0.10", "192.168.1.1", "fe80::1"]


def test_the_endpoints_are_columns_not_entities(db_session, test_project):
    """Fails on the entity form: it put FindingHost and Host objects in the
    session (selectin), one per endpoint and host."""
    _estate(db_session, test_project)
    pid = test_project.id
    db_session.expunge_all()
    findings = ClientReportService(db_session)._included(pid)
    assert findings
    assert not [o for o in db_session.identity_map.values() if isinstance(o, (FindingHost, Host, DNSName))]
    assert all(not isinstance(fh, FindingHost) for f in findings for fh in f._report_endpoints)


