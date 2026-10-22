"""The one parser registry (hazard H-g, backlog 2026-10-22): old == new.

``format_registry.FORMATS`` became the one list the dispatcher's attempts, the
dispatch map, the sniffer surface and the coverage page are built from.  This
file proves the refactor changed nothing an upload can observe:

* every attempt list ``_build_parsing_attempts`` produced BEFORE the change, for
  a corpus of samples (the native fixtures plus one hand-made sample per
  format) under every filename shape the dispatcher branches on, is recorded
  in ``fixtures/parser_attempts_before_registry.json`` — file type, fallback
  flag, parser class and description, in order — and the code must produce
  exactly that;
* the dispatch map holds the same classes it held before.

Regenerate the JSON only for a deliberate change to detection, never to make
this pass: ``python -m tests.test_parser_registry_old_equals_new`` (inside the
backend container) prints the current table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
NATIVE = HERE / "fixtures" / "native"
BEFORE = HERE / "fixtures" / "parser_attempts_before_registry.json"

NAMES = (
    "upload.xml", "upload.nessus", "upload.gnmap", "upload.json", "upload.jsonl",
    "upload.ndjson", "upload.zip", "upload.csv", "upload.txt", "upload.dat",
    "masscan.json", "eyewitness.json", "report.json", "eyewitness.csv", "report.csv",
    "openvas.xml", "nikto.txt", "nikto.json", "smbmap.txt", "gobuster.txt",
    "ffuf.json", "nxc.txt", "amass.txt", "naabu.txt", "rustscan.txt",
)

HAND_MADE = {
    "nmap_xml": b'<?xml version="1.0"?>\n<nmaprun scanner="nmap" args="nmap -sS 10.9.9.1" version="7.94">'
                b'<host><address addr="10.9.9.1" addrtype="ipv4"/></host></nmaprun>\n',
    "rootless_xml": b'<?xml version="1.0"?>\n<nmaprun args="x"><host/></nmaprun>\n',
    "openvas_xml": b'<?xml version="1.0"?>\n<report id="r1" format_id="a994b278"><report id="r1">'
                   b'<results><result><host>10.0.0.1</host></result></results></report></report>\n',
    "nessus_xml": b'<?xml version="1.0" ?>\n<NessusClientData_v2><Report name="x">'
                  b'<ReportHost name="10.0.0.1"></ReportHost></Report></NessusClientData_v2>\n',
    "other_xml": b'<?xml version="1.0"?>\n<foo><bar/></foo>\n',
    "gnmap": b"# Nmap 7.94 scan initiated Mon Jan 1 00:00:00 2024 as: nmap -oG x 10.0.0.1\n"
             b"Host: 10.0.0.1 ()\tStatus: Up\nHost: 10.0.0.1 ()\tPorts: 22/open/tcp//ssh///\n",
    "httpx": b'{"url":"https://x/","tech":["Nginx"],"webserver":"nginx","status_code":200}\n',
    "dnsx": b'{"host":"example.com","a":["1.2.3.4"],"resolver":["8.8.8.8:53"]}\n',
    "nuclei": b'{"template-id":"vnc-service-detect","info":{"name":"VNC","severity":"info"},'
              b'"type":"tcp","host":"10.0.0.6:5900","port":"5900","ip":"10.0.0.6","matched-at":"10.0.0.6:5900"}\n',
    "whatweb": b'[{"target":"http://x/","http_status":200,"plugins":{"Apache":{"version":["2.4"]}}}]\n',
    "bloodhound": b'{"data":[{"Properties":{"name":"PC.CORP.LOCAL","operatingsystem":"Windows"},'
                  b'"ObjectIdentifier":"S-1-5-21-1"}],"meta":{"type":"computers","count":1,"version":5}}\n',
    "amass_json": b'{"name":"a.example.com","domain":"example.com","addresses":[{"ip":"1.2.3.4"}]}\n',
    "subfinder_json": b'{"host":"a.example.com","name":"a.example.com","input":"example.com","source":"crtsh"}\n',
    "hostnames": b"a.example.com\nb.example.com\nc.example.com\n",
    "naabu_json": b'{"host":"x.example.com","ip":"1.2.3.4","port":22,"protocol":"tcp"}\n',
    "host_ports": b"1.2.3.4:22\n1.2.3.5:80\n1.2.3.6:443\n",
    "rustscan": b"Open 10.0.0.1:22\nOpen 10.0.0.1:80\n",
    "smbmap_json": b'[{"host":"10.0.0.1","shares":[{"name":"C$","permissions":"NO ACCESS"}]}]\n',
    "ffuf_json": b'{"results":[{"url":"http://x/admin","status":200,"length":10}]}\n',
    "ferox_json": b'[{"url":"http://x/admin","status_code":200}]\n',
    "gobuster_txt": b"/admin                (Status: 200) [Size: 10]\n/login (Status: 302) [Size: 0]\n",
    "dirsearch_csv": b"URL,Status,Size\nhttp://x/admin,200,10\n",
    "dns_csv": b"hostname,type,value\na.example.com,A,1.2.3.4\n",
    "masscan_json": b'[{"ip":"10.0.0.1","timestamp":"1700000000","ports":[{"port":22,"proto":"tcp","status":"open"}]}]\n',
    "eyewitness_json": b'[{"url":"http://x/","page_title":"Home","screenshot_path":"screens/x.png"}]\n',
    "nikto_flat": b'[{"ip":"10.0.0.1","port":"80","id":"nikto-000001","msg":"Header missing","severity":"low"}]\n',
    "netexec_txt": b"SMB         10.0.0.1        445    DC01             [*] Windows Server 2019 (name:DC01) (domain:corp)\n",
    "empty": b"",
    "garbage": b"\x00\x01\x02 not a scan at all\n",
}


def corpus():
    """``{sample name: bytes}`` — hand-made samples and every native fixture
    (its first 64 KB, the dispatcher's sample size)."""
    samples = dict(HAND_MADE)
    for path in sorted(NATIVE.iterdir()):
        samples[f"native:{path.name}"] = path.read_bytes()[: 64 * 1024]
    rdap = HERE / "fixtures" / "rdap-live.ndjson"
    samples["rdap-live"] = rdap.read_bytes()[: 64 * 1024]
    return samples


def current_table():
    """``{"parsers": {file_type: [class, description]}, "attempts":
    {"<sample>|<filename>": "nmap_xml masscan_xml*"}}`` — every pair that
    yields at least one attempt, in order, ``*`` marking a fallback."""
    from app.services.ingestion_service import IngestionService

    svc = IngestionService.__new__(IngestionService)  # no storage probe
    parsers, attempts = {}, {}
    for sample_name, sample in corpus().items():
        own = [sample_name.split(":", 1)[1]] if sample_name.startswith("native:") else []
        for filename in (*NAMES, *own):
            job = SimpleNamespace(original_filename=filename, options={}, format_override=None)
            found = list(svc._build_parsing_attempts(job, sample))
            for file_type, cls, description in found:
                pair = [cls.__name__, description]
                assert parsers.setdefault(file_type, pair) == pair, f"{file_type} has two parsers"
            if found:
                attempts[f"{sample_name}|{filename}"] = " ".join(
                    a[0] + ("*" if getattr(a, "fallback", False) else "") for a in found)
    return {"parsers": dict(sorted(parsers.items())), "attempts": dict(sorted(attempts.items()))}


def test_every_attempt_list_is_what_it_was_before_the_registry():
    before = json.loads(BEFORE.read_text())
    now = current_table()
    assert now["parsers"] == before["parsers"]
    old, new = before["attempts"], now["attempts"]
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    assert not changed, "detection changed for: " + ", ".join(
        f"{k}: {old.get(k)!r} -> {new.get(k)!r}" for k in changed[:20]) + (
        f" (+{len(changed) - 20} more)" if len(changed) > 20 else "")


def test_the_corpus_reached_every_format():
    """The old == new proof means something only if every format was in it:
    the recording reached all 34 formats the registry held when it was made.
    (A format added later needs no recording; one removed must leave it.)"""
    from app.services.format_registry import FORMATS

    recorded = set(json.loads(BEFORE.read_text())["parsers"])
    assert len(recorded) == 34
    assert recorded <= set(FORMATS)


DISPATCH_BEFORE = sorted([
    "AmassParser", "BloodHoundParser", "DNSParser", "DirBusterParser", "DnsxParser",
    "EyewitnessParser", "GnmapParser", "HttpxParser", "MasscanParser", "NaabuParser",
    "NetexecParser", "NiktoParser", "NmapXMLParser", "NucleiParser", "OpenVASParser",
    "RdapParser", "RustScanParser", "SMBMapParser", "TestsslParser", "WhatwebParser",
])


def test_the_dispatch_map_holds_the_same_classes():
    from app.services.ingestion_service import build_parser_dispatch_map

    dispatch = build_parser_dispatch_map()
    assert all(k is v for k, v in dispatch.items())
    assert sorted(cls.__name__ for cls in dispatch) == DISPATCH_BEFORE


if __name__ == "__main__":  # pragma: no cover — regenerates the recorded table
    json.dump(current_table(), sys.stdout, indent=1)
    sys.stdout.write("\n")
