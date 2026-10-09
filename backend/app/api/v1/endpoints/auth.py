"""
Authentication API Endpoints

Endpoints for user login, logout, registration, and session management.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.session import get_db
from app.db.models_auth import User, UserSession, UserRole
from app.core.security import (
    authenticate_user,
    create_access_token,
    verify_token,
    get_password_hash,
    validate_password_strength,
    log_audit_event,
    create_session,
    renew_session,
    revoke_session,
    login_lockout_active,
    login_throttle_exceeded,
    verify_password,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_2FA_THROTTLE_PER_USERNAME,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_THROTTLE_WINDOW_MINUTES,
)
from app.services import totp_service
from app.services.agent_session_service import end_sessions_of_operator
# The auth DEPENDENCIES are defined in ``app.api.deps`` (review 2026-10-01
# B4: a router file is not where ~45 other routers should import from).  They
# are re-exported here so ``from app.api.v1.endpoints.auth import
# get_current_user`` — and a test's ``dependency_overrides`` keyed on it —
# is the SAME function object.  New code imports them from ``app.api.deps``.
from app.api.deps import (
    TWO_FACTOR_CHALLENGE_PURPOSE as _2FA_CHALLENGE_PURPOSE,
    get_client_info,
    get_current_user,
    optional_bearer as _optional_bearer,
    require_password_changed,
    require_role,
    security,
)

__all__ = [
    "router",
    "get_client_info",
    "get_current_user",
    "require_password_changed",
    "require_role",
    "security",
]

_2FA_CHALLENGE_TTL_MINUTES = 5

router = APIRouter()


# Pydantic models for request/response
class LoginRequest(BaseModel):
    # Bounded so an unauthenticated caller can't ship a multi-megabyte body to
    # the login route (the global Nginx body cap is large for scan uploads).
    username: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1024)


class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    # v2.46.0 — global role is binary: "admin" or "member".  Optional;
    # defaults to "member" (rights come from project memberships).
    role: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: Dict[str, Any]


class SessionRenewalResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class TwoFactorChallengeResponse(BaseModel):
    """Returned by /login when the password is correct but the account has 2FA
    enabled — the client must complete /login/2fa with a code."""
    two_factor_required: bool = True
    challenge_token: str
    expires_in: int


class TwoFactorLoginRequest(BaseModel):
    # challenge_token is a signed JWT (~hundreds of bytes); code is a 6-digit
    # TOTP or a recovery code (xxxxx-xxxxx).  Bounded so the unauthenticated
    # 2FA route can't be used as an oversized-body sink.
    challenge_token: str = Field(..., max_length=4096)
    code: str = Field(..., max_length=64)


class UserProfile(BaseModel):
    id: int
    username: str
    full_name: Optional[str]
    role: str
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login", response_model=Union[LoginResponse, TwoFactorChallengeResponse])
def login(
    login_data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Authenticate user; create a session, or return a 2FA challenge."""
    client_info = get_client_info(request)

    # Reject before doing any bcrypt work.  The lockout is this address's own
    # failures for this username (so guessing never locks the account's owner
    # out from elsewhere); the throttle bounds an address across usernames and
    # a username across addresses.
    if login_lockout_active(
        db,
        username=login_data.username,
        ip_address=client_info.get("ip_address"),
    ):
        log_audit_event(
            db=db,
            user_id=None,
            action="login_throttled",
            details={"username": login_data.username, "reason": "lockout"},
            success=False,
            error_message="Locked out",
            **client_info,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed login attempts from this address. Try again in "
                f"{LOGIN_LOCKOUT_MINUTES} minutes."
            ),
        )
    if login_throttle_exceeded(
        db,
        username=login_data.username,
        ip_address=client_info.get("ip_address"),
    ):
        log_audit_event(
            db=db,
            user_id=None,
            action="login_throttled",
            details={"username": login_data.username},
            success=False,
            error_message="Throttled",
            **client_info,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many recent failed login attempts. Try again in "
                f"{LOGIN_THROTTLE_WINDOW_MINUTES} minutes."
            ),
        )

    # Authenticate user
    user = authenticate_user(db, login_data.username, login_data.password)

    if not user:
        # Log failed login attempt
        log_audit_event(
            db=db,
            user_id=None,
            action="login_failed",
            details={"username": login_data.username},
            success=False,
            error_message="Invalid credentials",
            **client_info
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    # 2FA gate: password is correct, but if the account has TOTP enabled we
    # issue a short-lived challenge (NOT a session) and require /login/2fa.
    if user.totp_enabled:
        challenge = create_access_token(
            data={"sub": str(user.id), "purpose": _2FA_CHALLENGE_PURPOSE},
            expires_delta=timedelta(minutes=_2FA_CHALLENGE_TTL_MINUTES),
        )
        log_audit_event(
            db=db,
            user_id=user.id,
            action="login_2fa_challenge",
            details={"method": "password"},
            **client_info,
        )
        return TwoFactorChallengeResponse(
            challenge_token=challenge,
            expires_in=_2FA_CHALLENGE_TTL_MINUTES * 60,
        )

    return _issue_user_session(db, user, client_info, method="password")


def _issue_user_session(
    db: Session, user: User, client_info: Dict[str, Optional[str]], method: str,
) -> LoginResponse:
    """Mint the access token, record the session, audit, and build the
    LoginResponse.  Shared by the password-only path and the 2FA-completed
    path so both produce an identical, fully-authenticated session."""
    token_data = {"sub": str(user.id), "username": user.username, "role": user.role}
    access_token = create_access_token(data=token_data)
    token_jti = verify_token(access_token)["jti"]

    create_session(db=db, user=user, token_jti=token_jti, **client_info)

    log_audit_event(
        db=db,
        user_id=user.id,
        action="login_success",
        details={"method": method},
        **client_info,
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "must_change_password": bool(user.must_change_password),
            # Mandatory-2FA enrollment is pending — the client redirects to the
            # forced-setup page at login (deterministic, not reliant on a later
            # gated-call 403).  Only true when REQUIRE_2FA is on and the user
            # hasn't enrolled (and isn't also forced to change password first).
            "must_setup_2fa": bool(
                settings.REQUIRE_2FA
                and not user.totp_enabled
                and not user.must_change_password
            ),
        },
    )


