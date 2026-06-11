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
import io
import logging
import re
import secrets
from typing import List, Optional

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

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


def verify_code(secret_b32: str, code: str) -> bool:
    """True if ``code`` is a currently-valid TOTP for ``secret_b32``."""
    if not secret_b32 or not code:
        return False
    code = re.sub(r"\s+", "", code)
    if not code.isdigit():
        return False
    try:
        return pyotp.TOTP(secret_b32).verify(code, valid_window=_VALID_WINDOW)
    except Exception:
        return False


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
