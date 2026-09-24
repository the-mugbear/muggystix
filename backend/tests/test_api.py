import pytest
import json
import io
import zipfile
from fastapi.testclient import TestClient
from app.db import models
from app.db.models_auth import AuditLog
from app.db.models import Annotation as HostNoteModel
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

        # has_open_ports=false ALONE must exclude them.  The port block was
        # guarded by truthiness, so False as the only port filter skipped the
        # filter entirely and "no open ports" returned every host.
        response = client.get(f"{base}?has_open_ports=false")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

        # …and it keeps the standalone meaning it always had beside other port
        # filters (an exclusion of open-port hosts; the other filters are ignored).
        response = client.get(f"{base}?has_open_ports=false&ports=22")
        assert response.status_code == 200
        assert response.json()["items"] == []

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

        note = models.Annotation(host_id=host.id, user_id=1, body="Needs review")
        db_session.add(note)
        db_session.commit()

        response = client.get(f"/api/v1/projects/{test_project.id}/hosts/notes/activity")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_notes"] == 1
        assert payload["notes"][0]["host_id"] == host.id

    def test_enqueue_agent_package_report(self, client, test_project):
        """Agent-package export enqueues an async report job (the generation +
        ZIP contents are verified in test_report_jobs.py against the service)."""
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/reports/jobs",
            params={"format": "agent-package"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["format"] == "agent-package"

    def test_enqueue_markdown_bundle_report(self, client, test_project):
        """Markdown-bundle export enqueues an async report job."""
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/reports/jobs",
            params={"format": "markdown-bundle", "report_type": "comprehensive"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["format"] == "markdown-bundle"

    def test_enqueue_report_rejects_sync_format(self, client, test_project):
        """csv/html are sync-streamed, not enqueueable — the job endpoint rejects them."""
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/reports/jobs",
            params={"format": "csv"},
        )
        assert resp.status_code == 422, resp.text


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
        # The viewer's "N in the last 24 hours" reads this exact field.
        assert payload["recent_logs_24h"] == 1

    def test_audit_logs_carry_the_actors_name(self, client, db_session, test_user):
        """The viewer shows who acted by name, not by user id; the names come
        from one batched lookup, so a user-less row (failed login) and a
        deleted account (user_id no longer resolves) both read as null."""
        from sqlalchemy import event
        from app.db.models_auth import User, UserRole

        # Explicit id: test_user is inserted with id=1, which the sequence
        # does not know about.
        other = User(
            id=4242, username="eval-ana", email="ana@example.com", full_name="Ana Ortiz",
            hashed_password="x", role=UserRole.MEMBER, is_active=True,
        )
        db_session.add(other)
        db_session.commit()
        for uid in (test_user.id, other.id, other.id, None):
            db_session.add(AuditLog(action="login_success", user_id=uid, success=True))
        db_session.commit()

        statements = []

        def _record(conn, cursor, statement, *args):
            if "FROM users" in statement:
                statements.append(statement)

        event.listen(db_session.bind, "before_cursor_execute", _record)
        try:
            response = client.get("/api/v1/audit/logs")
        finally:
            event.remove(db_session.bind, "before_cursor_execute", _record)
        assert response.status_code == 200
        by_user = {}
        for row in response.json()["logs"]:
            by_user.setdefault(row["user_id"], set()).add(
                (row["user_username"], row["user_full_name"]),
            )
        assert by_user[other.id] == {("eval-ana", "Ana Ortiz")}
        assert by_user[test_user.id] == {("test-admin", "Test Admin")}
        assert by_user[None] == {(None, None)}
        # Auth resolves the caller once; the names are ONE more query, however
        # many rows share or differ in user.
        name_lookups = [s for s in statements if "users.full_name" in s and " IN " in s.upper()]
        assert len(name_lookups) == 1

        stats = client.get("/api/v1/audit/stats").json()
        top = {u["user_id"]: u for u in stats["top_users"]}
        assert top[other.id]["user_full_name"] == "Ana Ortiz"

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
    


class TestErrorHandling:
    """Test API error handling."""
    
    def test_404_endpoints(self, client):
        """Test that non-existent endpoints return 404."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404
    
    def test_invalid_json_request(self, client, test_project):
        """Test handling of invalid JSON in request body."""
        # v2.244.0 — POST /scopes/ was removed; any endpoint with a required
        # JSON body exercises the same handler.
        response = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/subnet-labels",
            headers={"Content-Type": "application/json"},
            data="invalid json"
        )
        assert response.status_code == 422  # Unprocessable Entity

    def test_missing_required_parameters(self, client, test_project):
        """Test handling of missing required parameters."""
        # Creating a subnet label without its required `name`.
        response = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/subnet-labels",
            json={}
        )
        assert response.status_code == 422
