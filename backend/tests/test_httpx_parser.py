"""Contract tests for the httpx JSONL parser and web_interfaces endpoints (v2.12.0).

Covers:
- Content-based detection (looks_like_httpx)
- JSONL ingestion — one record per line
- JSON array ingestion
- Host/port resolution
- Technology flattening (list + dict shapes)
- Idempotent re-ingest via (scan_id, url, source) unique key
- /hosts/{id}/web-interfaces endpoint returns rows linked to the host
- /hosts/web-interfaces/{id}/screenshot path-traversal guard

Parser-level logic is exercised against SQLite in-memory; does NOT
round-trip through HTTP upload for ingest correctness (that's covered
by the parser unit tests; the live ingest path is a thin wrapper).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Pure-function sniffer tests
# ---------------------------------------------------------------------------

class TestLooksLikeHttpx:
    def test_filename_match(self):
        from app.parsers.httpx_parser import looks_like_httpx
        assert looks_like_httpx("", "httpx-results.jsonl")

    def test_content_match_with_tech(self):
        from app.parsers.httpx_parser import looks_like_httpx
        sample = '{"timestamp":"...","url":"https://10.0.1.5/","tech":["Nginx"],"status_code":200}\n'
        assert looks_like_httpx(sample, "web.jsonl")

    def test_content_match_with_webserver_and_status(self):
        from app.parsers.httpx_parser import looks_like_httpx
        sample = '{"url":"https://10.0.1.5/","webserver":"nginx","status_code":200}'
        assert looks_like_httpx(sample, "web.json")

    def test_rejects_bare_list(self):
        from app.parsers.httpx_parser import looks_like_httpx
        assert not looks_like_httpx('["not", "httpx"]', "random.json")

    def test_rejects_plain_object_without_signature(self):
        from app.parsers.httpx_parser import looks_like_httpx
        assert not looks_like_httpx('{"foo": "bar"}', "random.json")

    def test_rejects_non_json(self):
        from app.parsers.httpx_parser import looks_like_httpx
        assert not looks_like_httpx("plain text output", "x.txt")


# ---------------------------------------------------------------------------
# Parser ingestion tests
# ---------------------------------------------------------------------------

@pytest.fixture
def httpx_fixture_jsonl(tmp_path):
    """Two httpx probe records as JSONL."""
    records = [
        {
            "timestamp": "2026-04-16T04:00:00Z",
            "url": "https://10.99.1.10/",
            "input": "10.99.1.10:443",
            "host": "10.99.1.10",
            "scheme": "https",
            "port": "443",
            "status_code": 200,
            "content_length": 612,
            "title": "Welcome to nginx",
            "webserver": "nginx/1.18.0",
            "tech": ["Nginx:1.18.0", "React"],
            "favicon": "a1b2c3d4e5",
            "tls": {"issuer_dn": "CN=acme", "not_after": "2027-01-01"},
        },
        {
            "timestamp": "2026-04-16T04:00:01Z",
            "url": "http://10.99.1.20:8080/",
            "input": "10.99.1.20:8080",
            "host": "10.99.1.20",
            "scheme": "http",
            "port": "8080",
            "status_code": 302,
            "title": "Jenkins",
            "webserver": "Jetty",
            "tech": {"Jenkins": "2.289.1", "Jetty": ""},
        },
    ]
    p = tmp_path / "httpx.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


class TestHttpxIngest:
    def test_writes_rows_per_record(
        self, db_session, test_project, httpx_fixture_jsonl
    ):
        """Parsing a 2-record JSONL file should create one
        web_interfaces row per record, linked to hosts created
        on-demand when not previously seen."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        parser = HttpxParser(db_session)
        scan = parser.parse_file(
            str(httpx_fixture_jsonl),
            httpx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        assert scan.tool_name == "httpx"
        assert scan.scan_type == "web_fingerprint"
        assert scan.project_id == test_project.id

        rows = (
            db_session.query(models.WebInterface)
            .filter(models.WebInterface.scan_id == scan.id)
            .order_by(models.WebInterface.url)
            .all()
        )
        assert len(rows) == 2
        # First row — 10.99.1.10, nginx
        r1 = rows[0]
        assert r1.source == "httpx"
        assert r1.url == "http://10.99.1.20:8080/"
        assert r1.port == 8080
        assert r1.protocol == "http"
        assert r1.ip_address == "10.99.1.20"
        # Jenkins dict-shape tech should flatten to "Jenkins 2.289.1" + "Jetty"
        assert any("Jenkins 2.289.1" in t for t in r1.technologies)
        # Row 2 — 10.99.1.10
        r2 = rows[1]
        assert r2.url == "https://10.99.1.10/"
        assert r2.port == 443
        assert "Nginx:1.18.0" in r2.technologies
        assert "React" in r2.technologies
        assert r2.favicon_hash == "a1b2c3d4e5"
        assert r2.tls_info == {"issuer_dn": "CN=acme", "not_after": "2027-01-01"}

    def test_creates_hosts_on_demand(
        self, db_session, test_project, httpx_fixture_jsonl
    ):
        """If an IP isn't already in hosts_v2 for this project, the
        parser creates it (state=up) so web_interfaces.host_id has
        something to link to."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        parser = HttpxParser(db_session)
        parser.parse_file(
            str(httpx_fixture_jsonl),
            httpx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        hosts = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address.in_(["10.99.1.10", "10.99.1.20"]),
            )
            .all()
        )
        assert len(hosts) == 2
        # All hosts should be linked from web_interfaces
        for h in hosts:
            wi = (
                db_session.query(models.WebInterface)
                .filter(models.WebInterface.host_id == h.id)
                .first()
            )
            assert wi is not None

    def test_reingest_updates_in_place(
        self, db_session, test_project, httpx_fixture_jsonl
    ):
        """Same URL + source + scan shouldn't duplicate — the unique
        constraint + upsert logic handles it.  Re-ingest within a
        single scan is possible if the JSONL file has duplicate URLs
        (httpx can emit multiple probes per URL when chains redirect)."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        # Single-record file with the SAME URL twice — simulates a
        # chase-redirect output.
        dup_records = [
            {"url": "https://10.99.1.77/", "host": "10.99.1.77",
             "scheme": "https", "port": "443", "status_code": 301,
             "tech": ["OldServer"], "webserver": "Apache"},
            {"url": "https://10.99.1.77/", "host": "10.99.1.77",
             "scheme": "https", "port": "443", "status_code": 200,
             "tech": ["NewServer"], "webserver": "nginx"},
        ]
        path = httpx_fixture_jsonl.parent / "dup.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in dup_records))

        parser = HttpxParser(db_session)
        scan = parser.parse_file(str(path), path.name, project_id=test_project.id)
        rows = db_session.query(models.WebInterface).filter(
            models.WebInterface.scan_id == scan.id,
            models.WebInterface.url == "https://10.99.1.77/",
        ).all()
        # Still exactly one row — the second record updates the first
        # via the upsert path.
        assert len(rows) == 1
        # Updated values from the second record
        assert rows[0].status_code == 200
        assert rows[0].server_header == "nginx"
        assert "NewServer" in rows[0].technologies

    def test_json_array_input(self, db_session, test_project, tmp_path):
        """httpx piped through ``jq -s`` produces a JSON array; parser
        must accept that shape too."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        records = [
            {"url": "https://10.99.2.1/", "host": "10.99.2.1",
             "scheme": "https", "port": "443", "status_code": 200,
             "tech": ["Bootstrap"], "webserver": "nginx"},
        ]
        path = tmp_path / "httpx-array.json"
        path.write_text(json.dumps(records))

        parser = HttpxParser(db_session)
        scan = parser.parse_file(str(path), path.name, project_id=test_project.id)
        rows = db_session.query(models.WebInterface).filter(
            models.WebInterface.scan_id == scan.id,
        ).all()
        assert len(rows) == 1
        assert rows[0].url == "https://10.99.2.1/"
        assert "Bootstrap" in rows[0].technologies


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestHostWebInterfacesEndpoint:
    def test_lists_interfaces_for_host(
        self, client, db_session, test_project, test_user
    ):
        """GET /hosts/{id}/web-interfaces returns all rows linked to
        the host, ordered by port."""
        from app.db import models
        host = models.Host(
            ip_address="10.99.5.5", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        scan = models.Scan(
            filename="x.jsonl", scan_type="web_fingerprint",
            tool_name="httpx", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add_all([
            models.WebInterface(
                host_id=host.id, scan_id=scan.id, project_id=test_project.id,
                source="httpx", url="https://10.99.5.5:443/", port=443,
                protocol="https", status_code=200, title="Main",
                technologies=["nginx"], ip_address="10.99.5.5",
            ),
            models.WebInterface(
                host_id=host.id, scan_id=scan.id, project_id=test_project.id,
                source="httpx", url="http://10.99.5.5:8080/", port=8080,
                protocol="http", status_code=302, title="Admin",
                technologies=["Tomcat"], ip_address="10.99.5.5",
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/{host.id}/web-interfaces"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 2
        # Sorted by port ascending
        assert body[0]["port"] == 443
        assert body[1]["port"] == 8080
        assert body[0]["technologies"] == ["nginx"]
        assert body[0]["has_screenshot"] is False
        assert body[0]["source"] == "httpx"

    def test_unknown_host_returns_404(self, client, test_project):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/99999/web-interfaces"
        )
        assert resp.status_code == 404

    def test_host_without_interfaces_returns_empty(
        self, client, db_session, test_project
    ):
        from app.db import models
        host = models.Host(
            ip_address="10.99.5.99", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/{host.id}/web-interfaces"
        )
        assert resp.status_code == 200
        assert resp.json() == []


class TestHostFilterByWebInterface:
    """v2.12.1 — has_web_interface + tech query params on GET /hosts/."""

    @pytest.fixture
    def hosts_with_mixed_web(self, db_session, test_project):
        """Three hosts: one with an nginx httpx row, one with a jenkins
        httpx row, one with no web_interfaces rows."""
        from app.db import models
        nginx_host = models.Host(
            ip_address="10.99.10.1", state="up", project_id=test_project.id,
        )
        jenkins_host = models.Host(
            ip_address="10.99.10.2", state="up", project_id=test_project.id,
        )
        bare_host = models.Host(
            ip_address="10.99.10.3", state="up", project_id=test_project.id,
        )
        db_session.add_all([nginx_host, jenkins_host, bare_host])
        db_session.flush()
        scan = models.Scan(
            filename="web.jsonl", scan_type="web_fingerprint",
            tool_name="httpx", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add_all([
            models.WebInterface(
                host_id=nginx_host.id, scan_id=scan.id,
                project_id=test_project.id, source="httpx",
                url="https://10.99.10.1/", port=443, protocol="https",
                status_code=200, technologies=["Nginx 1.18.0", "React"],
                ip_address="10.99.10.1",
            ),
            models.WebInterface(
                host_id=jenkins_host.id, scan_id=scan.id,
                project_id=test_project.id, source="httpx",
                url="http://10.99.10.2:8080/", port=8080, protocol="http",
                status_code=200, technologies=["Jenkins 2.289.1", "Jetty"],
                ip_address="10.99.10.2",
            ),
        ])
        db_session.commit()
        return {
            "nginx": nginx_host, "jenkins": jenkins_host, "bare": bare_host,
        }

    def test_has_web_interface_true_filters_to_web_hosts(
        self, client, test_project, hosts_with_mixed_web
    ):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/?has_web_interface=true"
        )
        assert resp.status_code == 200, resp.text
        ips = {h["ip_address"] for h in resp.json()["items"]}
        assert "10.99.10.1" in ips
        assert "10.99.10.2" in ips
        assert "10.99.10.3" not in ips

    def test_has_web_interface_false_filters_to_bare_hosts(
        self, client, test_project, hosts_with_mixed_web
    ):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/?has_web_interface=false"
        )
        assert resp.status_code == 200
        ips = {h["ip_address"] for h in resp.json()["items"]}
        assert "10.99.10.3" in ips
        assert "10.99.10.1" not in ips

    def test_tech_filter_substring_match(
        self, client, test_project, hosts_with_mixed_web
    ):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/?tech=nginx"
        )
        assert resp.status_code == 200
        ips = {h["ip_address"] for h in resp.json()["items"]}
        assert ips == {"10.99.10.1"}  # only the nginx host

    def test_tech_filter_or_semantics(
        self, client, test_project, hosts_with_mixed_web
    ):
        """tech=nginx,jenkins should match both hosts — OR semantics."""
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/?tech=nginx,jenkins"
        )
        assert resp.status_code == 200
        ips = {h["ip_address"] for h in resp.json()["items"]}
        assert "10.99.10.1" in ips
        assert "10.99.10.2" in ips
        assert "10.99.10.3" not in ips

    def test_tech_filter_case_insensitive(
        self, client, test_project, hosts_with_mixed_web
    ):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/?tech=NGINX"
        )
        assert resp.status_code == 200
        ips = {h["ip_address"] for h in resp.json()["items"]}
        assert ips == {"10.99.10.1"}


class TestFilterDataTechnologies:
    """v2.12.1 — GET /hosts/filters/data now includes a technologies list."""

    def test_returns_technologies_with_host_counts(
        self, client, db_session, test_project
    ):
        from app.db import models
        host = models.Host(
            ip_address="10.99.11.5", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        scan = models.Scan(
            filename="web.jsonl", scan_type="web_fingerprint",
            tool_name="httpx", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add(models.WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=test_project.id,
            source="httpx", url="https://10.99.11.5/",
            technologies=["Nginx 1.20", "Bootstrap"],
            ip_address="10.99.11.5",
        ))
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/filters/data"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "technologies" in body
        names = {t["name"] for t in body["technologies"]}
        assert "Nginx 1.20" in names
        assert "Bootstrap" in names

    def test_empty_technologies_when_no_web_interfaces(
        self, client, test_project
    ):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/filters/data"
        )
        assert resp.status_code == 200
        assert resp.json()["technologies"] == []


class TestScopeCoverageTopTechnologies:
    """v2.12.1 — ScopeCoverageSummary.top_technologies rollup."""

    def test_rollup_counts_distinct_hosts(
        self, client, db_session, test_project
    ):
        """A technology seen on two distinct hosts counts as host_count=2;
        seen on the same host through two interfaces still counts as 1."""
        from app.db import models
        host1 = models.Host(
            ip_address="10.99.12.1", state="up", project_id=test_project.id,
        )
        host2 = models.Host(
            ip_address="10.99.12.2", state="up", project_id=test_project.id,
        )
        db_session.add_all([host1, host2])
        db_session.flush()
        scan = models.Scan(
            filename="web.jsonl", scan_type="web_fingerprint",
            tool_name="httpx", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add_all([
            # host1 — two interfaces, both nginx (dedupe to count=1)
            models.WebInterface(
                host_id=host1.id, scan_id=scan.id, project_id=test_project.id,
                source="httpx", url="https://10.99.12.1/", technologies=["nginx"],
                ip_address="10.99.12.1",
            ),
            models.WebInterface(
                host_id=host1.id, scan_id=scan.id, project_id=test_project.id,
                source="httpx", url="https://10.99.12.1:8443/", technologies=["nginx"],
                ip_address="10.99.12.1",
            ),
            # host2 — nginx again (count=2 total), and jenkins (count=1)
            models.WebInterface(
                host_id=host2.id, scan_id=scan.id, project_id=test_project.id,
                source="httpx", url="http://10.99.12.2/", technologies=["nginx", "jenkins"],
                ip_address="10.99.12.2",
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/scopes/coverage"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "top_technologies" in body
        by_name = {t["name"]: t["host_count"] for t in body["top_technologies"]}
        assert by_name.get("nginx") == 2   # host1 + host2, deduped
        assert by_name.get("jenkins") == 1  # host2 only

    def test_empty_rollup_when_no_web_interfaces(self, client, test_project):
        resp = client.get(
            f"/api/v1/projects/{test_project.id}/scopes/coverage"
        )
        assert resp.status_code == 200
        assert resp.json()["top_technologies"] == []


class TestParserRegistration:
    """v2.12.2 — every parser class returned by ``_build_parsing_attempts``
    must be registered in the dispatcher's ``parser_map``, or the
    dispatcher silently falls through to the next attempt and the file
    gets misparsed.

    Phase-1 bug: HttpxParser was wired into _build_parsing_attempts but
    NOT into parser_map.  Every httpx upload silently fell through to
    NaabuParser (which had a too-broad sniffer) and was misparsed.
    This test would have caught it on the first run.
    """

    def test_httpx_parser_registered_in_dispatcher(self):
        """Direct import of the parser_map key check — sanity that
        HttpxParser is one of the recognized classes."""
        from app.parsers.httpx_parser import HttpxParser
        from app.services.ingestion_service import ingestion_service

        # Build a minimal parser_map by exercising the dispatch code
        # path with a known-bad parser_class to extract the live map.
        # Easier: construct the map the same way _execute_parser does
        # and assert HttpxParser is in it.  This test is fragile to the
        # internal name, which is by design — if the registration moves,
        # this should fail until the new location is updated.
        from app.parsers.nmap_parser import NmapXMLParser
        from app.parsers.eyewitness_parser import EyewitnessParser
        from app.parsers.masscan_parser import MasscanParser
        from app.parsers.naabu_parser import NaabuParser

        # The actual parser_map is built inside _execute_parser per call.
        # The cleanest assertion is "the parser is importable and has the
        # standard parse_file interface" — and that the dispatcher's
        # explicit registration block in _execute_parser includes it.
        # Grep-based check on the source file:
        import inspect
        source = inspect.getsource(ingestion_service._execute_parser)
        assert "HttpxParser" in source, (
            "HttpxParser must be registered in _execute_parser parser_map. "
            "If you add a new parser to _build_parsing_attempts, you MUST "
            "also add it to parser_map or the dispatcher will silently "
            "fall through and misparse files."
        )


class TestNaabuSnifferDoesNotMatchHttpx:
    """v2.12.2 — _looks_like_naabu was too broad and false-positived on
    httpx JSONL (which contains "port" and URL colons).  This test pins
    the tightened signature so a regression doesn't re-introduce the
    misparse."""

    def test_naabu_sniffer_rejects_httpx_jsonl(self):
        # v2.27.0 — content-detection helpers extracted to
        # app.parsers.content_detection as module-level functions.
        from app.parsers.content_detection import looks_like_naabu
        httpx_record = (
            b'{"timestamp":"...","url":"https://10.0.1.5/","host":"10.0.1.5",'
            b'"scheme":"https","port":"443","status_code":200,"tech":["Nginx"],'
            b'"webserver":"nginx/1.18.0"}'
        )
        assert not looks_like_naabu(httpx_record, "web.jsonl")

    def test_naabu_sniffer_still_matches_real_naabu(self):
        from app.parsers.content_detection import looks_like_naabu
        naabu_record = b'{"ip":"10.0.1.5","port":443,"host":"server.example"}'
        assert looks_like_naabu(naabu_record, "scan.json")

    def test_naabu_sniffer_filename_match(self):
        from app.parsers.content_detection import looks_like_naabu
        # Even with weird content, the filename naabu-* still routes there
        assert looks_like_naabu(b"{}", "naabu-scan.json")


class TestRecordHostsInScanHelper:
    """v2.12.2 — extracted helper for web-fingerprint parsers to write
    HostScanHistory rows so /agent/recon/summary's host counts work."""

    def test_writes_history_row_for_each_distinct_host(
        self, db_session, test_project
    ):
        from app.db import models
        from app.parsers.parser_utils import record_hosts_in_scan

        host1 = models.Host(ip_address="10.0.0.1", state="up", project_id=test_project.id)
        host2 = models.Host(ip_address="10.0.0.2", state="up", project_id=test_project.id)
        db_session.add_all([host1, host2])
        db_session.flush()
        scan = models.Scan(filename="x.jsonl", scan_type="web_fingerprint",
                           tool_name="httpx", project_id=test_project.id)
        db_session.add(scan)
        db_session.flush()

        record_hosts_in_scan(db_session, scan.id, {host1.id, host2.id})
        db_session.commit()

        rows = db_session.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan.id,
        ).all()
        assert len(rows) == 2
        assert {r.host_id for r in rows} == {host1.id, host2.id}

    def test_idempotent_on_repeat_calls(
        self, db_session, test_project
    ):
        from app.db import models
        from app.parsers.parser_utils import record_hosts_in_scan

        host = models.Host(ip_address="10.0.0.3", state="up", project_id=test_project.id)
        db_session.add(host)
        db_session.flush()
        scan = models.Scan(filename="x.jsonl", scan_type="web_fingerprint",
                           tool_name="httpx", project_id=test_project.id)
        db_session.add(scan)
        db_session.flush()

        record_hosts_in_scan(db_session, scan.id, {host.id})
        db_session.commit()
        record_hosts_in_scan(db_session, scan.id, {host.id})
        db_session.commit()

        rows = db_session.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan.id,
        ).all()
        assert len(rows) == 1  # second call is no-op


