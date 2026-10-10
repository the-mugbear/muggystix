"""Report text is read by someone who has never seen BlueStick (the owner,
2026-10-08: an accepted proposal printed "Finding #277" in a client report).

One definition — ``report_text.internal_references`` — and three readers: a
proposal naming a BlueStick record is refused, the in-app drafter declines the
section, and a report's summary lists the text that already holds one.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import AgentProposal
from app.services import report_draft_service
from app.services.report_draft_service import ReportDraftService
from app.services.report_text import internal_references


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _host(db, project, ip="10.41.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _finding(client, project, host=None, title="SMB signing not required"):
    r = client.post(f"/api/v1/projects/{project.id}/findings",
                    json={"title": title, "severity": "medium", "host_ids": [host.id] if host else []})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("text, found", [
    ("As described in Finding #277, signing is off.", ["Finding #277"]),
    ("See findings #12 and evidence record 57.", ["findings #12", "evidence record 57"]),
    ("Confirmed by host test #9 on Host #4512.", ["host test #9", "Host #4512"]),
    ("Evidence #3 and evidence   #3 again.", ["Evidence #3"]),
    ("Recorded in BlueStick as proposal id: 88.", ["BlueStick", "proposal id: 88"]),
    ("The scanner observation #40 was promoted.", ["scanner observation #40"]),
])
def test_a_record_named_by_its_number_is_found(text, found):
    assert internal_references(text) == found


@pytest.mark.parametrize("text", [
    "",
    "The session ID 4821 is predictable, and user_id=5 returns another account.",
    "CVE-2024-3094 affects 10.0.0.5; see MS17-010 and KB5005565.",
    "Two findings on this host share a cause; 3 hosts answer on port 445.",
    "Test #2 repeated the request with host_id=7 in the body.",
    "Run it:\n\n```\n$ tool --list\nFinding #277 open\n```\n\nand `evidence #3` is the tool's own label.",
])
def test_the_clients_own_subject_matter_and_code_are_left_alone(text):
    assert internal_references(text) == []


def test_a_proposed_section_that_names_a_record_is_refused_and_nothing_is_stored(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(client, test_project, _host(db_session, test_project))
    url = "/api/v1/agent/proposals/finding-text"

    r = client.post(url, headers=key, json={
        "finding_id": finding["id"],
        "fields": {"impact": "Relay attacks.", "description": "Related to Finding #277: signing is off."},
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("description: “Finding #277”") and "by its title" in detail
    # All-or-nothing: the section that was fine is not stored either.
    assert db_session.query(AgentProposal).count() == 0

    # The ids belong in the rationale, which is never report text.
    r = client.post(url, headers=key, json={
        "finding_id": finding["id"], "fields": {"description": "SMB signing is not required on 10.41.0.1."},
        "rationale": "Same cause as finding #277; evidence record 57 shows it.",
    })
    assert r.status_code == 201, r.text


def test_a_proposed_findings_report_text_is_held_to_the_same_rule(client, db_session, test_project):
    key = _start(client, test_project)
    host = _host(db_session, test_project)
    body = {"title": "Default credentials", "severity": "high", "host_ids": [host.id]}

    r = client.post("/api/v1/agent/proposals/finding", headers=key,
                    json={**body, "report_text": {"impact": "See evidence record 12 in BlueStick."}})
    assert r.status_code == 422, r.text
    assert "impact: “evidence record 12”, “BlueStick”" in r.json()["detail"]
    assert db_session.query(AgentProposal).count() == 0

    r = client.post("/api/v1/agent/proposals/finding", headers=key,
                    json={**body, "report_text": {"impact": "An attacker signs in as admin."}})
    assert r.status_code == 201, r.text


def test_the_drafter_declines_a_section_that_names_a_record(client, db_session, test_project, monkeypatch):
    seen = {}

    def fake_chat(provider, *, system, messages, max_tokens, temperature):
        seen["system"] = system
        return {"content": json.dumps({
            "description": "TLS 1.0 is accepted on the portal.",
            "impact": "As noted under Finding #31, traffic can be read.",
            "recommendation": "Disable TLS 1.0.",
        }), "raw": {}}

    monkeypatch.setattr(report_draft_service, "chat_completion", fake_chat)
    monkeypatch.setattr(ReportDraftService, "_provider",
                        lambda self, pid: SimpleNamespace(id=9, provider_type="openai", model_id="m"))
    fid = _finding(client, test_project, title="Weak TLS")["id"]

    r = client.post(f"/api/v1/projects/{test_project.id}/reports/draft/finding-text", json={"finding_id": fid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(p["field"] for p in body["proposals"]) == ["description", "recommendation"]
    assert list(body["declined"]) == ["impact"]
    assert "“Finding #31”" in body["declined"]["impact"]
    assert all("Finding #31" not in p["payload"]["value"] for p in body["proposals"])
    # The model is told who reads it.
    assert "has never seen the assessment tooling" in seen["system"]


def test_a_report_lists_the_written_text_that_names_a_record(client, db_session, test_project, tmp_path, monkeypatch):
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({"title": "T", "entry": "report.qmd", "formats": ["html"]}))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(tmp_path / "report-templates"))
    host = _host(db_session, test_project)
    clean = Finding(project_id=test_project.id, title="Clean", severity="high", status="confirmed",
                    source="manual", description="d", impact="i", recommendation="r")
    named = Finding(project_id=test_project.id, title="Named", severity="low", status="confirmed",
                    source="manual", description="Same root cause as Finding #277.", impact="i",
                    recommendation="See host #4 and Finding #277.")
    db_session.add_all([clean, named])
    db_session.flush()
    for f in (clean, named):
        db_session.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db_session.commit()

    r = client.post(f"/api/v1/projects/{test_project.id}/client-reports", json={"kind": "full"})
    assert r.status_code == 201, r.text
    assert r.json()["summary"]["internal_references"] == [{
        "id": named.id, "ref": "F-02", "title": "Named", "fields": [
            {"field": "description", "phrases": ["Finding #277"]},
            {"field": "recommendation", "phrases": ["host #4", "Finding #277"]},
        ],
    }]
    # A warning, never a block.
    assert client.post(
        f"/api/v1/projects/{test_project.id}/client-reports/{r.json()['id']}/issue").status_code == 200


def test_every_report_writing_surface_says_who_the_reader_is():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.services.agents_guide_service import read_agent_guide

    assert "THE READER HAS NEVER SEEN BLUESTICK" in TOOLS["propose_finding_text"]["description"]
    assert "has never seen BlueStick" in TOOLS["propose_finding"]["description"]
    guide = read_agent_guide()
    if guide is None:
        pytest.skip("the agent guide is not mounted in this environment")
    assert "**The reader has never seen BlueStick.**" in guide


def test_a_finding_written_by_hand_needs_a_title(client, db_session, test_project):
    host = _host(db_session, test_project)
    url = f"/api/v1/projects/{test_project.id}/findings"
    r = client.post(url, json={"title": "   ", "severity": "low", "host_ids": [host.id]})
    assert r.status_code == 422 and r.json()["detail"] == "A finding needs a title."
    r = client.post(url, json={"title": "  Shared local admin password  ", "severity": "high",
                               "status": "confirmed", "host_ids": [host.id]})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Shared local admin password" and r.json()["status"] == "confirmed"
    assert [h["host_id"] for h in r.json()["hosts"]] == [host.id]
