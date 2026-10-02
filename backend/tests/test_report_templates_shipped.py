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


# --- review 2026-10-01 B8: how a finding was confirmed ---------------------------------

@needs_templates
def test_the_pentest_report_prints_how_a_finding_was_confirmed():
    sample = _sample("pentest")
    out = _fill("pentest", sample)
    smb = out.split("{#finding-11}")[1].split("{#finding-12}")[0]
    evidence = smb.split("Evidence / proof of concept")[1].split("Recommendations")[0]
    # Who, what, where and when are printed values (escaped) …
    assert "**nmap** against 192\\.0\\.2\\.10, 2026\\-09\\-18, by Alex Tester" in evidence
    assert "The SMB service answers the vulnerable request without authentication\\." in evidence
    # … the command and the output are placeholders the filter fills verbatim:
    # neither is in the source.
    assert '::: {.bs-code key="findings.0.confirmations.0.command"}\n:::' in evidence
    assert '::: {.bs-code key="findings.0.confirmations.0.output"}\n:::' in evidence
    assert "Output (excerpt):" in evidence
    assert "smb-vuln-ms17-010" not in out and "smb\\-vuln" not in out
    # A confirmed test is the evidence: no "mark the screenshots" TODO for it.
    assert "TODO" not in evidence
    # A finding with neither still asks for evidence; an agent's record names its operator.
    tls = out.split("{#finding-13}")[1].split("{#finding-14}")[0]
    assert "bs-code" not in tls and "Mark the evidence screenshots" in tls
    assert "by Sam Analyst \\(agent session 7\\)" in out


@needs_templates
def test_results_that_were_not_printed_are_counted_and_old_reports_still_fill():
    data = _sample("pentest")
    data["findings"][0]["confirmations_omitted"] = 4
    assert "*4 further test result(s) confirming this finding are not printed.*" in _fill("pentest", data)
    # A report issued before the list existed carries no such keys.
    old = _sample("pentest")
    for f in old["findings"]:
        for key in ("confirmations", "confirmations_omitted", "previous_severity", "previous_severity_label"):
            f.pop(key)
    out = _fill("pentest", old)
    assert "bs-code" not in out and "Mark the evidence screenshots" in out
    for name in ("executive-brief", "remediation-worklist"):
        assert _fill(name, old)


@needs_templates
def test_only_the_pentest_template_asks_for_test_results():
    """Opt-in per template (template.json → evidence_records): the brief leaves
    evidence out by design and the worklist is a list of fixes."""
    asked = {
        name: json.loads((ROOT / name / "template.json").read_text()).get("evidence_records")
        for name in ("pentest", "executive-brief", "remediation-worklist")
    }
    assert asked == {"pentest": True, "executive-brief": False, "remediation-worklist": False}
    for name in ("executive-brief", "remediation-worklist"):
        data = _sample(name)
        # Even handed some, they print none.
        data["findings"][0]["confirmations"] = _sample("pentest")["findings"][0]["confirmations"]
        assert "bs-code" not in _fill(name, data)


# --- owner's decision 2026-10-01 (B17): a re-rated finding in an addendum ----------------

def _rerated(sample: dict, *, also_new_hosts: bool = False) -> dict:
    """An addendum in which F-03 (reported as Medium) is now High, and nothing
    else changed — or, with ``also_new_hosts``, F-02 was re-rated from Low AND
    is on a further system."""
    data = copy.deepcopy(sample)
    data["report"].update({
        "kind": "addendum", "title": "Addendum", "number": 2,
        "baseline": {"number": 1, "title": sample["report"]["title"], "date": "2026-09-23"},
    })
    tls = data["findings"][2]
    tls.update({"change": "severity_changed", "severity": "high", "severity_label": "High",
                "previous_severity": "medium", "previous_severity_label": "Medium", "status": "confirmed",
                "status_note": None})
    shown = [tls]
    if also_new_hosts:
        ldap = data["findings"][1]
        ldap.update({"change": "new_hosts", "previous_severity": "low", "previous_severity_label": "Low",
                     "new_affected": [{"address": "192.0.2.99", "hostname": "app09", "name": None,
                                       "port": "389/tcp", "state": None}]})
        shown = [ldap, tls]
    data["findings"] = shown
    for i, f in enumerate(shown):
        f["_path"] = f"findings.{i}"
    data["delta"] = {
        "new_findings": 0, "findings_with_new_endpoints": int(also_new_hosts),
        "findings_with_changed_severity": len(shown), "withdrawn": [],
    }
    return data


@needs_templates
def test_the_pentest_addendum_lists_a_rerated_finding():
    out = _fill("pentest", _rerated(_sample("pentest")))
    assert "Nothing has changed" not in out
    assert "1 reported finding(s) whose severity changed" in out
    section = out.split("# Reported findings with a changed severity")[1].split("\n# ")[0]
    assert "| F\\-03 | TLS 1\\.0 accepted by the web portal | Medium | High |" in section
    assert "# New findings" not in out and "# Reported findings on further systems" not in out

    both = _fill("pentest", _rerated(_sample("pentest"), also_new_hosts=True))
    grown = both.split("# Reported findings on further systems")[1].split("\n# ")[0]
    assert "Severity changed: was Low in report 1, now High." in grown and "192\\.0\\.2\\.99" in grown
    # Listed once under its new systems, and named in the severity table too.
    assert both.count("## F\\-02 — ") == 1
    table = both.split("# Reported findings with a changed severity")[1].split("\n# ")[0]
    assert "| F\\-02 |" in table and "| F\\-03 |" in table
    assert "2 reported finding(s) whose severity changed" in both