class TestHttpxWritesHostScanHistory:
    """v2.12.2 — httpx parser must write HostScanHistory rows so the
    recon summary's hosts_discovered + per-host breakdown work for
    web-only ingests."""

    def test_httpx_ingest_creates_host_scan_history(
        self, db_session, test_project, tmp_path
    ):
        import json as _json
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        record = {
            "url": "https://10.0.42.1/",
            "host": "10.0.42.1",
            "scheme": "https",
            "port": "443",
            "status_code": 200,
            "tech": ["Nginx"],
            "webserver": "nginx/1.20",
        }
        path = tmp_path / "httpx.jsonl"
        path.write_text(_json.dumps(record))

        parser = HttpxParser(db_session)
        scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

        history = db_session.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan.id,
        ).all()
        assert len(history) == 1
        # The host_id should match the one created on-demand by the parser
        host = db_session.query(models.Host).filter(
            models.Host.ip_address == "10.0.42.1",
            models.Host.project_id == test_project.id,
        ).first()
        assert history[0].host_id == host.id


class TestHttpxIpResolution:
    """v2.13.1 — recon session #6 produced httpx output containing

    records whose ``input``/``host`` fields were TLS-SAN hostnames like
    ``localhost`` and ``pi.hole``.  Pre-fix, those strings were stored
    verbatim as ``hosts_v2.ip_address``, polluting the host table and
    surfacing as out-of-scope entries in ``/agent/recon/summary``.  The
    fix in ``_resolve_ip_and_hostname`` prefers ``host_ip`` (the
    resolved IP), validates candidates with ``ipaddress.ip_address``,
    and routes any non-IP string to the ``hostname`` column instead.
    """

    def _write_jsonl(self, tmp_path, records):
        import json
        f = tmp_path / "httpx.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return f

    def test_hostname_in_host_field_does_not_create_string_ip_host(
        self, db_session, test_project, tmp_path
    ):
        """The TLS-SAN expansion case: httpx emits a record with
        ``host="localhost"``, ``host_ip="127.0.0.1"``, ``input="localhost"``.
        Pre-fix this wrote a Host row with ``ip_address="localhost"``.
        Post-fix the IP comes from ``host_ip`` and the hostname lands
        in the ``hostname`` column."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        f = self._write_jsonl(tmp_path, [
            {
                "url": "https://localhost",
                "input": "localhost",
                "host": "localhost",
                "host_ip": "127.0.0.1",
                "scheme": "https",
                "port": "443",
                "status_code": 200,
                "title": "NetworkMapper",
                "tech": ["Nginx"],
            },
        ])
        parser = HttpxParser(db_session)
        parser.parse_file(str(f), f.name, project_id=test_project.id)

        # No host row should exist with ip_address = "localhost" or any
        # non-IP string.
        bogus = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address.in_(["localhost", "pi.hole", ""]),
            )
            .all()
        )
        assert bogus == [], f"string-typed ip_address leaked: {[h.ip_address for h in bogus]}"

        # The real host row must have the resolved IP + the hostname.
        real = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address == "127.0.0.1",
            )
            .first()
        )
        assert real is not None
        assert real.hostname == "localhost"

    def test_record_without_any_valid_ip_is_skipped(
        self, db_session, test_project, tmp_path
    ):
        """If neither ``host_ip`` nor ``host``/``input``/URL parses as
        an IP literal, the record is dropped rather than synthesized."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        f = self._write_jsonl(tmp_path, [
            {
                "url": "https://pi.hole/admin/",
                "input": "pi.hole",
                "host": "pi.hole",
                # No host_ip
                "scheme": "https",
                "status_code": 200,
                "tech": ["Pi-hole"],
            },
        ])
        parser = HttpxParser(db_session)
        parser.parse_file(str(f), f.name, project_id=test_project.id)

        # No Host row should have been created at all.
        hosts = (
            db_session.query(models.Host)
            .filter(models.Host.project_id == test_project.id)
            .all()
        )
        for h in hosts:
            assert h.ip_address != "pi.hole"
            # Valid IP literal check
            import ipaddress
            try:
                ipaddress.ip_address(h.ip_address)
            except ValueError:
                assert False, f"non-IP stored as ip_address: {h.ip_address!r}"

    def test_hostname_enriches_existing_host(
        self, db_session, test_project, tmp_path
    ):
        """If the IP already exists in hosts_v2 with no hostname and the
        httpx record carries one (from the TLS cert or input), the
        parser should backfill it."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        existing = models.Host(
            ip_address="10.20.30.40",
            state="up",
            project_id=test_project.id,
            hostname=None,
        )
        db_session.add(existing)
        db_session.commit()

        f = self._write_jsonl(tmp_path, [
            {
                "url": "https://web.example.com/",
                "input": "web.example.com",
                "host": "web.example.com",
                "host_ip": "10.20.30.40",
                "scheme": "https",
                "port": "443",
                "status_code": 200,
                "tech": ["Nginx"],
            },
        ])
        parser = HttpxParser(db_session)
        parser.parse_file(str(f), f.name, project_id=test_project.id)

        db_session.refresh(existing)
        assert existing.hostname == "web.example.com"

    def test_ip_port_input_is_not_stored_as_hostname(
        self, db_session, test_project, tmp_path
    ):
        """v2.13.2 — httpx's ``input`` field often carries ``ip:port``
        (the original target locator).  Pre-fix this was being stored
        verbatim as the hostname, producing rows like
        ``hostname="192.168.0.1:80"``.  The fix rejects any ``X:port``
        shape where X parses as an IP literal."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        f = self._write_jsonl(tmp_path, [
            {
                "url": "http://192.168.0.1/",
                "input": "192.168.0.1:80",
                "host": "192.168.0.1",
                "host_ip": "192.168.0.1",
                "scheme": "http",
                "port": "80",
                "status_code": 200,
            },
        ])
        parser = HttpxParser(db_session)
        parser.parse_file(str(f), f.name, project_id=test_project.id)

        h = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address == "192.168.0.1",
            )
            .first()
        )
        assert h is not None
        assert h.hostname is None, (
            f"ip:port input should NOT be stored as hostname; got {h.hostname!r}"
        )

    def test_ipv6_literal_is_accepted(
        self, db_session, test_project, tmp_path
    ):
        """IPv6 literals in host_ip / host should pass validation, with
        the square-bracket form from URLs stripped."""
        from app.db import models
        from app.parsers.httpx_parser import HttpxParser

        f = self._write_jsonl(tmp_path, [
            {
                "url": "https://[2001:db8::1]/",
                "input": "2001:db8::1",
                "host": "2001:db8::1",
                "host_ip": "2001:db8::1",
                "scheme": "https",
                "port": "443",
                "status_code": 200,
                "tech": ["nginx"],
            },
        ])
        parser = HttpxParser(db_session)
        parser.parse_file(str(f), f.name, project_id=test_project.id)

        h = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address == "2001:db8::1",
            )
            .first()
        )
        assert h is not None


