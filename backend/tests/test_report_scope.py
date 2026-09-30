"""The client report's scope cutoff and its separate scope file (v2.441.0).

A project with thousands of scoped networks printed them as a table that ran
for pages.  Over the template's cutoff the report now gives totals and names a
CSV (with its SHA-256) that carries the complete list; the file is built from
the report's own dataset, so an issued report's file never changes.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.core.config import settings
from app.db import models
from app.services import report_scope, report_template_service


@pytest.fixture(autouse=True)
def template_dir(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    folder = root / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html"],
        "scope_inline_max": 3, "scope_domains_inline_max": 5,
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    return folder


def _scope(db_session, project, cidrs, domains=(), site="HQ", description=None):
    scope = models.Scope(project_id=project.id, name="s")
    db_session.add(scope)
    db_session.flush()
    for cidr in cidrs:
        db_session.add(models.Subnet(scope_id=scope.id, cidr=cidr, site=site, description=description))
    for d in domains:
        db_session.add(models.ScopeDomain(scope_id=scope.id, domain=d, include_subdomains=True))
    db_session.commit()
    return scope


def _base(project):
    return f"/api/v1/projects/{project.id}/client-reports"


def _draft(client, project):
    r = client.post(_base(project), json={"kind": "full"})
    assert r.status_code == 201, r.text
    return r.json()


# --- the rules ------------------------------------------------------------------

def test_the_cutoff_prints_up_to_it_and_summarises_past_it():
    nets = [{"cidr": f"10.0.{i}.0/24", "site": None} for i in range(4)]
    at = report_scope.summarise(nets[:3], [], inline_max=3)
    over = report_scope.summarise(nets, [], inline_max=3)
    assert (at["subnets_inline"], at["external"]) == (True, False)
    assert (over["subnets_inline"], over["external"]) == (False, True)
    assert over["totals"]["ipv4_addresses"] == 4 * 256
    # Domains have their own cutoff.
    assert report_scope.summarise([], [{"domain": f"d{i}.lab"} for i in range(6)], domains_inline_max=5)["external"]
    # None: the template prints no scope, so it never names a file.
    assert not report_scope.summarise(nets * 1000, [], inline_max=None, domains_inline_max=None)["external"]


def test_ipv6_networks_are_counted_not_their_addresses_and_sites_are_capped():
    nets = [{"cidr": "2001:db8::/64", "site": "V6"}]
    nets += [{"cidr": f"10.{i}.0.0/24", "site": f"Site {i:02d}"} for i in range(20)]
    s = report_scope.summarise(nets, [])
    assert s["totals"]["ipv6_networks"] == 1 and s["totals"]["ipv4_addresses"] == 20 * 256
    assert len(s["by_site"]) == report_scope.SITE_ROWS + 1
    assert s["by_site"][-1]["site"].startswith(report_scope.OTHER_SITES)
    assert sum(r["networks"] for r in s["by_site"]) == 21


def test_the_file_is_deterministic_and_safe_to_open_in_a_spreadsheet():
    scope = {
        "subnets": [{"cidr": "10.0.0.0/24", "site": "=HYPERLINK(\"http://x\")", "description": "@SUM(1)"}],
        "domains": [{"domain": "example.lab", "include_subdomains": False}],
    }
    data = report_scope.scope_csv(scope)
    assert data == report_scope.scope_csv(scope)
    text = data.decode("utf-8")
    assert text.startswith("﻿kind,value,site,description,subdomains\r\n")
    # A formula-looking cell is text: a leading apostrophe, never "=…" at the start of a cell.
    assert "'=HYPERLINK" in text and ",=" not in text and "'@SUM(1)" in text
    assert "domain,example.lab,,,not included" in text
    assert report_scope.file_name("Acme Corp!", 3, 9) == "scope-acme-corp-report-3.csv"
    assert report_scope.file_name(None, None, 9) == "scope-project-draft-9.csv"


def test_a_cutoff_that_is_not_a_count_makes_the_template_a_problem(template_dir):
    (template_dir / "template.json").write_text(json.dumps({
        "title": "T", "entry": "report.qmd", "scope_inline_max": "lots",
    }))
    problems = report_template_service.template_problems()
    assert any("scope_inline_max" in p["error"] for p in problems)


# --- the report and its file ------------------------------------------------------

def test_over_the_cutoff_the_report_names_the_file_whose_hash_it_prints(client, db_session, test_project):
    _scope(db_session, test_project, [f"10.1.{i}.0/24" for i in range(4)], domains=["example.lab"])
    rid = _draft(client, test_project)["id"]
    body = client.get(f"{_base(test_project)}/{rid}").json()
    warning = body["summary"]["scope_external"]
    assert (warning["networks"], warning["domains"], warning["inline_max"]) == (4, 1, 3)
    assert warning["file"]["name"].startswith("scope-") and warning["file"]["name"].endswith(f"draft-{rid}.csv")

    r = client.get(f"{_base(test_project)}/{rid}/scope.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert hashlib.sha256(r.content).hexdigest() == warning["file"]["sha256"]
    assert r.content.decode("utf-8").count("network,10.1.") == 4


def test_at_or_under_the_cutoff_there_is_no_file_to_send(client, db_session, test_project):
    _scope(db_session, test_project, ["10.2.0.0/24", "10.2.1.0/24", "10.2.2.0/24"])
    rid = _draft(client, test_project)["id"]
    assert client.get(f"{_base(test_project)}/{rid}").json()["summary"]["scope_external"] is None


def test_an_issued_reports_scope_file_never_changes(client, db_session, test_project):
    scope = _scope(db_session, test_project, [f"10.3.{i}.0/24" for i in range(5)])
    rid = _draft(client, test_project)["id"]
    issued = client.post(f"{_base(test_project)}/{rid}/issue")
    assert issued.status_code == 200, issued.text
    number = issued.json()["number"]
    warning = issued.json()["summary"]["scope_external"]
    assert warning["file"]["name"].endswith(f"report-{number}.csv")
    before = client.get(f"{_base(test_project)}/{rid}/scope.csv").content
    assert hashlib.sha256(before).hexdigest() == warning["file"]["sha256"]

    # The project's scope changes after issue; the issued report's file does not.
    db_session.add(models.Subnet(scope_id=scope.id, cidr="10.99.0.0/24", site="Later"))
    db_session.commit()
    after = client.get(f"{_base(test_project)}/{rid}/scope.csv").content
    assert after == before and b"10.99.0.0" not in after


def test_an_agent_downloads_the_same_file(client, db_session, test_project):
    _scope(db_session, test_project, [f"10.4.{i}.0/24" for i in range(4)])
    rid = _draft(client, test_project)["id"]
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]
    agent = client.get(f"/api/v1/agent/assist/client-reports/{rid}/scope.csv", headers={"X-API-Key": key})
    assert agent.status_code == 200, agent.text
    assert agent.content == client.get(f"{_base(test_project)}/{rid}/scope.csv").content
