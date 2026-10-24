"""Profile pictures (2.530.0) — `services/profile_image_service`, the one writer.

Pins: only a real square PNG / JPEG of at most 512 KB is stored; the address
names the bytes (an old sha 404s after a replace); who may see a picture (the
person, users who share a project with them, global admins — anyone else the
same 404); the file lifecycle (replace, remove, user delete); the SAME bytes
twice keep the picture and its file; two replacements at once leave exactly
the file the row names; a restore of an earlier picture racing a
replacement's cleanup keeps its file.
"""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.main import app
from app.services import profile_image_service as svc

from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def png(side=256, height=None, tag=b"x"):
    """A minimal PNG the header reader accepts: signature, IHDR, a chunk, IEND."""
    height = side if height is None else height
    ihdr = struct.pack(">II", side, height) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0"
            + struct.pack(">I", len(tag)) + b"IDAT" + tag + b"\0\0\0\0"
            + struct.pack(">I", 0) + b"IEND" + b"\xaeB`\x82")


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path / "profile_images"


def _user(db, uid, name, role=UserRole.MEMBER):
    u = User(id=uid, username=name, email=f"{name}@example.com", full_name=name.title(), hashed_password="x",
             role=role, is_active=True, created_at=datetime.now(timezone.utc))
    db.add(u)
    db.commit()
    return u


def _put(client, data, ctype="image/png"):
    return client.put("/api/v1/users/profile/image", files={"file": ("me.png", data, ctype)})


def _files(uploads, uid):
    folder = uploads / str(uid)
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def test_a_real_square_picture_is_stored_and_named_by_its_bytes(client, test_user, uploads):
    data = png(tag=b"one")
    r = _put(client, data)
    assert r.status_code == 200, r.text
    sha = hashlib.sha256(data).hexdigest()
    assert r.json() == {"profile_image": sha}
    assert _files(uploads, test_user.id) == [f"{sha}.png"]
    got = client.get(f"/api/v1/users/{test_user.id}/profile-image/{sha}")
    assert got.status_code == 200 and got.content == data
    assert "immutable" in got.headers["cache-control"]
    assert client.get("/api/v1/auth/profile").json()["profile_image"] == sha


@pytest.mark.parametrize("data,ctype,code", [
    (b"GIF89a....", "image/gif", 400),                      # not PNG / JPEG
    (b"not a png at all", "image/png", 400),                # renamed
    (png(256, 200), "image/png", 400),                      # not square
    (png(2048), "image/png", 400),                          # too large a side
    (png(tag=b"x" * (600 * 1024)), "image/png", 413),       # over 512 KB
    (png(256)[:-12], "image/png", 400),                     # truncated (no IEND)
])
def test_anything_else_is_refused(client, test_user, uploads, data, ctype, code):
    assert _put(client, data, ctype).status_code == code
    assert _files(uploads, test_user.id) == []


def test_a_replacement_is_a_new_address_and_the_old_file_goes(client, test_user, uploads):
    first, second = png(tag=b"one"), png(tag=b"two")
    old = _put(client, first).json()["profile_image"]
    new = _put(client, second).json()["profile_image"]
    assert old != new
    assert client.get(f"/api/v1/users/{test_user.id}/profile-image/{old}").status_code == 404
    assert client.get(f"/api/v1/users/{test_user.id}/profile-image/{new}").status_code == 200
    assert _files(uploads, test_user.id) == [f"{new}.png"]


def test_the_same_bytes_twice_keep_the_picture_and_its_file(client, test_user, uploads):
    data = png(tag=b"same")
    sha = _put(client, data).json()["profile_image"]
    assert _put(client, data).json()["profile_image"] == sha
    assert _files(uploads, test_user.id) == [f"{sha}.png"]
    assert client.get(f"/api/v1/users/{test_user.id}/profile-image/{sha}").status_code == 200


