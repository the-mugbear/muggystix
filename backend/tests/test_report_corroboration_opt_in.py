"""Which tools observed a finding is printed only when the template asks.

Owner, 2026-10-14: the report showed "Observed by Nessus" on every finding.
A finding's ``corroboration`` (the scanners whose rows it was promoted from)
is in the report's data only for a template whose ``template.json`` sets
``"corroboration": true``; otherwise it is empty, so nothing names the
identifying tool and nothing of it is frozen at issue.  No shipped template
asks.
"""
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import report_template_service as templates
from app.services.client_report_service import ClientReportService
from tests.test_report_review_2026_10_01 import _build, _create, _finding, _host

PRINTS_CORROBORATION = (
    "---\ntitle: x\n---\n"
    "<% for f in findings %><% if f.corroboration %>Observed by << f.corroboration|join(', ') >><% endif %>"
    "<< md(f, 'description') >><% endfor %>\n"
)


@pytest.fixture(autouse=True)
def template_dir(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    for name, extra in (("plain", {}), ("observed", {"corroboration": True})):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "template.json").write_text(json.dumps({
            "title": name, "entry": "report.qmd", "formats": ["html", "docx"], **extra,
        }))
        (folder / "report.qmd").write_text(PRINTS_CORROBORATION)
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    return root


@pytest.fixture
def seen_by_nessus(monkeypatch):
    """Every finding was promoted from a Nessus row (the lookup itself is
    the service's; what is tested here is who gets it)."""
    asked = []

    def corroboration(self, finding_ids):
        asked.append(list(finding_ids))
        return {fid: ["Nessus"] for fid in finding_ids}

    monkeypatch.setattr(ClientReportService, "_corroboration", corroboration)
    return asked


def test_a_template_that_does_not_ask_names_no_tool(client, db_session, test_project, seen_by_nessus):
    host = _host(db_session, test_project, "10.66.0.1")
    _finding(db_session, test_project, "SMB signing not required", hosts=[host])
    dataset, _, _ = _build(db_session, _create(client, test_project, template="plain")["id"])
    assert dataset["findings"][0]["corroboration"] == []
    assert seen_by_nessus == []                                      # not even looked up


def test_a_template_that_asks_gets_the_tools(client, db_session, test_project, seen_by_nessus):
    host = _host(db_session, test_project, "10.66.0.2")
    _finding(db_session, test_project, "SMB signing not required", hosts=[host])
    dataset, _, _ = _build(db_session, _create(client, test_project, template="observed")["id"])
    assert dataset["findings"][0]["corroboration"] == ["Nessus"]
    assert templates.get_template("observed").corroboration is True
    assert templates.get_template("plain").corroboration is False


def test_no_shipped_template_asks():
    shipped = Path(__file__).resolve().parents[2] / "report-templates"
    manifests = sorted(shipped.glob("*/template.json"))
    if not manifests:
        pytest.skip("the shipped templates are not mounted here")
    for manifest in manifests:
        assert json.loads(manifest.read_text()).get("corroboration") is not True, manifest.parent.name