class TestScreenshotEndpoint:
    def test_returns_404_when_no_screenshot_path(
        self, client, db_session, test_project
    ):
        from app.db import models
        host = models.Host(
            ip_address="10.99.6.1", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        scan = models.Scan(
            filename="x.json", scan_type="web_fingerprint",
            tool_name="httpx", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        wi = models.WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=test_project.id,
            source="httpx", url="https://10.99.6.1/",
            # no screenshot_path — httpx doesn't produce screenshots
        )
        db_session.add(wi)
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/web-interfaces/{wi.id}/screenshot"
        )
        assert resp.status_code == 404
        assert "No screenshot" in resp.json()["detail"]

    def test_rejects_path_traversal_in_stored_value(
        self, client, db_session, test_project
    ):
        """A web_interfaces row with a screenshot_path that escapes
        the storage root via ``../`` must 404, not stream whatever
        file the path resolves to."""
        from app.db import models
        host = models.Host(
            ip_address="10.99.6.2", state="up", project_id=test_project.id,
        )
        db_session.add(host)
        db_session.flush()
        scan = models.Scan(
            filename="x.json", scan_type="web_screenshot",
            tool_name="eyewitness", project_id=test_project.id,
        )
        db_session.add(scan)
        db_session.flush()
        wi = models.WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=test_project.id,
            source="eyewitness", url="https://10.99.6.2/",
            screenshot_path="../../../../etc/passwd",  # traversal attempt
        )
        db_session.add(wi)
        db_session.commit()

        resp = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/web-interfaces/{wi.id}/screenshot"
        )
        # Either "invalid" (traversal caught) or "missing on disk"
        # (resolved but file not present) — both are safe.
        assert resp.status_code == 404


def test_httpx_caches_host_lookups_across_records(db_session, test_project, tmp_path):
    """All records sharing one host trigger a single host-by-IP lookup, not one
    per record — the per-file resolve_host_cached memoization (v2.195.0)."""
    from sqlalchemy import event
    from app.db import models
    from app.parsers.httpx_parser import HttpxParser

    records = [
        {
            "url": f"https://10.77.0.5:{p}/", "host": "10.77.0.5", "host_ip": "10.77.0.5",
            "scheme": "https", "port": str(p), "status_code": 200,
        }
        for p in (443, 8443, 9000, 10000)
    ]
    path = tmp_path / "httpx_same_host.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    host_ip_selects: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        s = statement.lower()
        if s.startswith("select") and "hosts_v2" in s and "ip_address =" in s:
            host_ip_selects.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        HttpxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(host_ip_selects) == 1, f"expected 1 cached host lookup, got {len(host_ip_selects)}"
    assert (
        db_session.query(models.Host)
        .filter(models.Host.ip_address == "10.77.0.5", models.Host.project_id == test_project.id)
        .count()
        == 1
    )
