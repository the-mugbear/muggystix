"""Review 2026-10-23b, FEP-5: a Hosts list row names the weaknesses that made
it match from the filter's OWN parse of the query, not a regex in the browser.

The page's `utils/weaknessMatch` read `has:` / `check:` out of the query text:
`issue:"check:…"` and `kind:misconfiguration` matched the host on the server
and named nothing on the row.
"""
from __future__ import annotations

import pytest

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


@pytest.fixture
def smb_host(db_session, test_project):
    h = models.Host(project_id=test_project.id, ip_address="10.235.0.1", state="up", smb_signing="not_required")
    db_session.add(h)
    db_session.flush()
    scan = models.Scan(project_id=test_project.id, filename="n.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    db_session.add(Vulnerability(
        host_id=h.id, scan_id=scan.id, title="SMB signing not required",
        severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NMAP,
        check_id="smb_signing_not_required", issue_key="check:smb_signing_not_required",
    ))
    db_session.commit()
    return h


def _row(client, project_id, **params):
    r = client.get(f"/api/v1/projects/{project_id}/hosts/", params=params)
    assert r.status_code == 200, r.text
    rows = [h for h in r.json()["items"] if h["ip_address"] == "10.235.0.1"]
    assert rows, params
    return rows[0]["matched_weakness_flags"], rows[0]["matched_check_ids"]


@pytest.mark.parametrize("params, flags, checks", [
    ({"q": "has:smb_unsigned"}, ["smb_unsigned"], []),
    ({"weaknesses": "smb_unsigned"}, ["smb_unsigned"], []),
    ({"q": "check:smb_signing_not_required"}, [], ["smb_signing_not_required"]),
    ({"checks": "smb_signing_not_required"}, [], ["smb_signing_not_required"]),
    # A comma list inside a group; a named check the host lacks names nothing.
    ({"q": '(has:smb_unsigned,weak_tls OR check:"vnc_no_auth") AND state:up'}, ["smb_unsigned"], []),
    # The two the browser's regex missed.
    ({"q": 'issue:"check:smb_signing_not_required"'}, [], ["smb_signing_not_required"]),
    ({"q": "kind:misconfiguration"}, [], ["smb_signing_not_required"]),
    # A negated leaf names nothing; a condition about something else neither.
    ({"q": "state:up AND NOT has:eol"}, [], []),
    ({"q": "state:up"}, [], []),
])
def test_a_row_names_what_the_conditions_name_and_it_carries(client, test_project, smb_host, params, flags, checks):
    assert _row(client, test_project.id, **params) == (flags, checks)
