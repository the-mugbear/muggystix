"""The ``Content-Disposition`` header of a download (review 2026-10-10 H19).

A header is Latin-1 on the wire.  A scan file called ``сканирование.xml`` or
a site called ``東京`` went into ``filename=`` as it was and the response
could not be encoded — a 500 with an empty body; a comma in the name made
Chrome refuse the download.  ``app.api.downloads`` is the one implementation
(RFC 6266 / 5987: an ASCII ``filename`` and the real name in ``filename*``).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

import app as app_package
from app.api.downloads import attachment, content_disposition
from app.db import models

NOW = datetime.now(timezone.utc)

CYRILLIC = "сканирование.xml"
CJK = "東京"
COMMA = "dmz, east (final).xml"


def _real_name(header: str) -> str:
    """The name a browser saves under: ``filename*`` when sent, else ``filename``."""
    extended = re.search(r"filename\*=UTF-8''([^;]+)", header)
    if extended:
        return unquote(extended.group(1))
    return re.search(r"filename=([^;]+)", header).group(1)


def _sendable(header: str) -> str:
    """What every header must be: Latin-1 (here: ASCII), one line, and with
    nothing a browser reads as a second header or parameter."""
    header.encode("ascii")
    assert "\r" not in header and "\n" not in header
    assert "," not in header and '"' not in header
    assert header.startswith("attachment; filename=")
    return header


@pytest.mark.parametrize("name, plain", [
    (f"{CYRILLIC}_hosts.txt", "xml_hosts.txt"),
    (f"systemic_insights_{CJK}_20261010.html", "systemic_insights_20261010.html"),
    (f"{COMMA}_hosts.csv", "dmz_east_final.xml_hosts.csv"),
    (f"{CJK}.csv", "download.csv"),                 # nothing ASCII left of the stem
    (CJK, "download"),                              # … nor an extension
    ("résumé final.json", "resume_final.json"),     # accents fold, the real name is kept
    ('quote".txt', "quote.txt"),
])
def test_a_name_outside_plain_ascii_travels_in_both_forms(name, plain):
    header = _sendable(content_disposition(name))
    assert header.split(";")[1].strip() == f"filename={plain}"
    assert _real_name(header) == name


@pytest.mark.parametrize("name", ["hosts.csv", "names.txt", "scope-acme-report-3.csv", "out_of_scope_hosts.json"])
def test_a_plain_name_is_sent_as_it_always_was(name):
    assert content_disposition(name) == f"attachment; filename={name}"
    assert attachment(name) == {"Content-Disposition": f"attachment; filename={name}"}


def test_nothing_in_a_name_can_start_another_header_or_leave_its_folder():
    header = _sendable(content_disposition("a\r\nSet-Cookie: x=1\x00/../b\\c.txt"))
    assert _real_name(header) == "aSet-Cookie: x=1-..-b-c.txt"
    assert content_disposition("") == "attachment; filename=download"
    assert content_disposition("   ", fallback="scan_7") == "attachment; filename=scan_7"
    # A name no file system would take is cut, in both forms.
    long = _sendable(content_disposition("ж" * 5000 + ".txt"))
    assert len(long) < 2000


@pytest.mark.parametrize("filename", [CYRILLIC, f"{CJK}.xml", COMMA])
@pytest.mark.parametrize("format_type", ["txt", "csv", "json"])
def test_a_scan_export_downloads_whatever_the_scan_file_is_called(
    client, db_session, test_project, filename, format_type,
):
    scan = models.Scan(project_id=test_project.id, filename=filename, tool_name="nmap", scan_type="nmap_xml")
    db_session.add(scan)
    db_session.flush()
    host = models.Host(project_id=test_project.id, ip_address="10.90.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan="up", discovered_at=NOW, host_created=True,
    ))
    db_session.commit()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/export/scan/{scan.id}", params={"format_type": format_type},
    )

    assert r.status_code == 200, r.text
    assert "10.90.0.1" in r.text
    header = _sendable(r.headers["content-disposition"])
    # The route's own naming (spaces as underscores), then the format.
    assert _real_name(header) == f"{filename.replace(' ', '_')}_hosts.{format_type}"


# The files this change could not edit (other streams own them in the same
# batch) and how many hand-written headers each still has; every name in them
# is a constant or built from an integer, so none can fail to encode.  Move
# each to ``attachment(...)`` and take its line out — the list only shrinks.
HAND_WRITTEN_ELSEWHERE = {
    "api/v1/endpoints/agent_assist.py": 2,
    "api/v1/endpoints/agent_recon.py": 1,
    "api/v1/endpoints/hosts.py": 1,
    "api/v1/endpoints/references.py": 2,
}


def test_no_route_writes_the_header_by_hand():
    """One implementation: a route names its download with ``attachment(...)``
    (or ``FileResponse(filename=…)``, Starlette's own RFC 6266)."""
    root = Path(app_package.__file__).parent
    written = re.compile(r"""["']Content-Disposition["']\s*:""")
    found = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "api/downloads.py":
            continue
        count = len(written.findall(path.read_text(encoding="utf-8")))
        if count:
            found[rel] = count
    assert found == HAND_WRITTEN_ELSEWHERE
