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
    write it to ``/app/uploads/initial-admin-password.txt`` (a file
    only the backend container can write to) so the operator can
    retrieve it without it being captured in environment dumps.  The
    well-known fallback is gone; there is no path to a guessable
    first-boot admin.
    """
    import secrets

    from sqlalchemy.exc import IntegrityError
    from app.db.session import SessionLocal
    from app.db.models_auth import User, UserRole
    from app.core.security import get_password_hash

    with SessionLocal() as db:
        existing_admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
        if existing_admin:
            return

        admin_username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
        raw_password = os.getenv("DEFAULT_ADMIN_PASSWORD")
        # Treat unset, empty, or the literal "admin" as "no password
        # provided" — that last case used to be a silent foot-gun.
        operator_supplied = bool(raw_password) and raw_password != "admin"
        marker = os.path.join("/app", "uploads", "initial-admin-password.txt")

        if operator_supplied:
            admin_password = raw_password
        else:
            admin_password = secrets.token_urlsafe(24)
            # SECURE THE RECOVERY CHANNEL *BEFORE* CREATING THE ADMIN.  The
            # random password is never logged (docker logs are retained and
            # shipped off-box — code-review C4), so this 0600 marker file is the
            # ONLY way to recover it.  Previously the admin was committed first
            # and a marker-write failure was swallowed with a warning — on an
            # unwritable uploads volume (UID 999 vs a host-root-owned dir) that
            # created a *strictly unrecoverable* admin.  Now: write the marker
            # first; if it can't be secured, fail startup loudly instead.
            try:
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                # O_EXCL doubles as the multi-worker bootstrap token: only one
                # worker wins the marker and goes on to create the admin.
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                # Another worker already owns the bootstrap (wrote the marker),
                # or a prior partial boot left one.  Don't create a second /
                # mismatched admin from this worker.
                logger.info(
                    "Initial-admin credential marker already present (%s) — "
                    "another worker is bootstrapping or a prior marker exists; "
                    "skipping admin creation here.", marker,
                )
                return
            except OSError as exc:
                # The recovery file can't be written — fail closed rather than
                # create an admin nobody can log in as.
                raise RuntimeError(
                    f"Refusing to create the default admin: the credential "
                    f"recovery file {marker} could not be written ({exc}). The "
                    f"generated password is NOT logged, so this would leave an "
                    f"unrecoverable admin. Fix the uploads volume permissions "
                    f"(chown 999:999 ./uploads) and restart, or set "
                    f"DEFAULT_ADMIN_PASSWORD before first boot."
                ) from exc
            # Persist the password to the reserved marker before the admin is
            # committed, so an admin can never exist without its recovery file.
            with os.fdopen(fd, "w") as f:
                f.write(
                    f"username: {admin_username}\n"
                    f"password: {admin_password}\n"
                    "(created on first boot only; auto-deleted after the first "
                    "login forces a password change)\n"
                )

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
            # Catches the multi-worker race on first boot (two workers both
            # see "no admin" and both try to insert).  Also catches any
            # other constraint violation — schema drift, FK miss — which
            # the audit (review N8) flagged as broader-than-named.
            db.rollback()
            # We reserved the marker but our admin didn't land — remove it so a
            # retry (or the race winner) isn't blocked by a stale credential.
            if not operator_supplied:
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
