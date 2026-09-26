"""DNS inventory CSV: values are validated by record type (v2.416.0).

Every value used to be checked as an IP, so a zone export's CNAME / MX / NS /
TXT / SRV rows were dropped (logged only), and so was compressed IPv6.
"""
from app.db import models
from app.parsers.dns_parser import DNSParser


def test_non_address_records_and_compressed_ipv6_are_kept(db_session, test_project, tmp_path):
    p = tmp_path / "zone.csv"
    p.write_text(
        "record_type,name,address\n"
        "A,www.example.com,10.9.0.1\n"
        "AAAA,www.example.com,2001:db8::1\n"
        "CNAME,app.example.com,www.example.com\n"
        "MX,example.com,10 mail.example.com\n"
        "TXT,example.com,v=spf1 -all\n"
        "A,bad.example.com,2001:db8::2\n"
        "BOGUS,x.example.com,10.9.0.2\n"
    )
    parser = DNSParser(db_session)
    scan = parser.parse_file(str(p), p.name, project_id=test_project.id)

    kinds = sorted(
        (r.record_type, r.value)
        for r in db_session.query(models.DNSRecord).filter(models.DNSRecord.scan_id == scan.id)
    )
    assert kinds == [
        ("A", "10.9.0.1"), ("AAAA", "2001:db8::1"), ("CNAME", "www.example.com"),
        ("MX", "10 mail.example.com"), ("TXT", "v=spf1 -all"),
    ]
    # The two rejected rows are on the import, not only in the log.
    stats = parser.last_parse_stats
    assert stats["skipped"] == 2 and stats["partial"] is True
    assert "A value is not IPv4" in stats["warnings"]
    assert "not a DNS record type" in stats["warnings"]
