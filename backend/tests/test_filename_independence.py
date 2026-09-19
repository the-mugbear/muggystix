"""Filename conventions are not a requirement (v2.353.0; staged-import phase D).

Pins, for the three outputs the second /scans review named:

* naabu ``host:port`` text routes by its lines under any filename;
* a hostname list (bare names, or ``name ip``) routes by its lines under any
  filename, and never as naabu (and vice versa);
* an EyeWitness CSV routes by its header under any filename;
* Amass-vs-Subfinder attribution comes from the source tool named at
  import, else the JSON shape, else a filename hint; a plain list with none
  of these is recorded as ``hostname-list``, not guessed;
* the reviewer's stability fixture: upload A, A again, then B with one
  changed line — the detected format is identical every time.
"""
from types import SimpleNamespace

from app.parsers import content_detection as _cd
from app.parsers.amass_parser import attribute_tool
from app.services.ingestion_service import IngestionService

NAABU_TXT = b"192.168.0.10:53\n192.168.0.14:22\n192.168.0.37:443\n"
NAMES_TXT = b"wiki.lab.local\nportal.lab.local\nvpn.lab.local\n"
NAMES_WITH_IP_TXT = b"wiki.lab.local 192.168.0.23\nportal.lab.local 192.168.0.37\n"
EYEWITNESS_CSV = (
    b"URL,Protocol,Port,Title,Server,Content Length,Screenshot Path,Response Code,Page Text\n"
    b"https://192.168.0.37:443/,https,443,Portal,IIS,9001,screens/a.png,200,home\n"
)
DNS_CSV = b"record_type,name,address\nA,www.example.com,10.0.0.1\n"


def _types(name: str, sample: bytes):
    svc = IngestionService()
    job = SimpleNamespace(original_filename=name, options={}, format_override=None)
    return [ft for ft, _cls, _desc in svc._build_parsing_attempts(job, sample)]


def test_naabu_text_routes_by_its_lines_under_any_name():
    assert _cd._looks_like_host_port_lines(NAABU_TXT)
    assert "naabu_output" in _types("results.txt", NAABU_TXT)
    assert "amass_output" not in _types("results.txt", NAABU_TXT)
    # Hostname:port lines are naabu's too; bracketed IPv6 as well.
    assert _cd._looks_like_host_port_lines(b"web.lab.local:443\n[fe80::1]:22\n")
    # One line is not a scan; a masscan list and a gnmap line are not naabu.
    assert not _cd._looks_like_host_port_lines(b"10.0.0.1:22\n")
    assert not _cd._looks_like_host_port_lines(b"Timestamp: 1 Host: 10.0.0.1 () Ports: 22/open\n")


def test_hostname_list_routes_by_its_lines_under_any_name():
    for sample in (NAMES_TXT, NAMES_WITH_IP_TXT):
        assert _cd._looks_like_hostname_lines(sample)
        types = _types("list.txt", sample)
        assert "amass_output" in types
        assert "naabu_output" not in types
    # A bare IP list is not a hostname list; neither is host:port text.
    assert not _cd._looks_like_hostname_lines(b"10.0.0.1\n10.0.0.2\n")
    assert not _cd._looks_like_hostname_lines(NAABU_TXT)
    assert not _cd._looks_like_hostname_lines(b"only.one.line\n")


def test_eyewitness_csv_routes_by_its_header_under_any_name():
    assert _cd.looks_like_eyewitness_csv(EYEWITNESS_CSV)
    assert not _cd.looks_like_eyewitness_csv(DNS_CSV)
    assert "eyewitness_csv" in _types("export.csv", EYEWITNESS_CSV)
    assert "eyewitness_csv" not in _types("export.csv", DNS_CSV)
    assert "dns_csv" in _types("export.csv", DNS_CSV)


def test_attribution_prefers_the_named_tool_then_the_shape_then_the_hint(tmp_path):
    amass = tmp_path / "a.json"
    amass.write_text('[{"name": "x.lab.local", "addresses": ["10.0.0.1"], "tag": "cert"}]')
    subfinder = tmp_path / "s.json"
    subfinder.write_text('{"host": "x.lab.local", "input": "lab.local", "source": "crtsh"}\n')
    text = tmp_path / "list.txt"
    text.write_bytes(NAMES_TXT)

    # The operator's word wins, whatever the file looks like.
    assert attribute_tool("list.txt", "Subfinder 2.6", str(text)) == "subfinder"
    assert attribute_tool("a.json", "amass", str(amass)) == "amass"
    assert attribute_tool("list.txt", "my-recon-script", str(text)) == "my-recon-script"
    # Then the JSON shape.
    assert attribute_tool("export.json", None, str(amass)) == "amass"
    assert attribute_tool("export.json", None, str(subfinder)) == "subfinder"
    # Then a filename hint — the operator named the file, we did not invent it.
    assert attribute_tool("subfinder-run.txt", None, str(text)) == "subfinder"
    # A plain list with no signal at all is a hostname list, not a guess.
    assert attribute_tool("list.txt", None, str(text)) == "hostname-list"


def test_detection_is_stable_across_repeat_and_one_line_change(client, db_session, test_project):
    def stage(data: bytes, name: str, **form):
        r = client.post(
            f"/api/v1/projects/{test_project.id}/upload/",
            files={"file": (name, data, "text/plain")},
            data={"stage": "true", **{k: str(v).lower() for k, v in form.items()}},
        )
        assert r.status_code == 200, r.text
        d = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{r.json()['job_id']}/detection").json()
        return d["primary"], d["candidates"][0]["basis"] if d["candidates"] else None, d["needs_choice"]

    a = stage(NAABU_TXT, "results.txt")
    a_again = stage(NAABU_TXT, "results.txt", allow_duplicate=True)
    b = stage(NAABU_TXT.replace(b":443", b":8443"), "results.txt")
    assert a == a_again == b == ("naabu_output", "structure", False)
