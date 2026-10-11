"""A DNS import records which hosts its answers addressed.

Reported from production (2026-10-10): a dnsx upload over a project of known
hosts showed no hosts on the Scans page, nothing in Ingestion results and no
Hosts filter.  The dnsx parser and the DNS CSV parser applied a name to a
host that ALREADY existed and wrote no ``host_scan_history`` row for it —
only a host the import CREATED got one (from the dedup service) — and every
"which hosts did this scan touch" reader asks that table.

The existing hosts are now noted through ``ScanHostObservations`` and written
by ``parser_utils.record_addressed_hosts`` (the web parsers' writer), ahead of
each heartbeat and after the last record.  Nothing about names, DNS records or
display-name precedence changes.
"""
from __future__ import annotations

import json
from datetime import timedelta

from app.db import models
from app.parsers.dns_parser import DNSParser
from app.parsers.dnsx_parser import DnsxParser
from app.services.host_change_service import hosts_changed_since_prior_scan
from app.services.host_deduplication_service import HostDeduplicationService
from tests.ingestion_job_harness import active_job, on_heartbeat


# ---------------------------------------------------------------- fixtures

def _nmap_hosts(db, pid, hosts, filename="known.xml", state="up"):
    """Hosts as an nmap import leaves them: created by the dedup service, with
    their history row, one open port each.  ``hosts`` is {address: name}."""
    scan = models.Scan(project_id=pid, filename=filename, tool_name="nmap", scan_type="nmap_xml")
    db.add(scan)
    db.flush()
    dedup = HostDeduplicationService(db)
    for address, name in hosts.items():
        host = dedup.find_or_create_host(
            address, scan.id, {"state": state, "hostname": name}, project_id=pid,
        )
        dedup.find_or_create_port(
            host.id, scan.id, {"port_number": 22, "protocol": "tcp", "state": "open"},
        )
    db.flush()
    # An earlier import: a test is one transaction, so every row would
    # otherwise carry the same now().
    db.execute(
        models.HostScanHistory.__table__.update()
        .where(models.HostScanHistory.scan_id == scan.id)
        .values(discovered_at=models.HostScanHistory.discovered_at - timedelta(hours=1))
    )
    db.commit()
    return scan


def _jsonl(tmp_path, name, records):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def _history(db, scan_id):
    """{address: (host_created, state_at_scan, hostname_at_scan)} of a scan."""
    db.expire_all()
    return {
        address: (created, state, name)
        for address, created, state, name in (
            db.query(
                models.Host.ip_address, models.HostScanHistory.host_created,
                models.HostScanHistory.state_at_scan, models.HostScanHistory.hostname_at_scan,
            )
            .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
            .filter(models.HostScanHistory.scan_id == scan_id)
        )
    }


def _host(db, pid, address):
    db.expire_all()
    return db.query(models.Host).filter_by(project_id=pid, ip_address=address).one_or_none()