@router.post("/login/2fa", response_model=LoginResponse)
def login_2fa(
    body: TwoFactorLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Complete a 2FA login: verify the challenge token + a TOTP or recovery
    code, then issue the full session."""
    client_info = get_client_info(request)

    # The challenge token proves the password step already succeeded.
    try:
        payload = verify_token(body.challenge_token)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired 2FA challenge")
    if payload.get("purpose") != _2FA_CHALLENGE_PURPOSE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA challenge")

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA challenge")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active or not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA challenge")

    # Throttle the second factor too. The TOTP space is only 1e6 and the
    # challenge JWT is reusable for its whole TTL, so without this an attacker
    # who already holds a valid challenge could spray codes.  The address's
    # budget is shared with the password step; the account's is its wrong
    # codes only, so password guessing by a stranger cannot use it up.
    if login_throttle_exceeded(
        db, username=user.username, ip_address=client_info.get("ip_address"),
        actions=("login_failed", "login_2fa_failed"),
        username_actions=("login_2fa_failed",),
        per_username=LOGIN_2FA_THROTTLE_PER_USERNAME,
    ):
        log_audit_event(
            db=db, user_id=user.id, action="login_throttled",
            details={"username": user.username, "stage": "2fa"},
            success=False, error_message="Throttled", **client_info,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many recent failed attempts. Try again in "
                f"{LOGIN_THROTTLE_WINDOW_MINUTES} minutes."
            ),
        )

    if not _verify_second_factor(db, user, body.code):
        log_audit_event(
            db=db, user_id=user.id, action="login_2fa_failed",
            # username in details so the per-username throttle branch counts it
            # (login_throttle_exceeded reads the name from details, not user_id).
            details={"username": user.username},
            success=False, error_message="Invalid 2FA code", **client_info,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication code")

    return _issue_user_session(db, user, client_info, method="totp")


def _verify_second_factor(db: Session, user: User, code: str) -> bool:
    """True if ``code`` is a valid TOTP for the user OR an unused recovery code
    (which is then consumed).  Recovery codes contain a '-'; TOTP codes don't."""
    code = (code or "").strip()
    if not code:
        return False
    # Recovery code path — single-use, consumed atomically.  A read-then-write
    # (.first() then set used_at) lets two concurrent requests both observe the
    # same unused row and consume it twice.  A single conditional UPDATE makes
    # the second request match zero rows: exactly one consumer wins.
    if "-" in code:
        from app.db.models_auth import UserRecoveryCode
        code_hash = totp_service.hash_recovery_code(code)
        consumed = (
            db.query(UserRecoveryCode)
            .filter(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.code_hash == code_hash,
                UserRecoveryCode.used_at.is_(None),
            )
            .update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
        )
        db.commit()
        return consumed == 1
    # TOTP path.
    secret = totp_service.decrypt_secret(user.totp_secret_encrypted)
    return bool(secret) and totp_service.verify_code(secret, code)


@router.post("/logout")
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """Logout user and revoke session"""
    client_info = get_client_info(request)

    # Get token JTI for session revocation
    token = credentials.credentials
    payload = verify_token(token)
    token_jti = payload.get("jti")

    if token_jti:
        revoke_session(db, token_jti, "logout")

    # Log logout
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="logout",
        **client_info
    )

    return {"message": "Successfully logged out"}


