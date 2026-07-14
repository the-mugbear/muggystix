"""First-boot default-admin seeding (app/startup.py:seed_default_admin).

Regression suite for the stale-marker bug: ./uploads is a host bind mount
that outlives the database (Nuclear Clean, `down -v`, copying the deploy
dir to a new host).  Pre-v2.230.1 the seeder used O_EXCL creation of
uploads/initial-admin-password.txt as its multi-worker bootstrap token, so
a marker left by a previous install made every worker skip admin creation
entirely — a fresh deploy ended up with NO admin while the marker file
advertised a dead instance's password.
"""
import os
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.db.models_auth import User, UserRole
from app.startup import admin_marker_path, seed_default_admin

STALE_MARKER = (
    "username: admin\n"
    "password: password-from-a-previous-install\n"
    "(created on first boot only; auto-deleted after the first login forces "
    "a password change)\n"
)


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """Point UPLOAD_DIR at a per-test directory so the seeder's marker file
    lands somewhere writable and inspectable."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _marker_password(path):
    for line in path.read_text().splitlines():
        if line.startswith("password: "):
            return line[len("password: "):].strip()
    return None


def _seeded_admin(db_session):
    return (
        db_session.query(User)
        .filter(User.role == UserRole.ADMIN, User.username == "admin")
        .first()
    )


def test_fresh_db_with_stale_marker_still_creates_admin(
    db_session, uploads_dir, monkeypatch
):
    """The reported bug: new instance, empty DB, leftover marker in ./uploads.
    The admin must be created anyway and the marker replaced with the
    working credential."""
    monkeypatch.delenv("DEFAULT_ADMIN_PASSWORD", raising=False)
    marker = uploads_dir / "initial-admin-password.txt"
    marker.write_text(STALE_MARKER)

    seed_default_admin()

    admin = _seeded_admin(db_session)
    assert admin is not None, "no admin was created despite an empty DB"
    assert admin.must_change_password is True
    new_password = _marker_password(marker)
    assert new_password != "password-from-a-previous-install"
    assert verify_password(new_password, admin.hashed_password), (
        "the credential written to uploads/ must actually log in"
    )


def test_first_boot_marker_credential_logs_in(db_session, uploads_dir, monkeypatch):
    """Normal first boot with no DEFAULT_ADMIN_PASSWORD: marker is written
    0600 and holds the password the admin row was hashed from."""
    monkeypatch.delenv("DEFAULT_ADMIN_PASSWORD", raising=False)
    marker = uploads_dir / "initial-admin-password.txt"

    seed_default_admin()

    admin = _seeded_admin(db_session)
    assert admin is not None
    assert marker.exists()
    assert (marker.stat().st_mode & 0o777) == 0o600
    assert verify_password(_marker_password(marker), admin.hashed_password)


def test_operator_supplied_password_writes_no_marker(
    db_session, uploads_dir, monkeypatch
):
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "operator-chosen-secret")

    seed_default_admin()

    admin = _seeded_admin(db_session)
    assert admin is not None
    assert verify_password("operator-chosen-secret", admin.hashed_password)
    assert not (uploads_dir / "initial-admin-password.txt").exists()


def _persist_admin(db_session, password):
    admin = User(
        username="admin",
        full_name="Administrator",
        hashed_password=get_password_hash(password),
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(admin)
    db_session.commit()
    return admin


def test_stale_marker_removed_when_admin_already_exists(db_session, uploads_dir):
    """Admin exists (e.g. restored DB) but the lingering marker's credential
    doesn't open it — the misleading file must be deleted."""
    _persist_admin(db_session, "the-real-current-password")
    marker = uploads_dir / "initial-admin-password.txt"
    marker.write_text(STALE_MARKER)

    seed_default_admin()

    assert not marker.exists(), "a marker with dead credentials must not survive"


def test_valid_marker_kept_until_first_login_rotation(db_session, uploads_dir):
    """Marker still matches the not-yet-rotated admin (normal restart during
    the first-boot window) — it must be kept."""
    _persist_admin(db_session, "password-from-a-previous-install")
    marker = uploads_dir / "initial-admin-password.txt"
    marker.write_text(STALE_MARKER)

    seed_default_admin()

    assert marker.exists()
    assert _marker_password(marker) == "password-from-a-previous-install"
