""""Draft with AI" on the Report defaults page (2.543.0).

The executive summary is the project's since 2026-10-14, written on Report
defaults — which had no AI help: "Draft with AI…" was on a draft's own
override only.  ``POST /reports/draft/report-details`` drafts it from what a
NEW full report of the project would contain today, with the caller's LLM
provider, as ONE ``report_detail`` proposal (source ``llm_draft``) reviewed
like an agent's.  Nothing is written until someone accepts it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.models_proposals import AgentProposal
from app.db.models_reports import Report, ReportProfile
from app.services import report_draft_service
from app.services.report_draft_service import ReportDraftService
from tests.test_agent_assist_reporting import _host
from tests.test_client_reports import _finding, template_dir  # noqa: F401 — the fixture


@pytest.fixture
def llm(monkeypatch):
    """A stub provider whose answer the test sets; records the prompt."""
    seen = {}
    answer = {"content": ""}

    def fake_chat(provider, *, system, messages, max_tokens, temperature):
        seen["system"], seen["user"] = system, messages[0]["content"]
        return {"content": answer["content"], "raw": {}}

    monkeypatch.setattr(report_draft_service, "chat_completion", fake_chat)
    monkeypatch.setattr(
        ReportDraftService, "_provider",
        lambda self, pid: SimpleNamespace(id=9, provider_type="openai", model_id="m-1"),
    )
    return answer, seen


def _url(project):
    return f"/api/v1/projects/{project.id}/reports/draft/report-details"


def _defaults(client, project):
    return client.get(f"/api/v1/projects/{project.id}/client-reports/profile").json()


def _reportable(db, project):
    host = _host(db, project, "10.80.0.1")
    _finding(db, project, "SMB signing not required", "critical", hosts=[host],
             description="Unsigned SMB sessions are accepted.", impact="Relay.", recommendation="Require signing.")
    # Under investigation: a report does not print it, so the draft must not see it.
    _finding(db, project, "Unconfirmed rumour", "high", hosts=[host], status="open",
             description="Not confirmed.", impact="-", recommendation="-")


def test_the_summary_is_drafted_as_one_proposal_from_what_a_new_report_would_list(
    client, db_session, test_project, test_user, llm,
):
    answer, seen = llm
    _reportable(db_session, test_project)
    answer["content"] = "  The assessment found one critical weakness: SMB signing is not required.  "
    r = client.post(_url(test_project), json={"audience": "executives"})
    assert r.status_code == 200, r.text
    body = r.json()
    (row,) = body["proposals"]
    assert (row["kind"], row["field"], row["status"], row["source"]) == (
        "report_detail", "executive_summary", "pending", "llm_draft")
    assert row["payload"]["value"] == "The assessment found one critical weakness: SMB signing is not required."
    assert (row["agent_model"], row["agent_session_id"], body["declined"]) == ("m-1", None, {})
    assert "1 finding a new report of this project would list" in row["rationale"]

    # The prompt holds the report's own selection — and only that.
    context = json.loads(seen["user"].split("```json\n")[1].split("\n```")[0])
    assert [f["title"] for f in context["findings"]] == ["SMB signing not required"]
    assert context["severity_counts"] == {"critical": 1}
    assert "Intended audience: executives." in seen["user"]
    assert "EXECUTIVE SUMMARY" in seen["user"] and "never mention BlueStick" in seen["system"]

    # Nothing was written, and no report was made to draft from.
    assert _defaults(client, test_project)["executive_summary"] is None
    assert db_session.query(Report).filter(Report.project_id == test_project.id).count() == 0

    # Accepting it is the page's write; the defaults then say an AI draft wrote it.
    accepted = client.post(f"/api/v1/projects/{test_project.id}/proposals/{row['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    page = _defaults(client, test_project)
    assert page["executive_summary"] == row["payload"]["value"]
    source = page["from_proposals"]["executive_summary"]
    assert (source["source"], source["agent_session_id"], source["agent_model"]) == ("llm_draft", None, "m-1")
    assert source["proposed_by"] == (test_user.full_name or test_user.username)


def test_a_project_with_nothing_to_report_is_told_so_and_the_provider_is_not_called(
    client, db_session, test_project, llm,
):
    answer, seen = llm
    r = client.post(_url(test_project), json={})
    assert r.status_code == 400 and "no findings yet" in r.json()["detail"]
    assert seen == {}
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0


@pytest.mark.parametrize("content, reason", [
    ("As Finding #277 shows, the domain fell.", "means nothing to the report's reader"),   # names a record
    ("The summary as it stands.", "current text"),                                          # changes nothing
])
def test_a_draft_that_cannot_be_a_proposal_is_declined_with_the_reason(
    client, db_session, test_project, llm, content, reason,
):
    answer, _ = llm
    _reportable(db_session, test_project)
    r = client.patch(f"/api/v1/projects/{test_project.id}/client-reports/profile",
                     json={"executive_summary": "The summary as it stands."})
    assert r.status_code == 200, r.text
    answer["content"] = content
    r = client.post(_url(test_project), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposals"] == [] and reason in body["declined"]["executive_summary"]
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0
    assert _defaults(client, test_project)["executive_summary"] == "The summary as it stands."


def test_a_provider_that_answers_nothing_is_a_502_and_nothing_is_proposed(client, db_session, test_project, llm):
    answer, _ = llm
    _reportable(db_session, test_project)
    answer["content"] = "   "
    r = client.post(_url(test_project), json={})
    assert r.status_code == 502, r.text
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0


def test_the_draft_follows_the_projects_default_template_and_makes_no_profile(client, db_session, test_project, llm):
    """The unsaved report is built with the project's template (the
    installation's default when the project names none) — and drafting never
    creates the defaults row."""
    answer, _ = llm
    _reportable(db_session, test_project)
    answer["content"] = "One critical weakness was found."
    assert client.post(_url(test_project), json={}).status_code == 200
    assert db_session.query(ReportProfile).filter(ReportProfile.project_id == test_project.id).count() == 0
