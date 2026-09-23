"""Review 2026-09-23 B-Ops-5 — the report's "missing report text" list meets
AI drafting: ``POST /reports/draft/finding-text`` suggests Markdown for one
finding's empty sections.  Nothing is written; the author saves through the
finding update, so only someone who may edit that text may ask."""
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
    assert r.json()["suggestions"] == {
        "impact": "An attacker can relay NTLM.", "recommendation": "Require signing.",
    }
    # Asked for exactly the empty required sections, with the written one as context.
    assert '["impact", "recommendation"]' in seen["user"]
    assert "Hosts accept unsigned SMB sessions." in seen["user"]
    db_session.refresh(finding)
    assert finding.impact is None and finding.recommendation is None

    # Explicit fields are honoured; an unknown field is refused by the schema.
    answer["content"] = '{"steps_to_reproduce": "1. Run nxc smb."}'
    r = client.post(_url(test_project), json={"finding_id": fid, "fields": ["steps_to_reproduce"]})
    assert r.json()["suggestions"] == {"steps_to_reproduce": "1. Run nxc smb."}
    r = client.post(_url(test_project), json={"finding_id": fid, "fields": ["cvss_score"]})
    assert r.status_code == 422

    # An answer that is not the requested JSON is a 502, not an empty success.
    answer["content"] = "I cannot help with that."
    r = client.post(_url(test_project), json={"finding_id": fid})
    assert r.status_code == 502


def test_only_someone_who_may_edit_the_text_may_draft_it(client, db_session, test_project, llm):
    answer, _ = llm
    answer["content"] = '{"impact": "x", "recommendation": "y", "description": "z"}'
    alice = _member(db_session, test_project, 311, "alice", ProjectRole.ANALYST)
    bob = _member(db_session, test_project, 312, "bob", ProjectRole.ANALYST)
    padmin = _member(db_session, test_project, 313, "padmin", ProjectRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: alice
    fid = client.post(f"/api/v1/projects/{test_project.id}/findings",
                      json={"title": "Weak TLS", "severity": "medium"}).json()["id"]

    app.dependency_overrides[get_current_user] = lambda: bob
    assert client.post(_url(test_project), json={"finding_id": fid}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: padmin
    assert client.post(_url(test_project), json={"finding_id": fid}).status_code == 200
    assert client.post(_url(test_project), json={"finding_id": 999999}).status_code == 404


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
