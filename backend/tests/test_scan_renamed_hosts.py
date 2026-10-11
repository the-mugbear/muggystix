"""A scan records the hosts whose DISPLAY NAME it changed, and the figure opens its list.

Owner, 2026-10-11: "Add display names changed."  The number of hosts an import
renamed was a figure inside the DNS import's result sentence and nowhere else.
It is now ``host_scan_history.hostname_changed`` (revision ``d7b3e9a1c5f2``):

* written where the name is actually changed, for EVERY tool — the dedup
  service's shared path, ``resolve_host_cached`` (the web parsers), the two
  DNS parsers and NetExec's one direct write — by the two writers of the
  history row and no third;
* never on the row of the scan that CREATED the host ("3 added, 2 renamed" do
  not overlap), never turned back to false by a later record of the same scan;
* read through ONE condition, ``host_query_predicates.renamed_in_scan_condition``:
  a scan's ``renamed_hosts`` (and a batch's) equals the ``total`` of the Hosts
  list it opens, ``scan_ids=<id>&renamed_in_scan=true``;
* the DNS result sentence's "N display names changed" comes from the set that
  sets the flag.

Nothing about WHICH name wins or WHEN a name is applied changes: the tests
that pin precedence (``test_dns_names*``, ``test_dns_import_addressed_hosts``)
are untouched and still pass.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.config import settings
from app.db import models
from app.db.models_project import Project
from app.parsers.dns_parser import DNSParser
from app.parsers.dnsx_parser import DnsxParser
from app.parsers.parser_utils import (
    ScanHostObservations,
    record_hosts_in_scan,
    resolve_host_cached,
)
from app.services import host_query_predicates as P
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.scan_snapshot_service import scan_host_snapshots
from tests.test_every_import_records_the_hosts_it_touches import (
    CASES,
    Case,
    _import,
    _known_hosts,
    _text,
)


@pytest.fixture(autouse=True)
def _scan_files_in_the_test_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))


# ---------------------------------------------------------------- helpers

def _scan(db, pid, filename, tool="nmap", batch_id=None):
    scan = models.Scan(project_id=pid, filename=filename, tool_name=tool, scan_type=tool, batch_id=batch_id)
    db.add(scan)
    db.flush()
    return scan


def _earlier(db, scan):
    """A test is one transaction, so every row would carry the same now()."""
    db.flush()
    db.execute(
        models.HostScanHistory.__table__.update()
        .where(models.HostScanHistory.scan_id == scan.id)
        .values(discovered_at=models.HostScanHistory.discovered_at - timedelta(hours=1))
    )


def _flags(db, scan_id):
    """{address: (host_created, hostname_changed)} of a scan's history rows."""
    db.flush()
    db.expire_all()
    return {
        address: (bool(created), bool(changed))
        for address, created, changed in (
            db.query(
                models.Host.ip_address, models.HostScanHistory.host_created,
                models.HostScanHistory.hostname_changed,
            )
            .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
            .filter(models.HostScanHistory.scan_id == scan_id)
        )
    }


def _renamed(db, scan_id):
    return sorted(address for address, (_, changed) in _flags(db, scan_id).items() if changed)


def _names(db, pid):
    db.expire_all()
    return dict(db.query(models.Host.ip_address, models.Host.hostname).filter(models.Host.project_id == pid))


