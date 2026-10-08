"""
Security utilities for authentication and authorization
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Sequence
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
import bcrypt
import secrets
from fastapi import HTTPException, status
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_auth import User, UserSession, AuditLog

# Password hashing — bcrypt directly (v2.395.0; passlib 1.7.4, last released
# 2020, only wrapped it).  The behaviour is passlib's: ``$2b$`` hashes at cost
# 12, stored ``$2a$`` / ``$2y$`` hashes still verify, a password is used up to
# bcrypt's 72-byte limit (longer ones were always truncated there, so existing
# hashes keep matching), and a NUL byte is refused rather than silently ending
# the password.  One deliberate difference: a malformed stored hash now reads
# as a failed login instead of raising.
_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72


def _password_bytes(password: str) -> bytes:
    data = password.encode("utf-8")
    if b"\x00" in data:
        raise ValueError("A password may not contain a NUL byte.")
    return data[:_BCRYPT_MAX_BYTES]


# Pre-computed bcrypt hash used to equalize timing when authenticate_user()
# rejects a request for "user does not exist / inactive / locked" reasons.
# Verifying against this still pays the full bcrypt cost so attackers cannot
# distinguish those branches from "user exists but wrong password" via timing.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    _password_bytes("dummy-password-for-timing-equalization"), bcrypt.gensalt(_BCRYPT_ROUNDS),
).decode("ascii")

# JWT settings — use JWT_SECRET_KEY consistently for token signing
_configured_secret = getattr(settings, 'JWT_SECRET_KEY', None)
if not _configured_secret:
    # Fail closed in any real deployment.  An unset secret previously
    # auto-generated a DIFFERENT ephemeral key in every uvicorn worker,
    # so tokens signed by one worker failed verification on another
    # (intermittent 401s across the fleet) and every restart silently
    # dropped all sessions.  The only context where an ephemeral secret
    # is acceptable is local dev / the test suite, which run against a
    # sqlite DATABASE_URL — reuse config.py's own dev/test signal.
    _is_dev_or_test = str(getattr(settings, 'DATABASE_URL', '')).startswith("sqlite")
    if not _is_dev_or_test:
        raise RuntimeError(
            "JWT_SECRET_KEY (or SECRET_KEY) is not configured. Set it in your "
            "environment or .env file. Refusing to start with an ephemeral "
            "per-worker secret: it breaks multi-worker auth and drops every "
            "session on restart."
        )
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "JWT_SECRET_KEY is not configured; generating an ephemeral secret for "
        "this dev/test process (sqlite DATABASE_URL detected). Do NOT rely on "
        "this in a multi-worker or production deployment."
    )
    _configured_secret = secrets.token_urlsafe(32)
JWT_SECRET_KEY = _configured_secret
ALGORITHM = getattr(settings, 'JWT_ALGORITHM', 'HS256')
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(settings, 'ACCESS_TOKEN_EXPIRE_MINUTES', 480)  # 8 hours


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(_password_bytes(plain_password), (hashed_password or "").encode("ascii"))
    except ValueError:
        # A NUL byte in the attempt, or a stored value that is not a bcrypt
        # hash: neither can match.
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with bcrypt (``$2b$``, cost 12)."""
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("ascii")


def validate_password_strength(password: str) -> Dict[str, Any]:
    """
    Validate password meets security requirements

    Returns:
        Dict with 'valid' boolean and 'errors' list
    """
    errors = []

    if len(password) < 12:
        errors.append("Password must be at least 12 characters long")

    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")

    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")

    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number")

    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        errors.append("Password must contain at least one special character")

    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create JWT access token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_urlsafe(16)  # JWT ID for session tracking
    })

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode JWT token

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """
    Authenticate user credentials

    Returns:
        User object if authentication successful, None otherwise
    """
    user = db.query(User).filter(User.username == username).first()

    if not user:
        # Equalize timing: still pay bcrypt cost so unknown vs known usernames are indistinguishable.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    if not user.is_active:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    # Failed attempts are not counted on the user row: a counter and lock
    # there let anyone who knows a username keep its owner out.  The lockout
    # is per (username, client address) — ``login_lockout_active`` — and is
    # counted from the ``login_failed`` audit rows the login route writes.
    if not verify_password(password, user.hashed_password):
        return None

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    return user


