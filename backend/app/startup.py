"""Startup helpers extracted from main.py in v2.42.0.

Owns the first-boot seeding (admin / project / system identity), the
cached instance-id lookup, and the hourly expired-session reaper.
Imported by ``app.main._run_startup_sequence`` (the body of the
FastAPI lifespan handler, v2.68.0+) and by the references endpoints
that need to surface ``instance_id``.

The split keeps ``main.py`` to app construction + middleware + lifecycle
hookup; everything that runs *during* boot or in the background lives
here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# --- housekeeping leader election (B2-2) -----------------------------------
#
# The background loops below run in EVERY uvicorn worker (default 4).  Their
# DELETEs are idempotent, so multiple workers firing the same pass is correct
# but wasteful — 4x the purge query load at each interval.  A Postgres
# advisory lock elects a single leader per pass: whoever grabs the lock does
# the work, the rest skip.  Distinct bigint keys per loop; on non-Postgres
# (dev/sqlite, single-process) the gate is a no-op and the loop just runs.

_LEADER_LOCK_EXPIRED_SESSIONS = 0x42535F455850  # "BS_EXP"
_LEADER_LOCK_AGENT_API_RETENTION = 0x42535F524554  # "BS_RET"


def _try_housekeeping_leader(db, key: int) -> bool:
    """True when this worker should run the pass — it acquired the advisory
    lock, or we're not on Postgres.  The lock is session-scoped; release it
    with ``_release_housekeeping_leader`` once the work is done."""
    if db.bind.dialect.name != "postgresql":
        return True
    return bool(
        db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
    )


def _release_housekeeping_leader(db, key: int) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


# --- expired-session cleanup loop ------------------------------------------

EXPIRED_SESSION_CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour


async def expired_session_cleanup_loop() -> None:
    """Background task: periodically reap expired UserSession rows.

    Idempotent across uvicorn workers (each worker fires this; wasteful
    but not incorrect — `cleanup_expired_sessions` only marks rows whose
    `revoked_at IS NULL`, so concurrent reapers can't double-revoke).
    """
    from app.db.session import SessionLocal
    from app.core.security import cleanup_expired_sessions

    while True:
        try:
            await asyncio.sleep(EXPIRED_SESSION_CLEANUP_INTERVAL_SECONDS)
            with SessionLocal() as db:
                if not _try_housekeeping_leader(db, _LEADER_LOCK_EXPIRED_SESSIONS):
                    continue  # another worker is the leader this pass
                try:
                    reaped = cleanup_expired_sessions(db)
                finally:
                    _release_housekeeping_leader(db, _LEADER_LOCK_EXPIRED_SESSIONS)
                if reaped:
                    logger.info("Reaped %d expired user sessions", reaped)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Expired-session cleanup tick failed; will retry next interval"
            )


# --- agent_api_calls retention loop (v2.65.0) ------------------------------
#
# `agent_api_calls` records every authenticated agent request and grows
# monotonically.  On a busy deployment it becomes the largest table in
# the DB within months and starts dragging activity-log queries that
# join against it.  The `purge_older_than` helper in
# `agent_api_log_service` has existed since v2.24.0 with a docstring
# pointing at "manual / cron use" — but no cron was ever wired.  This
# loop wires it.
#
# Default retention is 90 days, matching the audit-log retention
# default conventionally expected for security-relevant data.  Operators
# can override via AGENT_API_CALL_RETENTION_DAYS in .env (0 disables
# the loop entirely, which is the right escape hatch for operators who
# want unbounded retention or who manage retention out-of-band).

AGENT_API_CALL_PURGE_INTERVAL_SECONDS = 24 * 3600  # 1 day


async def agent_api_call_retention_loop() -> None:
    """Background task: daily delete agent_api_call rows older than the
    configured retention window.

    Honours ``AGENT_API_CALL_RETENTION_DAYS`` env var (default 90).
    Setting it to 0 (or any value <= 0) disables the loop so operators
    who want unbounded retention or out-of-band management get an
    explicit opt-out rather than fighting a daemon they didn't know
    was running.

    Like the session-cleanup loop above, idempotent across uvicorn
    workers — multiple workers each fire it, but `purge_older_than`'s
    DELETE-WHERE-older-than-cutoff is naturally idempotent (a row only
    matches once).
    """
    from app.db.session import SessionLocal
    from app.services.agent_api_log_service import purge_older_than

    retention_days = int(os.getenv("AGENT_API_CALL_RETENTION_DAYS", "90"))
    if retention_days <= 0:
        logger.info(
            "Agent API call retention loop disabled "
            "(AGENT_API_CALL_RETENTION_DAYS=%s)",
            retention_days,
        )
        return

    logger.info(
        "Agent API call retention loop active "
        "(window=%d days, interval=%ds)",
        retention_days,
        AGENT_API_CALL_PURGE_INTERVAL_SECONDS,
    )

    while True:
        try:
            await asyncio.sleep(AGENT_API_CALL_PURGE_INTERVAL_SECONDS)
            with SessionLocal() as db:
                if not _try_housekeeping_leader(db, _LEADER_LOCK_AGENT_API_RETENTION):
                    continue  # another worker is the leader this pass
                try:
                    deleted = purge_older_than(db, days=retention_days)
                finally:
                    _release_housekeeping_leader(db, _LEADER_LOCK_AGENT_API_RETENTION)
                if deleted:
                    logger.info(
                        "Agent API call retention: purged %d rows older than %d days",
                        deleted,
                        retention_days,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Agent API call retention tick failed; will retry next interval"
            )


# --- DB seeding (default admin, anchor project, system identity) -----------

# Serializes first-boot admin creation across uvicorn workers (default 4,
# each runs the lifespan).  Replaces the pre-v2.230.1 scheme that used
# O_EXCL creation of the marker file as the bootstrap token — ./uploads is
# a host bind mount that survives Nuclear Clean, `down -v`, and file-copy
# deploys, so a marker left by a PREVIOUS install made every worker think
# a sibling was mid-bootstrap and skip admin creation entirely: fresh DB,
# no admin, and a credential file advertising a dead instance's password.
_BOOTSTRAP_ADMIN_LOCK = 0x42535F41444D  # "BS_ADM"


def admin_marker_path() -> str:
    """Where the auto-generated first-boot admin password is persisted.

    ``/app/uploads/initial-admin-password.txt`` in the container (UPLOAD_DIR
    defaults to <cwd>/uploads and WORKDIR is /app).  Shared with the
    change-password endpoint, which deletes the file after the forced
    first-login rotation.
    """
    from app.core.config import settings

    return os.path.join(settings.UPLOAD_DIR, "initial-admin-password.txt")


def _parse_admin_marker(marker: str) -> tuple[Optional[str], Optional[str]]:
    """Return the (username, password) recorded in the marker file, or
    (None, None) when the file is missing/unreadable/not ours."""
    username = password = None
    try:
        with open(marker) as f:
            for line in f.read().splitlines():
                if line.startswith("username: "):
                    username = line[len("username: "):].strip()
                elif line.startswith("password: "):
                    password = line[len("password: "):].strip()
    except OSError:
        pass
    return username, password


def _reconcile_admin_marker(db) -> None:
    """An admin already exists — make sure a lingering first-boot credential
    file still opens a live account.

    The ./uploads bind mount outlives the database (Nuclear Clean, volume
    wipes, copying the deploy dir to a new host), so the marker can refer to
    an account that no longer exists or whose password has changed.  A
    credential file that doesn't work reads as "seeding is broken" to the
    operator; delete it and say why.  While the credential still verifies
    (admin hasn't done the forced first-login rotation yet) it stays.
    """
    from app.core.security import verify_password
    from app.db.models_auth import User

    marker = admin_marker_path()
    if not os.path.exists(marker):
        return
    username, password = _parse_admin_marker(marker)
    if username and password:
        user = db.query(User).filter(User.username == username).first()
        if user and user.is_active and verify_password(password, user.hashed_password):
            return  # still the valid, not-yet-rotated first-boot credential
    logger.warning(
        "Removing stale first-boot credential file %s — it does not match any "
        "live account (leftover from a previous install; the uploads directory "
        "outlives the database).", marker,
    )
    try:
        os.unlink(marker)
    except OSError:
        pass


def seed_default_admin() -> None:
    """Create a default admin account on first boot if no admin exists.

    v2.90.3 (code review NEW B) — pre-fix this fell back to the
    literal password ``"admin"`` when ``DEFAULT_ADMIN_PASSWORD`` was
    unset.  ``must_change_password=True`` was enforced, but that gate
    only triggers AFTER the user logs in — an exposed fresh deploy
    could be taken over by any remote caller racing the legitimate
    operator's first login with admin/admin.

    Post-fix: if ``DEFAULT_ADMIN_PASSWORD`` is unset (or set to the
    literal ``"admin"`` for back-compat), generate a cryptographically
    random URL-safe password and log it ONCE to the boot output +
    write it to ``uploads/initial-admin-password.txt`` (a file
    only the backend container can write to) so the operator can
    retrieve it without it being captured in environment dumps.  The
    well-known fallback is gone; there is no path to a guessable
    first-boot admin.

    v2.230.1 — multi-worker serialization moved from an O_EXCL marker
    file to a Postgres advisory lock (see ``_BOOTSTRAP_ADMIN_LOCK``); a
    stale marker from a previous install is now overwritten instead of
    silently blocking admin creation, and a marker that no longer
    matches a live account is deleted (``_reconcile_admin_marker``).
    """
    import secrets

    from sqlalchemy.exc import IntegrityError
    from app.db.session import SessionLocal
    from app.db.models_auth import User, UserRole
    from app.core.security import get_password_hash

    with SessionLocal() as db:
        # Blocks until this worker owns the bootstrap; released at the
        # commit/rollback/close of this session's transaction.  Losing
        # workers then see the winner's admin row and return.  Non-Postgres
        # (sqlite dev/tests, single process) needs no lock.
        if db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(:k)"),
                {"k": _BOOTSTRAP_ADMIN_LOCK},
            )

        existing_admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
        if existing_admin:
            _reconcile_admin_marker(db)
            return

        admin_username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
        raw_password = os.getenv("DEFAULT_ADMIN_PASSWORD")
        # Treat unset, empty, or the literal "admin" as "no password
        # provided" — that last case used to be a silent foot-gun.
        operator_supplied = bool(raw_password) and raw_password != "admin"
        marker = admin_marker_path()

        if operator_supplied:
            admin_password = raw_password
        else:
            admin_password = secrets.token_urlsafe(24)
            # SECURE THE RECOVERY CHANNEL *BEFORE* CREATING THE ADMIN.  The
            # random password is never logged (docker logs are retained and
            # shipped off-box — code-review C4), so this 0600 marker file is
            # the ONLY way to recover it: if it can't be written, fail startup
            # loudly instead of creating an admin nobody can log in as.
            # Written via tmp-file + rename so a stale marker from a previous
            # install is atomically replaced, never half-overwritten.
            try:
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                tmp = marker + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(
                        f"username: {admin_username}\n"
                        f"password: {admin_password}\n"
                        "(created on first boot only; auto-deleted after the "
                        "first login forces a password change)\n"
                    )
                os.replace(tmp, marker)
            except OSError as exc:
                raise RuntimeError(
                    f"Refusing to create the default admin: the credential "
                    f"recovery file {marker} could not be written ({exc}). The "
                    f"generated password is NOT logged, so this would leave an "
                    f"unrecoverable admin. Fix the uploads volume permissions "
                    f"(chown 999:999 ./uploads) and restart, or set "
                    f"DEFAULT_ADMIN_PASSWORD before first boot."
                ) from exc

        admin_user = User(
            username=admin_username,
            full_name="Administrator",
            hashed_password=get_password_hash(admin_password),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True,
            must_change_password=True,
        )
        db.add(admin_user)
        try:
            db.commit()
        except IntegrityError:
            # Backstop for the multi-worker insert race on non-Postgres
            # (the advisory lock above serializes Postgres workers), and
            # for any other constraint violation — schema drift, FK miss —
            # which the audit (review N8) flagged as broader-than-named.
            db.rollback()
            # Our admin didn't land — remove the marker so it can't advertise
            # a credential that opens nothing, but ONLY if it still holds OUR
            # password (the racing winner may have replaced it with theirs).
            if (
                not operator_supplied
                and _parse_admin_marker(marker)[1] == admin_password
            ):
                try:
                    os.unlink(marker)
                except OSError:
                    pass
            return

        logger.info("=" * 60)
        logger.info("DEFAULT ADMIN ACCOUNT CREATED")
        logger.info("  Username: %s", admin_username)
        if operator_supplied:
            logger.info(
                "  Password: set via DEFAULT_ADMIN_PASSWORD env var (not logged)"
            )
        else:
            logger.info(
                "  Password was AUTO-GENERATED because DEFAULT_ADMIN_PASSWORD was unset."
            )
            logger.info("  Password written (0600) to: %s", marker)
        logger.info("  Password change will be required on first login.")
        logger.info("=" * 60)


def ensure_default_project() -> None:
    """Create an anchor project on first boot if (and only if) there's
    orphaned data needing a project_id backfill.

    On a clean install nothing should be created; the first admin lands on
    the empty-state "Create your first project" UI and names something
    meaningful instead of inheriting "Default Project".  On legacy installs
    we anchor the backfill under a clearly-labeled project so the rows
    are visible.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.db.session import SessionLocal
    from app.db.models_project import Project, ProjectMembership
    from app.db.models_auth import User

    with SessionLocal() as db:
        tables = [
            "hosts_v2", "scans", "scopes", "ingestion_jobs",
            "parse_errors", "dns_records", "out_of_scope_hosts",
        ]
        orphan_count = 0
        for tbl in tables:
            try:
                row = db.execute(text(
                    f'SELECT COUNT(*) FROM "{tbl}" WHERE project_id IS NULL'
                )).first()
                if row and row[0]:
                    orphan_count += int(row[0])
            except Exception:
                # Table may not exist on a partial schema; the backfill
                # loop below tolerates missing tables too.
                pass

        if orphan_count == 0:
            return

        anchor = db.query(Project).order_by(Project.id.asc()).first()
        if not anchor:
            anchor = Project(
                name="Imported pre-project data",
                slug="imported-pre-project-data",
                description=(
                    "Auto-created to anchor rows that pre-date the multi-project "
                    "schema. Rename or archive in Project Settings."
                ),
            )
            db.add(anchor)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                anchor = db.query(Project).order_by(Project.id.asc()).first()
                if not anchor:
                    return
            else:
                logger.info(
                    "Created backfill anchor project (id=%d) for %d orphan rows",
                    anchor.id, orphan_count,
                )
                users = db.query(User).all()
                for user in users:
                    membership = ProjectMembership(
                        project_id=anchor.id,
                        user_id=user.id,
                        role=user.role if isinstance(user.role, str) else user.role.value,
                    )
                    db.add(membership)
                db.commit()
                logger.info("Added %d users to backfill anchor project", len(users))

        for tbl in tables:
            try:
                result = db.execute(text(
                    f'UPDATE "{tbl}" SET project_id = :pid WHERE project_id IS NULL'
                ), {"pid": anchor.id})
                if result.rowcount:
                    logger.info(
                        "Backfilled %d rows in %s with project_id=%d",
                        result.rowcount, tbl, anchor.id,
                    )
            except Exception as exc:
                logger.warning("Backfill of %s failed (non-fatal): %s", tbl, exc)
        db.commit()


def seed_system_identity() -> None:
    """Generate the per-deployment instance identity on first boot.

    Stored in system_identity with a random UUID.  Never rewritten once
    set (a container rebuild preserves identity — only a volume wipe
    forfeits it).  Used by the /.well-known/networkmapper.json endpoint
    and by the prompt provenance block so hesitant agents can verify
    they're talking to the instance that generated their instructions.
    """
    from app.db.session import SessionLocal
    from app.db.models_auth import SystemIdentity

    with SessionLocal() as db:
        existing = db.query(SystemIdentity).first()
        if existing:
            logger.info("System identity: %s (existing)", existing.instance_id)
            return
        new_id = str(uuid.uuid4())
        row = SystemIdentity(instance_id=new_id)
        db.add(row)
        db.commit()
        logger.info("System identity: %s (newly generated)", new_id)


# --- cached instance-id lookup ---------------------------------------------
# This module-level cache is intentionally process-local (one cache per
# uvicorn worker) so writes to system_identity from a different worker's
# seed step are observed via SELECT on first access.

_cached_instance_id: Optional[str] = None


def get_instance_id() -> Optional[str]:
    """Load the instance_id from the DB, cached per process.

    Returns None and logs the exception if the lookup fails (DB unreachable,
    schema not yet migrated).  Callers render a "(pending)" placeholder.
    """
    global _cached_instance_id
    if _cached_instance_id is not None:
        return _cached_instance_id
    from app.db.session import SessionLocal
    from app.db.models_auth import SystemIdentity
    try:
        with SessionLocal() as db:
            row = db.query(SystemIdentity).first()
            if row:
                _cached_instance_id = row.instance_id
                return _cached_instance_id
    except Exception:
        logger.exception("Failed to load system identity")
    return None
