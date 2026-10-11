"""TOTP two-factor authentication helpers (RFC 6238).

Standalone TOTP — no external IdP.  Users either generate a fresh secret
(scan the QR) or IMPORT an existing base32 secret (e.g. the seed already
enrolled for their PAM machine login) so the same authenticator entry produces
valid codes here too.

The secret is encrypted at rest with a Fernet key HKDF-derived from
``CREDENTIAL_ENCRYPTION_KEY`` under a TOTP-dedicated salt — separate from the
LLM/integration credential key (key separation: compromising one purpose's
ciphertext doesn't help against the other).
"""

import base64
import hashlib
import hmac
import io
import logging
import re
import secrets
import time
from typing import List, Optional

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_auth import User

logger = logging.getLogger(__name__)

ISSUER = "BlueStick"
# valid_window=1 accepts the adjacent 30s steps, tolerating modest clock skew
# between the operator's authenticator and the server.
_VALID_WINDOW = 1

_FERNET_CACHE: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Cached Fernet for TOTP-secret encryption (TOTP-dedicated derived key)."""
    global _FERNET_CACHE
    if _FERNET_CACHE is not None:
        return _FERNET_CACHE
    source = getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "") or (settings.SECRET_KEY or "")
    if not source:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY (or SECRET_KEY) must be set to encrypt "
            "TOTP secrets at rest. Set it in your .env file."
        )
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"networkmapper-totp-v1",
        info=b"fernet-key-derivation",
    )
    key = base64.urlsafe_b64encode(kdf.derive(source.encode("utf-8")))
    _FERNET_CACHE = Fernet(key)
    return _FERNET_CACHE


def encrypt_secret(secret_b32: str) -> str:
    return _get_fernet().encrypt(secret_b32.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Failed to decrypt a TOTP secret — encryption key rotated?")
        return None


def generate_secret() -> str:
    """A fresh random base32 TOTP secret."""
    return pyotp.random_base32()


_B32_RE = re.compile(r"^[A-Z2-7]+=*$")


def normalize_secret(raw: str) -> Optional[str]:
    """Clean + validate a user-supplied base32 secret (import path).

    Authenticator apps/export tools present the seed with spaces and in mixed
    case; strip whitespace, upper-case, and reject anything that isn't valid
    base32 (so we never store an unusable secret).  Returns None on invalid.
    """
    if not raw:
        return None
    cleaned = re.sub(r"\s+", "", raw).upper()
    if not cleaned or not _B32_RE.match(cleaned):
        return None
    # Must decode as base32 (pad to a multiple of 8 first).
    try:
        base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except Exception:
        return None
    return cleaned


def code_time_step(secret_b32: str, code: str, *, now: Optional[float] = None) -> Optional[int]:
    """The RFC 6238 time step ``code`` is valid for — the current one or one
    of its ``_VALID_WINDOW`` neighbours — or None when it is valid for none.

    Pure: it says a code is RIGHT, not that it may be used.  A right code is
    used through :func:`accept_code`, which lets each step through once.
    """
    if not secret_b32 or not code:
        return None
    code = re.sub(r"\s+", "", code)
    if not code.isdigit():
        return None
    try:
        totp = pyotp.TOTP(secret_b32)
        current = int(time.time() if now is None else now) // totp.interval
        # Newest first: when a code is right for two steps (one chance in a
        # million) the later one is recorded, which refuses both afterwards.
        for step in range(current + _VALID_WINDOW, current - _VALID_WINDOW - 1, -1):
            if hmac.compare_digest(totp.generate_otp(step), code):
                return step
    except Exception:
        return None
    return None


def accept_code(db: Session, user: User, code: str, *, now: Optional[float] = None) -> bool:
    """True when ``code`` is a valid authenticator code for ``user`` that has
    not been accepted before; it is then spent.  COMMITS.

    A TOTP code is valid for its 30-second step and the neighbours on either
    side, so without a memory the same six digits were accepted again and
    again for about 90 seconds (review 2026-10-10, H26) — by anyone who saw
    them typed, or took them off the wire with the challenge token.
    ``users.totp_last_step`` is that memory: a code for that step or an
    earlier one is refused (RFC 6238 §5.2).  The claim is ONE conditional
    UPDATE, so of two requests carrying the same code exactly one wins, and it
    is committed here so that nothing later in the request can un-spend it.

    This is the one place a TOTP code is accepted: sign-in's second step and
    the confirmation of an enrolment both come here.  Wrong codes are counted
    by the callers, from the audit log; this column is not a limit.
    """
    step = code_time_step(decrypt_secret(user.totp_secret_encrypted) or "", code, now=now)
    if step is None:
        return False
    spent = (
        db.query(User)
        .filter(
            User.id == user.id,
            or_(User.totp_last_step.is_(None), User.totp_last_step < step),
        )
        .update({User.totp_last_step: step}, synchronize_session=False)
    )
    db.commit()
    return spent == 1


def provisioning_uri(secret_b32: str, account_name: str) -> str:
    """otpauth:// URI for QR/manual entry."""
    return pyotp.TOTP(secret_b32).provisioning_uri(name=account_name, issuer_name=ISSUER)


def qr_svg_data_uri(otpauth_uri: str) -> str:
    """Render an otpauth URI as an inline SVG data-URI (no Pillow; uses lxml)."""
    img = qrcode.make(otpauth_uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


# --- Recovery codes --------------------------------------------------------

RECOVERY_CODE_COUNT = 10


def generate_recovery_codes(n: int = RECOVERY_CODE_COUNT) -> List[str]:
    """``n`` high-entropy one-time codes formatted ``xxxxx-xxxxx`` (Crockford-ish
    base32, no ambiguous chars)."""
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no I/L/O/0/1
    out = []
    for _ in range(n):
        chunk = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{chunk[:5]}-{chunk[5:]}")
    return out


def hash_recovery_code(code: str) -> str:
    """SHA-256 hex of a normalized recovery code (high-entropy → fast hash ok)."""
    normalized = re.sub(r"\s+", "", code).upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
