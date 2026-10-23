"""The request-body cap that runs before authentication (review 2026-10-07)."""
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.body_limit import RequestBodyLimitMiddleware, allows_large_body

LIMIT = 1024


def _app():
    seen = {"bytes": 0}

    async def echo(request: Request):
        body = await request.body()
        seen["bytes"] = len(body)
        return JSONResponse({"bytes": len(body)})

    paths = ["/api/v1/projects/1/findings", "/api/v1/projects/1/upload/", "/api/v1/agent/uploads"]
    app = Starlette(routes=[Route(p, echo, methods=["POST"]) for p in paths])
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=LIMIT)
    return app, seen


def test_a_declared_body_over_the_limit_is_refused_before_the_route_reads_it():
    app, seen = _app()
    r = TestClient(app).post("/api/v1/projects/1/findings", content=b"x" * (LIMIT + 1))
    assert r.status_code == 413
    assert str(LIMIT) in r.json()["detail"]
    assert seen["bytes"] == 0


def test_a_body_at_the_limit_passes():
    app, _ = _app()
    r = TestClient(app).post("/api/v1/projects/1/findings", content=b"x" * LIMIT)
    assert r.status_code == 200 and r.json() == {"bytes": LIMIT}


def test_a_chunked_body_is_counted_as_it_arrives():
    """No Content-Length to trust: the route never gets the whole body."""
    app, seen = _app()

    def chunks():
        for _ in range(8):
            yield b"x" * 512

    r = TestClient(app).post("/api/v1/projects/1/findings", content=chunks())
    assert r.status_code == 413
    assert seen["bytes"] == 0


def test_scan_uploads_are_not_capped_here():
    app, _ = _app()
    for path in ("/api/v1/projects/1/upload/", "/api/v1/agent/uploads"):
        r = TestClient(app).post(path, content=b"x" * (LIMIT * 4))
        assert r.status_code == 200, path


def test_only_the_two_scan_upload_routes_take_a_large_body():
    assert allows_large_body("/api/v1/projects/12/upload/")
    assert allows_large_body("/api/v1/projects/12/upload")
    assert allows_large_body("/api/v1/agent/uploads")
    for path in (
        "/api/v1/projects/12/upload/jobs/3/start",
        "/api/v1/agent/evidence",
        "/api/v1/auth/login",
        "/api/v1/projects/12/findings",
        "/api/v1/projects/x/upload/",
    ):
        assert not allows_large_body(path), path


# --- The two exempt uploads are refused before their body is read when they
#     carry no credential that could pass (review 2026-10-10, S3). ---

def _gated_app(refusal):
    seen = {"read": False}

    async def upload(request: Request):
        await request.body()
        seen["read"] = True
        return JSONResponse({"ok": True})

    paths = ["/api/v1/projects/1/upload/", "/api/v1/agent/uploads"]
    app = Starlette(routes=[Route(p, upload, methods=["POST"]) for p in paths])
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=LIMIT, large_body_refusal=refusal)
    return app, seen


def test_a_refused_upload_is_a_401_and_its_body_is_never_read():
    asked = []

    def refusal(path, headers):
        asked.append((path, headers.get("authorization")))
        if headers.get("authorization") == "Bearer good":
            return None
        if headers.get("authorization") == "Bearer viewer":
            return 403, "Insufficient project role. Required: analyst"
        return 401, "Not authenticated"

    app, seen = _gated_app(refusal)

    def body():
        seen["streamed"] = True
        yield b"x" * (LIMIT * 4)

    r = TestClient(app).post("/api/v1/agent/uploads", content=body())
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
    assert seen["read"] is False
    r = TestClient(app).post("/api/v1/projects/1/upload/", content=b"x" * (LIMIT * 4),
                             headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and seen["read"] is True
    assert [p for p, _ in asked] == ["/api/v1/agent/uploads", "/api/v1/projects/1/upload/"]
    # A caller the route would refuse for its role is answered as the route
    # would answer — before a byte of the body is read (review 2026-10-23b, PLT-2).
    seen["read"] = False
    r = TestClient(app).post("/api/v1/projects/1/upload/", content=body(),
                             headers={"Authorization": "Bearer viewer"})
    assert r.status_code == 403 and r.json() == {"detail": "Insufficient project role. Required: analyst"}
    assert seen["read"] is False


def test_the_page_upload_needs_a_token_that_verifies(db_session, test_user):
    from app.api.deps import TWO_FACTOR_CHALLENGE_PURPOSE, large_upload_refusal
    from app.core.security import create_access_token, create_session, verify_token

    path = "/api/v1/projects/1/upload/"
    good = create_access_token({"sub": str(test_user.id)})
    create_session(db_session, test_user, verify_token(good)["jti"])
    challenge = create_access_token({"sub": str(test_user.id), "purpose": TWO_FACTOR_CHALLENGE_PURPOSE})
    assert large_upload_refusal(path, {}) == (401, "Not authenticated")
    assert large_upload_refusal(path, {"authorization": "Basic abc"}) == (401, "Not authenticated")
    assert large_upload_refusal(path, {"authorization": "Bearer not-a-jwt"}) == (401, "Invalid token")
    assert large_upload_refusal(path, {"authorization": f"Bearer {challenge}"}) == (
        401, "Two-factor authentication not completed",
    )
    assert large_upload_refusal(path, {"authorization": f"Bearer {good}"}) is None


def test_the_agent_upload_needs_a_key_that_exists(client, db_session, test_project):
    from app.api.deps import large_upload_refusal

    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    key = started.json()["api_key"]
    path = "/api/v1/agent/uploads"
    status, detail = large_upload_refusal(path, {})
    assert status == 401 and detail.startswith("Missing agent API key")
    assert large_upload_refusal(path, {"x-api-key": "nm_agent_made_up"}) == (401, "Invalid agent API key")
    assert large_upload_refusal(path, {"x-api-key": key}) is None
    assert large_upload_refusal(path, {"authorization": f"Bearer {key}"}) is None


def test_the_application_refuses_a_keyless_upload_before_reading_it(client):
    from app.main import app

    middleware = next(m for m in app.user_middleware if m.cls is RequestBodyLimitMiddleware)
    from app.api.deps import large_upload_refusal

    assert middleware.kwargs["large_body_refusal"] is large_upload_refusal
    r = client.post("/api/v1/agent/uploads", files={"file": ("a.xml", b"<x/>" * 1000)})
    assert r.status_code == 401
    assert r.json()["detail"].startswith("Missing agent API key")


def test_the_application_has_the_cap_and_refuses_before_auth(client):
    from app.core.config import settings
    from app.main import app

    assert any(m.cls is RequestBodyLimitMiddleware for m in app.user_middleware)
    # No credentials: the answer is the size refusal, not a 401 after the read.
    r = client.post(
        "/api/v1/agent/evidence",
        content=b"{",
        headers={"content-length": str(settings.MAX_REQUEST_BODY_BYTES + 1),
                 "content-type": "application/json"},
    )
    assert r.status_code == 413
