from typing import List, Optional, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, distinct, func
from app.db.session import get_db
from app.db import models
from app.db.models_vulnerability import Vulnerability, enum_value, SEVERITY_KEYS
from app.schemas.schemas import Host
from app.core.config import settings
from app.services.report_templates import ReportTemplates
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.services.host_serialization import _serialize_follow, _serialize_note  # CR4-2
from app.api.v1.endpoints.hosts import _build_filtered_host_query, HostFilterParams
from app.db.models import HostFollow
from app.db.models_confidence import HostConfidence, PortConfidence, ConflictHistory
import io
import csv
import json
import zipfile
from datetime import datetime, timezone
import hashlib
import html

router = APIRouter(dependencies=[Depends(get_current_user)])


# v2.86.4 — the CSV formula-injection helpers (``csv_safe`` and
# ``safe_csv_row``) were hoisted to ``app.services.csv_utils`` so the
# export_service code path could share them.  This module re-exports
# under the previous private names so existing call sites in this file
# keep working without churn.
from app.services.csv_utils import csv_safe as _csv_safe, safe_csv_row as _safe_csv_row  # noqa: F401


def _id_chunks(ids, size: int = 1000):
    """Yield slices of an id list for chunked ``IN (...)`` queries.

    A bundle/agent-package export can hold up to MAX_REPORT_HOSTS hosts and
    their ports — at ~10 ports/host that's ~100k port_ids, which as a single
    ``IN`` list approaches PostgreSQL's bind-param ceiling and degrades the
    query plan toward a seq-scan.  Chunking keeps each statement bounded.
    """
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


