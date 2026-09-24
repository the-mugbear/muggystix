"""A rejected scope entry says so in a plain sentence (v2.404.0).

The Scope page shows the API's 400 detail under the field as is; it used to
carry Python's parser text ("... does not appear to be an IPv4 or IPv6
network").
"""
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.scopes import _validate_cidr


@pytest.mark.parametrize("raw", ["10.0.0.300/24", "example.com", "10.0.0.0/33", " 10.0.0.300/24 "])
def test_invalid_entry_message_is_plain(raw):
    with pytest.raises(HTTPException) as exc:
        _validate_cidr(raw)
    assert exc.value.status_code == 400
    assert exc.value.detail == f"{raw.strip()!r} is not an IP address or CIDR range"
    assert "does not appear" not in exc.value.detail


@pytest.mark.parametrize(
    "raw, normalised",
    [("10.0.0.5/24", "10.0.0.0/24"), ("10.0.0.5", "10.0.0.5/32"), ("2001:db8::1/32", "2001:db8::/32")],
)
def test_valid_entries_are_normalised(raw, normalised):
    assert _validate_cidr(raw) == normalised
