"""Production diagnostics bundle, 2026-09-26 (v2.420.0).

Two NetExec captures (1.6 and 4.5 MB) failed whole with PostgreSQL's "A string
literal cannot contain NUL (0x00) characters": the stored tool line carried a
NUL.  A PowerShell ``>`` redirect writes UTF-16 — a NUL after every character
when read as UTF-8 — and a terminal capture can carry stray NUL bytes.
"""
from app.db.models_confidence import NetexecResult
from app.parsers.netexec_parser import NetexecParser
from app.parsers.parser_utils import read_tool_text
from app.services.import_attention_service import _plain_reason

LINES = [
    "SMB         10.62.0.5       445    DC01    [*] Windows Server 2022 Build 20348 x64 (name:DC01) (domain:corp.example) (signing:False) (SMBv1:False)",
    "SMB         10.62.0.6       445    WS01    [*] Windows 10 Build 19041 x64 (name:WS01) (domain:corp.example) (signing:False) (SMBv1:True)",
]


def _import(db, project, path):
    parser = NetexecParser(db)
    scan = parser.parse_file(str(path), path.name, project_id=project.id)
    rows = db.query(NetexecResult).filter(NetexecResult.scan_id == scan.id).all()
    return parser, rows


def test_stray_nul_bytes_do_not_fail_the_import(db_session, test_project, tmp_path):
    path = tmp_path / "nxc.txt"
    path.write_bytes((LINES[0] + "\x00\x00\n" + "\x00garbage\x00\n" + LINES[1] + "\n").encode())
    parser, rows = _import(db_session, test_project, path)
    assert len(rows) == 2
    assert all("\x00" not in (r.raw_output or "") for r in rows)
    assert "4 NUL bytes removed" in parser.last_parse_stats["warnings"]


def test_a_utf16_powershell_capture_is_read_as_text(db_session, test_project, tmp_path):
    path = tmp_path / "nxc-ps.txt"
    path.write_bytes(("\r\n".join(LINES) + "\r\n").encode("utf-16"))  # BOM + UTF-16LE
    parser, rows = _import(db_session, test_project, path)
    assert sorted(r.hostname for r in rows) == ["DC01", "WS01"]
    assert "read as UTF-16" in parser.last_parse_stats["warnings"]


def test_utf16_without_a_bom_is_recognised(tmp_path):
    path = tmp_path / "x.txt"
    path.write_bytes(("\n".join(LINES) + "\n").encode("utf-16-le"))
    read = read_tool_text(str(path))
    assert read.encoding == "utf-16-le" and read.text.startswith("SMB") and read.nul_removed == 0


def test_plain_utf8_is_unchanged(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("\n".join(LINES))
    read = read_tool_text(str(path))
    assert (read.encoding, read.nul_removed) == ("utf-8", 0) and read.text == "\n".join(LINES)


def test_queue_metrics_report_disk_space(tmp_path, monkeypatch):
    """The admin Queue Health card shows free space; production's disk filled
    and Postgres stopped with nothing in the app having said so."""
    from collections import namedtuple
    from app.services import queue_metrics_service as qm

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda p: Usage(160 * 1024 ** 3, 0, 4 * 1024 ** 3))
    assert qm.disk_snapshot(str(tmp_path)) == {
        "total_bytes": 160 * 1024 ** 3, "free_bytes": 4 * 1024 ** 3, "low": True}
    monkeypatch.setattr("shutil.disk_usage", lambda p: Usage(160 * 1024 ** 3, 0, 40 * 1024 ** 3))
    assert qm.disk_snapshot(str(tmp_path))["low"] is False


def test_the_nul_failure_is_explained_in_plain_words():
    reason = _plain_reason("A string literal cannot contain NUL (0x00) characters.")
    assert "NUL bytes" in reason and "re-import" in reason