# Failed sign-ins are limited three ways, all counted from ``audit_logs``:
#
#   * (username, client address): LOGIN_LOCKOUT_FAILURES in
#     LOGIN_LOCKOUT_MINUTES locks that address out of that account.  This is
#     the tight limit, and it only ever locks out the address that guessed —
#     the account's owner signs in from anywhere else.  The client address is
#     the peer nginx saw (it overwrites X-Forwarded-For), so a caller cannot
#     choose it.
#   * client address, any username: LOGIN_THROTTLE_PER_IP per window.
#   * username, any address: LOGIN_THROTTLE_PER_USERNAME per window.  A ceiling
#     for guessing spread over many addresses; it is deliberately far above
#     the per-address limit, because anyone can reach it for any username and
#     while it holds the owner is kept out too.
#
# The second factor has its own per-username limit (LOGIN_2FA_THROTTLE_PER_USERNAME)
# counted over ``login_2fa_failed`` only: reaching that step takes the
# password, so the limit can stay low without letting a stranger lock the
# account, and password failures do not use it up.
LOGIN_LOCKOUT_FAILURES = 5
LOGIN_LOCKOUT_MINUTES = 30
LOGIN_THROTTLE_WINDOW_MINUTES = 15
LOGIN_THROTTLE_PER_USERNAME = 100
LOGIN_THROTTLE_PER_IP = 20
LOGIN_2FA_THROTTLE_PER_USERNAME = 10


def _audit_username():
    # AuditLog.details is sqlalchemy.JSON, which maps to Postgres `json`
    # (NOT `jsonb`): `.contains()` and `.astext` do not compile against it.
    # `json_extract_path_text` does, and matches the text written by
    # `log_audit_event(details={"username": ...})`.
    return func.json_extract_path_text(AuditLog.details, "username")


def login_lockout_active(
    db: Session, username: Optional[str], ip_address: Optional[str],
) -> bool:
    """True when this client address has failed ``LOGIN_LOCKOUT_FAILURES``
    sign-ins for ``username`` in the last ``LOGIN_LOCKOUT_MINUTES`` — counted
    since that address last got the account's password right.

    Decided by the username as typed, whether or not the account exists, so
    the answer says nothing about which usernames are real.

    Takes a transaction-scoped advisory lock on the pair, held until the
    caller's audit row for this attempt commits: parallel guesses from one
    address are counted one at a time, never all against the same total.
    """
    if not username:
        return False
    db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(
            literal(f"login:{username}|{ip_address or ''}"), 0,
        )))
    )
    same_address = (
        AuditLog.ip_address == ip_address if ip_address else AuditLog.ip_address.is_(None)
    )
    # Ordered by id, not timestamp: the reset must be exact for two rows
    # written in the same instant.
    last_success = (
        select(func.coalesce(func.max(AuditLog.id), 0))
        .where(
            AuditLog.action.in_(("login_success", "login_2fa_challenge")),
            AuditLog.user_id == select(User.id).where(User.username == username).scalar_subquery(),
            same_address,
        )
        .scalar_subquery()
    )
    since = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
    failures = (
        db.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action == "login_failed",
            AuditLog.timestamp >= since,
            _audit_username() == username,
            same_address,
            AuditLog.id > last_success,
        )
        .scalar()
    )
    return failures >= LOGIN_LOCKOUT_FAILURES


