"""The lines an import did not interpret, as redacted shapes (v2.418.0).

Without real samples from a client network, the import itself says what it
could not read: each such line's structure with its values replaced, grouped
and counted, on the job (Ingestion Results) — never the values themselves.
"""
import os
import re

from app.db import models
from app.parsers.netexec_parser import NetexecParser
from app.services.line_shapes import ShapeTally, nxc_line_shape, redact_message

NATIVE = os.path.join(os.path.dirname(__file__), "fixtures", "native")


def test_flags_keep_their_meaning_and_lose_their_values():
    line = ("LDAP 10.9.0.5 389 DC01 [*] Windows Server 2022 Build 20348 "
            "(name:DC01) (domain:corp.example.com) (signing:None) (channel binding:Never)")
    assert nxc_line_shape(line, ("DC01",)) == (
        "LDAP <IP> 389 <HOST> [*] Windows Server 2022 Build 20348 "
        "(name:<VALUE>) (domain:<VALUE>) (signing:None) (channel binding:Never)"
    )


def test_addresses_names_paths_and_hashes_are_replaced():
    text = redact_message(
        r"reached 10.1.2.3 and [2001:db8::7] via files.corp.example.com, "
        r"wrote \\FS01\share\x and C:\Temp\y, hash 31d6cfe0d16ae931b73c59d7e0c089c0, mail a.b@corp.example"
    )
    for leaked in ("10.1.2.3", "2001:db8", "corp.example", "FS01", "Temp", "31d6cfe0", "a.b@"):
        assert leaked not in text, leaked
    assert "<IP>" in text and "<NAME>" in text and "<PATH>" in text and "<HASH>" in text


def test_tally_groups_by_shape_most_frequent_first():
    tally = ShapeTally()
    for _ in range(3):
        tally.add("text_only", "RDP <IP> 3389 <HOST> [*] (nla:False)")
    tally.add("dropped", "X")
    receipt = tally.receipt()
    assert receipt["total"] == 4 and receipt["distinct"] == 2
    assert receipt["shapes"][0] == {"kind": "text_only", "shape": "RDP <IP> 3389 <HOST> [*] (nla:False)", "count": 3}
    assert ShapeTally().receipt() is None


def _parse(db, project, tmp_path, text, name="nxc.txt"):
    p = tmp_path / name
    p.write_text(text)
    parser = NetexecParser(db)
    parser.parse_file(str(p), name, project_id=project.id)
    return parser


def test_netexec_reports_what_it_did_not_read(db_session, test_project, tmp_path):
    text = "\n".join([
        # Interpreted: the SMB banner (signing, SMBv1) — not reported.
        "SMB 10.9.0.5 445 DC01 [*] Windows Server 2022 Build 20348 x64 (name:DC01) (domain:corp.example.com) (signing:True) (SMBv1:False)",
        # LDAP / RDP flags: kept as text only.
        "LDAP 10.9.0.5 389 DC01 [*] Windows Server 2022 Build 20348 (name:DC01) (domain:corp.example.com) (signing:None) (channel binding:Never)",
        "RDP 10.9.0.6 3389 WS01 [*] Windows 10 (name:WS01) (domain:corp.example.com) (nla:False)",
        "RDP 10.9.0.7 3389 WS02 [*] Windows 10 (name:WS02) (domain:corp.example.com) (nla:False)",
        # A module in the protocol column.
        "ZEROLOGON 10.9.0.5 445 DC01 [+] VULNERABLE",
        # A hyphenated module: no pattern reads it.
        "MS17-010 10.9.0.6 445 WS01 [+] WS01 is VULNERABLE to MS17-010",
        # A plain banner: read (host, port, banner) — not reported.
        "SSH 10.9.0.8 22 10.9.0.8 [*] SSH-2.0-OpenSSH_8.9p1",
        # nxc chatter with no host columns: not a result.
        "[*] First time use detected",
    ])
    parser = _parse(db_session, test_project, tmp_path, text)
    receipt = parser.uninterpreted
    by_shape = {s["shape"]: (s["kind"], s["count"]) for s in receipt["shapes"]}
    assert by_shape["RDP <IP> 3389 <HOST> [*] Windows 10 (name:<VALUE>) (domain:<VALUE>) (nla:False)"] == ("text_only", 2)
    assert ("LDAP <IP> 389 <HOST> [*] Windows Server 2022 Build 20348 (name:<VALUE>) "
            "(domain:<VALUE>) (signing:None) (channel binding:Never)") in by_shape
    assert by_shape["ZEROLOGON <IP> 445 <HOST> [+] VULNERABLE"] == ("module_as_login", 1)
    assert by_shape["MS17-010 <IP> 445 <HOST> [+] <HOST> is VULNERABLE to MS17-010"] == ("dropped", 1)
    assert receipt["total"] == 5
    assert not any("SMB" in s or "SSH" in s or "First time" in s for s in by_shape)
    # The stats carry it to the job.
    assert parser.last_parse_stats["uninterpreted"] is receipt
    assert "5 lines not interpreted" in parser.last_parse_stats["warnings"]