def test_remove_and_user_delete_take_the_file(client, db_session, test_user, uploads):
    _put(client, png(tag=b"r"))
    assert client.delete("/api/v1/users/profile/image").json() == {"profile_image": None}
    assert _files(uploads, test_user.id) == []
    other = _user(db_session, 611, "gone")
    svc.set_picture(db_session, other.id, png(tag=b"g"), "image/png")
    assert _files(uploads, other.id)
    assert client.delete(f"/api/v1/users/{other.id}").status_code == 200
    assert not (uploads / str(other.id)).exists()


def test_who_may_see_a_picture(client, db_session, test_project, test_user, uploads):
    alice = _user(db_session, 621, "alice")
    bob = _user(db_session, 622, "bob")             # shares a project with alice
    carol = _user(db_session, 623, "carol")         # shares none
    db_session.add_all([
        ProjectMembership(project_id=test_project.id, user_id=alice.id, role=ProjectRole.VIEWER.value),
        ProjectMembership(project_id=test_project.id, user_id=bob.id, role=ProjectRole.VIEWER.value),
    ])
    db_session.commit()
    sha = svc.set_picture(db_session, alice.id, png(tag=b"a"), "image/png").profile_image_sha256
    url = f"/api/v1/users/{alice.id}/profile-image/{sha}"
    for who, code in ((alice, 200), (bob, 200), (carol, 404), (test_user, 200)):   # test_user: global admin
        app.dependency_overrides[get_current_user] = (lambda w: (lambda: w))(who)
        assert client.get(url).status_code == code, who.username
    app.dependency_overrides[get_current_user] = lambda: carol
    assert client.get(f"/api/v1/users/{alice.id}/profile-image/{'0' * 64}").status_code == 404


def test_a_global_admin_removes_anyones_picture_and_it_is_audited(client, db_session, test_user, uploads):
    alice = _user(db_session, 631, "alice2")
    svc.set_picture(db_session, alice.id, png(tag=b"a"), "image/png")
    r = client.delete(f"/api/v1/users/{alice.id}/profile-image")
    assert r.status_code == 200
    db_session.refresh(alice)
    assert alice.profile_image_sha256 is None and _files(uploads, alice.id) == []
    assert db_session.query(AuditLog).filter(AuditLog.action == "profile_image_removed").count() == 1
    app.dependency_overrides[get_current_user] = lambda: alice
    assert client.delete(f"/api/v1/users/{test_user.id}/profile-image").status_code == 403


def test_two_replacements_at_once_leave_exactly_the_file_the_row_names(two_sessions, uploads):  # noqa: F811
    _project, user = two_sessions.project()
    one, two = png(tag=b"race-1"), png(tag=b"race-2")
    results = two_sessions.race(
        lambda db: svc.set_picture(db, user.id, one, "image/png").profile_image_sha256,
        lambda db: svc.set_picture(db, user.id, two, "image/png").profile_image_sha256,
    )
    assert all(isinstance(r, str) for r in results), results
    db = two_sessions.a
    db.expire_all()
    current = db.get(User, user.id).profile_image_sha256
    assert current in results
    assert _files(uploads, user.id) == [f"{current}.png"]


def test_a_restore_racing_a_replacement_keeps_its_file(two_sessions, uploads):  # noqa: F811
    _project, user = two_sessions.project()
    earlier, later = png(tag=b"earlier"), png(tag=b"later")
    svc.set_picture(two_sessions.a, user.id, earlier, "image/png")
    svc.set_picture(two_sessions.a, user.id, later, "image/png")
    # Put the earlier picture back while the later one is being written again.
    results = two_sessions.race(
        lambda db: svc.set_picture(db, user.id, earlier, "image/png").profile_image_sha256,
        lambda db: svc.set_picture(db, user.id, later, "image/png").profile_image_sha256,
    )
    db = two_sessions.a
    db.expire_all()
    current = db.get(User, user.id).profile_image_sha256
    assert current in results
    assert _files(uploads, user.id) == [f"{current}.png"]
    assert (uploads / str(user.id) / f"{current}.png").read_bytes() in (earlier, later)