def login_throttle_exceeded(
    db: Session,
    username: Optional[str],
    ip_address: Optional[str],
    actions: Sequence[str] = ("login_failed",),
    username_actions: Optional[Sequence[str]] = None,
    per_username: int = LOGIN_THROTTLE_PER_USERNAME,
) -> bool:
    """Return True if recent failed-auth activity for this username OR this
    source IP exceeds the throttle. Reads ``audit_logs`` rows produced by
    ``log_audit_event(action=...)`` — no extra table needed.

    ``actions`` selects which failure events count against the address. The
    password step passes the default (``login_failed``); the 2FA step passes
    both ``login_failed`` and ``login_2fa_failed`` (the TOTP space is only
    1e6, and the challenge JWT is reusable for its full TTL, so an uncounted
    2FA path would be sprayable).  ``username_actions`` / ``per_username``
    are the events and the limit counted against the username across all
    addresses; they default to ``actions`` and the password step's ceiling.
    """
    window_start = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES)
    base = db.query(AuditLog).filter(
        AuditLog.action.in_(list(actions)),
        AuditLog.timestamp >= window_start,
    )

    if username:
        per_user = db.query(AuditLog).filter(
            AuditLog.action.in_(list(username_actions or actions)),
            AuditLog.timestamp >= window_start,
            _audit_username() == username,
        ).count()
        if per_user >= per_username:
            return True

    if ip_address:
        per_ip = base.filter(AuditLog.ip_address == ip_address).count()
        if per_ip >= LOGIN_THROTTLE_PER_IP:
            return True

    return False


def check_permissions(user_role: str, required_role: str) -> bool:
    """Return True when ``user_role`` meets or exceeds ``required_role``.

    v2.46.0 — serves BOTH role axes.  The dict is keyed by the bare
    string values, so ``UserRole`` and ``ProjectRole`` members (both
    ``str`` enums) and plain DB strings all resolve identically:

      * Global gate (``require_role``): only ``admin`` is ever
        required; ``member`` sits below it.
      * Project gate (``require_project_role``): the per-project
        hierarchy ``admin > analyst > auditor > viewer``.

    ``admin`` outranks everything on either axis.  ``member`` only
    ever appears in a global check (a ProjectMembership.role is never
    ``member``), where it must simply fall short of ``admin``.
    """
    role_hierarchy = {
        "admin": 100,
        "analyst": 3,
        "auditor": 2,
        "viewer": 1,
        "member": 1,   # global non-admin; only compared against "admin"
    }

    user_level = role_hierarchy.get(user_role, 0)
    required_level = role_hierarchy.get(required_role, 0)

    return user_level >= required_level


def log_audit_event(
    db: Session,
    user_id: Optional[int],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    commit: bool = True,
):
    """Log security audit event.

    Commits by default (most callers log a finished action).  A multi-step
    workflow passes ``commit=False`` to stage the row in its own transaction
    and commit once, so the audit row cannot land without the rest — or the
    rest half-land because this helper committed in the middle (review
    2026-09-23 B-Debt-8)."""
    audit_log = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details,
        success=success,
        error_message=error_message
    )

    db.add(audit_log)
    if commit:
        db.commit()
        db.refresh(audit_log)
    else:
        db.flush()
    return audit_log.id


# NOTE (code review): the former ``create_api_key`` / ``verify_api_key``
# helpers were removed here — they were dead code and a foot-gun.  Live
# agent API-key auth runs through ``app.api.deps.get_current_agent`` and
# key minting lives in the agents/scopes/assist/test-plans endpoints,
# all of which enforce scope + rate limiting that the deleted verifier
# bypassed.


def create_session(
    db: Session,
    user: User,
    token_jti: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> UserSession:
    """Create user session record"""
    session = UserSession(
        user_id=user.id,
        token_jti=token_jti,
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return session


def revoke_session(db: Session, token_jti: str, reason: str = "logout"):
    """Revoke user session"""
    session = db.query(UserSession).filter(UserSession.token_jti == token_jti).first()

    if session:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = reason
        db.commit()


def cleanup_expired_sessions(db: Session):
    """Clean up expired sessions (called periodically)"""
    expired_sessions = db.query(UserSession).filter(
        UserSession.expires_at < datetime.now(timezone.utc),
        UserSession.revoked_at.is_(None)
    ).all()

    for session in expired_sessions:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = "expired"

    db.commit()

    return len(expired_sessions)
