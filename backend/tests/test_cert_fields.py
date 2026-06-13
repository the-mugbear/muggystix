"""Shared cert-field derivation (column-vs-blob promotion).

derive_cert_fields turns a raw tls_info blob into the promoted columns
(cert_not_after, cert_self_signed) once at ingest; cert_issue_from_columns is
the hygiene verdict the insight surfaces read off those columns.
"""
from datetime import datetime, timezone

import pytest

from app.services.cert_fields import (
    parse_cert_not_after,
    derive_cert_fields,
    cert_issue_from_columns,
)

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("value,expected_year", [
    ("2030-01-01T00:00:00Z", 2030),
    ("2020-01-01T00:00:00+00:00", 2020),
    ("2025-12-31 23:59:59", 2025),
    ("Jan 15 12:00:00 2027 GMT", 2027),
    ("2028-03-04", 2028),
])
def test_parse_cert_not_after_formats(value, expected_year):
    dt = parse_cert_not_after(value)
    assert dt is not None and dt.year == expected_year
    assert dt.tzinfo is not None  # always tz-aware


@pytest.mark.parametrize("value", [None, "", "not-a-date", {}])
def test_parse_cert_not_after_unparseable(value):
    assert parse_cert_not_after(value) is None


def test_derive_self_signed_true():
    na, ss = derive_cert_fields(
        {"not_after": "2030-01-01T00:00:00Z", "issuer_dn": "CN=box", "subject_dn": "CN=box"}
    )
    assert na is not None and na.year == 2030
    assert ss is True


def test_derive_self_signed_false_when_issuer_differs():
    _, ss = derive_cert_fields({"issuer": "CN=DigiCert", "subject": "CN=example.com"})
    assert ss is False


def test_derive_self_signed_unknown_without_both_parts():
    # issuer present, subject missing -> can't decide -> None (unknown)
    _, ss = derive_cert_fields({"issuer": "CN=DigiCert", "not_after": "2030-01-01T00:00:00Z"})
    assert ss is None


def test_derive_non_dict():
    assert derive_cert_fields(None) == (None, None)
    assert derive_cert_fields("garbage") == (None, None)


def test_cert_issue_expired_takes_priority():
    expired = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert cert_issue_from_columns(expired, True, NOW) == "expired"


def test_cert_issue_self_signed():
    future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert cert_issue_from_columns(future, True, NOW) == "self-signed"


def test_cert_issue_clean():
    future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert cert_issue_from_columns(future, False, NOW) is None
    assert cert_issue_from_columns(None, None, NOW) is None
