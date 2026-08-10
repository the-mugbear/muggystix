"""
The RDAP script must consume BlueStick's own host export cleanly.

The documented workflow is: export Host Inventory CSV → run
``scripts/rdap-lookup.py --input hosts.csv`` → upload the result. That only
works if the script picks the address column out of the real export.

It originally matched ``ip_address`` / ``ip`` / ``address`` while the export
header is ``IP Address``, so the CSV path was skipped and a whole-row regex
scan ran instead — which pulled CIDRs out of the Subnet column and stray
addresses out of free-text service banners, sending registry lookups for
blocks nobody asked about.

This pins the script against the header the report generator actually emits,
so a rename on either side is caught here rather than in the field.
"""
import importlib.util
import os

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "rdap-lookup.py",
)


@pytest.fixture(scope="module")
def rdap_script():
    if not os.path.exists(SCRIPT):
        pytest.skip("rdap-lookup.py not present in this checkout")
    spec = importlib.util.spec_from_file_location("rdap_lookup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reads_only_the_address_column_of_the_real_export(rdap_script, tmp_path):
    """The crux: Subnet CIDRs and banner text must not become lookups."""
    from app.services.report_generator import ReportGenerator

    header = ReportGenerator.INVENTORY_CSV_HEADER
    assert header[0] == "IP Address", (
        "the export's first column changed; update _ADDR_COLUMNS in "
        "scripts/rdap-lookup.py to match"
    )

    csv_path = tmp_path / "hosts.csv"
    csv_path.write_text(
        "IP Address,Hostname,State,Site,Subnet,OS Name,Services\n"
        '203.0.113.10,web1.acme.test,up,HQ,203.0.113.0/24,Linux,"banner 8.8.8.8"\n'
        "203.0.113.11,web2.acme.test,up,HQ,203.0.113.0/24,Linux,https\n"
    )

    assert rdap_script._read_input(str(csv_path)) == ["203.0.113.10", "203.0.113.11"]


def test_accepts_the_snake_case_headers_other_tools_emit(rdap_script, tmp_path):
    csv_path = tmp_path / "other.csv"
    csv_path.write_text("ip_address,note\n198.51.100.5,x\n")
    assert rdap_script._read_input(str(csv_path)) == ["198.51.100.5"]


def test_plain_lists_still_work(rdap_script, tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text("# comment\n203.0.113.10\n198.51.100.0/24\n\n")
    assert rdap_script._read_input(str(path)) == ["203.0.113.10", "198.51.100.0/24"]


def test_private_and_reserved_space_is_never_queried(rdap_script):
    """Sending internal addressing to a public registry leaks the client's
    topology and returns nothing useful."""
    import ipaddress

    for addr in ("10.0.0.1", "192.168.1.1", "127.0.0.1", "169.254.1.1"):
        assert rdap_script._usable(ipaddress.ip_address(addr)) is False
    # RFC 5737 documentation ranges (203.0.113.0/24, 198.51.100.0/24) are also
    # skipped — Python classifies them as non-global, and they have no useful
    # registration. Only genuinely routable space is worth a lookup.
    assert rdap_script._usable(ipaddress.ip_address("203.0.113.10")) is False
    assert rdap_script._usable(ipaddress.ip_address("8.8.8.8")) is True
