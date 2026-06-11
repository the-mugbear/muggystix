"""
Authentication API Endpoints

Endpoints for user login, logout, registration, and session management.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel

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
    revoke_session,
    check_permissions,
    login_throttle_exceeded,
    verify_password,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_THROTTLE_WINDOW_MINUTES,
)
from app.services import totp_service

# Short-lived purpose claim minted after password (but before TOTP) succeeds.
# A token carrying it is NOT a session: it has no UserSession row, and
# get_current_user rejects the purpose explicitly (belt-and-suspenders).
_2FA_CHALLENGE_PURPOSE = "2fa_challenge"
_2FA_CHALLENGE_TTL_MINUTES = 5

router = APIRouter()
security = HTTPBearer()

# v2.91.3 (code review #6) — debounce window for UserSession.last_activity
# updates on the get_current_user dep.  Mirrors the agent-side debounce
# constant in app.api.deps; see the get_current_user docstring for why
# coarse-grained resolution is fine here.
_USER_SESSION_ACTIVITY_DEBOUNCE_SECONDS = 60.0


# Pydantic models for request/response
class LoginRequest(BaseModel):
    username: str
    password: str


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


class TwoFactorChallengeResponse(BaseModel):
    """Returned by /login when the password is correct but the account has 2FA
    enabled — the client must complete /login/2fa with a code."""
    two_factor_required: bool = True
    challenge_token: str
    expires_in: int


class TwoFactorLoginRequest(BaseModel):
    challenge_token: str
    # A 6-digit TOTP code OR a recovery code (xxxxx-xxxxx).
    code: str


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


def get_client_info(request: Request) -> Dict[str, Optional[str]]:
    """Extract client information from request"""
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent")
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    # v2.91.4 (third code review #3) — switched from `async def` to
    # plain `def`.  Pre-fix this dep was `async def` but every call
    # inside it (verify_token / db.query.first() / db.commit())
    # is synchronous psycopg2 / passlib work.  FastAPI runs `async
    # def` deps directly on the event loop, so on every
    # authenticated request the loop blocked on two SELECTs + an
    # UPDATE; a slow DB stalled unrelated requests on the same
    # Uvicorn worker.  Switching to `def` lets FastAPI dispatch
    # this dep to its thread pool, freeing the loop.  Same
    # contract — the caller awaits the same Depends().
    """
    Get current authenticated user from JWT token
    """
    token = credentials.credentials
    payload = verify_token(token)

    # A 2FA-challenge token proves password-but-not-yet-TOTP; it must never
    # authenticate a request.  (It also has no session row, so the lookup
    # below would reject it anyway — this is the explicit, earlier guard.)
    if payload.get("purpose") == _2FA_CHALLENGE_PURPOSE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor authentication not completed",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    # `sub` is a numeric user id at mint time, but a malformed or
    # foreign-issued token could carry a non-numeric subject; int() would
    # then raise ValueError and escape as a 500.  Auth-boundary type
    # failures must be 401, not 500.
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    user = db.query(User).filter(User.id == user_id_int).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )

    # Check if session is still valid
    token_jti = payload.get("jti")
    session = db.query(UserSession).filter(
        UserSession.token_jti == token_jti,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > datetime.now(timezone.utc)
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or revoked"
        )

    # v2.91.3 (code review #6) — debounce the per-request session
    # activity write.  Pre-fix every authenticated user request issued
    # an UPDATE + commit on user_sessions, which on a polling-heavy UI
    # turns read traffic into write traffic with all the WAL + row-
    # contention costs.  The agent path was debounced in v2.26.0
    # (see deps._AGENT_ACTIVITY_DEBOUNCE_SECONDS); apply the same
    # pattern here.  ``last_activity`` is used as a "when did this
    # user last show signs of life" coarse signal — second-level
    # resolution isn't required (the per-request audit trail lives
    # elsewhere).  Stateless across workers because the persisted
    # value is itself the source of truth.
    now = datetime.now(timezone.utc)
    prior = session.last_activity
    if prior is not None and prior.tzinfo is None:
        prior = prior.replace(tzinfo=timezone.utc)
    if prior is None or (now - prior).total_seconds() >= _USER_SESSION_ACTIVITY_DEBOUNCE_SECONDS:
        session.last_activity = now
        db.commit()

    return user


def require_role(required_role: str):
    """Decorator to require specific user role"""
    def role_checker(current_user: User = Depends(get_current_user)):
        if not check_permissions(current_user.role, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_role}"
            )
        return current_user
    return role_checker


def require_password_changed(
    current_user: User = Depends(get_current_user),
) -> User:
    """Post-login account-readiness gate, applied to every data endpoint.

    Blocks API access (403 with a machine-readable detail the frontend
    intercepts) until the user has finished account setup:
      * ``password_change_required`` — a forced password change is pending.
      * ``two_factor_setup_required`` — mandatory 2FA (``REQUIRE_2FA``) is on
        and the user hasn't enrolled yet.

    Password change is checked first (most urgent).  The ``/auth/*`` surface —
    login, logout, change-password, profile, and the ``/auth/2fa/*`` enrollment
    endpoints — is intentionally NOT behind this gate, so a blocked user can
    still complete setup.
    """
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required",
        )
    if settings.REQUIRE_2FA and not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="two_factor_setup_required",
        )
    return current_user


@router.post("/login", response_model=Union[LoginResponse, TwoFactorChallengeResponse])
def login(
    login_data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Authenticate user; create a session, or return a 2FA challenge."""
    client_info = get_client_info(request)

    # Reject before doing any bcrypt work if this username or source IP is
    # already over the recent-failure threshold. Defends against distributed
    # brute force that would otherwise re-use a fresh IP after the
    # per-account 5-strike lockout in authenticate_user() expires.
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

    if not _verify_second_factor(db, user, body.code):
        log_audit_event(
            db=db, user_id=user.id, action="login_2fa_failed",
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
    # Recovery code path — single-use, marked consumed.
    if "-" in code:
        from app.db.models_auth import UserRecoveryCode
        code_hash = totp_service.hash_recovery_code(code)
        row = (
            db.query(UserRecoveryCode)
            .filter(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.code_hash == code_hash,
                UserRecoveryCode.used_at.is_(None),
            )
            .first()
        )
        if not row:
            return False
        row.used_at = datetime.now(timezone.utc)
        db.commit()
        return True
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
    from app.core.security import verify_password
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
        os.unlink(os.path.join("/app", "uploads", "initial-admin-password.txt"))
    except OSError:
        pass  # already gone / never existed / operator-supplied password

    return {"message": "Password successfully changed. All sessions have been revoked — please log in again."}


@router.get("/sessions")
def get_active_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's active sessions"""
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
            "expires_at": session.expires_at
        }
        for session in sessions
    ]


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