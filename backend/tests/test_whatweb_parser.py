"""Contract tests for the whatweb JSON parser (v2.140.0).

Covers:
- Content-based detection (looks_like_whatweb) + disambiguation from httpx
- JSON array + JSONL ingestion into web_interfaces (source="whatweb")
- Host/port/scheme resolution (IP plugin -> ip; URL netloc -> hostname)
- Technology flattening from the plugins map (versions appended;
  HTTP/metadata plugins excluded)
- Idempotent re-ingest via the (scan_id, url, source) unique key

Parser-level logic is exercised against the test DB; the live ingest
path is a thin wrapper covered by the dispatch wiring.
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Pure-function sniffer tests
# ---------------------------------------------------------------------------

class TestLooksLikeWhatweb:
    def test_filename_match(self):
        from app.parsers.whatweb_parser import looks_like_whatweb
        assert looks_like_whatweb(b"", "whatweb-results.json")

    def test_content_match(self):
        from app.parsers.whatweb_parser import looks_like_whatweb
        sample = json.dumps(
            {"target": "http://10.0.0.5/", "http_status": 200,
             "plugins": {"IP": {"string": ["10.0.0.5"]}}}
        ).encode()
        assert looks_like_whatweb(sample, "web.json")

    def test_rejects_httpx_record(self):
        from app.parsers.whatweb_parser import looks_like_whatweb
        sample = json.dumps(
            {"url": "http://10.0.0.5/", "tech": ["nginx"], "status_code": 200}
        ).encode()
        assert not looks_like_whatweb(sample, "web.json")

    def test_not_misdetected_as_httpx(self):
        from app.parsers.httpx_parser import looks_like_httpx
        sample = json.dumps(
            {"target": "http://10.0.0.5/", "plugins": {"IP": {"string": ["10.0.0.5"]}}}
        ).encode()
        assert not looks_like_httpx(sample, "web.json")

    def test_rejects_plain_object(self):
        from app.parsers.whatweb_parser import looks_like_whatweb
        assert not looks_like_whatweb(b'{"foo": "bar"}', "random.json")


# ---------------------------------------------------------------------------
# Parser ingestion tests
# ---------------------------------------------------------------------------

@pytest.fixture
def whatweb_fixture_json(tmp_path):
    """Two whatweb records as a JSON array — one IP target, one hostname
    target whose IP comes from the IP plugin."""
    records = [
        {
            "target": "http://10.99.1.10:80/",
            "http_status": 200,
            "plugins": {
                "IP": {"string": ["10.99.1.10"]},
                "Title": {"string": ["Index of /"]},
                "HTTPServer": {"string": ["Apache/2.4.41 (Ubuntu)"]},
                "Apache": {"version": ["2.4.41"]},
                "PHP": {"version": ["7.4.3"]},
                "Country": {"string": ["RESERVED"], "module": ["ZZ"]},
                "jQuery": {},
            },
        },
        {
            "target": "https://web.example.com/",
            "http_status": 403,
            "plugins": {
                "IP": {"string": ["10.99.1.20"]},
                "Title": {"string": ["Forbidden"]},
                "HTTPServer": {"string": ["nginx"]},
                "nginx": {"version": ["1.18.0"]},
            },
        },
    ]
    p = tmp_path / "whatweb.json"
    p.write_text(json.dumps(records))
    return p


class TestWhatwebIngest:
    def test_writes_rows_per_record(self, db_session, test_project, whatweb_fixture_json):
        from app.db import models
        from app.parsers.whatweb_parser import WhatwebParser

        parser = WhatwebParser(db_session)
        scan = parser.parse_file(
            str(whatweb_fixture_json),
            whatweb_fixture_json.name,
            project_id=test_project.id,
        )
        assert scan.tool_name == "whatweb"
        assert scan.scan_type == "web_fingerprint"
        assert scan.project_id == test_project.id

        rows = (
            db_session.query(models.WebInterface)
            .filter(models.WebInterface.scan_id == scan.id)
            .order_by(models.WebInterface.url)
            .all()
        )
        assert len(rows) == 2

        r1 = [r for r in rows if r.url == "http://10.99.1.10:80/"][0]
        assert r1.source == "whatweb"
        assert r1.ip_address == "10.99.1.10"
        assert r1.port == 80
        assert r1.protocol == "http"
        assert r1.status_code == 200
        assert r1.title == "Index of /"
        assert r1.server_header == "Apache/2.4.41 (Ubuntu)"
        # Tech chips: detected tech with versions; HTTP/metadata plugins excluded.
        assert set(r1.technologies) == {"Apache 2.4.41", "PHP 7.4.3", "jQuery"}
        assert "IP" not in r1.technologies and "Title" not in r1.technologies

        r2 = [r for r in rows if r.url == "https://web.example.com/"][0]
        assert r2.ip_address == "10.99.1.20"
        assert r2.port == 443
        assert r2.protocol == "https"
        assert r2.status_code == 403
        assert r2.server_header == "nginx"
        assert r2.technologies == ["nginx 1.18.0"]

    def test_creates_hosts_and_history(self, db_session, test_project, whatweb_fixture_json):
        from app.db import models
        from app.parsers.whatweb_parser import WhatwebParser

        scan = WhatwebParser(db_session).parse_file(
            str(whatweb_fixture_json), whatweb_fixture_json.name,
            project_id=test_project.id,
        )
        hosts = {
            h.ip_address: h.hostname
            for h in db_session.query(models.Host)
            .filter(models.Host.project_id == test_project.id).all()
        }
        assert "10.99.1.10" in hosts
        # Hostname comes from the URL netloc when it's not an IP literal.
        assert hosts.get("10.99.1.20") == "web.example.com"
        hsh = (
            db_session.query(models.HostScanHistory)
            .filter(models.HostScanHistory.scan_id == scan.id)
            .count()
        )
        assert hsh == 2

    def test_idempotent_reingest(self, db_session, test_project, whatweb_fixture_json):
        """Re-running the same file produces a fresh scan but the same number
        of web_interfaces rows; within one file an identical duplicate target
        folds via the (scan_id, url, source) unique key."""
        from app.db import models
        from app.parsers.whatweb_parser import WhatwebParser

        s1 = WhatwebParser(db_session).parse_file(
            str(whatweb_fixture_json), whatweb_fixture_json.name,
            project_id=test_project.id,
        )
        s2 = WhatwebParser(db_session).parse_file(
            str(whatweb_fixture_json), whatweb_fixture_json.name,
            project_id=test_project.id,
        )
        assert s1.id != s2.id
        for scan in (s1, s2):
            n = (
                db_session.query(models.WebInterface)
                .filter(models.WebInterface.scan_id == scan.id)
                .count()
            )
            assert n == 2
