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