@router.post("/register", response_model=UserProfile)
def register(
    registration_data: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN))
):
    """Register new user (admin only)"""
    client_info = get_client_info(request)

    # Check if username already exists
    if db.query(User).filter(User.username == registration_data.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    # Validate password strength
    password_validation = validate_password_strength(registration_data.password)
    if not password_validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password validation failed: {', '.join(password_validation['errors'])}"
        )

    # Resolve the global role.  v2.46.0 — binary {admin, member};
    # defaults to member when the caller doesn't specify one.
    requested_role = (registration_data.role or UserRole.MEMBER.value).lower()
    if requested_role not in (UserRole.ADMIN.value, UserRole.MEMBER.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{requested_role}'. Global role must be "
                   f"'admin' or 'member' — per-project capabilities are "
                   f"assigned via project membership.",
        )

    # Create new user
    hashed_password = get_password_hash(registration_data.password)
    new_user = User(
        username=registration_data.username,
        hashed_password=hashed_password,
        full_name=registration_data.full_name,
        role=requested_role,
        created_by_id=current_user.id
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Log user creation
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="user_created",
        resource_type="user",
        resource_id=str(new_user.id),
        details={"new_username": new_user.username, "role": new_user.role},
        **client_info
    )

    return UserProfile(
        id=new_user.id,
        username=new_user.username,
        full_name=new_user.full_name,
        role=new_user.role,
        is_active=new_user.is_active,
        last_login=new_user.last_login,
        created_at=new_user.created_at
    )


@router.get("/profile", response_model=UserProfile)
def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserProfile(
        id=current_user.id,
        username=current_user.username,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        last_login=current_user.last_login,
        created_at=current_user.created_at
    )


@router.post("/change-password")
def change_password(
    password_data: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change user password"""
    client_info = get_client_info(request)

    # Verify current password
    if not verify_password(password_data.current_password, current_user.hashed_password):
        log_audit_event(
            db=db,
            user_id=current_user.id,
            action="password_change_failed",
            success=False,
            error_message="Invalid current password",
            **client_info
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Validate new password strength
    password_validation = validate_password_strength(password_data.new_password)
    if not password_validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password validation failed: {', '.join(password_validation['errors'])}"
        )

    # Reject reuse of the current password.  Without this a forced-change
    # user could "rotate" to the same password and clear must_change_password,
    # defeating the forced-rotation entirely.
    if verify_password(password_data.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from your current password"
        )

    # Update password and clear forced-change flag
    current_user.hashed_password = get_password_hash(password_data.new_password)
    current_user.password_changed_at = datetime.now(timezone.utc)
    current_user.must_change_password = False

    # Revoke all existing sessions so stolen tokens become invalid.
    # The user will need to log in again with the new password.
    db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.revoked_at.is_(None),
    ).update(
        {"revoked_at": datetime.now(timezone.utc), "revoked_reason": "password_changed"},
        synchronize_session=False,
    )
    # Agent keys are credentials issued under the old password too: end the
    # user's agent sessions, or a session started with a stolen token would
    # keep answering and renewing.
    end_sessions_of_operator(
        db, current_user.id, ended_by=current_user,
        reason="the operator changed their password",
    )

    db.commit()

    # Log password change
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="password_changed",
        **client_info
    )

    # Auto-delete the first-boot admin-password marker once a rotation has
    # happened — it must not outlive the forced first-login change (C4).
    try:
        import os
        from app.startup import admin_marker_path
        os.unlink(admin_marker_path())
    except OSError:
        pass  # already gone / never existed / operator-supplied password

    return {"message": "Password successfully changed. All sessions have been revoked — please log in again."}


def _request_token_jti(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    """The ``jti`` of the bearer token on this request, or None.

    ``get_current_user`` has already validated the token; this only reads
    which session row it belongs to, so a failure here means "unknown", never
    a 401.
    """
    if credentials is None:
        return None
    try:
        return verify_token(credentials.credentials).get("jti")
    except Exception:
        return None


@router.get("/sessions")
def get_active_sessions(
    current_user: User = Depends(get_current_user),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db)
):
    """Get user's active sessions.

    v2.402.0 — each row carries ``current``: true for the session the calling
    token belongs to (matched on the token's ``jti``), so the Profile page can
    mark "This session" and warn that revoking it signs the caller out.
    """
    current_jti = _request_token_jti(credentials)
    sessions = db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > datetime.now(timezone.utc)
    ).all()

    return [
        {
            "id": session.id,
            "ip_address": session.ip_address,
            "user_agent": session.user_agent,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "expires_at": session.expires_at,
            "current": bool(current_jti) and session.token_jti == current_jti,
        }
        for session in sessions
    ]


@router.post("/session/renew", response_model=SessionRenewalResponse)
def renew_current_session(
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Start the calling session's lifetime again from now.

    A session ends ``ACCESS_TOKEN_EXPIRE_MINUTES`` after its last renewal.
    The client calls this when the person at the keyboard does something (a
    key press, a click) — an ordinary request does NOT renew, because pages
    poll and an unattended tab would then stay signed in for ever.  The
    answer is a token for the same session with a later expiry; the one it
    replaces stays valid until its own.  Not audited: it is routine and
    frequent, and ``last_activity`` records it.
    """
    token = renew_session(db, current_user, _request_token_jti(credentials) or "")
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or revoked",
        )
    return SessionRenewalResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.delete("/sessions/{session_id}")
def revoke_session_endpoint(
    session_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Revoke a specific session"""
    client_info = get_client_info(request)

    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == current_user.id
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    revoke_session(db, session.token_jti, "manual_revocation")

    # Log session revocation
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="session_revoked",
        resource_type="session",
        resource_id=str(session_id),
        **client_info
    )

    return {"message": "Session revoked successfully"}