"""Password hashing without passlib (v2.395.0).

``core/security`` now calls bcrypt directly.  The fixtures below were made by
passlib 1.7.4 (``CryptContext(schemes=["bcrypt"])``) before it was removed, so
this pins that every stored hash keeps verifying and the edge behaviour is
unchanged: passwords past bcrypt's 72 bytes were truncated there, a NUL byte
was refused.  The one change is on purpose: a malformed stored hash reads as a
failed login (passlib raised ``UnknownHashError``).
"""
from __future__ import annotations

import bcrypt
import pytest

from app.core.security import get_password_hash, verify_password

# Made by passlib 1.7.4 on 2026-09-23.
PASSLIB_HASH = "$2b$12$XMC9Q56eA6kCb44mB7hqBudbbaMUW5TzsedQ8IUB3/WDRvjtGh2ce"   # Correct-Horse-9!
PASSLIB_LONG = "$2b$12$fZltyZvgM08aWdMioP79au3MllG2hz49fCVtS7Oolj9fnvds.LCQC"   # "L"*80 + "-tail"
PASSLIB_UNICODE = "$2b$12$LyiNnsLyvLAoDNrcWjjrcO4ZNzrg9QQCEzyy389xtHZ57N3HpjLRu"  # pässwörd-Ünïcode-1


def test_hashes_passlib_made_still_verify():
    assert verify_password("Correct-Horse-9!", PASSLIB_HASH)
    assert not verify_password("correct-horse-9!", PASSLIB_HASH)
    assert verify_password("pässwörd-Ünïcode-1", PASSLIB_UNICODE)
    # passlib truncated at 72 bytes too: any tail past it matched.
    assert verify_password("L" * 80 + "-tail", PASSLIB_LONG)
    assert verify_password("L" * 80 + "-other", PASSLIB_LONG)


def test_new_hashes_are_2b_cost_12_and_verify():
    h = get_password_hash("Correct-Horse-9!")
    assert h.startswith("$2b$12$")
    assert verify_password("Correct-Horse-9!", h)
    assert not verify_password("Correct-Horse-9", h)


def test_2a_and_2y_hashes_verify():
    h2a = bcrypt.hashpw(b"Correct-Horse-9!", bcrypt.gensalt(4, prefix=b"2a")).decode()
    assert verify_password("Correct-Horse-9!", h2a)
    h2y = "$2y$" + get_password_hash("Correct-Horse-9!")[4:]
    assert verify_password("Correct-Horse-9!", h2y)


def test_a_nul_byte_is_refused_not_truncated():
    with pytest.raises(ValueError):
        get_password_hash("abc\x00def")
    h = get_password_hash("abc-Password-1!")
    # "abc-Password-1!\x00anything" must not pass as "abc-Password-1!".
    assert not verify_password("abc-Password-1!\x00anything", h)


@pytest.mark.parametrize("stored", ["not-a-hash", "", None, "$2b$12$short"])
def test_a_malformed_stored_hash_is_a_failed_login(stored):
    assert verify_password("anything", stored) is False