@needs_templates
def test_an_addendum_with_no_change_at_all_still_says_so():
    data = _rerated(_sample("pentest"))
    data["findings"] = []
    data["delta"]["findings_with_changed_severity"] = 0
    out = _fill("pentest", data)
    assert "Nothing has changed since report 1." in out
    assert "# Reported findings with a changed severity" not in out
    brief = _rerated(_sample("executive-brief"))
    brief["findings"] = []
    assert "Nothing has changed since report 1." in _fill("executive-brief", brief)


@needs_templates
def test_the_brief_addendum_has_a_row_for_a_rerated_finding():
    out = _fill("executive-brief", _rerated(_sample("executive-brief")))
    assert "Nothing has changed" not in out
    assert "| Severity changed | F\\-03 | TLS 1\\.0 accepted by the web portal | High (was Medium) |" in out
    both = _fill("executive-brief", _rerated(_sample("executive-brief"), also_new_hosts=True))
    assert "| Found on 1 more system(s) | F\\-02 | Anonymous LDAP bind returns the directory | High (was Low) |" in both
    assert both.count("| F\\-02 |") == 1
    # An earlier addendum's data (no previous severity) prints as before.
    assert "(was" not in _fill("executive-brief", _addendum(_sample("executive-brief")))


@needs_templates
def test_the_worklist_addendum_names_a_rerated_finding_without_adding_work():
    out = _fill("remediation-worklist", _rerated(_sample("remediation-worklist")))
    section = out.split("# Severity changed since report 1")[1].split("\n# ")[0]
    assert "| F\\-03 | TLS 1\\.0 accepted by the web portal | Medium | High |" in section
    # Re-rating adds no system to fix.
    assert "Nothing to fix" in out and "## 198\\.51\\.100\\.5" not in out
    both = _fill("remediation-worklist", _rerated(_sample("remediation-worklist"), also_new_hosts=True))
    assert "## 192\\.0\\.2\\.99" in both
    table = both.split("# Severity changed since report 1")[1].split("\n# ")[0]
    assert "| F\\-02 |" in table and "| Low | High |" in table
    # A full report and an earlier addendum have no such section.
    assert "# Severity changed" not in _fill("remediation-worklist", _sample("remediation-worklist"))
    assert "# Severity changed" not in _fill("remediation-worklist", _addendum(_sample("remediation-worklist")))


# --- branch review 2026-10-01 S2: which evidence images a template prints ----------------

SHIPPED = ("pentest", "executive-brief", "remediation-worklist")
ALL_FIELDS = ["description", "impact", "recommendation", "references", "steps_to_reproduce"]


@needs_templates
def test_each_shipped_template_declares_the_images_it_prints(monkeypatch):
    """template.json → "images": stated once by the author, shown on the
    report page ("this template prints no evidence images").  It must be what
    the .qmd does: filled with an image placed in every written field and one
    placed nowhere, the fields printed with images and the trailing block are
    the declared ones.  (The rendered figures: test_quarto_render.py.)"""
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(ROOT))
    declared = {name: templates.get_template(name).images for name in SHIPPED}
    assert declared == {
        "pentest": {"fields": ALL_FIELDS, "trailing": True},
        "executive-brief": {"fields": [], "trailing": False},
        "remediation-worklist": {"fields": ["recommendation"], "trailing": False},
    }
    for name in SHIPPED:
        template = templates.get_template(name)
        assert json.loads((ROOT / name / "template.json").read_text()).get("images") is not None, name
        assert templates.image_declaration_problem(template, templates.image_probe_dataset(_sample(name))) is None
        assert template.as_dict()["images"] == declared[name]


