from typing import List, Optional, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, distinct, func
from app.db.session import get_db
from app.db import models
from app.db.models_vulnerability import Vulnerability, enum_value, SEVERITY_KEYS
from app.schemas.schemas import Host
from app.core.config import settings
from app.services.report_templates import ReportTemplates
from app.services.subnet_insight_service import resolve_host_locations, compute_subnet_insights
from app.services.attention_service import compute_site_attention
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.services.host_serialization import _serialize_follow, _serialize_note  # CR4-2
from app.api.v1.endpoints.hosts import _build_filtered_host_query, HostFilterParams
from app.db.models import HostFollow
from app.db.models_confidence import HostConfidence, PortConfidence, ConflictHistory
import base64
import io
import csv
import ipaddress
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import html

logger = logging.getLogger(__name__)

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
        # Lazily-computed, report-lifetime caches so the host→site/subnet map
        # and the hotspots roll-up are each built at most once per report even
        # though multiple format sections consume them.
        self._host_loc_cache: Optional[Dict[int, Dict[str, Any]]] = None
        self._hotspots_cache: Optional[Dict[str, Any]] = None
        # Set by get_hosts_for_report: True when the filter matched more than
        # MAX_REPORT_HOSTS and the in-memory report was capped.  Surfaced to
        # the user (HTML banner / JSON flag / X-Report-Truncated header) so a
        # capped report is never mistaken for a complete one.
        self.report_truncated = False

    # Maximum number of hosts an in-memory report (PDF/HTML/JSON, zips) can
    # include to prevent OOM.  Configurable; the streaming CSV ignores it.
    MAX_REPORT_HOSTS = settings.REPORT_MAX_HOSTS

    # --- Site / subnet enrichment (shared across formats) -----------------

    def _host_locations(self) -> Dict[int, Dict[str, Any]]:
        """host_id → {subnet_id, cidr, site, site_id, scope_name} for the
        project's in-scope hosts (most-specific subnet wins).  Single source
        with the subnet-insights view via ``resolve_host_locations``."""
        if self._host_loc_cache is None:
            self._host_loc_cache = (
                resolve_host_locations(self.db, self.project_id) if self.project_id else {}
            )
        return self._host_loc_cache

    def _host_site(self, host_id: int) -> str:
        loc = self._host_locations().get(host_id)
        return (loc.get("site") or "") if loc else ""

    def _host_subnet(self, host_id: int) -> str:
        loc = self._host_locations().get(host_id)
        return (loc.get("cidr") or "") if loc else ""

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
            # Tags ride along on the inventory CSV (a queryable working-set
            # dimension analysts filter by); eager-load to avoid an N+1.
            selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
            # Fetch one past the cap so we can detect (and surface) truncation
            # without a separate COUNT.
        ).distinct().limit(self.MAX_REPORT_HOSTS + 1)

        rows = query.all()
        self.report_truncated = len(rows) > self.MAX_REPORT_HOSTS
        return rows[: self.MAX_REPORT_HOSTS]

    def _filtered_host_id_query(self, filters: Dict[str, Any]):
        """Just the matching host ids, ordered — the cheap driver for the
        streaming CSV (ints only; the per-chunk hydrate loads the heavy
        relationships)."""
        return (
            _build_filtered_host_query(
                self.db, self.current_user, **filters, project_id=self.project_id,
            )
            .with_entities(models.Host.id)
            .distinct()
        )

    def iter_inventory_csv(self, filters: Dict[str, Any], chunk_size: int = None):
        """Yield the Host Inventory CSV incrementally over a chunked cursor —
        no MAX_REPORT_HOSTS cap, bounded memory (one chunk hydrated at a time).

        Drives the streaming CSV export so a project with >cap hosts still gets
        a complete inventory.  Header first, then rows in id-ordered chunks.
        ``_host_locations`` is one project-wide query built up front (cached);
        each chunk re-loads only its own hosts' ports/vulns/tags/scan history.
        """
        chunk_size = chunk_size or settings.REPORT_STREAM_CHUNK
        # Header row.
        _hdr = io.StringIO()
        csv.writer(_hdr).writerow(self.INVENTORY_CSV_HEADER)
        yield _hdr.getvalue()

        host_ids = [row[0] for row in self._filtered_host_id_query(filters).all()]
        for start in range(0, len(host_ids), chunk_size):
            chunk_ids = host_ids[start:start + chunk_size]
            hosts = (
                self.db.query(models.Host)
                .filter(models.Host.id.in_(chunk_ids))
                .options(
                    selectinload(models.Host.ports),
                    selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
                    selectinload(models.Host.last_updated_scan),
                    selectinload(models.Host.notes),
                    selectinload(models.Host.vulnerabilities),
                    selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
                )
                .all()
            )
            # Preserve the id-query ordering within the chunk.
            order = {hid: i for i, hid in enumerate(chunk_ids)}
            hosts.sort(key=lambda h: order.get(h.id, 0))
            yield self._inventory_csv_rows(hosts)
            # Release the chunk's ORM objects so peak memory stays ~chunk_size.
            self.db.expunge_all()
    
    @staticmethod
    def _host_vuln_counts(host: models.Host) -> Dict[str, int]:
        """Per-severity vulnerability counts from the host's loaded
        ``vulnerabilities`` relationship (no extra query — already
        selectin-loaded by ``get_hosts_for_report``)."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for v in (host.vulnerabilities or []):
            sev = enum_value(v.severity)
            if sev in counts:
                counts[sev] += 1
        return counts

    @staticmethod
    def _host_tags(host: models.Host) -> str:
        names = []
        for a in (getattr(host, "tag_assignments", None) or []):
            tag = getattr(a, "tag", None)
            if tag is not None and tag.name:
                names.append(tag.name)
        return ", ".join(sorted(names, key=str.lower))

    # Enriched beyond identity with the columns analysts actually triage on
    # (severity counts, SMB signing, tags, recency).  Shared by the single-shot
    # and streaming CSV paths so the two can never drift.
    INVENTORY_CSV_HEADER = [
        'IP Address', 'Hostname', 'State', 'Site', 'Subnet',
        'OS Name', 'OS Family', 'OS Type', 'OS Accuracy', 'SMB Signing',
        'Open Ports', 'Total Ports', 'Services',
        'Critical', 'High', 'Medium', 'Low',
        'Tags', 'Notes', 'Last Seen', 'Scan File', 'Scan Date',
    ]

    def generate_csv_report(self, hosts: List[models.Host]) -> str:
        """Generate the Host Inventory CSV — header + a flat, queryable row per
        host.

        This is the "concise identification" report: who/where/what + the
        risk-triage signals (vuln counts, SMB-signing posture, tags) an
        analyst or manager needs to say "these hosts are problematic",
        without the full per-host findings detail (that lives in the
        Comprehensive report).
        """
        output = io.StringIO()
        csv.writer(output).writerow(self.INVENTORY_CSV_HEADER)
        return output.getvalue() + self._inventory_csv_rows(hosts)

    def _inventory_csv_rows(self, hosts: List[models.Host]) -> str:
        """Inventory CSV body rows (no header) for ``hosts`` — the unit the
        streaming path yields per chunk."""
        output = io.StringIO()
        writer = csv.writer(output)
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

            vc = self._host_vuln_counts(host)
            last_seen_str = host.last_seen.strftime('%Y-%m-%d %H:%M:%S') if host.last_seen else ''

            _safe_csv_row(writer, [
                host.ip_address,
                host.hostname or '',
                host.state or '',
                self._host_site(host.id),
                self._host_subnet(host.id),
                host.os_name or '',
                host.os_family or '',
                host.os_type or '',
                host.os_accuracy or '',
                host.smb_signing or '',
                open_ports_str,
                total_ports,
                services_str,
                vc['critical'],
                vc['high'],
                vc['medium'],
                vc['low'],
                self._host_tags(host),
                len(host.notes or []),
                last_seen_str,
                scan_filename,
                scan_date_str
            ])

        return output.getvalue()
    
    def generate_html_report(
        self, hosts: List[models.Host], filters: Dict[str, Any],
        report_type: str = "comprehensive",
    ) -> str:
        """Generate the HTML host report.

        ``report_type``:
          * ``"comprehensive"`` (default) — the full security report: summary +
            metrics + exposure charts + **Findings** + **Site/Subnet Hotspots** +
            the host table.  The artifact a security review hands over.
          * ``"inventory"`` — the concise "these hosts are problematic" list:
            summary + metrics + exposure + host table only (no project-wide
            findings/hotspots roll-ups).
        """
        is_comprehensive = report_type != "inventory"
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

        # Comprehensive-only sections.  Computed here (not inline in the main
        # f-string) so the inventory variant can omit them entirely — and so
        # the per-finding "Evidence" gallery (screenshots attached to notes/
        # findings) can later slot into the Findings section without touching
        # the page skeleton.
        if is_comprehensive:
            hotspots_nav = '<a href="#hotspots">Hotspots</a>'
            findings_nav = '<a href="#findings">Findings</a>'
            hotspots_section = f"""
    <div class="section" id="hotspots">
        <div class="section-header">Site &amp; Subnet Hotspots</div>
        <div class="section-content">
            <p class="muted">Worst-first ranking of sites and subnets by exposure (severity-weighted active findings, scaled by site criticality), neglect, and hygiene (end-of-life OS, certificate issues, weak auth, risky services). Project-wide — not limited to the filtered hosts above.</p>
            {self._generate_hotspots_html()}
        </div>
    </div>"""
            findings_section = f"""
    <div class="section" id="findings">
        <div class="section-header">Findings</div>
        <div class="section-content">
            {self._generate_findings_html(hosts)}
        </div>
    </div>"""
        else:
            hotspots_nav = findings_nav = ""
            hotspots_section = findings_section = ""

        # Loud truncation banner — a capped report must never read as complete.
        if self.report_truncated:
            truncation_banner = (
                '<div class="section" style="border-left:4px solid #c0392b;background:#fdecea;'
                'padding:12px 16px;margin:0 0 16px;color:#7b241c;">'
                f'<strong>⚠ Report truncated.</strong> Your filters matched more than '
                f'{self.MAX_REPORT_HOSTS:,} hosts; this report includes only the first '
                f'{self.MAX_REPORT_HOSTS:,}. Narrow the filters, or use the CSV inventory export '
                '(it streams the full set).</div>'
            )
        else:
            truncation_banner = ""

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
        {hotspots_nav}
        {findings_nav}
        <a href="#hosts">Host Details</a>
    </nav>

    {truncation_banner}

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

    {hotspots_section}
    {findings_section}

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
                            <th>Site</th>
                            <th>Subnet</th>
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

            site_label = html.escape(self._host_site(host.id) or '—')
            subnet_label = html.escape(self._host_subnet(host.id) or '—')

            rows.append(f"""
                <tr class="host-row">
                    <td>{html.escape(host.ip_address)}</td>
                    <td>{html.escape(host.hostname or '')}</td>
                    <td class="{state_class}">{html.escape(host.state or '')}</td>
                    <td>{site_label}</td>
                    <td>{subnet_label}</td>
                    <td>{html.escape(host.os_name or '')}</td>
                    <td class="port-list">{html.escape(open_ports_str)}</td>
                    <td class="service-list">{html.escape(services_str)}</td>
                    <td>{scan_label}</td>
                </tr>
            """)
        
        return ''.join(rows)

    def generate_json_report(
        self, hosts: List[models.Host], report_type: str = "comprehensive",
    ) -> Dict[str, Any]:
        """Generate JSON host report.  ``report_type='inventory'`` omits the
        project-wide findings + hotspots roll-ups (host records are unchanged)."""
        is_comprehensive = report_type != "inventory"
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "report_type": "comprehensive" if is_comprehensive else "inventory",
            "summary": {
                "total_hosts": len(hosts),
                "hosts_up": len([h for h in hosts if h.state == 'up']),
                "hosts_down": len([h for h in hosts if h.state == 'down']),
                "total_open_ports": sum(len([p for p in (h.ports or []) if p.state == 'open']) for h in hosts),
                # True when the filter matched more than the in-memory cap and
                # this payload was truncated to ``cap`` hosts (use the CSV
                # inventory for the complete set).
                "truncated": self.report_truncated,
                "host_cap": self.MAX_REPORT_HOSTS,
            },
            "hosts": []
        }
        
        for host in hosts:
            location = self._host_locations().get(host.id)
            host_data = {
                "id": host.id,
                "ip_address": host.ip_address,
                "hostname": host.hostname,
                "state": host.state,
                "location": {
                    "site": location.get("site") if location else None,
                    "subnet": location.get("cidr") if location else None,
                    "scope_name": location.get("scope_name") if location else None,
                    "in_scope": location is not None,
                },
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

        if is_comprehensive:
            report_data["findings"] = self._findings_for_report(hosts)
            report_data["hotspots"] = self._build_hotspots()
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
        comments_by_finding = self._finding_comments([f.id for f in findings])
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
                # The note thread this finding was promoted from — its image
                # attachments are the finding's visual evidence.  Just the int
                # here (cheap, harmless in JSON exports); the HTML/PDF report
                # resolves it to embedded images, the other formats ignore it.
                "evidence_annotation_id": f.evidence_annotation_id,
                # The finding's own comment/evidence thread (repro steps,
                # rationale, discussion the analyst added while refining it).
                # Text rides into every format; screenshots are HTML/PDF-only.
                "comments": comments_by_finding.get(f.id, []),
            }
            for f in findings
        ]
        out.sort(key=lambda x: self.SEVERITY_ORDER.get(x["severity"], 5))
        return out

    def _finding_comments(self, finding_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Map finding id → its comment thread (author + body, oldest-first) —
        the discussion/repro/rationale analysts add on the Findings page.  This
        is the textual half of "reports include evidence when attached to a
        finding"; the image half is _finding_evidence_images."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        ids = [fid for fid in finding_ids if fid]
        if not ids:
            return out
        rows = (
            self.db.query(models.Annotation)
            .options(selectinload(models.Annotation.author))
            .filter(models.Annotation.finding_id.in_(ids))
            .order_by(models.Annotation.created_at.asc(), models.Annotation.id.asc())
            .all()
        )
        for ann in rows:
            author = None
            if ann.author:
                author = ann.author.full_name or ann.author.username
            out.setdefault(ann.finding_id, []).append({
                "author": author,
                "body": ann.body or "",
                "created_at": ann.created_at.isoformat() if ann.created_at else None,
            })
        return out

    # Total bytes of evidence images embedded into one report, so a project
    # with many large screenshots can't produce a runaway-sized PDF.
    _EVIDENCE_IMAGE_BUDGET = 20 * 1024 * 1024

    def _finding_evidence_images(self, findings: List[Dict[str, Any]]) -> Dict[int, List[Tuple[str, str]]]:
        """Map finding id → [(data_uri, caption), …] of image attachments that
        are evidence for that finding, from BOTH sources: the promoted source-
        note thread (``evidence_annotation_id`` root + replies) and the
        finding's own comment thread (``annotations.finding_id``).  Images are
        read from disk and inlined as base64 data URIs (WeasyPrint never fetches
        a resource — see the PDF endpoint's deny-all fetcher).  One shared
        ``_EVIDENCE_IMAGE_BUDGET`` across all findings so a screenshot-heavy
        project can't produce a runaway-sized PDF."""
        out: Dict[int, List[Tuple[str, str]]] = {}
        if not findings:
            return out
        base = Path(settings.UPLOAD_DIR) / "note_attachments"
        try:
            base_resolved = base.resolve()
        except OSError:
            return out
        used = 0
        for f in findings:
            fid = f.get("id")
            root = f.get("evidence_annotation_id")
            # All annotation ids whose attachments count as this finding's
            # evidence: the source-note thread (root + replies) and every
            # comment on the finding itself.
            ann_q = self.db.query(models.Annotation.id).filter(
                or_(
                    models.Annotation.finding_id == fid,
                    models.Annotation.id == root,
                    models.Annotation.thread_root_id == root,
                ) if root else (models.Annotation.finding_id == fid)
            )
            ann_ids = [r[0] for r in ann_q.all()]
            if not ann_ids:
                continue
            atts = (
                self.db.query(models.NoteAttachment)
                .filter(models.NoteAttachment.annotation_id.in_(ann_ids))
                .order_by(models.NoteAttachment.id)
                .all()
            )
            uris: List[Tuple[str, str]] = []
            for att in atts:
                if used >= self._EVIDENCE_IMAGE_BUDGET:
                    break
                try:
                    target = (base / att.storage_path).resolve()
                    target.relative_to(base_resolved)
                except (ValueError, OSError):
                    continue
                if not target.exists() or not target.is_file():
                    continue
                try:
                    data = target.read_bytes()
                except OSError:
                    continue
                used += len(data)
                b64 = base64.b64encode(data).decode("ascii")
                uris.append((f"data:{att.content_type};base64,{b64}", att.filename))
            if uris:
                out[fid] = uris
        return out

    def _generate_findings_html(self, hosts: List[models.Host]) -> str:
        """Findings table + an Evidence gallery (embedded screenshots) for the
        HTML/PDF report (severity-ordered)."""
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
        table = (
            '<table class="data-table"><thead><tr>'
            "<th>Severity</th><th>Finding</th><th>Status</th>"
            "<th>Source</th><th>Owner</th><th>Affected hosts</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )

        # Evidence section — per finding, the analyst's comment thread (repro
        # steps / rationale / discussion) and embedded screenshots, from both
        # the promoted source note and the finding's own thread.  Only findings
        # that actually have comments or images appear.
        images_by_finding = self._finding_evidence_images(findings)
        blocks = []
        for f in findings:
            fid = f.get("id")
            comments = f.get("comments") or []
            imgs = images_by_finding.get(fid)
            if not comments and not imgs:
                continue
            parts = [f'<p style="font-weight:600;margin:0 0 6px;">{html.escape(str(f["title"]))}</p>']
            for c in comments:
                who = html.escape(str(c.get("author") or "—"))
                body = html.escape(str(c.get("body") or "")).replace("\n", "<br/>")
                parts.append(
                    f'<div style="margin:0 0 8px;padding:6px 10px;border-left:3px solid var(--border);">'
                    f'<span class="muted" style="font-size:0.8em;">{who}</span><br/>{body}</div>'
                )
            if imgs:
                thumbs = "".join(
                    f'<figure style="margin:0;max-width:280px;">'
                    f'<img src="{uri}" alt="{html.escape(cap)}" '
                    f'style="max-width:280px;max-height:280px;border:1px solid var(--border);border-radius:6px;" />'
                    f'<figcaption class="muted" style="font-size:0.8em;word-break:break-all;">{html.escape(cap)}</figcaption>'
                    f'</figure>'
                    for uri, cap in imgs
                )
                parts.append(f'<div style="display:flex;flex-wrap:wrap;gap:10px;">{thumbs}</div>')
            blocks.append(f'<div style="margin-top:14px;">{"".join(parts)}</div>')
        if not blocks:
            return table
        return (
            f"{table}"
            f'<h3 style="margin-top:20px;">Evidence</h3>'
            f'<p class="muted">Analyst comments and screenshots attached to each finding.</p>'
            f'{"".join(blocks)}'
        )

    # --- Site / subnet hotspots (shared across formats) -------------------

    def _build_hotspots(self, top_n: int = 10) -> Dict[str, Any]:
        """Worst-first site + subnet hotspots, reusing the live attention /
        subnet-insights services so the report agrees with the dashboards.

        Project-wide (not filtered to the report's host subset) on purpose: a
        "where are the worst ranges" section is only meaningful against the
        whole engagement, and a filtered export shouldn't redefine which site
        is on fire.  Trimmed to ``top_n`` each; cached for the report's life.
        """
        if self._hotspots_cache is None:
            if not self.project_id:
                self._hotspots_cache = {
                    "sites_adopted": False, "subnets_adopted": False,
                    "sites": [], "subnets": [], "totals": None,
                }
            else:
                sites = compute_site_attention(self.db, self.project_id)
                # Only the worst top_n are rendered in the report — ask for
                # exactly that page rather than the default 50.
                subnets = compute_subnet_insights(self.db, self.project_id, limit=top_n)
                self._hotspots_cache = {
                    "sites_adopted": bool(sites.get("adopted")),
                    "subnets_adopted": bool(subnets.get("adopted")),
                    "sites": (sites.get("sites") or [])[:top_n],
                    "subnets": (subnets.get("subnets") or [])[:top_n],
                    "totals": subnets.get("totals"),
                }
        return self._hotspots_cache

    def _generate_hotspots_html(self) -> str:
        """HTML fragment: a Sites table + a Subnets table, worst-first."""
        data = self._build_hotspots()
        parts: List[str] = []

        sites = data["sites"]
        if data["sites_adopted"] and sites:
            rows = []
            for s in sites:
                name = "Unassigned" if s.get("unassigned") else (s.get("site") or "—")
                tier = "—" if s.get("criticality_tier") is None else f"T{s['criticality_tier']}"
                sev = s["exposure"]["by_severity"]
                gap = s.get("coverage_gap")
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(name))}</td>"
                    f"<td>{tier}</td>"
                    f"<td>{s.get('host_count', 0)}{f' (−{gap})' if gap else ''}</td>"
                    f"<td>{s['exposure'].get('weighted_score', 0)}</td>"
                    f"<td>{sev.get('critical', 0)}</td>"
                    f"<td>{sev.get('high', 0)}</td>"
                    f"<td>{s['neglect'].get('unowned_active_findings', 0)}</td>"
                    f"<td>{html.escape(str(s['recommended_action'].get('text', '')))}</td>"
                    "</tr>"
                )
            parts.append(
                '<h4>Site hotspots</h4>'
                '<table class="data-table"><thead><tr>'
                '<th>Site</th><th>Tier</th><th>Hosts</th><th>Exposure</th>'
                '<th>Crit</th><th>High</th><th>Unowned</th><th>Recommended action</th>'
                f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            )
        elif self.project_id:
            parts.append('<p class="muted">No sites defined — assign subnets to sites to rank site hotspots.</p>')

        subnets = data["subnets"]
        if data["subnets_adopted"] and subnets:
            rows = []
            for s in subnets:
                site = "—" if not s.get("site") else s["site"]
                tier = "—" if s.get("criticality_tier") is None else f"T{s['criticality_tier']}"
                sev = s["exposure"]["by_severity"]
                hy = s["hygiene"]
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(s.get('cidr', '')))}</td>"
                    f"<td>{html.escape(str(site))}</td>"
                    f"<td>{tier}</td>"
                    f"<td>{s.get('host_count', 0)}</td>"
                    f"<td>{s['exposure'].get('weighted_score', 0)}</td>"
                    f"<td>{sev.get('critical', 0)}</td>"
                    f"<td>{hy.get('eol_os_hosts', 0)}</td>"
                    f"<td>{hy.get('cert_issue_hosts', 0)}</td>"
                    f"<td>{hy.get('weak_auth_hosts', 0)}</td>"
                    f"<td>{hy.get('risky_service_hosts', 0)}</td>"
                    f"<td>{html.escape(str(s['recommended_action'].get('text', '')))}</td>"
                    "</tr>"
                )
            parts.append(
                '<h4>Subnet hotspots</h4>'
                '<table class="data-table"><thead><tr>'
                '<th>Subnet</th><th>Site</th><th>Tier</th><th>Hosts</th><th>Exposure</th>'
                '<th>Crit</th><th>EOL</th><th>Cert</th><th>Weak</th><th>Risky</th>'
                '<th>Recommended action</th>'
                f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            )
        elif self.project_id:
            parts.append('<p class="muted">No scoped subnets — define a scope to rank subnet hotspots.</p>')

        return "".join(parts) if parts else '<p class="muted">No site or subnet data available.</p>'

    def _hotspots_markdown_lines(self) -> List[str]:
        """Markdown lines for the Site & Subnet Hotspots section."""
        data = self._build_hotspots()
        lines: List[str] = [
            "## Site & Subnet Hotspots",
            "",
            "Worst-first by exposure (severity-weighted active findings, scaled by site"
            " criticality), neglect, and hygiene (EOL OS / cert issues / weak auth / risky"
            " services). Project-wide, not limited to the filtered hosts.",
            "",
        ]
        if data["sites_adopted"] and data["sites"]:
            lines += [
                "### Site hotspots",
                "| Site | Tier | Hosts | Exposure | Crit | High | Unowned | Action |",
                "|---|---|---:|---:|---:|---:|---:|---|",
            ]
            for s in data["sites"]:
                name = "Unassigned" if s.get("unassigned") else (s.get("site") or "—")
                tier = "—" if s.get("criticality_tier") is None else f"T{s['criticality_tier']}"
                sev = s["exposure"]["by_severity"]
                gap = s.get("coverage_gap")
                hosts_cell = f"{s.get('host_count', 0)}" + (f" (−{gap})" if gap else "")
                action = (s["recommended_action"].get("text") or "").replace("|", "/")
                lines.append(
                    f"| {name} | {tier} | {hosts_cell} | {s['exposure'].get('weighted_score', 0)} | "
                    f"{sev.get('critical', 0)} | {sev.get('high', 0)} | "
                    f"{s['neglect'].get('unowned_active_findings', 0)} | {action} |"
                )
            lines.append("")
        elif self.project_id:
            lines += ["_No sites defined — assign subnets to sites to rank site hotspots._", ""]

        if data["subnets_adopted"] and data["subnets"]:
            lines += [
                "### Subnet hotspots",
                "| Subnet | Site | Tier | Hosts | Exposure | Crit | EOL | Cert | Weak | Risky | Action |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
            for s in data["subnets"]:
                site = s.get("site") or "—"
                tier = "—" if s.get("criticality_tier") is None else f"T{s['criticality_tier']}"
                sev = s["exposure"]["by_severity"]
                hy = s["hygiene"]
                action = (s["recommended_action"].get("text") or "").replace("|", "/")
                lines.append(
                    f"| {s.get('cidr', '')} | {site} | {tier} | {s.get('host_count', 0)} | "
                    f"{s['exposure'].get('weighted_score', 0)} | {sev.get('critical', 0)} | "
                    f"{hy.get('eol_os_hosts', 0)} | {hy.get('cert_issue_hosts', 0)} | "
                    f"{hy.get('weak_auth_hosts', 0)} | {hy.get('risky_service_hosts', 0)} | {action} |"
                )
            lines.append("")
        elif self.project_id:
            lines += ["_No scoped subnets — define a scope to rank subnet hotspots._", ""]

        return lines

    def generate_agent_package(self, hosts: List[models.Host], filters: Dict[str, Any]) -> bytes:
        """Generate a ZIP package optimized for agentic workflows."""
        dataset, artifacts = self._build_export_dataset(hosts, filters)

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(dataset["manifest"], indent=2))
            bundle.writestr("schema.json", json.dumps(self._build_schema_reference(), indent=2))
            bundle.writestr("scans.json", json.dumps(dataset["scans"], indent=2))
            bundle.writestr("hotspots.json", json.dumps(self._build_hotspots(), indent=2, default=str))
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
            bundle.writestr("hotspots.json", json.dumps(self._build_hotspots(), indent=2, default=str))
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
            # True when the filter matched more than the host cap and this
            # bundle was truncated (use the streaming CSV for the full set).
            "truncated": self.report_truncated,
            "host_cap": self.MAX_REPORT_HOSTS,
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
                self.db.query(
                    models.HostSubnetMapping.host_id,
                    models.Subnet.cidr,
                    models.Subnet.site,
                    models.Scope.name,
                )
                .join(models.Subnet, models.HostSubnetMapping.subnet_id == models.Subnet.id)
                .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
                .filter(models.HostSubnetMapping.host_id.in_(host_ids))
                .all()
            )
            for host_id, cidr, site, scope_name in subnet_rows:
                subnet_map.setdefault(host_id, []).append(
                    {"cidr": cidr, "site": site, "scope_name": scope_name}
                )

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
                .filter(ConflictHistory.host_id.in_(host_ids))
                .order_by(ConflictHistory.resolved_at.desc())
                .all()
            )
            for conflict in host_conflicts:
                host_conflicts_map.setdefault(conflict.host_id, []).append(conflict)
        for chunk in _id_chunks(port_ids):
            port_conflicts = (
                self.db.query(ConflictHistory)
                .filter(ConflictHistory.port_id.in_(chunk))
                .order_by(ConflictHistory.resolved_at.desc())
                .all()
            )
            for conflict in port_conflicts:
                port_conflicts_map.setdefault(conflict.port_id, []).append(conflict)

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
        primary_site = self._primary_site(subnet_entries)
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
                "site": primary_site,
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
            "| IP | Hostname | Site | Scope | Risk | Critical | High | Key Services | Follow |",
            "|---|---|---|---|---:|---:|---:|---|---|",
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
            site = host["scope"].get("site") or "—"
            lines.append(
                f"| {host['identity']['ip_address']} | {host['identity'].get('hostname') or ''} | "
                f"{site} | {scope} | {risk_score if risk_score is not None else ''} | "
                f"{host['vulnerability_summary']['critical']} | {host['vulnerability_summary']['high']} | "
                f"{key_services} | {host['analyst_context'].get('follow_status') or ''} |"
            )

        lines.append("")
        lines.extend(self._hotspots_markdown_lines())

        lines.extend(["", "## Host Details", ""])
        for host in hosts:
            risk = host.get("risk") or {}
            lines.extend([
                f"### {host['identity']['ip_address']} - {host['identity'].get('hostname') or 'unresolved'}",
                f"- State: {host['identity'].get('state') or 'unknown'}",
                f"- Site: {host['scope'].get('site') or 'unassigned'}",
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
        writer.writerow(["IP Address", "Hostname", "State", "Site", "Subnet", "Scope", "Risk Score", "Critical", "High", "Open Services", "Follow Status"])
        for host in hosts:
            subnets = host["scope"].get("subnets") or []
            subnet_str = ", ".join(s.get("cidr") for s in subnets if s.get("cidr"))
            _safe_csv_row(writer, [
                host["identity"]["ip_address"],
                host["identity"].get("hostname") or "",
                host["identity"].get("state") or "",
                host["scope"].get("site") or "",
                subnet_str,
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
    def _primary_site(subnet_entries: List[Dict[str, Optional[str]]]) -> Optional[str]:
        """Pick the site of the most-specific (longest-prefix) subnet a host
        belongs to — matches the host→site rule used everywhere else so the
        bundle agrees with the dashboard on which site owns a host."""
        best_prefix = -1
        best_site: Optional[str] = None
        for entry in subnet_entries:
            site = entry.get("site")
            if not site:
                continue
            try:
                prefixlen = ipaddress.ip_network(entry.get("cidr"), strict=False).prefixlen
            except (ValueError, TypeError):
                prefixlen = 0
            if prefixlen > best_prefix:
                best_prefix = prefixlen
                best_site = site
        return best_site

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
    # Stream the inventory over a chunked cursor — no host cap, bounded memory,
    # so a project with >cap hosts still gets a complete CSV.
    filename = f"hosts_inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generator.iter_inventory_csv(filter_kwargs),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.get("/hosts/html")
def generate_hosts_html_report(
    filters: HostFilterParams = Depends(),
    report_type: str = Query(
        "comprehensive",
        pattern="^(inventory|comprehensive)$",
        description="'comprehensive' (full security report: findings + hotspots + host detail) or 'inventory' (concise host list, no project-wide roll-ups).",
    ),
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
    html_content = generator.generate_html_report(hosts, filter_kwargs, report_type)

    filename = f"hosts_{report_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

    return Response(
        content=html_content,
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Report-Truncated": "true" if generator.report_truncated else "false",
        },
    )


@router.get("/hosts/pdf")
def generate_hosts_pdf_report(
    filters: HostFilterParams = Depends(),
    report_type: str = Query(
        "comprehensive",
        pattern="^(inventory|comprehensive)$",
        description="'comprehensive' or 'inventory' — same content as the HTML report, rendered to PDF.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate a PDF host report (the HTML report rendered via WeasyPrint).

    The natural manager/handover artifact.  Renders the same HTML the /html
    route produces, then converts to PDF.  A deny-all ``url_fetcher`` blocks
    every external/file resource at render time: untrusted scan data flows
    into this HTML, so WeasyPrint must never be allowed to fetch a remote URL
    or read a local file an attacker-controlled value could point at (defence
    in depth over the already-escaped template).
    """
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    hosts = generator.get_hosts_for_report(filter_kwargs)
    html_content = generator.generate_html_report(hosts, filter_kwargs, report_type)

    try:
        # OSError (not just ImportError) when the native Pango/Cairo/GObject
        # libraries are missing — weasyprint dlopen's them at import time.
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        logger.warning("PDF export unavailable — weasyprint failed to load: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="PDF export is unavailable: the server's PDF rendering libraries are not installed.",
        )

    def _deny_all_fetcher(url, *args, **kwargs):
        # Block every resource resolution — no remote URLs, no file:// reads.
        # WeasyPrint treats any exception from the fetcher as an unfetchable
        # resource; our template is self-contained (inline CSS) so this should
        # never fire — it's a hard backstop against future template drift.
        raise ValueError(f"External resource blocked in PDF report: {url}")

    pdf_bytes = HTML(string=html_content, url_fetcher=_deny_all_fetcher).write_pdf()
    filename = f"hosts_{report_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Report-Truncated": "true" if generator.report_truncated else "false",
        },
    )


@router.get("/hosts/json")
def generate_hosts_json_report(
    filters: HostFilterParams = Depends(),
    report_type: str = Query(
        "comprehensive",
        pattern="^(inventory|comprehensive)$",
        description="'comprehensive' (adds findings + hotspots roll-ups) or 'inventory' (host records only).",
    ),
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
    report_data = generator.generate_json_report(hosts, report_type)

    filename = f"hosts_{report_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    return Response(
        content=json.dumps(report_data, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Report-Truncated": "true" if generator.report_truncated else "false",
        },
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