class ReportGenerator:
    SCHEMA_VERSION = "1.0"
    # Derived from the canonical SEVERITY_KEYS (critical=0 … unknown=5) so the
    # sort ordering has one source shared with the host serializer.
    SEVERITY_ORDER = {sev: i for i, sev in enumerate(SEVERITY_KEYS)}

    def __init__(self, db: Session, current_user, project_id: int = None):
        self.db = db
        self.current_user = current_user
        self.project_id = project_id
    
    # Maximum number of hosts a single report can include to prevent OOM
    MAX_REPORT_HOSTS = 10000

    def get_hosts_for_report(self, filters: Dict[str, Any]) -> List[models.Host]:
        """Get hosts based on filter parameters (capped at MAX_REPORT_HOSTS).

        ``filters`` must be ``build_filtered_host_query`` kwargs — every report
        route now derives it from ``HostFilterParams.as_builder_kwargs()`` and
        splats it in, so a new filter dimension can never be silently dropped
        from exports (the bug that let reports include more hosts than the
        visible list).  Splatting (not per-key ``.get()``) is what guarantees
        no drift.
        """
        query = _build_filtered_host_query(
            self.db,
            self.current_user,
            **filters,
            project_id=self.project_id,
        ).options(
            selectinload(models.Host.ports).selectinload(models.Port.scripts),
            selectinload(models.Host.host_scripts),
            selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
            selectinload(models.Host.last_updated_scan),
            selectinload(models.Host.notes).selectinload(models.Annotation.author),
            selectinload(models.Host.vulnerabilities).selectinload(Vulnerability.port),
        ).distinct().limit(self.MAX_REPORT_HOSTS)

        return query.all()
    
    def generate_csv_report(self, hosts: List[models.Host]) -> str:
        """Generate CSV report from hosts data"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Headers
        writer.writerow([
            'IP Address', 'Hostname', 'State', 'OS Name', 'OS Family', 'OS Type', 'OS Accuracy',
            'Open Ports', 'Total Ports', 'Services', 'Scan File', 'Scan Date'
        ])
        
        for host in hosts:
            open_ports = [p for p in (host.ports or []) if p.state == 'open']
            total_ports = len(host.ports or [])
            
            # Get unique services
            services = list(set([p.service_name for p in open_ports if p.service_name]))
            services_str = ', '.join(services[:5])  # Limit to 5 services
            if len(services) > 5:
                services_str += f' (+{len(services) - 5} more)'

            # Open ports string
            open_ports_str = ', '.join([f"{p.port_number}/{p.protocol}" for p in open_ports[:10]])
            if len(open_ports) > 10:
                open_ports_str += f' (+{len(open_ports) - 10} more)'

            scan_info = self._resolve_scan_info(host)
            scan = scan_info.get('scan')
            discovered_at = scan_info.get('discovered_at')
            scan_filename = getattr(scan, 'filename', None) or ''
            if scan and getattr(scan, 'created_at', None):
                scan_date_str = scan.created_at.strftime('%Y-%m-%d %H:%M:%S')
            elif discovered_at:
                scan_date_str = discovered_at.strftime('%Y-%m-%d %H:%M:%S')
            else:
                scan_date_str = ''

            _safe_csv_row(writer, [
                host.ip_address,
                host.hostname or '',
                host.state or '',
                host.os_name or '',
                host.os_family or '',
                host.os_type or '',
                host.os_accuracy or '',
                open_ports_str,
                total_ports,
                services_str,
                scan_filename,
                scan_date_str
            ])

        return output.getvalue()
    
    def generate_html_report(self, hosts: List[models.Host], filters: Dict[str, Any]) -> str:
        """Generate HTML report from hosts data"""
        # Calculate summary statistics
        total_hosts = len(hosts)
        hosts_up = len([h for h in hosts if h.state == 'up'])
        total_open_ports = sum(len([p for p in (h.ports or []) if p.state == 'open']) for h in hosts)
        
        # Get most common services
        service_count = {}
        for host in hosts:
            for port in (host.ports or []):
                if port.state == 'open' and port.service_name:
                    service_count[port.service_name] = service_count.get(port.service_name, 0) + 1
        
        top_services = sorted(service_count.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Get OS distribution
        os_count = {}
        for host in hosts:
            if host.os_name:
                os_count[host.os_name] = os_count.get(host.os_name, 0) + 1
        
        top_os = sorted(os_count.items(), key=lambda x: x[1], reverse=True)[:10]
        
        css = ReportTemplates.get_css_styles()
        scripts = ReportTemplates.get_interactive_scripts()
        generated_at = datetime.now(timezone.utc).isoformat()
        backend_version = settings.APP_VERSION
        frontend_version = settings.FRONTEND_VERSION
        max_service_value = max(dict(top_services).values()) if top_services else 1
        max_os_value = max(dict(top_os).values()) if top_os else 1

        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BlueStick Host Report</title>
    {css}
    <style>
        .charts {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 20px;
            margin-top: 10px;
        }}

        .chart-card {{
            background: var(--bg-panel-soft);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 12px 28px rgba(0,0,0,0.20);
        }}

        .chart-title {{
            margin-bottom: 15px;
            font-size: 1.1em;
            font-weight: 600;
            color: var(--text);
        }}

        .bar {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
            gap: 12px;
        }}

        .bar-label {{
            flex: 0 0 120px;
            font-size: 0.85em;
            color: var(--muted);
        }}

        .bar-fill {{
            flex: 1;
            height: 10px;
            border-radius: 999px;
            background: #0b1118;
            position: relative;
            overflow: hidden;
            border: 1px solid var(--border);
        }}

        .bar-fill::after {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            height: 100%;
            width: var(--bar-width, 0%);
            background: linear-gradient(135deg, var(--accent) 0%, var(--accent-warm) 100%);
        }}

        .bar-value {{
            flex: 0 0 40px;
            font-size: 0.85em;
            text-align: right;
            color: var(--muted);
        }}

        .filters-list {{
            margin-top: 10px;
            color: var(--muted);
            font-size: 0.9em;
        }}

        .filters-list strong {{
            color: var(--text);
        }}

        td.up {{
            color: var(--success);
            font-weight: 600;
        }}

        td.down {{
            color: var(--danger);
            font-weight: 600;
        }}
    </style>
</head>
<body>
    <div class="report-header">
        <div class="metadata">
            <div>
                <div class="report-title">BlueStick Host Report</div>
                <div class="report-subtitle">Detailed inventory of discovered hosts</div>
                <div class="version-tag">Backend v{backend_version} | Frontend v{frontend_version}</div>
            </div>
            <div>
                <strong>Generated:</strong> {datetime.fromisoformat(generated_at.replace('Z', '')).strftime('%B %d, %Y at %I:%M %p')}<br>
                <strong>Report ID:</strong> NM-HOST-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{int(hashlib.sha256(generated_at.encode()).hexdigest()[:4], 16) % 10000:04d}
            </div>
        </div>
    </div>

    <nav class="report-nav" aria-label="Report sections">
        <a href="#summary">Summary</a>
        <a href="#metrics">Metrics</a>
        <a href="#exposure">Exposure Highlights</a>
        <a href="#hosts">Host Details</a>
    </nav>

    <div class="executive-summary" id="summary">
        <h3>Executive Summary</h3>
        <p><strong>Scope:</strong> This report summarizes {total_hosts} discovered hosts filtered by the selected criteria. The dataset contains {hosts_up} hosts currently marked as up and {total_open_ports} detected open ports across the sample.</p>
        <p><strong>Service Exposure:</strong> We identified {len(service_count)} unique services. Review the Top Services panel below to focus on the most prevalent protocols.</p>
        <p><strong>Usage:</strong> Utilize the interactive filters beneath each table to refine results or sort by any column to prioritize follow-up actions.</p>
    </div>

    <div class="section" id="metrics">
        <div class="section-header">Key Metrics</div>
        <div class="section-content">
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">{total_hosts}</div><div class="stat-label">Total Hosts</div></div>
                <div class="stat-card"><div class="stat-value">{hosts_up}</div><div class="stat-label">Hosts Up</div></div>
                <div class="stat-card"><div class="stat-value">{total_open_ports}</div><div class="stat-label">Open Ports</div></div>
                <div class="stat-card"><div class="stat-value">{len(service_count)}</div><div class="stat-label">Unique Services</div></div>
            </div>
            <div class="filters-list">{self._format_filters_html(filters)}</div>
        </div>
    </div>

    <div class="section" id="exposure">
        <div class="section-header">Exposure Highlights</div>
        <div class="section-content">
            <div class="charts">
                <div class="chart-card">
                    <div class="chart-title">Top Services</div>
                    {self._generate_chart_bars(top_services, max_service_value)}
                </div>
                <div class="chart-card">
                    <div class="chart-title">Top Operating Systems</div>
                    {self._generate_chart_bars(top_os, max_os_value)}
                </div>
            </div>
        </div>
    </div>

    <div class="section" id="findings">
        <div class="section-header">Findings</div>
        <div class="section-content">
            {self._generate_findings_html(hosts)}
        </div>
    </div>

    <div class="section" id="hosts">
        <div class="section-header">Host Details</div>
        <div class="section-content">
            <div class="interactive-table-wrapper">
                <div class="table-controls">
                    <input type="text" class="table-search" placeholder="Filter hosts..." aria-label="Filter host rows">
                    <span class="table-hint">Click column headers to sort</span>
                </div>
                <table class="interactive-table">
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Hostname</th>
                            <th>State</th>
                            <th>OS</th>
                            <th>Open Ports</th>
                            <th>Services</th>
                            <th>Scan</th>
                        </tr>
                    </thead>
                    <tbody>
                        {self._generate_host_rows_html(hosts)}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>This report was generated by BlueStick - Professional Network Discovery Platform</p>
        <p>Platform Versions: Backend v{backend_version} | Frontend v{frontend_version}</p>
        <p>© {datetime.now(timezone.utc).year} BlueStick. For questions about this report, contact your security team or system administrator.</p>
    </div>

    {scripts}
</body>
</html>"""
        return html_content

    def _resolve_scan_info(self, host: models.Host) -> Dict[str, Any]:
        """Determine the most relevant scan metadata for a host."""
        scan = getattr(host, "last_updated_scan", None)
        scan_history = list(getattr(host, "scan_history", []) or [])
        discovered_at = None

        if not scan and scan_history:
            scan_history.sort(key=lambda entry: entry.discovered_at or datetime.min, reverse=True)
            primary_entry = scan_history[0]
            scan = getattr(primary_entry, "scan", None)
            discovered_at = primary_entry.discovered_at
        elif scan and scan_history:
            for entry in scan_history:
                if entry.scan_id == getattr(scan, "id", None):
                    discovered_at = entry.discovered_at
                    break

        if not discovered_at and scan_history:
            scan_history.sort(key=lambda entry: entry.discovered_at or datetime.min, reverse=True)
            discovered_at = scan_history[0].discovered_at

        return {"scan": scan, "discovered_at": discovered_at}

    def _format_filters_html(self, filters: Dict[str, Any]) -> str:
        """Format applied filters for HTML report"""
        if not filters:
            return "<p><strong>Applied Filters:</strong> None</p>"

        filter_items = []
        for key, value in filters.items():
            if value:
                if key == 'search':
                    filter_items.append(f"Search: {html.escape(str(value))}")
                elif key == 'state':
                    filter_items.append(f"Host State: {html.escape(str(value))}")
                elif key == 'ports':
                    filter_items.append(f"Ports: {html.escape(str(value))}")
                elif key == 'services':
                    filter_items.append(f"Services: {html.escape(str(value))}")
                elif key == 'os_filter':
                    filter_items.append(f"OS Filter: {html.escape(str(value))}")
        
        if filter_items:
            return f"<p><strong>Applied Filters:</strong> {', '.join(filter_items)}</p>"
        return "<p><strong>Applied Filters:</strong> None</p>"
    
    def _generate_chart_bars(self, data: List[tuple], max_value: int) -> str:
        """Generate HTML bars for charts"""
        if not data:
            return "<p>No data available</p>"
        
        bars = []
        for name, count in data[:10]:  # Top 10
            percentage = (count / max_value) * 100 if max_value > 0 else 0
            bars.append(f"""
                <div class="bar">
                    <div class="bar-label">{html.escape(str(name))}</div>
                    <div class="bar-fill" style="--bar-width: {percentage:.2f}%;"></div>
                    <div class="bar-value">{count}</div>
                </div>
            """)

        return ''.join(bars)
    
    def _generate_host_rows_html(self, hosts: List[models.Host]) -> str:
        """Generate HTML table rows for hosts"""
        rows = []
        for host in hosts:
            open_ports = [p for p in (host.ports or []) if p.state == 'open']
            services = list(set([p.service_name for p in open_ports if p.service_name]))
            
            open_ports_str = ', '.join([f"{p.port_number}" for p in open_ports[:10]])
            if len(open_ports) > 10:
                open_ports_str += f' (+{len(open_ports) - 10})'
            
            services_str = ', '.join(services[:5])
            if len(services) > 5:
                services_str += f' (+{len(services) - 5})'

            state_class = 'up' if host.state == 'up' else 'down'

            scan_info = self._resolve_scan_info(host)
            scan = scan_info.get('scan')
            scan_label = html.escape(getattr(scan, 'filename', '') or '')

            rows.append(f"""
                <tr class="host-row">
                    <td>{html.escape(host.ip_address)}</td>
                    <td>{html.escape(host.hostname or '')}</td>
                    <td class="{state_class}">{html.escape(host.state or '')}</td>
                    <td>{html.escape(host.os_name or '')}</td>
                    <td class="port-list">{html.escape(open_ports_str)}</td>
                    <td class="service-list">{html.escape(services_str)}</td>
                    <td>{scan_label}</td>
                </tr>
            """)
        
        return ''.join(rows)

    def generate_json_report(self, hosts: List[models.Host]) -> Dict[str, Any]:
        """Generate JSON report from hosts data"""
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_hosts": len(hosts),
                "hosts_up": len([h for h in hosts if h.state == 'up']),
                "hosts_down": len([h for h in hosts if h.state == 'down']),
                "total_open_ports": sum(len([p for p in (h.ports or []) if p.state == 'open']) for h in hosts)
            },
            "hosts": []
        }
        
        for host in hosts:
            host_data = {
                "id": host.id,
                "ip_address": host.ip_address,
                "hostname": host.hostname,
                "state": host.state,
                "os_info": {
                    "name": host.os_name,
                    "family": host.os_family,
                    "type": host.os_type,
                    "vendor": host.os_vendor,
                    "accuracy": host.os_accuracy
                },
                "ports": [],
                "scan_info": {}
            }

            scan_info = self._resolve_scan_info(host)
            scan = scan_info.get('scan')
            discovered_at = scan_info.get('discovered_at')
            host_data["scan_info"] = {
                "filename": getattr(scan, 'filename', None),
                "scan_date": scan.created_at.isoformat() if scan and getattr(scan, 'created_at', None) else None,
                "discovered_at": discovered_at.isoformat() if discovered_at else None,
            }
            
            for port in (host.ports or []):
                port_data = {
                    "port_number": port.port_number,
                    "protocol": port.protocol,
                    "state": port.state,
                    "service": {
                        "name": port.service_name,
                        "product": port.service_product,
                        "version": port.service_version
                    }
                }
                host_data["ports"].append(port_data)
            
            report_data["hosts"].append(host_data)

        report_data["findings"] = self._findings_for_report(hosts)
        return report_data

    def _findings_for_report(self, hosts: List[models.Host]) -> List[Dict[str, Any]]:
        """Findings affecting the report's hosts, severity-ordered — the
        triaged record that rolls up across hosts (note promotions, scanner
        promotions, execution results). Included in every export format so a
        report carries the analyst's conclusions, not just raw scan data."""
        from app.db.models_findings import Finding, FindingHost
        host_ids = [h.id for h in hosts]
        if not host_ids:
            return []
        findings = (
            self.db.query(Finding)
            .options(
                selectinload(Finding.hosts).selectinload(FindingHost.host),
                selectinload(Finding.owner),
            )
            .filter(
                Finding.project_id == self.project_id,
                Finding.hosts.any(FindingHost.host_id.in_(host_ids)),
            )
            .all()
        )
        out = [
            {
                "id": f.id,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "source": f.source,
                "owner": (f.owner.full_name or f.owner.username) if f.owner else None,
                "host_count": len(f.hosts),
                "affected_hosts": [fh.host.ip_address for fh in f.hosts if fh.host],
                "vuln_id": f.vuln_id,
            }
            for f in findings
        ]
        out.sort(key=lambda x: self.SEVERITY_ORDER.get(x["severity"], 5))
        return out

    def _generate_findings_html(self, hosts: List[models.Host]) -> str:
        """Findings table fragment for the HTML report (severity-ordered)."""
        findings = self._findings_for_report(hosts)
        if not findings:
            return '<p class="muted">No findings recorded for these hosts.</p>'
        rows = []
        for f in findings:
            affected = f["affected_hosts"]
            hosts_str = ", ".join(affected[:5])
            if len(affected) > 5:
                hosts_str += f" (+{len(affected) - 5} more)"
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(f['severity']))}</td>"
                f"<td>{html.escape(str(f['title']))}</td>"
                f"<td>{html.escape(str(f['status']))}</td>"
                f"<td>{html.escape(str(f['source']))}</td>"
                f"<td>{html.escape(str(f['owner'] or '—'))}</td>"
                f"<td>{html.escape(hosts_str)}</td>"
                "</tr>"
            )
        return (
            '<table class="data-table"><thead><tr>'
            "<th>Severity</th><th>Finding</th><th>Status</th>"
            "<th>Source</th><th>Owner</th><th>Affected hosts</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )

    def generate_agent_package(self, hosts: List[models.Host], filters: Dict[str, Any]) -> bytes:
        """Generate a ZIP package optimized for agentic workflows."""
        dataset, artifacts = self._build_export_dataset(hosts, filters)

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(dataset["manifest"], indent=2))
            bundle.writestr("schema.json", json.dumps(self._build_schema_reference(), indent=2))
            bundle.writestr("scans.json", json.dumps(dataset["scans"], indent=2))
            ndjson_lines = "\n".join(json.dumps(host, separators=(",", ":")) for host in dataset["hosts"])
            bundle.writestr("hosts.ndjson", f"{ndjson_lines}\n" if ndjson_lines else "")
            for artifact_path, content in artifacts.items():
                bundle.writestr(artifact_path, content)

        return archive.getvalue()

    def generate_markdown_bundle(self, hosts: List[models.Host], filters: Dict[str, Any]) -> bytes:
        """Generate a ZIP bundle for human-readable sharing across applications."""
        dataset, artifacts = self._build_export_dataset(hosts, filters)

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("report.md", self._generate_markdown_report(dataset))
            bundle.writestr("hosts.csv", self._generate_hosts_csv(dataset["hosts"]))
            bundle.writestr("findings.csv", self._generate_findings_csv(dataset["hosts"]))
            bundle.writestr("scans.csv", self._generate_scans_csv(dataset["scans"]))
            for artifact_path, content in artifacts.items():
                bundle.writestr(artifact_path, content)

        return archive.getvalue()

    def _build_export_dataset(self, hosts: List[models.Host], filters: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        context = self._build_export_context(hosts)
        artifacts: Dict[str, str] = {}
        records = [self._build_host_export_record(host, context, artifacts) for host in hosts]

        total_vulnerabilities = sum(len(record["vulnerabilities"]) for record in records)
        total_open_ports = sum(
            len([port for port in record["ports"] if port.get("state") == "open"])
            for record in records
        )
        findings = self._findings_for_report(hosts)

        manifest = {
            "export_type": "host_report_package",
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "BlueStick",
            "filters": filters,
            "counts": {
                "hosts": len(records),
                "hosts_up": len([record for record in records if record["identity"].get("state") == "up"]),
                "open_ports": total_open_ports,
                "vulnerabilities": total_vulnerabilities,
                "findings": len(findings),
            },
            "included_sections": [
                "identity",
                "scope",
                "timeline",
                "os",
                "ports",
                "host_scripts",
                "vulnerabilities",
                "analyst_context",
                "confidence",
            ],
        }

        return {
            "manifest": manifest,
            "findings": findings,
            "hosts": records,
            "scans": context["scans"],
        }, artifacts

    def _build_export_context(self, hosts: List[models.Host]) -> Dict[str, Any]:
        host_ids = [host.id for host in hosts]
        port_ids = [port.id for host in hosts for port in (host.ports or [])]
        scan_ids = {
            history.scan_id
            for host in hosts
            for history in (host.scan_history or [])
            if history.scan_id
        }
        scan_ids.update(
            host.last_updated_scan_id for host in hosts if getattr(host, "last_updated_scan_id", None)
        )
        scan_ids.update(
            vuln.scan_id
            for host in hosts
            for vuln in (host.vulnerabilities or [])
            if getattr(vuln, "scan_id", None)
        )
        scan_ids.update(
            script.scan_id
            for host in hosts
            for script in (host.host_scripts or [])
            if getattr(script, "scan_id", None)
        )
        scan_ids.update(
            script.scan_id
            for host in hosts
            for port in (host.ports or [])
            for script in (port.scripts or [])
            if getattr(script, "scan_id", None)
        )

        follow_map: Dict[int, HostFollow] = {}
        if host_ids:
            follow_records = (
                self.db.query(HostFollow)
                .filter(HostFollow.user_id == self.current_user.id, HostFollow.host_id.in_(host_ids))
                .all()
            )
            follow_map = {record.host_id: record for record in follow_records}

        subnet_map: Dict[int, List[Dict[str, Optional[str]]]] = {}
        if host_ids:
            subnet_rows = (
                self.db.query(models.HostSubnetMapping.host_id, models.Subnet.cidr, models.Scope.name)
                .join(models.Subnet, models.HostSubnetMapping.subnet_id == models.Subnet.id)
                .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
                .filter(models.HostSubnetMapping.host_id.in_(host_ids))
                .all()
            )
            for host_id, cidr, scope_name in subnet_rows:
                subnet_map.setdefault(host_id, []).append({"cidr": cidr, "scope_name": scope_name})

        host_confidence_map: Dict[int, List[HostConfidence]] = {}
        if host_ids:
            host_confidences = (
                self.db.query(HostConfidence)
                .filter(HostConfidence.host_id.in_(host_ids))
                .all()
            )
            for confidence in host_confidences:
                host_confidence_map.setdefault(confidence.host_id, []).append(confidence)

        port_confidence_map: Dict[int, List[PortConfidence]] = {}
        for chunk in _id_chunks(port_ids):
            port_confidences = (
                self.db.query(PortConfidence)
                .filter(PortConfidence.port_id.in_(chunk))
                .all()
            )
            for confidence in port_confidences:
                port_confidence_map.setdefault(confidence.port_id, []).append(confidence)

        host_conflicts_map: Dict[int, List[ConflictHistory]] = {}
        port_conflicts_map: Dict[int, List[ConflictHistory]] = {}
        if host_ids:
            host_conflicts = (
                self.db.query(ConflictHistory)
                .filter(
                    ConflictHistory.object_type == "host",
                    ConflictHistory.object_id.in_(host_ids),
                )
                .order_by(ConflictHistory.resolved_at.desc())
                .all()
            )
            for conflict in host_conflicts:
                host_conflicts_map.setdefault(conflict.object_id, []).append(conflict)
        for chunk in _id_chunks(port_ids):
            port_conflicts = (
                self.db.query(ConflictHistory)
                .filter(
                    ConflictHistory.object_type == "port",
                    ConflictHistory.object_id.in_(chunk),
                )
                .order_by(ConflictHistory.resolved_at.desc())
                .all()
            )
            for conflict in port_conflicts:
                port_conflicts_map.setdefault(conflict.object_id, []).append(conflict)

        scans: Dict[str, Any] = {}
        if scan_ids:
            scan_rows = (
                self.db.query(models.Scan)
                .filter(models.Scan.id.in_(scan_ids))
                .all()
            )
            scans = {
                str(scan.id): {
                    "scan_id": scan.id,
                    "filename": scan.filename,
                    "tool_name": scan.tool_name,
                    "scan_type": scan.scan_type,
                    "created_at": self._iso(scan.created_at),
                    "start_time": self._iso(scan.start_time),
                    "end_time": self._iso(scan.end_time),
                    "command_line": scan.command_line,
                    "version": scan.version,
                }
                for scan in scan_rows
            }

        return {
            "follow_map": follow_map,
            "subnet_map": subnet_map,
            "host_confidence_map": host_confidence_map,
            "port_confidence_map": port_confidence_map,
            "host_conflicts_map": host_conflicts_map,
            "port_conflicts_map": port_conflicts_map,
            "scans": scans,
        }

    def _build_host_export_record(self, host: models.Host, context: Dict[str, Any], artifacts: Dict[str, str]) -> Dict[str, Any]:
        discoveries = [
            {
                "scan_id": history.scan_id,
                "scan_filename": getattr(history.scan, "filename", None),
                "tool_name": getattr(history.scan, "tool_name", None),
                "scan_type": getattr(history.scan, "scan_type", None),
                "scan_start": self._iso(getattr(history.scan, "start_time", None)),
                "scan_end": self._iso(getattr(history.scan, "end_time", None)),
                "command_line": getattr(history.scan, "command_line", None),
                "discovered_at": self._iso(history.discovered_at),
            }
            for history in sorted(
                list(host.scan_history or []),
                key=lambda entry: entry.discovered_at or datetime.min,
            )
        ]
        notes = [
            self._serialize_note_for_export(note)
            for note in sorted(
                list(host.notes or []),
                key=lambda note: note.created_at or note.updated_at or datetime.min,
            )
        ]
        vulnerabilities = [
            self._serialize_vulnerability_for_export(vuln)
            for vuln in sorted(list(host.vulnerabilities or []), key=self._vulnerability_sort_key)
        ]
        subnet_entries = context["subnet_map"].get(host.id, [])
        follow_record = context["follow_map"].get(host.id)

        return {
            "host_id": host.id,
            "identity": {
                "ip_address": host.ip_address,
                "hostname": host.hostname,
                "state": host.state,
                "state_reason": host.state_reason,
            },
            "scope": {
                "in_scope": bool(subnet_entries),
                "out_of_scope": not bool(subnet_entries),
                "subnets": subnet_entries,
            },
            "timeline": {
                "first_seen": self._iso(host.first_seen),
                "last_seen": self._iso(host.last_seen),
                "last_updated_scan_id": host.last_updated_scan_id,
                "discoveries": discoveries,
            },
            "os": {
                "name": host.os_name,
                "family": host.os_family,
                "generation": host.os_generation,
                "type": host.os_type,
                "vendor": host.os_vendor,
                "accuracy": host.os_accuracy,
            },
            "ports": [
                self._serialize_port_for_export(host.id, port, context, artifacts)
                for port in sorted(
                    list(host.ports or []),
                    key=lambda item: (item.port_number, item.protocol),
                )
            ],
            "host_scripts": [
                self._serialize_host_script_for_export(host.id, script, artifacts)
                for script in sorted(
                    list(host.host_scripts or []),
                    key=lambda item: (item.script_id, item.id),
                )
            ],
            "vulnerabilities": vulnerabilities,
            "vulnerability_summary": self._build_vulnerability_summary(vulnerabilities),
            "analyst_context": {
                "follow_status": getattr(follow_record.status, "value", None) if follow_record else None,
                "follow": _serialize_follow(follow_record).model_dump(mode="json") if follow_record else None,
                "notes": notes,
            },
            "confidence": {
                "host_attributes": [
                    self._serialize_host_confidence(confidence)
                    for confidence in context["host_confidence_map"].get(host.id, [])
                ],
                "port_attributes": {
                    str(port.id): [
                        self._serialize_port_confidence(confidence)
                        for confidence in context["port_confidence_map"].get(port.id, [])
                    ]
                    for port in (host.ports or [])
                    if context["port_confidence_map"].get(port.id)
                },
                "conflicts": {
                    "host": [
                        self._serialize_conflict(conflict)
                        for conflict in context["host_conflicts_map"].get(host.id, [])
                    ],
                    "ports": {
                        str(port.id): [
                            self._serialize_conflict(conflict)
                            for conflict in context["port_conflicts_map"].get(port.id, [])
                        ]
                        for port in (host.ports or [])
                        if context["port_conflicts_map"].get(port.id)
                    },
                },
            },
        }

    def _serialize_port_for_export(
        self,
        host_id: int,
        port: models.Port,
        context: Dict[str, Any],
        artifacts: Dict[str, str],
    ) -> Dict[str, Any]:
        scripts = [
            self._serialize_port_script_for_export(host_id, port.port_number, port.protocol, script, artifacts)
            for script in sorted(list(port.scripts or []), key=lambda item: (item.script_id, item.id))
        ]
        return {
            "port_id": port.id,
            "port_number": port.port_number,
            "protocol": port.protocol,
            "state": port.state,
            "reason": port.reason,
            "service": {
                "name": port.service_name,
                "product": port.service_product,
                "version": port.service_version,
                "extra_info": port.service_extrainfo,
                "method": port.service_method,
                "confidence": port.service_conf,
            },
            "timestamps": {
                "first_seen": self._iso(port.first_seen),
                "last_seen": self._iso(port.last_seen),
                "last_updated_scan_id": port.last_updated_scan_id,
            },
            "scripts": scripts,
            "confidence": [
                self._serialize_port_confidence(confidence)
                for confidence in context["port_confidence_map"].get(port.id, [])
            ],
        }

    def _serialize_port_script_for_export(
        self,
        host_id: int,
        port_number: int,
        protocol: str,
        script: models.Script,
        artifacts: Dict[str, str],
    ) -> Dict[str, Any]:
        payload = {
            "script_id": script.script_id,
            "scan_id": script.scan_id,
            "first_seen": self._iso(script.first_seen),
            "last_seen": self._iso(script.last_seen),
        }
        if script.output:
            artifact_path = f"artifacts/hosts/{host_id}/ports/{port_number}-{protocol}/{script.script_id}.txt"
            artifacts[artifact_path] = script.output
            payload["output_ref"] = artifact_path
        return payload

    def _serialize_host_script_for_export(
        self,
        host_id: int,
        script: models.HostScript,
        artifacts: Dict[str, str],
    ) -> Dict[str, Any]:
        payload = {
            "script_id": script.script_id,
            "scan_id": script.scan_id,
            "first_seen": self._iso(script.first_seen),
            "last_seen": self._iso(script.last_seen),
        }
        if script.output:
            artifact_path = f"artifacts/hosts/{host_id}/host_scripts/{script.script_id}.txt"
            artifacts[artifact_path] = script.output
            payload["output_ref"] = artifact_path
        return payload

    def _serialize_vulnerability_for_export(self, vuln: Vulnerability) -> Dict[str, Any]:
        references = self._parse_json_list(vuln.references)
        return {
            "id": vuln.id,
            "source": enum_value(vuln.source),
            "scan_id": vuln.scan_id,
            "plugin_id": vuln.plugin_id,
            "source_plugin_name": vuln.source_plugin_name,
            "title": vuln.title,
            "description": vuln.description,
            "severity": enum_value(vuln.severity),
            "cvss_score": vuln.cvss_score,
            "cvss_vector": vuln.cvss_vector,
            "cve_id": vuln.cve_id,
            "port_id": vuln.port_id,
            "port_number": vuln.port.port_number if vuln.port else None,
            "protocol": vuln.port.protocol if vuln.port else None,
            "service_name": vuln.port.service_name if vuln.port else None,
            "exploitable": vuln.exploitable,
            "first_seen": self._iso(vuln.first_seen),
            "last_seen": self._iso(vuln.last_seen),
            "solution": vuln.solution,
            "references": references,
        }

    def _serialize_note_for_export(self, note: models.Annotation) -> Dict[str, Any]:
        return _serialize_note(note).model_dump(mode="json")

    def _serialize_host_confidence(self, confidence: HostConfidence) -> Dict[str, Any]:
        return {
            "field_name": confidence.field_name,
            "confidence_score": confidence.confidence_score,
            "scan_type": confidence.scan_type,
            "data_source": confidence.data_source,
            "method": confidence.method,
            "scan_id": confidence.scan_id,
            "updated_at": self._iso(confidence.updated_at),
            "additional_factors": confidence.additional_factors,
        }

    def _serialize_port_confidence(self, confidence: PortConfidence) -> Dict[str, Any]:
        return {
            "field_name": confidence.field_name,
            "confidence_score": confidence.confidence_score,
            "scan_type": confidence.scan_type,
            "data_source": confidence.data_source,
            "method": confidence.method,
            "scan_id": confidence.scan_id,
            "updated_at": self._iso(confidence.updated_at),
            "additional_factors": confidence.additional_factors,
        }

    def _serialize_conflict(self, conflict: ConflictHistory) -> Dict[str, Any]:
        return {
            "field_name": conflict.field_name,
            "previous_value": conflict.previous_value,
            "previous_confidence": conflict.previous_confidence,
            "previous_scan_id": conflict.previous_scan_id,
            "previous_method": conflict.previous_method,
            "new_value": conflict.new_value,
            "new_confidence": conflict.new_confidence,
            "new_scan_id": conflict.new_scan_id,
            "new_method": conflict.new_method,
            "resolved_at": self._iso(conflict.resolved_at),
        }

    def _build_vulnerability_summary(self, vulnerabilities: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {"total": len(vulnerabilities), "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for vulnerability in vulnerabilities:
            severity = vulnerability.get("severity") or "unknown"
            if severity in summary:
                summary[severity] += 1
        return summary

    def _vulnerability_sort_key(self, vuln: Vulnerability) -> Tuple[int, float, int]:
        severity = enum_value(vuln.severity) or "unknown"
        severity_rank = self.SEVERITY_ORDER.get(severity, self.SEVERITY_ORDER["unknown"])
        last_seen_dt = vuln.last_seen or vuln.first_seen or datetime.utcfromtimestamp(0)
        return (severity_rank, -last_seen_dt.timestamp(), vuln.id)

    def _generate_markdown_report(self, dataset: Dict[str, Any]) -> str:
        manifest = dataset["manifest"]
        hosts = dataset["hosts"]
        scans = dataset["scans"]
        lines = [
            "# BlueStick Host Report",
            "",
            "## Export Metadata",
            f"- Generated: {manifest['generated_at']}",
            f"- Schema Version: {manifest['schema_version']}",
            f"- Hosts: {manifest['counts']['hosts']}",
            f"- Findings: {manifest['counts']['vulnerabilities']}",
            f"- Filters: `{json.dumps(manifest['filters'], sort_keys=True)}`",
            "",
            "## Executive Summary",
            (
                f"This export contains {manifest['counts']['hosts']} hosts, "
                f"{manifest['counts']['hosts_up']} of them currently marked up, with "
                f"{manifest['counts']['open_ports']} open ports and "
                f"{manifest['counts']['vulnerabilities']} recorded vulnerabilities."
            ),
            "",
            "## Priority Hosts",
            "| IP | Hostname | Scope | Risk | Critical | High | Key Services | Follow |",
            "|---|---|---|---:|---:|---:|---|---|",
        ]

        priority_hosts = sorted(
            hosts,
            key=lambda host: (
                -(host.get("risk", {}) or {}).get("risk_score", -1),
                -host["vulnerability_summary"].get("critical", 0),
                -host["vulnerability_summary"].get("high", 0),
                host["identity"]["ip_address"],
            ),
        )[:25]
        for host in priority_hosts:
            scope = "in-scope" if host["scope"]["in_scope"] else "out-of-scope"
            key_services = ", ".join(
                f"{port['port_number']}/{port['service'].get('name') or 'unknown'}"
                for port in host["ports"]
                if port.get("state") == "open"
            ) or "none"
            risk_score = (host.get("risk") or {}).get("risk_score")
            lines.append(
                f"| {host['identity']['ip_address']} | {host['identity'].get('hostname') or ''} | "
                f"{scope} | {risk_score if risk_score is not None else ''} | "
                f"{host['vulnerability_summary']['critical']} | {host['vulnerability_summary']['high']} | "
                f"{key_services} | {host['analyst_context'].get('follow_status') or ''} |"
            )

        lines.extend(["", "## Host Details", ""])
        for host in hosts:
            risk = host.get("risk") or {}
            lines.extend([
                f"### {host['identity']['ip_address']} - {host['identity'].get('hostname') or 'unresolved'}",
                f"- State: {host['identity'].get('state') or 'unknown'}",
                f"- Scope: {'in-scope' if host['scope']['in_scope'] else 'out-of-scope'}",
                f"- OS: {host['os'].get('name') or 'unknown'}",
                f"- First seen: {host['timeline'].get('first_seen') or 'unknown'}",
                f"- Last seen: {host['timeline'].get('last_seen') or 'unknown'}",
                f"- Risk: {risk.get('risk_score', 'n/a')} / {risk.get('risk_level', 'n/a')}",
                "",
                "#### Exposed Services",
                "| Port | Proto | State | Service | Product | Version |",
                "|---|---|---|---|---|---|",
            ])
            for port in host["ports"]:
                lines.append(
                    f"| {port['port_number']} | {port['protocol']} | {port.get('state') or ''} | "
                    f"{port['service'].get('name') or ''} | {port['service'].get('product') or ''} | "
                    f"{port['service'].get('version') or ''} |"
                )

            lines.extend([
                "",
                "#### Vulnerabilities",
                "| Severity | Title | CVE | CVSS | Port | Source | Recommendation |",
                "|---|---|---|---:|---|---|---|",
            ])
            for vulnerability in host["vulnerabilities"]:
                lines.append(
                    f"| {vulnerability.get('severity') or ''} | {vulnerability.get('title') or ''} | "
                    f"{vulnerability.get('cve_id') or ''} | {vulnerability.get('cvss_score') or ''} | "
                    f"{vulnerability.get('port_number') or ''} | {vulnerability.get('source') or ''} | "
                    f"{(vulnerability.get('solution') or '').replace('|', '/')} |"
                )
            if not host["vulnerabilities"]:
                lines.append("|  | No recorded vulnerabilities |  |  |  |  |  |")

            lines.extend(["", "#### Analyst Context"])
            lines.append(f"- Follow status: {host['analyst_context'].get('follow_status') or 'none'}")
            if host["analyst_context"]["notes"]:
                for note in host["analyst_context"]["notes"]:
                    lines.append(f"- Note ({note['status']}): {note['body']}")
            else:
                lines.append("- Notes: none")
            lines.extend([
                "",
                "#### Suggested Next Tests",
            ])
            for next_test in self._suggest_next_tests(host):
                lines.append(f"- {next_test}")
            lines.append("")

        lines.extend([
            "## Scan Inventory",
            "| Scan ID | Tool | File | Created |",
            "|---|---|---|---|",
        ])
        for scan_id in sorted(scans.keys(), key=lambda value: int(value)):
            scan = scans[scan_id]
            lines.append(
                f"| {scan_id} | {scan.get('tool_name') or ''} | {scan.get('filename') or ''} | {scan.get('created_at') or ''} |"
            )

        lines.extend(["", "## Appendix", "- Confidence and artifact details are available in the companion files within this bundle."])
        return "\n".join(lines) + "\n"

    def _generate_hosts_csv(self, hosts: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["IP Address", "Hostname", "State", "Scope", "Risk Score", "Critical", "High", "Open Services", "Follow Status"])
        for host in hosts:
            _safe_csv_row(writer, [
                host["identity"]["ip_address"],
                host["identity"].get("hostname") or "",
                host["identity"].get("state") or "",
                "in-scope" if host["scope"]["in_scope"] else "out-of-scope",
                (host.get("risk") or {}).get("risk_score") or "",
                host["vulnerability_summary"]["critical"],
                host["vulnerability_summary"]["high"],
                ", ".join(
                    f"{port['port_number']}/{port['service'].get('name') or 'unknown'}"
                    for port in host["ports"]
                    if port.get("state") == "open"
                ),
                host["analyst_context"].get("follow_status") or "",
            ])
        return output.getvalue()

    def _generate_findings_csv(self, hosts: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["IP Address", "Hostname", "Severity", "Title", "CVE", "CVSS", "Port", "Service", "Source", "Recommendation"])
        for host in hosts:
            for vulnerability in host["vulnerabilities"]:
                _safe_csv_row(writer, [
                    host["identity"]["ip_address"],
                    host["identity"].get("hostname") or "",
                    vulnerability.get("severity") or "",
                    vulnerability.get("title") or "",
                    vulnerability.get("cve_id") or "",
                    vulnerability.get("cvss_score") or "",
                    vulnerability.get("port_number") or "",
                    vulnerability.get("service_name") or "",
                    vulnerability.get("source") or "",
                    vulnerability.get("solution") or "",
                ])
        return output.getvalue()

    def _generate_scans_csv(self, scans: Dict[str, Any]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Scan ID", "Filename", "Tool", "Type", "Created At", "Start Time", "End Time"])
        for scan_id in sorted(scans.keys(), key=lambda value: int(value)):
            scan = scans[scan_id]
            _safe_csv_row(writer, [
                scan_id,
                scan.get("filename") or "",
                scan.get("tool_name") or "",
                scan.get("scan_type") or "",
                scan.get("created_at") or "",
                scan.get("start_time") or "",
                scan.get("end_time") or "",
            ])
        return output.getvalue()

    def _build_schema_reference(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "record_type": "host",
            "format": "ndjson",
            "top_level_sections": [
                "identity",
                "scope",
                "timeline",
                "os",
                "ports",
                "host_scripts",
                "vulnerabilities",
                "risk",
                "analyst_context",
                "confidence",
            ],
            "enums": {
                "vulnerability_severity": ["critical", "high", "medium", "low", "info", "unknown"],
                "follow_status": ["watching", "in_review", "reviewed"],
                "note_status": ["open", "in_progress", "resolved"],
            },
        }

    def _suggest_next_tests(self, host: Dict[str, Any]) -> List[str]:
        suggestions: List[str] = []
        open_ports = [port for port in host["ports"] if port.get("state") == "open"]
        service_names = {((port.get("service") or {}).get("name") or "").lower() for port in open_ports}
        if "http" in service_names or "https" in service_names:
            suggestions.extend([
                "Web content discovery and hidden path enumeration",
                "TLS and HTTP header configuration validation",
            ])
        if "ssh" in service_names:
            suggestions.append("SSH authentication surface review and version-specific checks")
        if "smb" in service_names or any(port["port_number"] == 445 for port in open_ports):
            suggestions.append("SMB share, signing, and authentication policy enumeration")
        if host["vulnerability_summary"]["critical"] or host["vulnerability_summary"]["high"]:
            suggestions.append("Validate high-severity findings manually before exploitation")
        if not suggestions:
            suggestions.append("Service enumeration and banner validation")
        return suggestions

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    @staticmethod
    def _parse_json_list(value: Optional[str]) -> List[Any]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (TypeError, ValueError):
            return [value]

@router.get("/hosts/csv")
def generate_hosts_csv_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate CSV report of hosts based on filters"""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    csv_content = generator.generate_csv_report(hosts)
    
    filename = f"hosts_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/hosts/html")
def generate_hosts_html_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate HTML report of hosts based on filters"""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    html_content = generator.generate_html_report(hosts, filter_kwargs)
    
    filename = f"hosts_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    
    return Response(
        content=html_content,
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/hosts/json")
def generate_hosts_json_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate JSON report of hosts based on filters"""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    report_data = generator.generate_json_report(hosts)
    
    filename = f"hosts_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    return Response(
        content=json.dumps(report_data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/hosts/agent-package")
def generate_hosts_agent_package_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate a ZIP package with host data optimized for agentic workflows."""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    package_content = generator.generate_agent_package(hosts, filter_kwargs)

    filename = f"networkmapper_agent_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=package_content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/hosts/markdown-bundle")
def generate_hosts_markdown_bundle_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate a ZIP bundle with a Markdown report and companion flat files."""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    bundle_content = generator.generate_markdown_bundle(hosts, filter_kwargs)

    filename = f"networkmapper_markdown_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=bundle_content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
