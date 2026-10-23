"""Review 2026-10-23b, client reports and the inventory downloads.

* REP-1 — the inventory CSV's "Active Findings" / "Critical Findings" and the
  JSON record's ``dossier_summary.active_findings`` are the Hosts list's badge
  (``host_detail_service.active_finding_counts``): the finding's status
  counts, and a finding counts once per host however many of its names it is
  recorded on.
* REP-2 — the AI drafting routes end the request's read transaction before
  the provider is called (the call can take minutes).
* REP-4 — an addendum's build reads its baseline's ``reported`` map in the
  database, never the baseline's whole snapshot.
* REP-5 — issuing fills the template once.
* REP-6 — the CSV counts scanner observations and notes in grouped queries;
  the figures are the ones the relationships give.
* REP-8 — the CSV lists hosts by id and the first five services sorted.
"""
from __future__ import annotations

import csv
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import inspect as sa_inspect

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_llm import LLMProvider
from app.db.models_reports import Report
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource, enum_value
from app.services import host_detail_service, report_draft_service, report_template_service
from app.services.client_report_service import ClientReportService
from app.services.report_draft_service import ReportDraftService
from app.services.report_generator import ReportGenerator
from tests.test_client_reports import _create, _finding, _host, _issue, template_dir  # noqa: F401 — the fixture


def _gen(db, project, user):
    return ReportGenerator(db=db, current_user=MagicMock(id=user.id), project_id=project.id)


def _csv_rows(gen) -> dict:
    rows = list(csv.reader(io.StringIO("".join(gen.iter_inventory_csv({})))))
    return {row[0]: dict(zip(rows[0], row)) for row in rows[1:]}


def _record(gen, db, host):
    (record,) = gen.iter_host_records(db.query(models.Host.id).filter(models.Host.id == host.id))
    return record


# --- REP-1 -----------------------------------------------------------------------

def test_the_inventory_counts_active_findings_as_the_hosts_badge_does(db_session, test_project, test_user):
    accepted = _host(db_session, test_project, "10.91.0.1")
    named = _host(db_session, test_project, "10.91.0.2")
    # Accepted risk, its endpoint still "open": not active anywhere.
    _finding(db_session, test_project, "Weak TLS", "critical", status="accepted_risk", hosts=[accepted])
    # One confirmed critical on a host AND two of its names: one finding.
    f = _finding(db_session, test_project, "Default credentials", "critical", hosts=[named])
    for fqdn in ("a.example.test", "b.example.test"):
        name = models.DNSName(project_id=test_project.id, fqdn=fqdn)
        db_session.add(name)
        db_session.flush()
        db_session.add(FindingHost(finding_id=f.id, host_id=named.id, name_id=name.id, host_status="open"))
    _finding(db_session, test_project, "Directory listing", "medium", hosts=[named])
    db_session.commit()

    badge = host_detail_service.active_finding_counts(db_session, [accepted.id, named.id])
    assert badge == {named.id: 2}
    gen = _gen(db_session, test_project, test_user)
    rows = _csv_rows(gen)
    assert (rows["10.91.0.1"]["Active Findings"], rows["10.91.0.1"]["Critical Findings"]) == ("0", "0")
    assert (rows["10.91.0.2"]["Active Findings"], rows["10.91.0.2"]["Critical Findings"]) == ("2", "1")

    assert _record(gen, db_session, accepted)["dossier_summary"]["active_findings"] == 0
    summary = _record(gen, db_session, named)["dossier_summary"]
    assert summary["active_findings"] == badge[named.id] == 2
    # The totals count findings, not the endpoint rows the record lists.
    assert summary["total_findings"] == 2
    assert summary["findings_by_severity"] == {"critical": 1, "medium": 1}


# --- REP-2 -----------------------------------------------------------------------

def _no_open_transaction(db_session, seen):
    def fake_chat(provider, *, system, messages, max_tokens, temperature):
        # Read what a real call reads off the provider: none of it may open a
        # transaction again.
        seen["provider"] = (provider.provider_type, provider.model_id, provider.base_url)
        seen["in_transaction"] = db_session.in_transaction()
        return {"content": '{"impact": "An attacker can relay.", "missing": {}}', "raw": {}}
    return fake_chat


def test_drafting_a_summary_holds_no_transaction_through_the_provider_call(
        client, db_session, test_project, test_user, monkeypatch):
    host = _host(db_session, test_project, "10.92.0.1")
    _finding(db_session, test_project, "SQL injection", "critical", hosts=[host],
             description="d", impact="i", recommendation="r")
    db_session.add(LLMProvider(user_id=test_user.id, name="local", provider_type="ollama",
                               base_url="http://llm.example.test:11434", model_id="m1", is_default=True))
    db_session.commit()
    report = _create(client, test_project)
    seen: dict = {}
    monkeypatch.setattr(report_draft_service, "chat_completion", _no_open_transaction(db_session, seen))

    r = client.post(f"/api/v1/projects/{test_project.id}/reports/draft", json={"report_id": report["id"]})
    assert r.status_code == 200, r.text
    assert seen["in_transaction"] is False
    assert seen["provider"] == ("ollama", "m1", "http://llm.example.test:11434")
    assert (r.json()["provider_type"], r.json()["model_id"], r.json()["finding_total"]) == ("ollama", "m1", 1)


