"""Two-factor authentication (TOTP) management endpoints.

Self-service enrollment lifecycle for the logged-in user — mounted under
``/auth`` (no password-change gate, same stance as the rest of the auth
surface).  The login-time challenge/verify lives in ``auth.py``; this module
is setup / enable / disable / recovery-code management only.

Enrollment is two-step so a mistyped/loaded secret can't lock anyone out:
``/setup`` writes an INACTIVE secret (``totp_enabled`` stays False); ``/enable``
flips it on only after the user proves a working code.  ``/setup`` accepts an
existing base32 secret (import mode) so the operator's PAM machine-login seed
can be reused — the same authenticator entry then works here too.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User, UserRecoveryCode
from app.core.security import verify_password, log_audit_event
from app.api.v1.endpoints.auth import get_current_user, get_client_info
from app.services import totp_service

router = APIRouter()


class TwoFactorStatus(BaseModel):
    enabled: bool
    confirmed_at: Optional[datetime] = None
    pending: bool  # a secret has been set up but not yet confirmed
    unused_recovery_codes: int


class SetupRequest(BaseModel):
    # Optional: import an existing base32 secret (e.g. the PAM machine-login
    # seed) instead of generating a fresh one.  Omit to generate.
    existing_secret: Optional[str] = None


class SetupResponse(BaseModel):
    secret: str          # base32 — shown for manual entry
    otpauth_uri: str     # otpauth:// — for QR / authenticator import
    qr_svg: str          # inline SVG data-URI of the otpauth URI
    imported: bool       # True when an existing secret was supplied


class EnableRequest(BaseModel):
    code: str


class RecoveryCodesResponse(BaseModel):
    recovery_codes: List[str]


class DisableRequest(BaseModel):
    password: str


def _unused_recovery_count(db: Session, user_id: int) -> int:
    return (
        db.query(UserRecoveryCode)
        .filter(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        .count()
    )


def _issue_recovery_codes(db: Session, user: User) -> List[str]:
    """Replace the user's recovery codes with a fresh set; return the plaintext
    (shown once)."""
    db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id == user.id).delete()
    codes = totp_service.generate_recovery_codes()
    for c in codes:
        db.add(UserRecoveryCode(user_id=user.id, code_hash=totp_service.hash_recovery_code(c)))
    db.commit()
    return codes


@router.get("/2fa/status", response_model=TwoFactorStatus)
def two_factor_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return TwoFactorStatus(
        enabled=bool(current_user.totp_enabled),
        confirmed_at=current_user.totp_confirmed_at,
        pending=bool(current_user.totp_secret_encrypted) and not current_user.totp_enabled,
        unused_recovery_codes=_unused_recovery_count(db, current_user.id),
    )


@router.post("/2fa/setup", response_model=SetupResponse)
def two_factor_setup(
    body: SetupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Begin enrollment: store an INACTIVE secret (generated or imported) and
    return the provisioning material.  Idempotent until /enable — re-calling
    overwrites the pending secret."""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is already enabled. Disable it first to re-enroll.",
        )

    imported = False
    if body.existing_secret:
        secret = totp_service.normalize_secret(body.existing_secret)
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That doesn't look like a valid base32 authenticator secret.",
            )
        imported = True
    else:
        secret = totp_service.generate_secret()

    current_user.totp_secret_encrypted = totp_service.encrypt_secret(secret)
    current_user.totp_enabled = False
    current_user.totp_confirmed_at = None
    db.commit()

    uri = totp_service.provisioning_uri(secret, account_name=current_user.username)
    return SetupResponse(
        secret=secret,
        otpauth_uri=uri,
        qr_svg=totp_service.qr_svg_data_uri(uri),
        imported=imported,
    )


@router.post("/2fa/enable", response_model=RecoveryCodesResponse)
def two_factor_enable(
    body: EnableRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm enrollment: verify a code against the pending secret, activate
    2FA, and return one-time recovery codes (shown once)."""
    if current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Two-factor authentication is already enabled.")
    secret = totp_service.decrypt_secret(current_user.totp_secret_encrypted)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending 2FA setup found. Start setup first.",
        )
    if not totp_service.verify_code(secret, body.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That code is incorrect. Check your authenticator and try again.")

    current_user.totp_enabled = True
    current_user.totp_confirmed_at = datetime.now(timezone.utc)
    db.commit()
    codes = _issue_recovery_codes(db, current_user)
    log_audit_event(
        db=db, user_id=current_user.id, action="2fa_enabled", **get_client_info(request),
    )
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesResponse)
def regenerate_recovery_codes(
    body: DisableRequest,  # reuse: requires the account password
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate recovery codes (invalidates the old set). Password-gated."""
    if not current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Two-factor authentication is not enabled.")
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect password.")
    codes = _issue_recovery_codes(db, current_user)
    log_audit_event(
        db=db, user_id=current_user.id, action="2fa_recovery_codes_regenerated", **get_client_info(request),
    )
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/2fa/disable")
def two_factor_disable(
    body: DisableRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Turn off 2FA — requires the account password as re-auth."""
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect password.")
    current_user.totp_secret_encrypted = None
    current_user.totp_enabled = False
    current_user.totp_confirmed_at = None
    db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id == current_user.id).delete()
    db.commit()
    log_audit_event(
        db=db, user_id=current_user.id, action="2fa_disabled", **get_client_info(request),
    )
    return {"disabled": True}