@needs_templates
def test_a_declaration_that_is_not_what_the_template_does_is_named(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / "report-templates"
    shutil.copytree(ROOT / "remediation-worklist", root / "worklist")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    manifest = root / "worklist" / "template.json"
    data = json.loads(manifest.read_text())
    probe = templates.image_probe_dataset(_sample("remediation-worklist"))

    def problem(images):
        manifest.write_text(json.dumps({**data, "images": images}))
        return templates.image_declaration_problem(templates.get_template("worklist"), probe)

    assert problem({"fields": ["recommendation"], "trailing": False}) is None
    assert "prints the images placed in recommendation, which are not declared" in problem(
        {"fields": [], "trailing": False})
    assert "declares description, impact but prints no image placed there" in problem(
        {"fields": ["recommendation", "impact", "description"], "trailing": False})
    assert 'declares "trailing": true but prints no trailing evidence block' in problem(
        {"fields": ["recommendation"], "trailing": True})
    # Left out: taken to print everything — which this template does not.
    manifest.write_text(json.dumps({k: v for k, v in data.items() if k != "images"}))
    assumed = templates.get_template("worklist")
    assert assumed.images == {"fields": ALL_FIELDS, "trailing": True}
    assert templates.image_declaration_problem(assumed, probe) is not None


def test_an_unusable_images_declaration_keeps_the_template_from_being_offered(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    folder = root / "mine"
    folder.mkdir(parents=True)
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))

    def declare(images):
        (folder / "template.json").write_text(json.dumps({"title": "Mine", "formats": ["html"], "images": images}))

    for bad in (
        "all", [], {"fields": "all"}, {"trailing": True}, {"fields": "some", "trailing": True},
        {"fields": ["description", "summary"], "trailing": True}, {"fields": ["impact", "impact"], "trailing": True},
        {"fields": ["impact"], "trailing": "yes"}, {"fields": ["impact"], "trailing": 1},
        {"fields": [3], "trailing": False}, {"fields": "all", "trailing": True, "width": "6in"},
    ):
        declare(bad)
        assert templates.list_templates() == [], bad
        (problem,) = templates.template_problems()
        assert problem["name"] == "mine" and "images must be" in problem["error"], bad
        with pytest.raises(templates.TemplateError):
            templates.get_template("mine")
    # The usable forms; the fields come back in the one order.
    declare({"fields": "all", "trailing": False})
    assert templates.get_template("mine").images == {"fields": ALL_FIELDS, "trailing": False}
    declare({"fields": ["steps_to_reproduce", "description"], "trailing": True})
    assert templates.get_template("mine").images == {"fields": ["description", "steps_to_reproduce"], "trailing": True}
    declare({"fields": [], "trailing": False})
    assert templates.get_template("mine").images == {"fields": [], "trailing": False}
    assert templates.template_problems() == []


def _parts(name: str, data: dict) -> dict:
    return quarto_render.printed_parts(ROOT / name, "report.qmd", data)


@needs_templates
def test_what_each_template_prints_of_a_full_report_is_measured_from_its_fill():
    probe = templates.image_probe_dataset(_sample("pentest"))
    parts = _parts("pentest", probe)
    # Every finding in detail (the informational one in the appendix); the
    # first one's five sections with their images, and the unplaced image.
    assert parts["findings"] == [0, 1, 2, 3]
    assert parts["fields"][0] == {f: True for f in ALL_FIELDS}
    assert parts["figures"] == ["evidence/6.png"]
    assert parts["confirmations"] == [0, 1]          # the sample's findings with test results

    # The brief prints the recommendation of each finding above informational
    # — without its images — and nothing else of a finding.
    parts = _parts("executive-brief", templates.image_probe_dataset(_sample("executive-brief")))
    assert parts["fields"] == {i: {"recommendation": False} for i in (0, 1, 2)}
    assert parts["figures"] == [] and parts["confirmations"] == []

    # The worklist prints the recommendation of the findings still to FIX:
    # confirmed, above informational, with a system that is not remediated.
    sample = _sample("remediation-worklist")
    assert [(f["severity"], f["status"]) for f in sample["findings"]] == [
        ("critical", "confirmed"), ("high", "confirmed"), ("medium", "accepted_risk"), ("info", "confirmed"),
    ]
    parts = _parts("remediation-worklist", templates.image_probe_dataset(sample))
    assert parts["fields"] == {0: {"recommendation": True}, 1: {"recommendation": True}}
    assert parts["findings"] == [0, 1] and parts["figures"] == []
    # … so an image placed in the recommendation of the accepted-risk finding
    # prints nowhere, whatever the declaration says about recommendations.
    parts = _parts("remediation-worklist", templates.image_probe_dataset(sample, index=2))
    assert 2 not in parts["findings"]


@needs_templates
def test_an_addendum_prints_evidence_only_for_what_it_shows_in_detail():
    """pentest: a NEW finding in full; a finding on further systems, or
    re-rated, is a heading and a table — no section, image or test result.
    The worklist prints the recommendation of both; the brief of neither."""
    def addendum(name):
        data = _addendum(_sample(name))
        confirmations = _sample("pentest")["findings"][0]["confirmations"]
        for i, f in enumerate(data["findings"]):
            f["confirmations"] = [dict(c, _path=f"findings.{i}.confirmations.{n}") for n, c in enumerate(confirmations)]
        return data

    for index in (0, 1):                               # images on the new finding, then on the grown one
        probe = templates.image_probe_dataset(addendum("pentest"), index=index)
        parts = _parts("pentest", probe)
        assert parts["findings"] == [0] and parts["confirmations"] == [0]
        assert set(parts["fields"]) == {0}
        assert parts["figures"] == (["evidence/6.png"] if index == 0 else [])

    parts = _parts("remediation-worklist", addendum("remediation-worklist"))
    assert parts["fields"] == {0: {"recommendation": True}, 1: {"recommendation": True}}
    assert parts["confirmations"] == []
    parts = _parts("executive-brief", addendum("executive-brief"))
    assert parts == {"fields": {}, "figures": [], "confirmations": [], "findings": []}