#: Three known hosts — one unnamed, one with a scanner name, one whose name
#: the file repeats — and one address nobody has seen.
KNOWN = {"10.70.0.1": None, "10.70.0.2": "scanner-name.example", "10.70.0.3": "same.example"}
DNSX_ROWS = [
    # PTR fills an empty display name.
    {"host": "10.70.0.1", "ptr": ["one.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    # PTR outranks a scanner name: the display name changes.
    {"host": "10.70.0.2", "ptr": ["two.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    # A forward answer for a host that already has that name: nothing about
    # the host changes, and the file still addressed it.
    {"host": "same.example", "a": ["10.70.0.3"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    # An address the project did not have: created by the import.
    {"host": "new.corp.example", "a": ["10.70.0.9"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    # No address in it at all.
    {"host": "corp.example", "mx": ["10 mail.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
]


def _dnsx_import(db, pid, tmp_path, rows=DNSX_ROWS, name="dnsx.jsonl"):
    parser = DnsxParser(db)
    scan = parser.parse_file(str(_jsonl(tmp_path, name, rows)), name, project_id=pid)
    db.commit()
    return scan, parser


# ------------------------------------------------- what the pages now read

def test_the_scans_list_counts_the_hosts_a_dnsx_import_addressed(client, db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    scan, _ = _dnsx_import(db_session, pid, tmp_path)

    rows = client.get(f"/api/v1/projects/{pid}/scans/", params={"ids": str(scan.id)}).json()

    assert len(rows) == 1
    row = rows[0]
    # Three hosts that were already known, one the import created.
    assert (row["total_hosts"], row["new_hosts"], row["updated_hosts"]) == (4, 1, 3)
    # A DNS answer does not say a host is up.
    assert row["up_hosts"] == 0


def test_the_hosts_filter_by_scan_lists_the_hosts_a_dnsx_import_addressed(
    client, db_session, test_project, tmp_path,
):
    pid = test_project.id
    _nmap_hosts(db_session, pid, {**KNOWN, "10.70.0.200": "not-in-the-file.example"})
    scan, _ = _dnsx_import(db_session, pid, tmp_path)

    def addresses(**params):
        body = client.get(f"/api/v1/projects/{pid}/hosts/", params={"scan_ids": str(scan.id), **params}).json()
        return sorted(item["ip_address"] for item in body["items"])

    # "N already known" on the import's row opens this list…
    assert addresses() == ["10.70.0.1", "10.70.0.2", "10.70.0.3", "10.70.0.9"]
    # …and "+N hosts added" this one.
    assert addresses(first_seen_in_scan="true") == ["10.70.0.9"]
    # The query word for the same thing.
    by_word = client.get(f"/api/v1/projects/{pid}/hosts/ids", params={"scan_ids": str(scan.id)}).json()
    assert len(by_word["ids"]) == 4


def test_the_scan_page_lists_each_host_a_dnsx_import_addressed(client, db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    scan, _ = _dnsx_import(db_session, pid, tmp_path)

    body = client.get(f"/api/v1/projects/{pid}/scans/{scan.id}/host-snapshots").json()

    assert body["total"] == 4
    rows = {item["ip_address"]: item for item in body["items"]}
    # The name the file gave for the host, whether or not it became the
    # display name; no state (the file says nothing about it).
    assert (rows["10.70.0.1"]["hostname_at_scan"], rows["10.70.0.1"]["host_created"]) == ("one.corp.example", False)
    assert rows["10.70.0.2"]["hostname_at_scan"] == "two.corp.example"
    assert rows["10.70.0.3"]["hostname_at_scan"] == "same.example"
    assert all(rows[ip]["state_at_scan"] is None for ip in KNOWN)
    # The created host's row is the dedup service's, as before.
    assert (rows["10.70.0.9"]["host_created"], rows["10.70.0.9"]["state_at_scan"]) == (True, "unknown")


def test_a_dnsx_import_changes_nothing_else_about_the_hosts_it_addresses(db_session, test_project, tmp_path):
    """The record of who was addressed is ADDED; the names are decided by the
    same rule as before, and no state or 'last updated by' is written."""
    pid = test_project.id
    nmap = _nmap_hosts(db_session, pid, KNOWN)
    _dnsx_import(db_session, pid, tmp_path)

    names = {ip: (_host(db_session, pid, ip).hostname, _host(db_session, pid, ip).hostname_source) for ip in KNOWN}
    assert names == {
        "10.70.0.1": ("one.corp.example", "ptr"),
        "10.70.0.2": ("two.corp.example", "ptr"),
        "10.70.0.3": ("same.example", "scanner"),
    }
    for ip in KNOWN:
        host = _host(db_session, pid, ip)
        assert (host.state, host.last_updated_scan_id) == ("up", nmap.id)


def test_the_import_says_how_many_hosts_it_addressed_created_and_renamed(db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    _, parser = _dnsx_import(db_session, pid, tmp_path)

    # 4 addressed = 3 known + 1 created; the display name changed on two of
    # the known ones (10.70.0.3 already had the name the file gave).
    assert parser.last_parse_stats["summary"] == (
        "5 DNS records, 4 hosts addressed, 1 created, 2 display names changed"
    )
    assert "Host.hostname" not in (parser.last_parse_stats["warnings"] or "")


def test_an_import_that_addresses_no_host_says_so(db_session, test_project, tmp_path):
    _, parser = _dnsx_import(db_session, test_project.id, tmp_path, rows=[DNSX_ROWS[-1]])
    assert parser.last_parse_stats["summary"] == "1 DNS record, no hosts addressed"


def test_a_host_the_import_creates_and_then_names_again_has_one_history_row(db_session, test_project, tmp_path):
    """Two writers, one row per (host, scan): the dedup service writes the
    created host's row, and a later answer for the same address is a note."""
    pid = test_project.id
    scan, parser = _dnsx_import(db_session, pid, tmp_path, rows=[
        {"host": "www.corp.example", "a": ["10.70.3.1"], "status_code": "NOERROR"},
        {"host": "10.70.3.1", "ptr": ["real.corp.example"], "status_code": "NOERROR"},
        {"host": "alias.corp.example", "a": ["10.70.3.1"], "status_code": "NOERROR"},
    ])

    host = _host(db_session, pid, "10.70.3.1")
    rows = db_session.query(models.HostScanHistory).filter_by(host_id=host.id, scan_id=scan.id).all()
    assert len(rows) == 1 and rows[0].host_created is True
    assert rows[0].state_at_scan == "unknown"
    # Created, not "an existing host whose name changed".
    assert parser.last_parse_stats["summary"] == "3 DNS records, 1 host addressed, 1 created, 0 display names changed"


def test_a_transferred_zones_records_note_the_hosts_they_address(db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, {"10.70.4.1": None, "10.70.4.2": "kept.example"})
    scan, _ = _dnsx_import(db_session, pid, tmp_path, rows=[{
        "host": "corp.example", "status_code": "NOERROR",
        "axfr": {"host": "corp.example", "chain": [{
            "host": "corp.example", "resolver": ["10.70.4.53:53"],
            "all": [
                "www.corp.example.\t300\tIN\tA\t10.70.4.1",
                "1.4.70.10.in-addr.arpa.\t300\tIN\tPTR\tone.corp.example.",
                "db.corp.example.\t300\tIN\tA\t10.70.4.2",
            ],
        }]},
    }])

    assert set(_history(db_session, scan.id)) == {"10.70.4.1", "10.70.4.2"}


# ------------------------------------------------------------- the DNS CSV

def _csv_import(db, pid, tmp_path, text):
    path = tmp_path / "dns.csv"
    path.write_text(text)
    parser = DNSParser(db)
    scan = parser.parse_file(str(path), path.name, project_id=pid)
    db.commit()
    return scan, parser


CSV = (
    "record_type,name,address\n"
    "PTR,one.corp.example,10.70.0.1\n"
    "PTR,two.corp.example,10.70.0.2\n"
    "PTR,same.example,10.70.0.3\n"
    "PTR,new.corp.example,10.70.0.9\n"
    "MX,corp.example,10 mail.corp.example\n"
)


def test_a_dns_csv_import_records_the_hosts_its_ptr_rows_addressed(client, db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    scan, parser = _csv_import(db_session, pid, tmp_path, CSV)

    row = client.get(f"/api/v1/projects/{pid}/scans/", params={"ids": str(scan.id)}).json()[0]
    assert (row["total_hosts"], row["new_hosts"], row["updated_hosts"]) == (4, 1, 3)

    listed = client.get(f"/api/v1/projects/{pid}/hosts/", params={"scan_ids": str(scan.id)}).json()
    assert sorted(item["ip_address"] for item in listed["items"]) == [
        "10.70.0.1", "10.70.0.2", "10.70.0.3", "10.70.0.9",
    ]

    snapshots = client.get(f"/api/v1/projects/{pid}/scans/{scan.id}/host-snapshots").json()
    by_address = {item["ip_address"]: item for item in snapshots["items"]}
    assert snapshots["total"] == 4
    assert (by_address["10.70.0.2"]["hostname_at_scan"], by_address["10.70.0.2"]["state_at_scan"]) == (
        "two.corp.example", None,
    )
    assert by_address["10.70.0.9"]["host_created"] is True

    # same.example gained PTR provenance and kept its name: not a rename.
    assert parser.last_parse_stats["summary"] == (
        "5 DNS records, 4 hosts addressed, 1 created, 2 display names changed"
    )


def test_a_dns_csv_row_that_rolls_back_leaves_no_note_of_its_host(db_session, test_project, tmp_path):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    bad = _host(db_session, pid, "10.70.0.2").id
    path = tmp_path / "dns.csv"
    path.write_text(CSV)
    parser = DNSParser(db_session)
    note = parser._observed.note

    def note_then_fail(host, **kwargs):
        note(host, **kwargs)
        if host.id == bad:
            raise RuntimeError("boom")

    parser._observed.note = note_then_fail
    scan = parser.parse_file(str(path), path.name, project_id=pid)
    db_session.commit()

    assert set(_history(db_session, scan.id)) == {"10.70.0.1", "10.70.0.3", "10.70.0.9"}
    assert parser.last_parse_stats["skipped"] == 1
    # The row's rename went with its savepoint.
    assert _host(db_session, pid, "10.70.0.2").hostname == "scanner-name.example"
    assert parser.last_parse_stats["summary"] == "4 DNS records, 3 hosts addressed, 1 created, 1 display name changed"


# -------------------------------------------------------- record isolation

def test_a_dnsx_record_that_rolls_back_leaves_no_history_row(db_session, test_project, tmp_path, monkeypatch):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    real = DnsxParser._update_host_hostname

    def name_then_fail(self, scan_id, ip_address, hostname, *, source):
        real(self, scan_id, ip_address, hostname, source=source)
        if ip_address == "10.70.0.2":
            raise RuntimeError("boom")

    monkeypatch.setattr(DnsxParser, "_update_host_hostname", name_then_fail)
    scan, parser = _dnsx_import(db_session, pid, tmp_path)

    # The rolled-back record's host is not in the scan; the others are.
    assert set(_history(db_session, scan.id)) == {"10.70.0.1", "10.70.0.3", "10.70.0.9"}
    assert parser.last_parse_stats["skipped"] == 1
    assert _host(db_session, pid, "10.70.0.2").hostname == "scanner-name.example"
    assert parser.last_parse_stats["summary"] == "4 DNS records, 3 hosts addressed, 1 created, 1 display name changed"


def test_a_rolled_back_record_that_created_a_host_leaves_no_row_for_a_later_note(
    db_session, test_project, tmp_path, monkeypatch,
):
    """The record creates a host, addresses it again and then fails: neither
    the dedup service's row nor a note of the vanished host survives, and the
    import still finishes."""
    pid = test_project.id
    _nmap_hosts(db_session, pid, {"10.70.5.1": None})
    real = DnsxParser._persist_record

    def fail_on_the_last_answer(self, journal, domain, record_type, value, *args, **kwargs):
        if record_type == "TXT":
            raise RuntimeError("boom")
        return real(self, journal, domain, record_type, value, *args, **kwargs)

    monkeypatch.setattr(DnsxParser, "_persist_record", fail_on_the_last_answer)
    scan, _ = _dnsx_import(db_session, pid, tmp_path, rows=[
        {"host": "gone.corp.example", "a": ["10.70.5.9", "10.70.5.9"], "txt": ["x"], "status_code": "NOERROR"},
        {"host": "10.70.5.1", "ptr": ["one.corp.example"], "status_code": "NOERROR"},
    ])

    assert _host(db_session, pid, "10.70.5.9") is None
    assert set(_history(db_session, scan.id)) == {"10.70.5.1"}


# ------------------------------------------------------------- heartbeats

def test_the_addressed_hosts_are_written_ahead_of_each_heartbeat(db_session, test_project, tmp_path):
    """What a heartbeat's commit makes durable says which hosts the import has
    addressed so far (and a cancel there leaves the cleanup the whole truth)."""
    pid = test_project.id
    known = {f"10.70.6.{i}": None for i in range(1, 4)}
    _nmap_hosts(db_session, pid, known)
    rows = [
        {"host": f"10.70.6.{1 + i % 3}", "ptr": [f"n{i}.corp.example"], "status_code": "NOERROR"}
        for i in range(620)
    ]
    path = _jsonl(tmp_path, "many.jsonl", rows)
    seen_at_heartbeat = []

    def count_history():
        scan_id = db_session.query(models.Scan.id).filter_by(project_id=pid, tool_name="dnsx").scalar()
        seen_at_heartbeat.append(
            db_session.query(models.HostScanHistory).filter_by(scan_id=scan_id).count()
        )

    with active_job(db_session, pid) as (_job_id, svc):
        with on_heartbeat(svc, 1, count_history) as beats:
            scan = DnsxParser(db_session).parse_file(str(path), path.name, project_id=pid)
    db_session.commit()

    assert beats, "the parser never reached a heartbeat"
    assert seen_at_heartbeat == [3]
    assert set(_history(db_session, scan.id)) == set(known)


# ------------------------------------------------------------ side effects

def test_deleting_the_dnsx_scan_keeps_the_hosts_another_scan_brought(client, db_session, test_project, tmp_path):
    """The hand delete starts from every host the scan has history for, and a
    DNS import now has history for hosts it did not bring.  They have another
    scan's history, so they stay — with the name the DNS import gave them (no
    previous value is kept: the rule for every overwritten value)."""
    pid = test_project.id
    nmap = _nmap_hosts(db_session, pid, KNOWN)
    scan, _ = _dnsx_import(db_session, pid, tmp_path)
    scan_id = scan.id
    # The case under test: the DNS scan has history for hosts it did not bring.
    assert set(_history(db_session, scan_id)) == {*KNOWN, "10.70.0.9"}

    preview =client.get(f"/api/v1/projects/{pid}/scans/{scan_id}/deletion-impact").json()
    assert preview["hosts_removed"] == 1 and preview["hosts_with_work"] == 0

    response = client.delete(f"/api/v1/projects/{pid}/scans/{scan_id}")

    assert response.status_code == 200, response.text
    assert response.json()["hosts_removed"] == 1          # the one it created
    assert _host(db_session, pid, "10.70.0.9") is None
    for ip in KNOWN:
        assert _host(db_session, pid, ip) is not None, f"{ip} went with the DNS scan"
    assert set(_history(db_session, nmap.id)) == set(KNOWN)
    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan_id).count() == 0
    assert db_session.query(models.Port).join(models.Host).filter(models.Host.project_id == pid).count() == 3
    # Overwritten values are not restored.
    assert _host(db_session, pid, "10.70.0.2").hostname == "two.corp.example"


def test_a_failed_dnsx_import_keeps_the_hosts_it_only_addressed(db_session, test_project, tmp_path):
    from app.services.ingestion_service import delete_partial_scan

    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    scan, _ = _dnsx_import(db_session, pid, tmp_path)

    delete_partial_scan(db_session, scan.id)
    db_session.commit()

    assert _host(db_session, pid, "10.70.0.9") is None
    assert all(_host(db_session, pid, ip) is not None for ip in KNOWN)


def test_a_dns_import_is_not_a_change_of_the_hosts_state(db_session, test_project, tmp_path):
    """The Hosts list's "Changed" mark and the queue's "changed at its latest
    scan" compare the two latest scans that stated the host's liveness: a DNS
    import's row states none and is not one of them."""
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    _dnsx_import(db_session, pid, tmp_path)
    ids = [_host(db_session, pid, ip).id for ip in KNOWN]

    assert hosts_changed_since_prior_scan(db_session, ids) == set()

    # A later scan that says the host is down IS a change, across the DNS row.
    later = models.Scan(project_id=pid, filename="later.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add(later)
    db_session.flush()
    HostDeduplicationService(db_session).find_or_create_host(
        "10.70.0.1", later.id, {"state": "down"}, project_id=pid,
    )
    db_session.flush()
    db_session.execute(
        models.HostScanHistory.__table__.update()
        .where(models.HostScanHistory.scan_id == later.id)
        .values(discovered_at=models.HostScanHistory.discovered_at + timedelta(hours=1))
    )
    db_session.commit()

    assert hosts_changed_since_prior_scan(db_session, ids) == {_host(db_session, pid, "10.70.0.1").id}


def test_a_dns_import_does_not_make_a_host_vulnerability_assessed(client, db_session, test_project, tmp_path):
    from app.services.evidence_service import vuln_assessed_condition

    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)
    _dnsx_import(db_session, pid, tmp_path)

    assessed = db_session.query(models.Host.id).filter(
        models.Host.project_id == pid, vuln_assessed_condition(),
    ).count()
    assert assessed == 0
