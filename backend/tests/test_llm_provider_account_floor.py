"""Owner, 2026-10-11 (review 2026-10-10, H13) — "why can a viewer register an
LLM provider at all?"

A provider is per-user and self-service, and its only use is drafting report
text, which needs project ANALYST.  So adding, changing, testing or using one
needs an account that can write in at least one project (or a global admin);
an account that is viewer / auditor everywhere is refused, and can still list
and delete its own.  And the connection test no longer tells open from
refused from timed out for an address the caller chose.
"""
import httpx
import pytest

from app.api.deps import can_write_in_some_project, get_current_user
from app.api.v1.endpoints.llm_providers import PROVIDER_NEEDS_A_WRITER
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.main import app
from app.services import llm_provider_service
from app.services.llm_provider_service import PROVIDER_UNREACHABLE, LLMProviderService

URL = "/api/v1/llm-providers"
OLLAMA = {"name": "lab ollama", "provider_type": "ollama", "base_url": "http://10.1.2.3:8080"}


def _account(db, uid, memberships=(), role=UserRole.MEMBER):
    user = User(id=uid, username=f"acct-{uid}", email=f"acct-{uid}@example.com",
                hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=True)
    db.add(user)
    db.flush()
    for project, project_role in memberships:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=project_role))
    db.commit()
    return user


@pytest.fixture()
def world(client, db_session, test_project):
    second = Project(name="second-h13", slug="second-h13")
    archived = Project(name="archived-h13", slug="archived-h13", status="archived")
    db_session.add_all([second, archived])
    db_session.flush()
    return {
        "reader": _account(db_session, 97001, [(test_project, "viewer"), (second, "auditor")]),
        "nobody": _account(db_session, 97002),
        "analyst": _account(db_session, 97003, [(test_project, "viewer"), (second, "analyst")]),
        "project_admin": _account(db_session, 97004, [(second, "admin")]),
        "archived_analyst": _account(db_session, 97005, [(archived, "analyst")]),
    }


@pytest.fixture()
def acting_as(client):
    def switch(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return switch


def _own_provider(db, user):
    return LLMProviderService(db).create(
        user_id=user.id, name="from before", provider_type="ollama",
        base_url="http://10.1.2.3:8080", model_id=None, api_key_plaintext=None,
        extra_config=None, is_default=False,
    )


@pytest.mark.parametrize("who", ["reader", "nobody"])
def test_an_account_that_writes_nowhere_cannot_add_change_test_or_use_one(
    client, db_session, world, acting_as, monkeypatch, who,
):
    user = world[who]
    existing = _own_provider(db_session, user)        # e.g. made before a demotion
    called = []
    monkeypatch.setattr(llm_provider_service, "safe_request", lambda *a, **k: called.append(a))
    acting_as(user)

    attempts = {
        "create": client.post(f"{URL}/", json=OLLAMA),
        "update": client.patch(f"{URL}/{existing.id}", json={"base_url": "http://10.1.2.4:22"}),
        "test": client.post(f"{URL}/{existing.id}/test"),
        "complete": client.post(f"{URL}/{existing.id}/complete", json={"prompt": "hello"}),
    }
    for name, resp in attempts.items():
        assert resp.status_code == 403, f"{who} {name}: {resp.status_code} {resp.text[:200]}"
        assert resp.json()["detail"] == PROVIDER_NEEDS_A_WRITER
    assert "analyst" in PROVIDER_NEEDS_A_WRITER and "draft" in PROVIDER_NEEDS_A_WRITER
    assert called == []                                # the server sent nothing

    # Their own are still theirs to see and to remove.
    listed = client.get(f"{URL}/")
    assert listed.status_code == 200 and [p["id"] for p in listed.json()] == [existing.id]
    assert listed.json()[0]["base_url"] == "http://10.1.2.3:8080"     # unchanged
    assert client.get(f"{URL}/types").status_code == 200
    assert client.delete(f"{URL}/{existing.id}").status_code == 204
    assert client.get(f"{URL}/").json() == []


@pytest.mark.parametrize("who", ["analyst", "project_admin", "archived_analyst"])
def test_an_account_that_writes_in_one_project_manages_its_providers(
    client, world, acting_as, monkeypatch, who,
):
    acting_as(world[who])
    created = client.post(f"{URL}/", json=OLLAMA)
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert client.patch(f"{URL}/{pid}", json={"name": "renamed"}).json()["name"] == "renamed"

    class Answer:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{}, {}]}

    monkeypatch.setattr(llm_provider_service, "safe_request", lambda *a, **k: Answer())
    tested = client.post(f"{URL}/{pid}/test")
    assert tested.status_code == 200 and tested.json()["ok"] is True
    assert client.delete(f"{URL}/{pid}").status_code == 204


def test_the_global_admin_needs_no_membership(client, test_user):
    assert client.post(f"{URL}/", json=OLLAMA).status_code == 201     # ``client`` is the admin


def test_who_can_write_somewhere(db_session, world, test_user):
    assert can_write_in_some_project(db_session, test_user)
    assert {name: can_write_in_some_project(db_session, user) for name, user in world.items()} == {
        "reader": False, "nobody": False, "analyst": True, "project_admin": True, "archived_analyst": True,
    }


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("[Errno 111] Connection refused"),
    httpx.ConnectTimeout("timed out"),
    httpx.ReadTimeout("timed out"),
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ValueError("Host '10.1.2.3' resolves to a private address"),
    TimeoutError("request budget exceeded"),
    RuntimeError("anything else"),
])
def test_a_failed_connection_answers_one_fixed_text(client, world, acting_as, monkeypatch, failure):
    """Refused, timed out and "something that is not HTTP answered" read the
    same: the test is not a port scanner for whatever the address names."""
    acting_as(world["analyst"])
    pid = client.post(f"{URL}/", json=OLLAMA).json()["id"]

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(llm_provider_service, "safe_request", fail)
    resp = client.post(f"{URL}/{pid}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": False, "detail": PROVIDER_UNREACHABLE}
    for leaked in ("refused", "timed out", "10.1.2.3", "private", "disconnected", "anything else", "budget"):
        assert leaked not in resp.text


def test_a_provider_that_answered_still_says_its_status(client, world, acting_as, monkeypatch):
    acting_as(world["analyst"])
    pid = client.post(f"{URL}/", json=OLLAMA).json()["id"]

    def unauthorised(method, url, **kwargs):
        request = httpx.Request(method, url)
        return httpx.Response(401, request=request, text="bad key sk-echoed")

    monkeypatch.setattr(llm_provider_service, "safe_request", unauthorised)
    resp = client.post(f"{URL}/{pid}/test")
    assert resp.json() == {"ok": False, "detail": "Provider returned HTTP 401."}