def test_table_rows_keep_only_their_columns(db_session, test_project, tmp_path):
    text = "\n".join([
        "SMB 10.9.1.5 445 DC02 [*] Windows Server 2019 (name:DC02) (domain:corp.example.com) (signing:True) (SMBv1:False)",
        "SMB 10.9.1.5 445 DC02 [*] Trying to dump local users with SAMRPC protocol",
        "SMB 10.9.1.5 445 DC02 -Username- -Last PW Set- -BadPW- -Description-",
        "SMB 10.9.1.5 445 DC02 jsmith 2026-01-02 0 Finance team lead",
    ])
    shapes = [s["shape"] for s in _parse(db_session, test_project, tmp_path, text).uninterpreted["shapes"]]
    assert "SMB <IP> 445 <HOST> -Username- -Last PW Set- -BadPW- -Description-" in shapes
    assert "SMB <IP> 445 <HOST> <T> <T> <N> <T> <T> <T>" in shapes
    assert not any("jsmith" in s or "Finance" in s for s in shapes)


def test_credentials_never_reach_a_shape(db_session, test_project, tmp_path):
    """The wiki fixture's logins (user and password in the line) — the
    stored rows keep them (analysts need them), a shape never does."""
    with open(os.path.join(NATIVE, "netexec-wiki-vnc-ftp-ssh.txt")) as fh:
        content = fh.read()
    secrets = set()
    for line in content.splitlines():
        # A login's first token: user:password, or VNC's password alone.
        m = re.search(r"\[[+-]\]\s+(\S+)", line)
        if m and (":" in m.group(1) or line.startswith("VNC")):
            secrets.update(t for t in re.split(r"[:\\]", m.group(1)) if len(t) > 3)
    assert secrets, "the fixture's login lines were not found"
    parser = _parse(db_session, test_project, tmp_path, content)
    shapes = " ".join(s["shape"] for s in (parser.uninterpreted or {}).get("shapes", []))
    for secret in secrets:
        assert secret not in shapes


def test_the_command_line_is_never_a_shape(db_session, test_project, tmp_path):
    """The wiki captures start with the command the operator typed — with its
    -u / -p values.  It is not a result line and is never reported."""
    fixture = os.path.join(NATIVE, "netexec-wiki-ftp-get-and-put-files--block-04.txt")
    with open(fixture) as fh:
        content = fh.read()
    command = next(line for line in content.splitlines() if line.startswith("nxc "))
    typed = [tok for tok in command.split() if not tok.startswith("-")][3:]
    parser = _parse(db_session, test_project, tmp_path, content)
    shapes = [s["shape"] for s in (parser.uninterpreted or {}).get("shapes", [])]
    assert not any(s.startswith("nxc ") for s in shapes)
    assert not any(tok in " ".join(shapes) for tok in typed if len(tok) > 3)
    # The upload action is text, and its label is not taken for a credential.
    assert any("[+] Uploaded:" in s for s in shapes), shapes


def test_ingestion_results_serve_the_counts_and_the_shapes(client, db_session, test_project):
    job = models.IngestionJob(
        project_id=test_project.id, filename="j.txt", original_filename="nxc-run.txt",
        storage_path="/tmp/none", status="completed", tool_name="netexec",
        uninterpreted_lines={"total": 3, "distinct": 1, "shapes": [
            {"kind": "text_only", "shape": "RDP <IP> 3389 <HOST> [*] (nla:False)", "count": 3}]},
    )
    db_session.add(job)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results"
    row = next(i for i in client.get(base).json()["items"] if i["id"] == job.id)
    assert (row["uninterpreted_total"], row["uninterpreted_distinct"]) == (3, 1)
    body = client.get(f"{base}/{job.id}/uninterpreted").json()
    assert body["shapes"][0]["shape"] == "RDP <IP> 3389 <HOST> [*] (nla:False)"
    assert client.get(f"{base}/999999/uninterpreted").status_code == 404
