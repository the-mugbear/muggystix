"""Directory discovery against a NAMED target (parser review R05, v2.374.6).

``http://web.example.test/admin`` used to create a Host whose ``ip_address``
was literally ``web.example.test``.  A Host is an address; a name is a
DNSName, bound to a host only through evidence the inventory already holds.
"""
import ipaddress
from datetime import datetime

from app.db import models
from app.parsers.dirbuster_parser import DirBusterParser
from app.services.dns_name_service import record_observation

LINE = "http://{host}/admin (Status: 200) [Size: 123]\nhttp://{host}/login (Status: 301) [Size: 456]\n"


def _parse(db, project, tmp_path, text, name="gobuster_run.txt"):
    path = tmp_path / name
    path.write_text(text)
    parser = DirBusterParser(db)
    scan = parser.parse_file(str(path), name, project_id=project.id)
    hosts = db.query(models.Host).filter(models.Host.project_id == project.id).all()
    return parser, scan, hosts


def _names(db, project):
    return {n.fqdn for n in db.query(models.DNSName).filter(models.DNSName.project_id == project.id)}


def test_unresolved_name_is_a_named_asset_never_a_host(db_session, test_project, tmp_path):
    parser, _scan, hosts = _parse(db_session, test_project, tmp_path, LINE.format(host="web.example.test"))

    assert hosts == []
    assert "web.example.test" in _names(db_session, test_project)
    stats = parser.last_parse_stats
    assert stats["partial"] is True and stats["skipped"] == 2
    assert "web.example.test" in stats["warnings"]


def test_name_with_one_known_address_attaches_to_that_host(db_session, test_project, tmp_path):
    record_observation(
        db_session, project_id=test_project.id, name="app.example.test",
        record_type="A", value="10.7.0.5",
    )
    db_session.flush()

    _parser, _scan, hosts = _parse(db_session, test_project, tmp_path, LINE.format(host="app.example.test"))

    assert [h.ip_address for h in hosts] == ["10.7.0.5"]
    port = db_session.query(models.Port).filter(models.Port.host_id == hosts[0].id).one()
    assert port.port_number == 80
    # v2.390.0 — paths are web_paths rows, not a string in service_extrainfo.
    paths = {p.path for p in db_session.query(models.WebPath).filter(models.WebPath.host_id == hosts[0].id)}
    assert {"/admin", "/login"} <= paths
    # v2.416.0 — the URL is the one requested (the name), not the IP: the
    # IP's default site may be another application.
    urls = {p.url for p in db_session.query(models.WebPath).filter(models.WebPath.host_id == hosts[0].id)}
    assert "http://app.example.test/admin" in urls


def test_query_strings_are_distinct_requests(db_session, test_project, tmp_path):
    """v2.416.0 — query fuzzing: /admin?user=1 and ?user=2 were one row."""
    text = (
        "https://10.7.3.1/admin?user=1 (Status: 200) [Size: 10]\n"
        "https://10.7.3.1/admin?user=2 (Status: 500) [Size: 20]\n"
    )
    _p, _s, hosts = _parse(db_session, test_project, tmp_path, text)
    rows = sorted((p.path, p.url, p.status_code)
                  for p in db_session.query(models.WebPath).filter(models.WebPath.host_id == hosts[0].id))
    assert rows == [
        ("/admin?user=1", "https://10.7.3.1/admin?user=1", 200),
        ("/admin?user=2", "https://10.7.3.1/admin?user=2", 500),
    ]


def test_name_with_several_addresses_is_not_guessed(db_session, test_project, tmp_path):
    # One answer batch ("currently resolves to" is the LATEST batch, so the
    # two addresses must share its timestamp to both be current).
    batch = datetime(2026, 9, 1, 12, 0, 0)
    for ip in ("10.7.1.1", "10.7.1.2"):
        record_observation(
            db_session, project_id=test_project.id, name="lb.example.test",
            record_type="A", value=ip, observed_at=batch,
        )
    db_session.flush()

    parser, _scan, hosts = _parse(db_session, test_project, tmp_path, LINE.format(host="lb.example.test"))
    assert hosts == []
    assert parser.last_parse_stats["partial"] is True


def test_every_host_address_is_an_ip_literal(db_session, test_project, tmp_path):
    text = LINE.format(host="10.7.2.9") + LINE.format(host="named.example.test") + LINE.format(host="[2001:db8::7]")
    _parser, _scan, hosts = _parse(db_session, test_project, tmp_path, text)

    assert {h.ip_address for h in hosts} == {"10.7.2.9", "2001:db8::7"}
    for host in hosts:
        ipaddress.ip_address(host.ip_address)
