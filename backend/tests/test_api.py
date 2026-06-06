import pytest
import json
import io
import zipfile
from fastapi.testclient import TestClient
from app.db import models
from app.db.models_auth import AuditLog
from app.db.models import HostNote as HostNoteModel
from tests.conftest import USING_POSTGRES


class TestHostsAPI:
    """Test cases for hosts API endpoints."""
    
    def test_get_hosts_empty_database(self, client, test_project):
        """Test getting hosts from empty database."""
        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    def test_get_hosts_with_data(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test getting hosts with sample data."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()

        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/")
        assert response.status_code == 200

        payload = response.json()
        hosts = payload["items"]
        assert len(hosts) == 2
        assert payload["total"] == 2
        
        # Verify host structure
        host = hosts[0]
        assert "id" in host
        assert "ip_address" in host
        assert "hostname" in host
        assert "state" in host
        assert "ports" in host
        assert isinstance(host["ports"], list)
    
    def test_get_hosts_with_filters(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test hosts API with various filters."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        base = f"/api/v1/projects/{test_project.id}/hosts/"

        # Test state filter — the parser normalizes host state to
        # lowercase ('up'/'down'), and the endpoint filter is an exact match.
        response = client.get(f"{base}?state=up")
        assert response.status_code == 200
        hosts = response.json()["items"]
        assert len(hosts) == 2
        for host in hosts:
            assert host["state"] == "up"

        # Test port filter
        response = client.get(f"{base}?ports=22")
        assert response.status_code == 200
        hosts = response.json()["items"]
        assert len(hosts) == 1  # Only one host has port 22

        # Test service filter
        response = client.get(f"{base}?services=ssh")
        assert response.status_code == 200
        hosts = response.json()["items"]
        assert len(hosts) == 1

        # Test subnet filter
        response = client.get(f"{base}?subnet=192.168.1.0/24")
        assert response.status_code == 200
        hosts = response.json()["items"]
        assert len(hosts) == 2  # Both hosts are in this subnet

        # Test has_open_ports filter
        response = client.get(f"{base}?has_open_ports=true")
        assert response.status_code == 200
        hosts = response.json()["items"]
        assert len(hosts) == 2  # Both hosts have open ports

    def test_get_hosts_with_pagination_metadata(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test hosts API returns pagination metadata."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/?skip=0&limit=1&sort_by=ip_address&sort_order=asc")
        assert response.status_code == 200

        payload = response.json()
        assert payload["total"] == 2
        assert payload["skip"] == 0
        assert payload["limit"] == 1
        assert payload["sort_by"] == "ip_address"
        assert payload["sort_order"] == "asc"
        assert len(payload["items"]) == 1

    def test_get_hosts_supports_sorting(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test hosts API sorting options."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/?sort_by=ip_address&sort_order=asc")
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["ip_address"] == "192.168.1.1"
        assert items[1]["ip_address"] == "192.168.1.2"

    def test_get_hosts_with_notes_only_filter(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test hosts API with note-only filtering."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        noted_host = db_session.query(models.Host).filter(models.Host.ip_address == "192.168.1.1").first()
        db_session.add(HostNoteModel(host_id=noted_host.id, user_id=1, body="Needs review", status="open"))
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/?with_notes_only=true")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["ip_address"] == "192.168.1.1"

    def test_tool_ready_host_port_respects_service_filter(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Tool-ready host:port output should only include ports matching the active service filter."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/host-port?services=http")
        assert response.status_code == 200

        lines = [line.strip() for line in response.text.splitlines() if line.strip()]
        assert "192.168.1.1:80" in lines
        assert "192.168.1.1:22" not in lines
        assert "192.168.1.2:443" in lines
    
    def test_get_host_by_id(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test getting a specific host by ID."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        # Get host ID
        host = db_session.query(models.Host).first()
        host_id = host.id
        
        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host_id}")
        assert response.status_code == 200

        host_data = response.json()
        assert host_data["id"] == host_id
        assert host_data["ip_address"] == host.ip_address
    
    def test_get_nonexistent_host(self, client, test_project):
        """Test getting a host that doesn't exist."""
        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/99999")
        assert response.status_code == 404

    def test_get_note_activity_static_route_is_reachable(self, client, db_session, test_project):
        """Static /notes/activity route should not be shadowed by /{host_id}/notes."""
        host = models.Host(ip_address="192.168.50.10", state="up", project_id=test_project.id)
        db_session.add(host)
        db_session.flush()

        note = models.HostNote(host_id=host.id, user_id=1, body="Needs review")
        db_session.add(note)
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/notes/activity")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_notes"] == 1
        assert payload["notes"][0]["host_id"] == host.id

    def test_generate_agent_package_report(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Agent package export should return a ZIP with the expected structured files."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/reports/hosts/agent-package")
        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]

        bundle = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(bundle.namelist())
        assert {"manifest.json", "schema.json", "scans.json", "hosts.ndjson"}.issubset(names)

        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["schema_version"] == "1.0"
        assert manifest["counts"]["hosts"] == 2

        host_lines = [line for line in bundle.read("hosts.ndjson").decode().splitlines() if line.strip()]
        assert len(host_lines) == 2
        first_host = json.loads(host_lines[0])
        assert "identity" in first_host
        assert "ports" in first_host
        assert "vulnerabilities" in first_host

    def test_generate_markdown_bundle_report(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Markdown bundle export should return a ZIP with report and companion files."""
        from app.parsers.gnmap_parser import GnmapParser

        parser = GnmapParser(db_session)

        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)

        _scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        _scan.project_id = test_project.id
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/reports/hosts/markdown-bundle")
        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]

        bundle = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(bundle.namelist())
        assert {"report.md", "hosts.csv", "findings.csv", "scans.csv"}.issubset(names)

        report_md = bundle.read("report.md").decode()
        # v2.65.0 — was "NetworkMapper Host Report" before the
        # v2.58.0 BlueStick rename.
        assert "# BlueStick Host Report" in report_md
        assert "## Priority Hosts" in report_md


class TestScansAPI:
    """Test cases for scans API endpoints."""
    
    def test_get_scans_empty_database(self, client, test_project):
        """Test getting scans from empty database."""
        response = client.get(f"/api/v1/projects/{test_project.id}/scans/")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_scans_with_data(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test getting scans with sample data."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/scans/")
        assert response.status_code == 200

        scans = response.json()
        assert len(scans) == 1
        
        scan_data = scans[0]
        assert scan_data["filename"] == "test.gnmap"
        assert scan_data["scan_type"] == "nmap_gnmap"
        assert "total_hosts" in scan_data
        assert "up_hosts" in scan_data
        assert "total_ports" in scan_data
        assert "open_ports" in scan_data
    
    def test_get_scan_by_id(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test getting a specific scan by ID."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/scans/{scan.id}")
        assert response.status_code == 200

        scan_data = response.json()
        assert scan_data["id"] == scan.id
        assert scan_data["filename"] == scan.filename
    
    @pytest.mark.skipif(
        not USING_POSTGRES,
        reason="delete_scan does FK-graph discovery via PostgreSQL catalog "
        "queries (pg_constraint/pg_class/pg_attribute) and ANY(:ids) array "
        "syntax — runs only against the Postgres test DB, not SQLite.",
    )
    def test_delete_scan(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test deleting a scan."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        scan_id = scan.id
        
        # Delete scan
        response = client.delete(f"/api/v1/projects/{test_project.id}/scans/{scan_id}")
        assert response.status_code == 200

        # Verify scan is deleted
        response = client.get(f"/api/v1/projects/{test_project.id}/scans/{scan_id}")
        assert response.status_code == 404


class TestUploadAPI:
    """Test cases for file upload API."""
    
    def test_upload_gnmap_file(self, client, temp_file, sample_gnmap_data, test_project):
        """Test uploading a gnmap file.

        The upload endpoint is now an asynchronous background-ingestion
        queue: a valid file returns 200 with a job_id and a "queued"
        message rather than synchronously parsing.
        """
        # Write sample data to temp file. Real `nmap -oG` output starts
        # with a "# Nmap ..." comment header, which the ingestion
        # validator requires; the shared fixture omits the leading "# ".
        with open(temp_file, 'w') as f:
            f.write("# " + sample_gnmap_data)

        # Upload file
        with open(temp_file, 'rb') as f:
            response = client.post(
                f"/api/v1/projects/{test_project.id}/upload/",
                files={"file": ("test.gnmap", f, "application/octet-stream")}
            )

        assert response.status_code == 200
        result = response.json()

        assert "message" in result
        assert "job_id" in result
        assert "status" in result
        assert "filename" in result
        assert result["filename"] == "test.gnmap"
    
    def test_upload_unsupported_extension_rejected(self, client, temp_file, test_project):
        """Uploading a file with an extension outside ALLOWED_EXTENSIONS
        is rejected up front with 400."""
        with open(temp_file, 'w') as f:
            f.write("This is not a valid scan file")

        with open(temp_file, 'rb') as f:
            response = client.post(
                f"/api/v1/projects/{test_project.id}/upload/",
                files={"file": ("test.bogus", f, "application/octet-stream")}
            )

        assert response.status_code == 400
        result = response.json()
        assert "not allowed" in result["detail"].lower()


class TestAuditAPI:
    """Test cases for audit API endpoints."""

    def test_get_audit_logs_uses_timestamp_field(self, client, db_session):
        entry = AuditLog(action="login_success", user_id=1, success=True)
        db_session.add(entry)
        db_session.commit()

        response = client.get("/api/v1/audit/logs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["logs"][0]["action"] == "login_success"

    def test_get_audit_stats_uses_timestamp_field(self, client, db_session):
        entry = AuditLog(action="login_success", user_id=1, success=True)
        db_session.add(entry)
        db_session.commit()

        response = client.get("/api/v1/audit/stats")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_logs"] == 1
    
    def test_upload_malformed_file(self, client, temp_file, test_project):
        """A .gnmap file whose content does not look like greppable nmap
        output is rejected up front by the ingestion validator (400)."""
        # Write malformed content
        with open(temp_file, 'w') as f:
            f.write("This is not valid gnmap content")

        with open(temp_file, 'rb') as f:
            response = client.post(
                f"/api/v1/projects/{test_project.id}/upload/",
                files={"file": ("test.gnmap", f, "application/octet-stream")}
            )

        assert response.status_code == 400
        result = response.json()
        assert "gnmap" in result["detail"].lower()


class TestDashboardAPI:
    """Test cases for dashboard API endpoints."""
    
    def test_dashboard_stats_empty_database(self, client, test_project):
        """Test dashboard stats with empty database."""
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats")
        assert response.status_code == 200
        
        stats = response.json()
        assert stats["total_scans"] == 0
        assert stats["total_hosts"] == 0
        assert stats["total_ports"] == 0
        assert stats["total_subnets"] == 0
        assert isinstance(stats["recent_scans"], list)
        assert len(stats["recent_scans"]) == 0
    
    def test_dashboard_stats_with_data(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test dashboard stats with sample data."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats")
        assert response.status_code == 200

        stats = response.json()
        assert stats["total_scans"] == 1
        assert stats["total_hosts"] == 2
        assert stats["total_ports"] > 0  # Should have ports from parsed data
    
    def test_port_stats(self, client, db_session, sample_gnmap_data, temp_file, test_project):
        """Test port statistics endpoint."""
        from app.parsers.gnmap_parser import GnmapParser
        
        # Create sample data
        parser = GnmapParser(db_session)
        
        # Write sample data to temp file
        with open(temp_file, 'w') as f:
            f.write(sample_gnmap_data)
        
        scan = parser.parse_file(temp_file, "test.gnmap", project_id=test_project.id)
        scan.project_id = test_project.id
        db_session.commit()
        
        # Test API
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/port-stats")
        assert response.status_code == 200

        port_stats = response.json()
        assert isinstance(port_stats, list)
        assert len(port_stats) > 0
        
        # Check structure of port stats
        stat = port_stats[0]
        assert "port" in stat  # API uses "port", not "port_number"
        assert "count" in stat
        assert "service" in stat  # API uses "service", not "service_name"
    
    def test_os_stats(self, client, test_project):
        """Test OS statistics endpoint."""
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/os-stats")
        assert response.status_code == 200
        
        os_stats = response.json()
        assert isinstance(os_stats, list)


class TestErrorHandling:
    """Test API error handling."""
    
    def test_404_endpoints(self, client):
        """Test that non-existent endpoints return 404."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404
    
    def test_invalid_json_request(self, client, test_project):
        """Test handling of invalid JSON in request body."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/",
            headers={"Content-Type": "application/json"},
            data="invalid json"
        )
        assert response.status_code == 422  # Unprocessable Entity

    def test_missing_required_parameters(self, client, test_project):
        """Test handling of missing required parameters."""
        # Test creating scope without required name
        response = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/",
            json={}
        )
        assert response.status_code == 422