def _hosts(client, pid, **params):
    answer = client.get(f"/api/v1/projects/{pid}/hosts/", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _scan_row(client, pid, scan_id):
    rows = client.get(f"/api/v1/projects/{pid}/scans/", params={"ids": str(scan_id)}).json()
    assert len(rows) == 1
    return rows[0]


# ------------------------------------------------ the dedup service's path

def test_the_dedup_path_marks_only_an_existing_host_whose_name_it_changed(db_session, test_project):
    pid = test_project.id
    dedup = HostDeduplicationService(db_session)
    first = _scan(db_session, pid, "first.xml")
    before = {
        "10.60.0.1": None,                 # unnamed: the next scan fills it
        "10.60.0.2": "same.example",       # the next scan repeats the name
        "10.60.0.3": "kept.example",       # the next scan reports no name
        "10.60.0.4": "ptr.example",        # a PTR name a scanner name cannot replace
    }
    for address, name in before.items():
        dedup.find_or_create_host(
            address, first.id,
            {"state": "up", "hostname": name, "hostname_source": "ptr" if address.endswith(".4") else None},
            project_id=pid,
        )
    _earlier(db_session, first)
    # The scan that created the hosts renamed none of them, named or not.
    assert _renamed(db_session, first.id) == []

    second = _scan(db_session, pid, "second.xml")
    dedup.find_or_create_host("10.60.0.1", second.id, {"state": "up", "hostname": "filled.example"}, project_id=pid)
    dedup.find_or_create_host("10.60.0.2", second.id, {"state": "up", "hostname": "same.example"}, project_id=pid)
    dedup.find_or_create_host("10.60.0.3", second.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_host("10.60.0.4", second.id, {"state": "up", "hostname": "scanner.example"}, project_id=pid)
    # Created by this scan WITH a name: created, not renamed.
    dedup.find_or_create_host("10.60.0.9", second.id, {"state": "up", "hostname": "new.example"}, project_id=pid)
    # Created by this scan without a name and named by a later record of it:
    # still a host the scan created.
    dedup.find_or_create_host("10.60.0.10", second.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_host("10.60.0.10", second.id, {"state": "up", "hostname": "later.example"}, project_id=pid)

    assert _flags(db_session, second.id) == {
        "10.60.0.1": (False, True),
        "10.60.0.2": (False, False),
        "10.60.0.3": (False, False),
        "10.60.0.4": (False, False),
        "10.60.0.9": (True, False),
        "10.60.0.10": (True, False),
    }
    # What the flag says happened is what happened, and nothing else moved.
    assert _names(db_session, pid) == {
        "10.60.0.1": "filled.example", "10.60.0.2": "same.example", "10.60.0.3": "kept.example",
        "10.60.0.4": "ptr.example", "10.60.0.9": "new.example", "10.60.0.10": "later.example",
    }


def test_a_later_record_of_the_same_scan_never_takes_the_mark_back(db_session, test_project):
    pid = test_project.id
    dedup = HostDeduplicationService(db_session)
    first = _scan(db_session, pid, "first.xml")
    dedup.find_or_create_host("10.60.1.1", first.id, {"state": "up"}, project_id=pid)
    _earlier(db_session, first)

    second = _scan(db_session, pid, "second.gnmap")
    dedup.find_or_create_host("10.60.1.1", second.id, {"state": "up", "hostname": "named.example"}, project_id=pid)
    # gnmap's second line for the host: same name, then no name at all — both
    # through the pending row and, after a heartbeat released it, the stored one.
    dedup.find_or_create_host("10.60.1.1", second.id, {"state": "up", "hostname": "named.example"}, project_id=pid)
    db_session.flush()
    dedup.release_committed_history()
    dedup.find_or_create_host("10.60.1.1", second.id, {"state": "unknown"}, project_id=pid)

    assert _flags(db_session, second.id) == {"10.60.1.1": (False, True)}


# ------------------------------------------------------ the web parsers' path

def test_the_web_parsers_path_marks_a_host_it_named_and_not_one_it_created(db_session, test_project):
    pid = test_project.id
    dedup = HostDeduplicationService(db_session)
    first = _scan(db_session, pid, "first.xml")
    dedup.find_or_create_host("10.61.0.1", first.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_host("10.61.0.2", first.id, {"state": "up", "hostname": "ptr.example", "hostname_source": "ptr"}, project_id=pid)
    _earlier(db_session, first)
    db_session.commit()

    scan = _scan(db_session, pid, "httpx.jsonl", tool="httpx")
    observed, cache = ScanHostObservations(), {}

    def record(address, name):
        resolved = resolve_host_cached(db_session, pid, address, cache, hostname=name)
        observed.note(resolved.host, created=resolved.created, state="up", hostname=name, renamed=resolved.renamed)
        return resolved

    assert record("10.61.0.1", "web.example").renamed is True      # an empty name filled
    assert record("10.61.0.2", "vhost.example").renamed is False   # a PTR name stays
    assert record("10.61.0.9", "new.example") == (cache["10.61.0.9"], True, False)
    record_hosts_in_scan(db_session, scan.id, observed)   # ahead of a heartbeat…
    db_session.flush()                                     # …whose commit stores the rows
    # Later records of the same file: the name again, and the created host
    # named anew by a cache hit — neither changes what is recorded.
    assert record("10.61.0.1", "web.example").renamed is False
    cache["10.61.0.9"].hostname = None
    assert record("10.61.0.9", "again.example").renamed is True    # the rule's answer…
    record_hosts_in_scan(db_session, scan.id, observed)

    assert _flags(db_session, scan.id) == {
        "10.61.0.1": (False, True),
        "10.61.0.2": (False, False),
        "10.61.0.9": (True, False),                                 # …but the scan created it
    }
    assert observed.renamed_host_ids == {cache["10.61.0.1"].id}


# ----------------------------------------------------------- every format

@pytest.mark.parametrize("file_type", sorted(CASES))
def test_every_format_marks_exactly_the_known_hosts_it_named(db_session, test_project, tmp_path, file_type):
    """The contract for EVERY import format, read from the stored rows: over
    hosts that exist and have NO name, a host carries a name afterwards if and
    only if the import's history row says it changed the name.  A writer of
    ``Host.hostname`` that does not record it fails here, whatever the tool."""
    case, pid = CASES[file_type], test_project.id
    _known_hosts(db_session, pid, case.addresses)
    assert not any(_names(db_session, pid).values())

    scan_id = _import(db_session, pid, tmp_path, file_type, case)

    flags = _flags(db_session, scan_id)
    names = _names(db_session, pid)
    known = set(case.addresses)
    named = sorted(address for address in known if names[address])
    marked = sorted(address for address in known if flags.get(address, (False, False))[1])
    assert marked == named, (
        f"{file_type}: known hosts the import named {named} != hosts its history marks renamed {marked}"
    )
    # A host the import created is never marked, named or not.
    both = sorted(address for address, (created, changed) in flags.items() if created and changed)
    assert not both, f"{file_type}: created AND renamed: {both}"

    # The same content again changes no name, so the repeat marks nothing…
    again = _import(db_session, pid, tmp_path, file_type, case, copy=2)
    assert _names(db_session, pid) == names
    assert _renamed(db_session, again) == [], file_type
    # …and the first import keeps what it recorded.
    assert _flags(db_session, scan_id) == flags


@pytest.mark.parametrize("file_type", sorted(CASES))
def test_a_first_import_marks_no_host_renamed(db_session, test_project, tmp_path, file_type):
    """Every host of a first import is one it created: none had a name to change."""
    case, pid = CASES[file_type], test_project.id
    scan_id = _import(db_session, pid, tmp_path, file_type, case)
    assert _renamed(db_session, scan_id) == [], file_type


_NMAP_NAMED = (
    '<?xml version="1.0"?>\n'
    '<nmaprun scanner="nmap" args="nmap -sV" start="1700000000" version="7.94">\n'
    + "".join(
        f'<host><status state="up" reason="syn-ack"/><address addr="10.66.0.{n}" addrtype="ipv4"/>'
        + (f'<hostnames><hostname name="srv{n}.corp.example" type="PTR"/></hostnames>' if n < 3 else "")
        + '<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/>'
        '<service name="ssh" method="probed" conf="10"/></port></ports></host>\n'
        for n in (1, 2, 3)
    )
    + '<runstats><finished time="1700000100"/></runstats></nmaprun>\n'
)
_HTTPX_NAMED = "".join(
    json.dumps(record) + "\n" for record in (
        {"url": "https://web1.corp.example:443/", "input": "web1.corp.example", "host": "web1.corp.example",
         "host_ip": "10.66.1.1", "port": "443", "scheme": "https", "status_code": 200},
        {"url": "https://web2.corp.example:443/", "input": "web2.corp.example", "host": "web2.corp.example",
         "host_ip": "10.66.1.2", "port": "443", "scheme": "https", "status_code": 200},
        {"url": "http://10.66.1.3:80/", "input": "10.66.1.3", "host": "10.66.1.3", "port": "80", "status_code": 200},
    )
)
#: NetExec's one write of the display name outside the dedup service: a
#: further line of a host fills the name the first line did not carry.
_NXC_NAMED_BY_A_LATER_LINE = (
    "FTP         10.62.0.5   21     10.62.0.5    [*] Banner: (vsFTPd 3.0.5)\n"
    "LDAP        10.62.0.5   389    FILESRV01    [*] Windows Server 2019 Build 17763\n"
    "FTP         10.62.0.6   21     10.62.0.6    [*] Banner: (vsFTPd 3.0.5)\n"
)

#: A file per WRITER of the display name, each naming two known hosts and
#: addressing a third it gives no name: {format: (case, the hosts it names)}.
#: (The cases borrowed above mostly carry no names; without these the
#: every-format contract would prove little.)
NAMING_FILES = {
    # HostDeduplicationService._update_existing_host (nmap, and every parser
    # that reaches the inventory through find_or_create_host).
    "nmap_xml": (
        Case("named.xml", _text(_NMAP_NAMED), ["10.66.0.1", "10.66.0.2", "10.66.0.3"], "written here"),
        {"10.66.0.1": "srv1.corp.example", "10.66.0.2": "srv2.corp.example"},
    ),
    # parser_utils.resolve_host_cached (httpx, whatweb, testssl).
    "httpx_json": (
        Case("named.jsonl", _text(_HTTPX_NAMED), ["10.66.1.1", "10.66.1.2", "10.66.1.3"], "written here"),
        {"10.66.1.1": "web1.corp.example", "10.66.1.2": "web2.corp.example"},
    ),
    # The two DNS parsers' own apply_hostname_candidate calls.
    "dnsx_json": (
        Case("named-dnsx.jsonl", _text(
            json.dumps({"host": "10.66.2.1", "ptr": ["one.corp.example"], "status_code": "NOERROR"}) + "\n"
            + json.dumps({"host": "10.66.2.2", "ptr": ["two.corp.example"], "status_code": "NOERROR"}) + "\n"
            + json.dumps({"host": "corp.example", "mx": ["10 mail.corp.example"], "status_code": "NOERROR"}) + "\n"
        ), ["10.66.2.1", "10.66.2.2"], "written here"),
        {"10.66.2.1": "one.corp.example", "10.66.2.2": "two.corp.example"},
    ),
    "dns_csv": (
        Case("named-dns.csv", _text(
            "record_type,name,address\nPTR,one.corp.example,10.66.3.1\nPTR,two.corp.example,10.66.3.2\n"
            "A,three.corp.example,10.66.3.3\n"
        ), ["10.66.3.1", "10.66.3.2", "10.66.3.3"], "written here"),
        {"10.66.3.1": "one.corp.example", "10.66.3.2": "two.corp.example"},
    ),
    # netexec_parser._process_additional_observation's direct write.
    "netexec_output": (
        Case("nxc-later-line.txt", _text(_NXC_NAMED_BY_A_LATER_LINE), ["10.62.0.5", "10.62.0.6"], "written here"),
        {"10.62.0.5": "FILESRV01"},
    ),
}


@pytest.mark.parametrize("file_type", sorted(NAMING_FILES))
def test_each_writer_of_a_display_name_records_the_hosts_it_named(
    client, db_session, test_project, tmp_path, file_type,
):
    """Through the real job path, over known unnamed hosts: the hosts the
    file names are the hosts marked renamed, the figure and the list."""
    (case, named), pid = NAMING_FILES[file_type], test_project.id
    _known_hosts(db_session, pid, case.addresses)

    scan_id = _import(db_session, pid, tmp_path, file_type, case)

    names = _names(db_session, pid)
    assert {address: name for address, name in names.items() if name} == named
    assert _flags(db_session, scan_id) == {
        address: (False, address in named) for address in case.addresses
    }
    row = _scan_row(client, pid, scan_id)
    listed = _hosts(client, pid, scan_ids=str(scan_id), renamed_in_scan="true")
    assert row["renamed_hosts"] == listed["total"] == len(named)
    assert sorted(item["ip_address"] for item in listed["items"]) == sorted(named)
    assert (row["new_hosts"], row["updated_hosts"]) == (0, len(case.addresses))

    # Into an empty project the same file CREATES the hosts, names included:
    # added, never renamed.
    other = Project(name=f"empty-{file_type}", slug=f"empty-{file_type}".replace("_", "-"))
    db_session.add(other)
    db_session.commit()
    created = _import(db_session, other.id, tmp_path, file_type, case, copy=2)
    assert {a: n for a, n in _names(db_session, other.id).items() if n} == named
    assert _renamed(db_session, created) == []


# ------------------------------------------- the DNS sentence and the figure

KNOWN = {"10.70.0.1": None, "10.70.0.2": "scanner-name.example", "10.70.0.3": "same.example"}
DNSX_ROWS = [
    {"host": "10.70.0.1", "ptr": ["one.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    {"host": "10.70.0.2", "ptr": ["two.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    {"host": "same.example", "a": ["10.70.0.3"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    # Created by the import, then named again by a PTR answer of the same file.
    {"host": "new.corp.example", "a": ["10.70.0.9"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
    {"host": "10.70.0.9", "ptr": ["ptr-of-new.corp.example"], "resolver": ["10.70.0.53:53"], "status_code": "NOERROR"},
]
DNS_CSV = (
    "record_type,name,address\n"
    "PTR,one.corp.example,10.70.0.1\n"
    "PTR,two.corp.example,10.70.0.2\n"
    "PTR,same.example,10.70.0.3\n"
    "PTR,new.corp.example,10.70.0.9\n"
    "PTR,second-name-of-new.corp.example,10.70.0.9\n"
)


def _nmap_hosts(db, pid, hosts):
    scan = _scan(db, pid, "known.xml")
    dedup = HostDeduplicationService(db)
    for address, name in hosts.items():
        dedup.find_or_create_host(address, scan.id, {"state": "up", "hostname": name}, project_id=pid)
    _earlier(db, scan)
    db.commit()
    return scan


def _dnsx(db, pid, tmp_path):
    path = tmp_path / "dnsx.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in DNSX_ROWS))
    parser = DnsxParser(db)
    scan = parser.parse_file(str(path), "dnsx.jsonl", project_id=pid)
    db.commit()
    return scan, parser


def _dns_csv(db, pid, tmp_path, content=DNS_CSV):
    path = tmp_path / "dns.csv"
    path.write_text(content)
    parser = DNSParser(db)
    scan = parser.parse_file(str(path), "dns.csv", project_id=pid)
    db.commit()
    return scan, parser


@pytest.mark.parametrize("importer", [_dnsx, _dns_csv], ids=["dnsx", "dns_csv"])
def test_a_dns_imports_sentence_the_stored_rows_the_figure_and_the_list_agree(
    client, db_session, test_project, tmp_path, importer,
):
    pid = test_project.id
    _nmap_hosts(db_session, pid, KNOWN)

    scan, parser = importer(db_session, pid, tmp_path)

    # Two existing hosts renamed; the third already had the name; the fourth
    # was created by the import (and named twice by it).
    assert "4 hosts addressed, 1 created, 2 display names changed" in parser.last_parse_stats["summary"]
    assert _flags(db_session, scan.id) == {
        "10.70.0.1": (False, True),
        "10.70.0.2": (False, True),
        "10.70.0.3": (False, False),
        "10.70.0.9": (True, False),
    }
    row = _scan_row(client, pid, scan.id)
    assert (row["new_hosts"], row["updated_hosts"], row["renamed_hosts"]) == (1, 3, 2)
    listed = _hosts(client, pid, scan_ids=str(scan.id), renamed_in_scan="true")
    assert listed["total"] == row["renamed_hosts"]
    assert sorted(item["ip_address"] for item in listed["items"]) == ["10.70.0.1", "10.70.0.2"]


# ------------------------------------------------ the count opens its list

#: More renamed hosts than the page the list is asked for.
RENAMED = 23
PAGE = 10


def _project_with_every_kind(db, pid, batch_id=None):
    """Three scans.  ``first`` brings 40 hosts (the odd ones named).  ``second``
    then renames 23 of them, re-observes the rest unchanged and creates five
    (named).  ``third`` renames three hosts ``second`` also renamed and two
    of its own."""
    dedup = HostDeduplicationService(db)
    first = _scan(db, pid, "first.xml")
    for n in range(1, 41):
        dedup.find_or_create_host(
            f"10.63.0.{n}", first.id, {"state": "up", "hostname": f"old-{n}.example" if n % 2 else None},
            project_id=pid,
        )
    _earlier(db, first)
    _earlier(db, first)

    second = _scan(db, pid, "second.xml", batch_id=batch_id)
    for n in range(1, 41):
        data = {"state": "up"}
        if n <= RENAMED:
            # Fills an empty name (even n) — an odd host's scanner name is
            # replaced only by a stronger source, here PTR.
            data.update(hostname=f"new-{n}.example", hostname_source="ptr")
        elif n % 2:
            data.update(hostname=f"old-{n}.example")       # the name it already has
        dedup.find_or_create_host(f"10.63.0.{n}", second.id, data, project_id=pid)
    for n in range(101, 106):
        dedup.find_or_create_host(f"10.63.0.{n}", second.id, {"state": "up", "hostname": f"created-{n}.example"}, project_id=pid)
    _earlier(db, second)

    third = _scan(db, pid, "third.xml", batch_id=batch_id)
    for n in (1, 2, 3, 30, 32):
        dedup.find_or_create_host(
            f"10.63.0.{n}", third.id, {"state": "up", "hostname": f"operator-{n}.example", "hostname_source": "operator"},
            project_id=pid,
        )
    dedup.find_or_create_host("10.63.0.40", third.id, {"state": "up"}, project_id=pid)
    db.commit()
    return first, second, third


def test_a_scans_renamed_figure_is_the_total_of_the_hosts_list_it_opens(client, db_session, test_project):
    pid = test_project.id
    first, second, third = _project_with_every_kind(db_session, pid)

    expected = {first.id: 0, second.id: RENAMED, third.id: 5}
    for scan_id, renamed in expected.items():
        row = _scan_row(client, pid, scan_id)
        listed = _hosts(client, pid, scan_ids=str(scan_id), renamed_in_scan="true", limit=PAGE)
        assert row["renamed_hosts"] == renamed == listed["total"], (scan_id, row["renamed_hosts"], listed["total"])
        assert len(listed["items"]) == min(renamed, PAGE)
        # The whole list, paged through: exactly the hosts the rows mark.
        addresses, skip = [], 0
        while len(addresses) < listed["total"]:
            page = _hosts(client, pid, scan_ids=str(scan_id), renamed_in_scan="true", limit=PAGE, skip=skip)["items"]
            assert page
            addresses += [item["ip_address"] for item in page]
            skip += PAGE
        assert sorted(addresses) == _renamed(db_session, scan_id)
        # The three figures of the row partition what it observed.
        assert row["new_hosts"] + row["updated_hosts"] == row["total_hosts"]
        assert row["renamed_hosts"] <= row["updated_hosts"]
        ids = client.get(
            f"/api/v1/projects/{pid}/hosts/ids", params={"scan_ids": str(scan_id), "renamed_in_scan": "true"},
        ).json()
        assert ids["total"] == renamed

    second_row = _scan_row(client, pid, second.id)
    assert (second_row["total_hosts"], second_row["new_hosts"], second_row["updated_hosts"]) == (45, 5, 40)
    # The siblings still list what they listed.
    assert _hosts(client, pid, scan_ids=str(second.id))["total"] == 45
    assert _hosts(client, pid, scan_ids=str(second.id), first_seen_in_scan="true")["total"] == 5
    # Several scans: a host either of them renamed, once.
    both = _hosts(client, pid, scan_ids=f"{second.id},{third.id}", renamed_in_scan="true")
    assert both["total"] == RENAMED + 2
    # Composes with the other filters.
    assert _hosts(client, pid, scan_ids=str(second.id), renamed_in_scan="true", search="new-10.example")["total"] == 1


def test_a_batchs_renamed_figure_counts_each_host_once_and_is_its_list(client, db_session, test_project):
    pid = test_project.id
    batch = models.ScanBatch(project_id=pid, label="two files")
    db_session.add(batch)
    db_session.flush()
    _, second, third = _project_with_every_kind(db_session, pid, batch_id=batch.id)

    rows = client.get(f"/api/v1/projects/{pid}/scans/batches", params={"ids": str(batch.id)}).json()
    assert len(rows) == 1
    listed = _hosts(client, pid, scan_ids=f"{second.id},{third.id}", renamed_in_scan="true")
    # 23 + 5 rows, three hosts renamed by both files.
    assert rows[0]["renamed_hosts"] == listed["total"] == RENAMED + 2
    assert rows[0]["new_hosts"] == 5


def test_renamed_in_scan_without_a_scan_is_refused_on_every_hosts_route(client, db_session, test_project):
    pid = test_project.id
    _project_with_every_kind(db_session, pid)
    for path in ("/hosts/", "/hosts/ids", "/hosts/filters/data"):
        answer = client.get(f"/api/v1/projects/{pid}{path}", params={"renamed_in_scan": "true"})
        assert answer.status_code == 422, (path, answer.status_code)
        assert "renamed_in_scan" in answer.json()["detail"] and "scan_ids" in answer.json()["detail"]
        # An unusable scan id is the 422 it always was.
        bad = client.get(f"/api/v1/projects/{pid}{path}", params={"scan_ids": "x", "renamed_in_scan": "true"})
        assert bad.status_code == 422, path
    # false / absent is "not asked".
    assert _hosts(client, pid, renamed_in_scan="false")["total"] == 45
    # A scan of another project, or one that does not exist, matches nothing.
    assert _hosts(client, pid, scan_ids="999999", renamed_in_scan="true")["total"] == 0


def test_the_filter_is_a_correlated_exists_over_the_one_condition(db_session):
    """No ``Host.id IN (subquery)`` (the read-path rule), and the figure and
    the filter evaluate the same condition object."""
    from sqlalchemy.dialects import postgresql

    sql = str(P.scan_predicate([7], renamed_only=True).compile(dialect=postgresql.dialect()))
    assert sql.startswith("EXISTS (SELECT")
    assert "host_scan_history.host_id = hosts_v2.id" in sql
    condition = str(P.renamed_in_scan_condition().compile(dialect=postgresql.dialect()))
    assert condition in sql
    assert "hostname_changed IS true" in condition and "host_created IS false" in condition

    scans_source = (Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/scans.py").read_text()
    assert scans_source.count("renamed_in_scan_condition()") == 2      # the scan row and the batch row
    assert "hostname_changed" not in scans_source                       # never a second definition


def test_a_row_that_became_the_creators_is_not_counted_or_listed(client, db_session, test_project):
    """A hand delete of the scan that created a host hands ``host_created`` to
    the next scan's row (``scan_sightings.release_scan``).  That scan then
    reads as the one that added the host — "added" and "renamed" never overlap."""
    pid = test_project.id
    _, second, _ = _project_with_every_kind(db_session, pid)
    host = db_session.query(models.Host).filter_by(project_id=pid, ip_address="10.63.0.2").one()
    row = db_session.query(models.HostScanHistory).filter_by(scan_id=second.id, host_id=host.id).one()
    assert (row.host_created, row.hostname_changed) == (False, True)
    row.host_created = True          # what release_scan does when the first scan is deleted
    db_session.commit()

    scan_row = _scan_row(client, pid, second.id)
    listed = _hosts(client, pid, scan_ids=str(second.id), renamed_in_scan="true")
    assert scan_row["renamed_hosts"] == listed["total"] == RENAMED - 1
    assert scan_row["new_hosts"] == 6
    assert "10.63.0.2" not in [item["ip_address"] for item in listed["items"]]


def test_the_scan_pages_rows_say_which_hosts_the_scan_renamed(client, db_session, test_project):
    pid = test_project.id
    _, second, _ = _project_with_every_kind(db_session, pid)

    page = scan_host_snapshots(db_session, pid, second.id, limit=1000)
    assert sorted(item.ip_address for item in page.items if item.hostname_changed) == _renamed(db_session, second.id)
    assert not [item for item in page.items if item.hostname_changed and item.host_created]
    # The people's route and the agents' read are this function.
    served = client.get(f"/api/v1/projects/{pid}/scans/{second.id}/host-snapshots", params={"limit": 1000}).json()
    assert sum(1 for item in served["items"] if item["hostname_changed"]) == RENAMED


# ------------------------------------------------- DNS CSV forward rows (C)

def test_a_dns_csv_forward_row_notes_a_known_host_and_does_nothing_else(client, db_session, test_project, tmp_path):
    """Owner, 2026-10-11: an A / AAAA row whose value is a KNOWN host's address
    notes that host as addressed by the scan, as dnsx does for the same
    answer.  By this rule alone no name is applied and no host is created."""
    pid = test_project.id
    known = _nmap_hosts(db_session, pid, {
        "10.71.0.1": None, "10.71.0.2": "scanner-name.example", "2001:db8::71": None, "10.71.0.200": None,
    })
    content = (
        "record_type,name,address\n"
        "A,one.corp.example,10.71.0.1\n"
        "A,alias-of-one.corp.example,10.71.0.1\n"
        "A,two.corp.example,10.71.0.2\n"
        "AAAA,six.corp.example,2001:db8::71\n"
        "A,unknown.corp.example,10.71.0.99\n"            # no such host: not created
        "CNAME,www.corp.example,one.corp.example\n"      # names no address
    )

    scan, parser = _dns_csv(db_session, pid, tmp_path, content)

    # The three known hosts are recorded against the scan: not created, not
    # renamed, no state (a DNS answer does not say a host is up).
    assert _flags(db_session, scan.id) == {
        "10.71.0.1": (False, False), "10.71.0.2": (False, False), "2001:db8::71": (False, False),
    }
    states = dict(
        db_session.query(models.Host.ip_address, models.HostScanHistory.state_at_scan)
        .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
        .filter(models.HostScanHistory.scan_id == scan.id)
    )
    assert set(states.values()) == {None}
    # No display name applied — not even to an unnamed host — and no host created.
    assert _names(db_session, pid) == {
        "10.71.0.1": None, "10.71.0.2": "scanner-name.example", "2001:db8::71": None, "10.71.0.200": None,
    }
    # Every row is still stored as a record of the scan.
    assert db_session.query(models.DNSRecord).filter_by(scan_id=scan.id).count() == 6
    assert parser.last_parse_stats["summary"] == (
        "6 DNS records, 3 hosts addressed, 0 created, 0 display names changed"
    )
    # What the pages read.
    row = _scan_row(client, pid, scan.id)
    assert (row["total_hosts"], row["new_hosts"], row["updated_hosts"], row["renamed_hosts"], row["up_hosts"]) == (3, 0, 3, 0, 0)
    assert _hosts(client, pid, scan_ids=str(scan.id))["total"] == 3
    # The scan that brought the hosts is untouched.
    assert len(_flags(db_session, known.id)) == 4
