"""Unit tests for the subnet-insights hygiene heuristics.

These target the pure helper functions (no DB), covering the false-positive
cases surfaced in code review: NetExec weak-auth on unknown identities, and
the EOL OS matcher mis-flagging modern Windows ("NT 10.0") and Win10 LTSC.
"""
import pytest

from app.services.os_eol import match_eol_os
from app.services.subnet_insight_service import _is_weak_user, _cert_issue
from datetime import datetime, timezone


# --- Weak auth: only an EXPLICIT guest/blank identity is weak ---------------

@pytest.mark.parametrize("username,expected", [
    (None, False),            # identity not captured (parser leaves NULL) — unknown, never weak
    ("", True),               # explicit blank == null session
    ("guest", True),
    ("Guest", True),          # case-insensitive
    ("anonymous", True),
    ("administrator", False), # real account
    ("CORP\\jdoe", False),    # domain user
    ("svc_backup", False),    # service account
])
def test_is_weak_user(username, expected):
    assert _is_weak_user(username) is expected


# --- EOL OS matcher: conservative, no modern-Windows false positives --------

@pytest.mark.parametrize("os_name", [
    "Windows NT 10.0",            # modern Windows reports as NT 10.0 in SMB/HTTP
    "Microsoft Windows NT 10.0",
    "Windows NT 6.1",             # Win7 NT version — handled by 'Windows 7' string elsewhere, not the NT rule
    "Windows 10 Enterprise LTSC 2021",  # supported to 2027
    "Windows 10 Enterprise LTSB 2016",
    "Windows Server 2019",
    "Windows Server 2022",
    "Linux 5.15",
    "Ubuntu",
    "",
    None,
])
def test_match_eol_os_not_flagged(os_name):
    assert match_eol_os(os_name) is None


@pytest.mark.parametrize("os_name,label", [
    ("Windows 10 Pro", "Windows 10"),
    ("Microsoft Windows 10 1607", "Windows 10"),
    ("Windows 7", "Windows 7"),
    ("Windows NT 4.0", "Windows 2000 / NT 3.x–4.x"),
    ("Windows 2000 Server", "Windows 2000 / NT 3.x–4.x"),
    ("Windows Server 2008 R2", "Windows Server 2008 / R2"),
    ("Windows XP Professional", "Windows XP"),
    ("Linux 2.6.32", "Linux kernel 2.x"),
    ("CentOS 7", "CentOS 5–7"),
])
def test_match_eol_os_flagged(os_name, label):
    m = match_eol_os(os_name)
    assert m is not None and m.label == label


# --- Cert issue classification ---------------------------------------------

def test_cert_issue_expired():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert _cert_issue({"not_after": "2020-01-01T00:00:00Z"}, now) == "expired"


def test_cert_issue_valid():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert _cert_issue({"not_after": "2030-01-01T00:00:00Z"}, now) is None


def test_cert_issue_self_signed():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert _cert_issue(
        {"not_after": "2030-01-01T00:00:00Z", "issuer_dn": "CN=box", "subject_dn": "CN=box"}, now
    ) == "self-signed"


def test_cert_issue_none_for_non_dict():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert _cert_issue(None, now) is None
