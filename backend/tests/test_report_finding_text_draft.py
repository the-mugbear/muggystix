"""Review 2026-09-23 B-Ops-5 — the report's "missing report text" list meets
AI drafting: ``POST /reports/draft/finding-text`` drafts Markdown for one
finding's empty sections.  Since v2.437.0 the draft is a set of
``finding_text`` PROPOSALS (source ``llm_draft``) — the same thing an MCP
agent produces, reviewed in the same place.  Nothing is written until the
finding's author (or a project admin) accepts."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import get_args

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.services import report_draft_service
from app.services.report_draft_service import (
    FINDING_TEXT_FIELDS,
    ReportDraftService,
    parse_finding_text_suggestions,
)


def _member(db_session, project, user_id, username, role):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


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
        lambda self, pid: SimpleNamespace(id=9, provider_type="openai", model_id="m"),
    )
    return answer, seen


def _url(project):
    return f"/api/v1/projects/{project.id}/reports/draft/finding-text"


def test_drafts_only_the_empty_required_sections_and_writes_nothing(client, db_session, test_project, llm):
    answer, seen = llm
    alice = _member(db_session, test_project, 301, "alice", ProjectRole.ANALYST)
    app.dependency_overrides[get_current_user] = lambda: alice
    r = client.post(f"/api/v1/projects/{test_project.id}/findings",
                    json={"title": "SMB signing not required", "severity": "high"})
    fid = r.json()["id"]
    finding = db_session.get(Finding, fid)
    finding.description = "Hosts accept unsigned SMB sessions."
    db_session.commit()

    answer["content"] = (
        "Here you go:\n```json\n"
        '{"impact": "An attacker can relay NTLM.", "recommendation": "Require signing.", '
        '"description": "ignored — not asked", "extra": "dropped"}\n```'
    )
    r = client.post(_url(test_project), json={"finding_id": fid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {p["field"]: p["payload"]["value"] for p in body["proposals"]} == {
        "impact": "An attacker can relay NTLM.", "recommendation": "Require signing.",
    }
    assert all(
        (p["status"], p["source"], p["agent_model"], p["agent_session_id"]) == ("pending", "llm_draft", "m", None)
        for p in body["proposals"]
    )
    # Asked for exactly the empty required sections, with the written one as context.
    assert '["impact", "recommendation"]' in seen["user"]
    assert "Hosts accept unsigned SMB sessions." in seen["user"]
    db_session.refresh(finding)
    assert finding.impact is None and finding.recommendation is None

    # Explicit fields are honoured; an unknown field is refused by the schema.
    answer["content"] = '{"steps_to_reproduce": "1. Run nxc smb."}'
    r = client.post(_url(test_project), json={"finding_id": fid, "fields": ["steps_to_reproduce"]})
    assert [(p["field"], p["payload"]["value"]) for p in r.json()["proposals"]] == [
        ("steps_to_reproduce", "1. Run nxc smb."),
    ]
    r = client.post(_url(test_project), json={"finding_id": fid, "fields": ["cvss_score"]})
    assert r.status_code == 422

    # An answer that is not the requested JSON is a 502, not an empty success.
    answer["content"] = "I cannot help with that."
    r = client.post(_url(test_project), json={"finding_id": fid})
    assert r.status_code == 502


def test_any_analyst_may_draft_but_only_the_author_accepts_and_is_told(client, db_session, test_project, llm):
    """Proposing changes nothing, so a colleague may draft (v2.437.0); the
    author is notified and decides; a viewer cannot draft."""
    from app.db.models_project import Notification
    answer, _ = llm
    answer["content"] = '{"impact": "x", "recommendation": "y", "description": "z"}'
    alice = _member(db_session, test_project, 311, "alice", ProjectRole.ANALYST)
    bob = _member(db_session, test_project, 312, "bob", ProjectRole.ANALYST)
    vic = _member(db_session, test_project, 313, "vic", ProjectRole.VIEWER)
    app.dependency_overrides[get_current_user] = lambda: alice
    fid = client.post(f"/api/v1/projects/{test_project.id}/findings",
                      json={"title": "Weak TLS", "severity": "medium"}).json()["id"]

    app.dependency_overrides[get_current_user] = lambda: vic
    assert client.post(_url(test_project), json={"finding_id": fid}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: bob
    r = client.post(_url(test_project), json={"finding_id": fid})
    assert r.status_code == 200, r.text
    pid = r.json()["proposals"][0]["id"]
    assert client.post(_url(test_project), json={"finding_id": 999999}).status_code == 404
    # Bob cannot accept text on Alice's finding; Alice can.
    base = f"/api/v1/projects/{test_project.id}/proposals"
    assert client.post(f"{base}/{pid}/accept", json={}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: alice
    assert client.post(f"{base}/{pid}/accept", json={}).status_code == 200

    # ONE notification for Alice, not one per proposal, pointing at the finding.
    notes = db_session.query(Notification).filter(
        Notification.user_id == alice.id, Notification.type == "proposal").all()
    assert len(notes) == 1 and notes[0].finding_id == fid and "Weak TLS" in notes[0].title


def test_nothing_to_draft_when_every_required_section_is_written(client, db_session, test_project, llm):
    alice = _member(db_session, test_project, 321, "alice", ProjectRole.ANALYST)
    app.dependency_overrides[get_current_user] = lambda: alice
    fid = client.post(f"/api/v1/projects/{test_project.id}/findings",
                      json={"title": "Done", "severity": "low"}).json()["id"]
    f = db_session.get(Finding, fid)
    f.description, f.impact, f.recommendation = "d", "i", "r"
    db_session.commit()
    assert client.post(_url(test_project), json={"finding_id": fid}).status_code == 400


def test_parse_reads_json_in_a_fence_or_prose_and_drops_the_rest():
    assert parse_finding_text_suggestions('```json\n{"impact": " x "}\n```', ["impact"]) == {"impact": "x"}
    assert parse_finding_text_suggestions('Sure. {"impact": "x", "a": 1} Done.', ["impact", "a"]) == {"impact": "x"}
    assert parse_finding_text_suggestions('{"impact": ""}', ["impact"]) == {}
    assert parse_finding_text_suggestions("no json here", ["impact"]) == {}
    assert parse_finding_text_suggestions('{"impact": [1]}', ["impact"]) == {}


def test_the_request_schema_names_the_service_fields():
    from app.api.v1.endpoints.report_drafts import FindingTextField
    assert set(get_args(FindingTextField)) == set(FINDING_TEXT_FIELDS)


def test_a_section_the_data_does_not_support_is_declined_never_written(client, db_session, test_project, llm):
    """2.455.0 — the prompt used to say "where the data is too thin, say what is
    missing instead of inventing it", and that sentence became the proposal's
    value: accepted, it printed in the client report.  A section the model
    cannot support is now null with a reason under ``missing`` — no proposal,
    and the reason goes to the reviewer, never into a section."""
    answer, seen = llm
    alice = _member(db_session, test_project, 331, "alice", ProjectRole.ANALYST)
    app.dependency_overrides[get_current_user] = lambda: alice
    fid = client.post(f"/api/v1/projects/{test_project.id}/findings",
                      json={"title": "Weak TLS", "severity": "medium"}).json()["id"]

    answer["content"] = (
        '{"description": "TLS 1.0 is accepted on the portal.", "impact": null, "recommendation": null,'
        ' "missing": {"impact": "No evidence of what the portal protects or who reaches it.",'
        ' "recommendation": "  "}}'
    )
    r = client.post(_url(test_project), json={"finding_id": fid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["field"] for p in body["proposals"]] == ["description"]
    assert body["declined"] == {
        "impact": "No evidence of what the portal protects or who reaches it.",
        "recommendation": "The data does not support writing this section.",
    }
    # The reviewer reads what was left out beside the draft; no section holds it.
    assert "impact: No evidence of what the portal protects" in body["proposals"][0]["rationale"]
    assert all("No evidence" not in p["payload"]["value"] for p in body["proposals"])
    # The model is told it may decline, and that a section never holds a placeholder.
    assert '"missing"' in seen["user"] and "null" in seen["user"]
    assert "Declining a section is allowed" in seen["system"] and "placeholder" in seen["system"]

    # Declining everything is an answer, not a provider failure: nothing is proposed.
    answer["content"] = '{"description": null, "impact": null, "recommendation": null, "missing": {"description": "Nothing recorded."}}'
    r = client.post(_url(test_project), json={"finding_id": fid, "fields": ["impact", "recommendation"]})
    assert r.status_code == 200, r.text
    assert r.json()["proposals"] == []
    assert set(r.json()["declined"]) == {"impact", "recommendation"}


def test_parse_answer_separates_written_and_declined():
    from app.services.report_draft_service import parse_finding_text_answer
    written, declined = parse_finding_text_answer(
        '{"impact": "x", "description": null, "missing": {"description": "Need the version.", "steps": "n/a"}}',
        ["impact", "description", "recommendation"],
    )
    assert written == {"impact": "x"}
    # A field the answer never mentions is neither written nor declined.
    assert declined == {"description": "Need the version."}
    # An empty string is neither text nor a reason: declined with the default reason only if named.
    assert parse_finding_text_answer('{"impact": ""}', ["impact"]) == ({}, {"impact": "The data does not support writing this section."})
