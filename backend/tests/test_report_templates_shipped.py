"""v2.409.0 — the three shipped report templates, and a template that cannot be
offered saying why.

Penetration test report (the default), Executive brief and Remediation
worklist fill from the same dataset; switching a draft between them must
change what the report says, and each keeps the rules of the data it drops
(an addendum shows only what changed; nothing the worklist leaves out
disappears unnamed).  The renders themselves — and the hostile-text contract
for every template — are in test_quarto_render.py (report-worker image).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import quarto_render
from app.services import report_template_service as templates

HERE = Path(__file__).resolve()
ROOT = next(
    (p for p in (Path("/app/report-templates"), HERE.parents[2] / "report-templates")
     if (p / "pentest" / "template.json").is_file()),
    None,
)
needs_templates = pytest.mark.skipif(ROOT is None, reason="report-templates/ is not mounted here")


def _sample(name: str) -> dict:
    return json.loads((ROOT / name / "sample-data.json").read_text())


def _fill(name: str, data: dict) -> str:
    return quarto_render.render_source(ROOT / name, "report.qmd", data)


def _addendum(sample: dict) -> dict:
    """F-01 new; F-02 reported before and now also on 192.0.2.99; F-03 withdrawn."""
    data = copy.deepcopy(sample)
    data["report"].update({
        "kind": "addendum", "title": "Addendum", "number": 2,
        "baseline": {"number": 1, "title": sample["report"]["title"], "date": "2026-09-23"},
    })
    new, grown = data["findings"][0], data["findings"][1]
    new["change"] = "new"
    grown["change"] = "new_hosts"
    grown["new_affected"] = [{"address": "192.0.2.99", "hostname": "app09", "name": None,
                              "port": "389/tcp", "state": None}]
    data["findings"] = [new, grown]
    for i, f in enumerate(data["findings"]):
        f["_path"] = f"findings.{i}"
    data["delta"] = {
        "new_findings": 1, "findings_with_new_endpoints": 1,
        "withdrawn": [{"ref": "F-03", "title": "TLS 1.0", "severity_label": "Medium",
                       "reason": "The finding was judged a false positive.", "endpoints": []}],
    }
    return data


# --- what is installed -------------------------------------------------------------

@needs_templates
def test_the_three_templates_are_offered_and_none_has_a_problem(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(ROOT))
    names = [t.name for t in templates.list_templates()]
    assert {"pentest", "executive-brief", "remediation-worklist"} <= set(names)
    assert templates.template_problems() == []
    assert templates.default_template_name() == "pentest"
    brief = templates.get_template("executive-brief")
    assert brief.formats == ("docx", "html")      # a template may offer fewer formats
    worklist = templates.get_template("remediation-worklist")
    assert worklist.assets == () and worklist.postprocess == {}   # the minimal manifest


def test_a_template_that_cannot_be_offered_says_why(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    root.mkdir()
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    good = root / "good"
    good.mkdir()
    (good / "template.json").write_text(json.dumps({"title": "Good", "formats": ["html"]}))
    (good / "report.qmd").write_text("---\ntitle: x\n---\n")
    (root / "no-manifest").mkdir()
    (root / "My Template").mkdir()
    broken = root / "broken"
    broken.mkdir()
    (broken / "template.json").write_text("{ not json")
    wrong_entry = root / "wrong-entry"
    wrong_entry.mkdir()
    (wrong_entry / "template.json").write_text(json.dumps({"entry": "main.qmd"}))
    (root / "_drafts").mkdir()        # the author's own: not reported
    (root / ".git").mkdir()

    assert [t.name for t in templates.list_templates()] == ["good"]
    problems = {p["name"]: p["error"] for p in templates.template_problems()}
    assert set(problems) == {"no-manifest", "My Template", "broken", "wrong-entry"}
    assert "no template.json" in problems["no-manifest"]
    assert "lower-case" in problems["My Template"]
    assert "unreadable" in problems["broken"]
    assert "main.qmd" in problems["wrong-entry"]


def test_the_problems_endpoint_lists_them(client, test_project, tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    (root / "no-manifest").mkdir(parents=True)
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    res = client.get(f"/api/v1/projects/{test_project.id}/client-reports/templates/problems")
    assert res.status_code == 200
    assert res.json() == [{"name": "no-manifest", "error": "The folder has no template.json."}]


# --- the executive brief -------------------------------------------------------------

@needs_templates
def test_the_brief_is_the_summary_without_the_detail():
    sample = _sample("executive-brief")
    out = _fill("executive-brief", sample)
    order = ["# Summary", "# Findings at a glance", "# Scope", "# What to do"]
    positions = [out.index(h) for h in order]
    assert positions == sorted(positions)
    # Leadership reads the recommendation, never the evidence or the host list.
    assert 'key="findings.0.recommendation"' in out
    assert "description" not in out.split("---", 2)[2].replace("bs-md", "")
    assert "Steps to reproduce" not in out and "Affected system" not in out
    # One row per finding above informational, with its systems and status.
    assert "| F\\-02 | Anonymous LDAP bind returns the directory | High | 2 | Confirmed |" in out
    assert "Risk accepted" in out
    assert "F\\-04" not in out.split("# Findings at a glance")[1].split("# Scope")[0]


@needs_templates
def test_the_brief_addendum_is_the_changes_table_only():
    out = _fill("executive-brief", _addendum(_sample("executive-brief")))
    assert "# What changed since report 1" in out
    assert "| New finding | F\\-01 |" in out
    assert "| Found on 1 more system(s) | F\\-02 |" in out
    assert "| Withdrawn | F\\-03 |" in out
    assert "# What to do" not in out and "# Findings at a glance" not in out


# --- the remediation worklist ---------------------------------------------------------

@needs_templates
def test_the_worklist_turns_findings_into_systems_worst_first():
    out = _fill("remediation-worklist", _sample("remediation-worklist"))
    # dc01 carries F-01 (critical, SMB) and F-02 (high, LDAP): one section, worst first.
    dc01 = out.split("## 192\\.0\\.2\\.20")[1].split("\n## ")[0]
    assert dc01.index("F\\-01") < dc01.index("F\\-02")
    # Each fix is written once, however many systems it is on.
    assert out.count('key="findings.0.recommendation"') == 1
    # dc02's LDAP bind is fixed: not work, but named.
    assert "## 192\\.0\\.2\\.21" not in out
    assert "Already fixed on 192\\.0\\.2\\.21 (dc02\\.example\\.com)" in out
    # Accepted risk and informational findings are named, not dropped.
    tail = out.split("# Not on this list")[1]
    assert "F\\-03" in tail and "Risk accepted" in tail
    assert "F\\-04" in tail and "Informational" in tail


@needs_templates
def test_the_worklist_addendum_lists_only_new_work():
    out = _fill("remediation-worklist", _addendum(_sample("remediation-worklist")))
    # F-01 is new: all its systems.  F-02 was reported: only its new system.
    assert "## 192\\.0\\.2\\.10" in out and "## 192\\.0\\.2\\.99" in out
    ldap_sections = [s for s in out.split("\n## ") if "F\\-02" in s and s.startswith("192")]
    assert [s.split(" ")[0] for s in ldap_sections] == ["192\\.0\\.2\\.99"]
    assert "lists only the work that is new since report 1" in out


@needs_templates
def test_a_worklist_with_nothing_to_fix_says_so():
    data = _sample("remediation-worklist")
    for f in data["findings"]:
        f["status"] = "accepted_risk"
    out = _fill("remediation-worklist", data)
    assert "Nothing to fix" in out
    assert "# How to fix" not in out


# --- the scope cutoff (v2.441.0) ------------------------------------------------------

def _with_scope(sample: dict, networks: int, *, inline_max: int = 25) -> dict:
    """The sample with ``networks`` scoped /24s over two sites, as
    client_report_service builds the scope block."""
    from app.services import report_scope
    data = copy.deepcopy(sample)
    subnets = [
        {"cidr": f"10.{i // 256}.{i % 256}.0/24", "site": "Head office" if i % 3 else "Data centre",
         "description": None}
        for i in range(networks)
    ]
    domains = [{"domain": "example.com", "include_subdomains": True}]
    block = {"subnets": subnets, "domains": domains}
    block.update(report_scope.summarise(subnets, domains, inline_max=inline_max))
    data["scope"] = report_scope.attach_file(block, project_slug="acme", number=3, report_id=9)
    return data


@needs_templates
def test_a_large_scope_is_summarised_and_its_file_named_not_printed():
    """Thousands of networks printed as a table that ran for pages (the
    user's report); over the cutoff the report names the file instead."""
    data = _with_scope(_sample("pentest"), 3000)
    out = _fill("pentest", data)
    sha = data["scope"]["file"]["sha256"]
    assert "10\\.0\\.5\\.0\\/24" not in out and "10.0.5.0/24" not in out   # no network is listed
    assert "| Network | Site | Description |" not in out
    # Printed values are Markdown-escaped: "3\,000" prints as "3,000".
    assert "**3\\,000 networks**" in out and "768\\,000 IPv4 addresses" in out
    assert "scope\\-acme\\-report\\-3\\.csv" in out and sha in out
    assert "Scope by site" in out and "| Head office | 2\\,000 | 512\\,000 |" in out
    # One domain is under its own cutoff: still listed in its table.
    assert "| example\\.com | included |" in out

    brief = _fill("executive-brief", _with_scope(_sample("executive-brief"), 3000))
    assert "3\\,000 network(s)" in brief and sha in brief
    assert "10.0.5.0/24" not in brief and "10\\.0\\.5\\.0\\/24" not in brief


@needs_templates
def test_a_scope_at_the_cutoff_is_still_a_table():
    out = _fill("pentest", _with_scope(_sample("pentest"), 25))
    assert "| Network | Site | Description |" in out and "10\\.0\\.24\\.0\\/24" in out
    assert "Scope by site" not in out and "SHA\\-256" not in out and "SHA-256" not in out


@needs_templates
def test_the_shipped_templates_declare_the_default_cutoff(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(ROOT))
    for name in ("pentest", "executive-brief"):
        t = templates.get_template(name)
        assert (t.scope_inline_max, t.scope_domains_inline_max) == (25, 25)
    # The worklist prints no scope, so it never names a scope file.
    w = templates.get_template("remediation-worklist")
    assert (w.scope_inline_max, w.scope_domains_inline_max) == (None, None)
