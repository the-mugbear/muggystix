"""
Security utilities for authentication and authorization
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
import bcrypt
import secrets
from passlib.context import CryptContext
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_auth import User, UserSession, AuditLog, UserRole

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pre-computed bcrypt hash used to equalize timing when authenticate_user()
# rejects a request for "user does not exist / inactive / locked" reasons.
# Verifying against this still pays the full bcrypt cost so attackers cannot
# distinguish those branches from "user exists but wrong password" via timing.
_DUMMY_PASSWORD_HASH = pwd_context.hash("dummy-password-for-timing-equalization")

# JWT settings — use JWT_SECRET_KEY consistently for token signing
_configured_secret = getattr(settings, 'JWT_SECRET_KEY', None)
if not _configured_secret:
    import logging as _logging
    _logging.getLogger(__name__).critical(
        "JWT_SECRET_KEY is not configured. Generating a random ephemeral secret. "
        "All tokens will be invalidated on restart. Set JWT_SECRET_KEY in your "
        "environment or .env file for production deployments."
    )
    _configured_secret = secrets.token_urlsafe(32)
JWT_SECRET_KEY = _configured_secret
ALGORITHM = getattr(settings, 'JWT_ALGORITHM', 'HS256')
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(settings, 'ACCESS_TOKEN_EXPIRE_MINUTES', 480)  # 8 hours


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash"""
    return pwd_context.hash(password)


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
        pwd_context.verify(password, _DUMMY_PASSWORD_HASH)
        return None

    if not user.is_active:
        pwd_context.verify(password, _DUMMY_PASSWORD_HASH)
        return None

    # Check if account is locked
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        pwd_context.verify(password, _DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user.hashed_password):
        # v2.91.3 (code review #4) — atomic increment via a single
        # UPDATE ... RETURNING.  Pre-fix this was a read-modify-write
        # on user.failed_login_attempts, so two concurrent failed
        # attempts could both read counter=N, both write counter=N+1,
        # and the lockout-at-5 evaluation could be skipped.  The
        # SQL-level increment + RETURNING gives back the row's new
        # value so the lockout decision uses the authoritative count.
        from sqlalchemy import update
        new_count = db.execute(
            update(User)
            .where(User.id == user.id)
            .values(failed_login_attempts=User.failed_login_attempts + 1)
            .returning(User.failed_login_attempts)
        ).scalar_one()

        # Lock account after 5 failed attempts.  The lock window is
        # also written atomically (single UPDATE) so a second
        # concurrent failure that crosses the threshold can't race
        # the lock write either.
        if new_count >= 5:
            db.execute(
                update(User)
                .where(User.id == user.id)
                .values(locked_until=datetime.now(timezone.utc) + timedelta(minutes=30))
            )

        db.commit()
        return None

    # Reset failed login attempts on successful login
    user.failed_login_attempts = 0
    user.last_login = datetime.now(timezone.utc)
    user.locked_until = None
    db.commit()

    return user


# Login-throttling thresholds — counts apply over the trailing window.
# The existing per-account lockout in authenticate_user() still applies on
# top of this (5 failures from a single attacker lock the account for 30 min);
# these limits exist so a botnet split across many IPs can't simply re-use a
# fresh IP for every guess after the per-account lockout expires.
LOGIN_THROTTLE_WINDOW_MINUTES = 15
LOGIN_THROTTLE_PER_USERNAME = 10   # failures across all IPs in window → reject
LOGIN_THROTTLE_PER_IP = 20         # failures across all usernames in window → reject


def login_throttle_exceeded(
    db: Session,
    username: Optional[str],
    ip_address: Optional[str],
) -> bool:
    """Return True if recent failed-login activity for this username OR this
    source IP exceeds the throttle. Reads ``audit_logs`` rows produced by
    ``log_audit_event(action='login_failed')`` — no extra table needed.
    """
    window_start = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES)
    base = db.query(AuditLog).filter(
        AuditLog.action == "login_failed",
        AuditLog.timestamp >= window_start,
    )

    if username:
        # AuditLog.details is sqlalchemy.JSON, which maps to Postgres `json`
        # (NOT `jsonb`).  Two operator gotchas hit us during deploy:
        #   * `.contains({"username": ...})` emits `json @> json` — no such
        #     Postgres operator (only `jsonb @> jsonb` exists).
        #   * `details["username"].astext` requires a JSONB-typed column;
        #     the generic JSON type's comparator has no `astext` attr.
        # `func.json_extract_path_text` is the one form that compiles
        # cleanly against both `json` and `jsonb` columns and matches the
        # text written by `log_audit_event(details={"username": ...})`.
        per_user = base.filter(
            func.json_extract_path_text(AuditLog.details, "username") == username
        ).count()
        if per_user >= LOGIN_THROTTLE_PER_USERNAME:
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
    error_message: Optional[str] = None
):
    """Log security audit event"""
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
    db.commit()
    db.refresh(audit_log)
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
