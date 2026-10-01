"""OpenVAS: CVSS vector, exploit statement, result hostname and the report's
per-host OS / hostname (review 2026-10-01 B12).

No real GVM export is available to the suite; the element names are the GMP
report format's (``<nvt><severities>``, the ``cvss_base_vector=`` tag,
``<result><host>…<hostname>``, report-level ``<host><ip>…<detail>``), and both
vector shapes are accepted.
"""
from __future__ import annotations

from app.db import models
from app.db.models_vulnerability import Vulnerability
from app.parsers.openvas_parser import OpenVASParser, vector_states_exploit

REPORT = """<report id="outer"><report id="r1">
<scan_start>2026-09-20T10:00:00Z</scan_start>
<host>
  <ip>10.20.0.5</ip>
  <start>2026-09-20T10:00:05Z</start><end>2026-09-20T10:09:00Z</end>
  <detail><name>best_os_cpe</name><value>cpe:/o:canonical:ubuntu_linux:20.04</value></detail>
  <detail><name>best_os_txt</name><value>Ubuntu 20.04</value></detail>
  <detail><name>hostname</name><value>web01.corp.example</value></detail>
</host>
<host>
  <ip>10.20.0.7</ip>
  <detail><name>best_os_txt</name><value>Microsoft Windows Server 2019</value></detail>
</host>
<results>
<result id="a">
  <name>Modern NVT</name>
  <host>10.20.0.5<asset asset_id="x"/><hostname>web01.corp.example</hostname></host>
  <port>443/tcp</port>
  <nvt oid="1.3.6.1.4.1.25623.1.0.1">
    <name>Modern NVT</name>
    <severities score="9.8">
      <severity type="cvss_base_v2"><score>7.5</score><value>AV:N/AC:L/Au:N/C:P/I:P/A:P</value></severity>
      <severity type="cvss_base_v3"><score>9.8</score><value>CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H</value></severity>
    </severities>
    <tags>cvss_base_vector=AV:N/AC:L/Au:N/C:P/I:P/A:P|summary=bad</tags>
  </nvt>
  <severity>9.8</severity><threat>High</threat>
</result>
<result id="b">
  <name>Older NVT</name>
  <host>10.20.0.5</host>
  <port>22/tcp</port>
  <nvt oid="1.3.6.1.4.1.25623.1.0.2">
    <tags>cvss_base_vector=AV:N/AC:L/Au:N/C:P/I:N/A:N|summary=old</tags>
  </nvt>
  <severity>5.0</severity><threat>Medium</threat>
</result>
<result id="c">
  <name>Exploit code exists</name>
  <host>10.20.0.5</host>
  <port>80/tcp</port>
  <nvt oid="1.3.6.1.4.1.25623.1.0.3">
    <severities><severity type="cvss_base_v3"><value>CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:F/RL:O/RC:C</value></severity></severities>
  </nvt>
  <severity>9.8</severity><threat>High</threat>
</result>
<result id="d">
  <name>No vector at all</name>
  <host>10.20.0.5</host>
  <port>8080/tcp</port>
  <nvt oid="1.3.6.1.4.1.25623.1.0.4"><tags>summary=nothing</tags></nvt>
  <severity>10.0</severity><threat>High</threat>
</result>
</results>
<scan_end>2026-09-20T10:10:00Z</scan_end>
</report></report>"""


def _import(db, project, tmp_path, text=REPORT, name="gvm.xml"):
    path = tmp_path / name
    path.write_text(text)
    scan = OpenVASParser(db).parse_file(str(path), name, project_id=project.id)
    db.flush()
    return scan


def _vulns(db, project_id):
    rows = (
        db.query(Vulnerability).join(models.Host, models.Host.id == Vulnerability.host_id)
        .filter(models.Host.project_id == project_id).all()
    )
    return {v.title: v for v in rows}


def test_exploit_maturity_metric():
    assert vector_states_exploit("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:F/RL:O/RC:C")
    assert vector_states_exploit("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:P")
    assert vector_states_exploit("AV:N/AC:L/Au:N/C:P/I:P/A:P/E:POC/RL:OF/RC:C")
    assert vector_states_exploit("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A")
    assert not vector_states_exploit("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert not vector_states_exploit("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:U")
    assert not vector_states_exploit("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:X")
    assert not vector_states_exploit("AV:N/AC:L/Au:N/C:P/I:P/A:P/E:ND")
    assert not vector_states_exploit(None)


def test_the_vector_is_stored_from_either_shape(db_session, test_project, tmp_path):
    _import(db_session, test_project, tmp_path)
    vulns = _vulns(db_session, test_project.id)
    # The v3 entry of <severities> wins over the v2 entry and the tag.
    assert vulns["Modern NVT"].cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert vulns["Older NVT"].cvss_vector == "AV:N/AC:L/Au:N/C:P/I:N/A:N"
    assert vulns["No vector at all"].cvss_vector is None


def test_exploitable_only_when_the_report_says_so(db_session, test_project, tmp_path):
    _import(db_session, test_project, tmp_path)
    vulns = _vulns(db_session, test_project.id)
    assert vulns["Exploit code exists"].exploitable is True
    # A 9.8 and a 10.0 with no exploit statement: severity is not evidence.
    assert vulns["Modern NVT"].exploitable is False
    assert vulns["No vector at all"].exploitable is False


def test_a_reimport_without_the_vector_keeps_it(db_session, test_project, tmp_path):
    _import(db_session, test_project, tmp_path)
    stripped = REPORT.replace(
        '<severity type="cvss_base_v3"><value>CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:F/RL:O/RC:C</value></severity>', ""
    )
    _import(db_session, test_project, tmp_path, stripped, "gvm2.xml")
    vuln = _vulns(db_session, test_project.id)["Exploit code exists"]
    assert vuln.cvss_vector.endswith("E:F/RL:O/RC:C")
    assert vuln.exploitable is True


def test_report_hosts_give_the_os_and_the_name(db_session, test_project, tmp_path):
    _import(db_session, test_project, tmp_path)
    hosts = {h.ip_address: h for h in db_session.query(models.Host).filter_by(project_id=test_project.id)}
    assert hosts["10.20.0.5"].os_name == "Ubuntu 20.04"
    assert hosts["10.20.0.5"].hostname == "web01.corp.example"
    # A host the report lists with no result still exists, with its OS.
    assert hosts["10.20.0.7"].os_name == "Microsoft Windows Server 2019"
    assert hosts["10.20.0.7"].hostname is None


def test_the_result_hostname_names_the_host(db_session, test_project, tmp_path):
    text = """<report><results><result><name>n</name>
    <host>10.20.0.9<hostname>db01.corp.example</hostname></host><port>general/tcp</port>
    <nvt oid="1.2.3"/><severity>5.0</severity></result></results></report>"""
    _import(db_session, test_project, tmp_path, text)
    host = db_session.query(models.Host).filter_by(project_id=test_project.id, ip_address="10.20.0.9").one()
    assert host.hostname == "db01.corp.example"