def test_drafting_finding_text_holds_no_transaction_through_the_provider_call(
        client, db_session, test_project, test_user, monkeypatch):
    host = _host(db_session, test_project, "10.92.0.2")
    f = _finding(db_session, test_project, "SMB signing", "high", hosts=[host], description="d")
    db_session.add(LLMProvider(user_id=test_user.id, name="local", provider_type="ollama",
                               base_url="http://llm.example.test:11434", model_id="m2", is_default=True))
    db_session.commit()
    seen: dict = {}
    monkeypatch.setattr(report_draft_service, "chat_completion", _no_open_transaction(db_session, seen))

    r = client.post(f"/api/v1/projects/{test_project.id}/reports/draft/finding-text",
                    json={"finding_id": f.id, "fields": ["impact"]})
    assert r.status_code == 200, r.text
    assert seen["in_transaction"] is False and seen["provider"][1] == "m2"
    # The proposal is written after the call, against the finding read again.
    assert [(p["field"], p["finding_id"]) for p in r.json()["proposals"]] == [("impact", f.id)]


def test_a_stub_provider_still_drafts(db_session, test_project, test_user, monkeypatch):
    """The helper detaches only a mapped row: the drafting tests' stub
    provider is a plain object."""
    host = _host(db_session, test_project, "10.92.0.3")
    f = _finding(db_session, test_project, "Telnet", "high", hosts=[host])
    seen: dict = {}
    monkeypatch.setattr(report_draft_service, "chat_completion", _no_open_transaction(db_session, seen))
    monkeypatch.setattr(ReportDraftService, "_provider",
                        lambda self, pid: SimpleNamespace(id=9, provider_type="openai", model_id="m", base_url=None))
    out = ReportDraftService(db_session, test_user).draft_finding_text(f, ["impact"])
    assert out["suggestions"] == {"impact": "An attacker can relay."} and seen["in_transaction"] is False


# --- REP-4 / REP-5 -----------------------------------------------------------------

def test_an_addendum_reads_its_baselines_reported_map_without_its_snapshot(client, db_session, test_project):
    host = _host(db_session, test_project, "10.93.0.1")
    _finding(db_session, test_project, "Kerberoastable account", "high", hosts=[host],
             description="d", impact="i", recommendation="r")
    baseline = _issue(client, test_project, _create(client, test_project)["id"])
    _finding(db_session, test_project, "Open SMB share", "medium", hosts=[host],
             description="d", impact="i", recommendation="r")
    addendum = _create(client, test_project, kind="addendum")
    db_session.expire_all()

    report = db_session.get(Report, addendum["id"])
    dataset, _, _ = ClientReportService(db_session).build(report)
    assert "snapshot" in sa_inspect(db_session.get(Report, baseline["id"])).unloaded
    # What it compares against is still the baseline's: only the new finding.
    assert [item["title"] for item in dataset["findings"] if item.get("change") == "new"] == ["Open SMB share"]


def test_issuing_fills_the_template_once(client, db_session, test_project, monkeypatch):
    host = _host(db_session, test_project, "10.93.0.2")
    _finding(db_session, test_project, "Weak password policy", "high", hosts=[host],
             description="d", impact="i", recommendation="r")
    report = _create(client, test_project)
    real, calls = report_template_service.printed_parts, []

    def counting(template, dataset):
        calls.append(template)
        return real(template, dataset)

    monkeypatch.setattr(report_template_service, "printed_parts", counting)
    _issue(client, test_project, report["id"])
    assert len(calls) == 1


# --- REP-6 / REP-8 -----------------------------------------------------------------

def test_the_csv_counts_observations_and_notes_as_the_relationships_do(db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.94.0.1")
    scan = models.Scan(project_id=test_project.id, filename="n.nessus")
    db_session.add(scan)
    db_session.flush()
    for i, severity in enumerate([VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH,
                                  VulnerabilitySeverity.HIGH, VulnerabilitySeverity.LOW,
                                  VulnerabilitySeverity.INFO, VulnerabilitySeverity.UNKNOWN]):
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                     severity=severity, title=f"Issue {i}"))
    for body in ("one", "two", "three"):
        db_session.add(models.Annotation(host_id=host.id, user_id=test_user.id, body=body,
                                         note_type="observation"))
    db_session.commit()
    db_session.refresh(host)

    # The old computation, from the relationships.
    by_severity: dict = {}
    for v in host.vulnerabilities:
        by_severity[enum_value(v.severity)] = by_severity.get(enum_value(v.severity), 0) + 1
    notes, vulns = len(host.notes), len(host.vulnerabilities)
    row = _csv_rows(_gen(db_session, test_project, test_user))["10.94.0.1"]
    assert [row[k] for k in ("Critical", "High", "Medium", "Low")] == [
        str(by_severity.get(k, 0)) for k in ("critical", "high", "medium", "low")
    ] == ["1", "2", "0", "1"]
    assert row["Notes"] == str(notes) == "3"
    # Nothing judged: every row, of any severity, is untriaged.
    assert row["Untriaged Vulns"] == str(vulns) == "6"


def test_the_csv_lists_hosts_by_id_and_services_sorted(db_session, test_project, test_user):
    hosts = [_host(db_session, test_project, ip) for ip in ("10.95.0.9", "10.95.0.1", "10.95.0.5")]
    for port, service in enumerate(("telnet", "ssh", "http", "smtp", "ftp", "dns", "ldap"), start=20):
        db_session.add(models.Port(host_id=hosts[0].id, port_number=port, protocol="tcp", state="open",
                                   service_name=service))
    db_session.commit()
    gen = _gen(db_session, test_project, test_user)
    assert "ORDER BY" in str(gen._filtered_host_id_query({}))
    rows = list(csv.reader(io.StringIO("".join(gen.iter_inventory_csv({})))))
    assert [r[0] for r in rows[1:]] == ["10.95.0.9", "10.95.0.1", "10.95.0.5"]   # by id
    services = dict(zip(rows[0], rows[1]))["Services"]
    assert services == "dns, ftp, http, ldap, smtp (+2 more)"
