"""ReportGenerator — host-report dataset assembly + per-format rendering.

Extracted from ``app/api/v1/endpoints/reports.py`` (the router) so the report
worker / job service can build reports WITHOUT importing the HTTP layer
(enforced by ``tests/test_service_router_boundary.py``).  The endpoints in
reports.py re-export ``ReportGenerator`` from here.  No ``app.api.*`` imports:
the host-filter builder is pulled from the ``host_query`` service, not the
hosts router.
"""
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, distinct, func
from app.db import models
from app.db.models_vulnerability import Vulnerability, enum_value, SEVERITY_KEYS
from app.schemas.schemas import Host
from app.core.config import settings
from app.services.report_templates import ReportTemplates
from app.services.subnet_insight_service import resolve_host_locations, compute_subnet_insights
from app.services.attention_service import compute_site_attention
from app.services.host_serialization import _serialize_follow, _serialize_note
from app.services.host_query import build_filtered_host_query as _build_filtered_host_query
from app.db.models import HostFollow
from app.db.models_confidence import HostConfidence, PortConfidence, ConflictHistory
from app.db.models_findings import Finding, FindingHost, FindingHostStatus
from app.db.models_agent import TestPlan, TestPlanEntry, TestExecutionResult
from app.services.csv_utils import csv_safe as _csv_safe, safe_csv_row as _safe_csv_row
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

    # Maximum number of hosts the streamed HTML report can include — high,
    # because the dossier streams chunk-by-chunk so peak memory is bounded.
    # Configurable; the streaming CSV ignores it.
    MAX_REPORT_HOSTS = settings.REPORT_MAX_HOSTS
    # Lower cap for the formats that materialise the whole document in memory
    # (PDF/JSON/zip bundles) — see config for the rationale.
    MAX_INMEMORY_REPORT_HOSTS = settings.REPORT_MAX_INMEMORY_HOSTS

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

    def get_hosts_for_report(self, filters: Dict[str, Any], cap: Optional[int] = None) -> List[models.Host]:
        """Get hosts based on filter parameters (capped at ``cap``, default
        MAX_REPORT_HOSTS).

        ``cap`` lets each format pick its own ceiling — the in-memory formats
        (PDF/JSON/zip bundles) pass ``MAX_INMEMORY_REPORT_HOSTS`` so a heavy
        full-dossier export can't OOM the worker, while the streamed HTML uses
        the high default.  ``report_truncated`` reflects whichever cap applied.

        ``filters`` must be ``build_filtered_host_query`` kwargs — every report
        route now derives it from ``HostFilterParams.as_builder_kwargs()`` and
        splats it in, so a new filter dimension can never be silently dropped
        from exports (the bug that let reports include more hosts than the
        visible list).  Splatting (not per-key ``.get()``) is what guarantees
        no drift.
        """
        cap = cap or self.MAX_REPORT_HOSTS
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
        ).distinct().limit(cap + 1)

        rows = query.all()
        self.report_truncated = len(rows) > cap
        return rows[:cap]

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
        # Triaged-record columns (the dossier rolled up to counts) so the
        # inventory can be filtered/sorted on what was actually concluded, not
        # just raw scan vulns: active canonical findings, critical findings,
        # execution findings, open notes, and scanner vulns not yet triaged.
        'Active Findings', 'Critical Findings', 'Execution Findings',
        'Open Notes', 'Untriaged Vulns',
        'Tags', 'Notes', 'Last Seen', 'Scan File', 'Scan Date',
    ]

    def _inventory_finding_counts(self, host_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """``host_id -> {active, critical, exec, promoted_vuln_ids}`` via batched
        GROUP-BY queries — the counts the streaming inventory CSV needs without
        building a full dossier record per row."""
        out: Dict[int, Dict[str, Any]] = {}
        if not host_ids:
            return out

        def _slot(hid: int) -> Dict[str, Any]:
            return out.setdefault(hid, {"active": 0, "critical": 0, "exec": 0, "promoted_vuln_ids": set()})

        for chunk in _id_chunks(host_ids):
            for host_id, severity, source, vuln_id, host_status in (
                self.db.query(
                    FindingHost.host_id, Finding.severity, Finding.source,
                    Finding.vuln_id, FindingHost.host_status,
                )
                .join(Finding, FindingHost.finding_id == Finding.id)
                .filter(FindingHost.host_id.in_(chunk), Finding.project_id == self.project_id)
                .all()
            ):
                d = _slot(host_id)
                if host_status != FindingHostStatus.REMEDIATED.value:
                    d["active"] += 1
                if severity == "critical":
                    d["critical"] += 1
                if source == "scanner" and vuln_id:
                    d["promoted_vuln_ids"].add(vuln_id)
            for host_id, count in (
                self.db.query(TestPlanEntry.host_id, func.count(TestExecutionResult.id))
                .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
                .filter(TestPlanEntry.host_id.in_(chunk), TestExecutionResult.is_finding.is_(True))
                .group_by(TestPlanEntry.host_id)
                .all()
            ):
                _slot(host_id)["exec"] = count
        return out

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
        finding_counts = self._inventory_finding_counts([h.id for h in hosts])
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

            fc = finding_counts.get(host.id, {})
            open_notes = sum(
                1 for n in (host.notes or [])
                if enum_value(getattr(n, 'status', None)) not in ('resolved',)
            )
            untriaged_vulns = max(0, len(host.vulnerabilities or []) - len(fc.get('promoted_vuln_ids', ())))

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
                fc.get('active', 0),
                fc.get('critical', 0),
                fc.get('exec', 0),
                open_notes,
                untriaged_vulns,
                self._host_tags(host),
                len(host.notes or []),
                last_seen_str,
                scan_filename,
                scan_date_str
            ])

        return output.getvalue()
    
    def generate_html_report(
        self, hosts: List[models.Host], filters: Dict[str, Any],
        report_type: str = "comprehensive", for_pdf: bool = False,
    ) -> str:
        """Build the full HTML host report as one string — used by the PDF
        route (WeasyPrint needs the whole document at once).  The ``/hosts/html``
        route streams dossier-by-dossier via ``iter_html_report`` instead.

        ``report_type``: ``"comprehensive"`` (summary + metrics + exposure +
        Findings index + Site/Subnet Hotspots + per-host dossiers) or
        ``"inventory"`` (the same minus the project-wide findings/hotspots
        roll-ups).  ``for_pdf`` renders each dossier's ``<details>`` blocks
        expanded so collapsed content isn't dropped from the PDF.
        """
        is_comprehensive = report_type != "inventory"
        context = self._build_export_context(hosts)
        records = [self._build_host_export_record(host, context, {}) for host in hosts]
        stats = self._stats_from_records(records)
        findings = self._findings_for_report(hosts) if is_comprehensive else []
        parts = [self._html_open(stats, filters, report_type, findings)]
        for record in records:
            parts.append(self._render_host_dossier(record, for_pdf=for_pdf))
        parts.append(self._html_tail())
        return "".join(parts)

    def resolve_html_host_ids(self, filters: Dict[str, Any]) -> List[int]:
        """Resolve the capped, id-ordered host id list for the streamed HTML and
        set ``report_truncated`` — called by the endpoint BEFORE streaming so the
        ``X-Report-Truncated`` header is accurate (StreamingResponse flushes
        headers before the body)."""
        all_ids = [row[0] for row in self._filtered_host_id_query(filters).all()]
        self.report_truncated = len(all_ids) > self.MAX_REPORT_HOSTS
        return all_ids[: self.MAX_REPORT_HOSTS]

    def iter_html_report(self, host_ids: List[int], report_type: str = "comprehensive",
                         filters: Optional[Dict[str, Any]] = None):
        """Stream the HTML host report dossier-by-dossier over a chunked cursor,
        so peak memory ≈ one chunk (not the whole capped host set).  Header +
        findings index first, then host dossiers in id-ordered chunks, then the
        footer + scripts.  ``host_ids`` is the pre-resolved capped list from
        ``resolve_html_host_ids`` (which also set ``report_truncated``)."""
        is_comprehensive = report_type != "inventory"
        stats = self._html_summary_stats(host_ids)
        findings = self._findings_for_report_ids(host_ids) if is_comprehensive else []
        yield self._html_open(stats, filters or {}, report_type, findings)

        chunk_size = settings.REPORT_STREAM_CHUNK
        for start in range(0, len(host_ids), chunk_size):
            chunk_ids = host_ids[start:start + chunk_size]
            hosts = (
                self.db.query(models.Host)
                .filter(models.Host.id.in_(chunk_ids))
                .options(
                    selectinload(models.Host.ports).selectinload(models.Port.scripts),
                    selectinload(models.Host.host_scripts),
                    selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
                    selectinload(models.Host.last_updated_scan),
                    selectinload(models.Host.notes).selectinload(models.Annotation.author),
                    selectinload(models.Host.vulnerabilities).selectinload(Vulnerability.port),
                    selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
                )
                .all()
            )
            order = {hid: i for i, hid in enumerate(chunk_ids)}
            hosts.sort(key=lambda h: order.get(h.id, 0))
            context = self._build_export_context(hosts)
            buf = [self._render_host_dossier(self._build_host_export_record(host, context, {}), for_pdf=False)
                   for host in hosts]
            yield "".join(buf)
            # Drop the chunk's ORM objects so peak memory stays ~one chunk.
            self.db.expunge_all()

        yield self._html_tail()

    def _stats_from_records(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summary aggregates for the non-streaming (PDF) path, from the records
        already in hand."""
        services: Dict[str, int] = {}
        os_counter: Dict[str, int] = {}
        open_ports = 0
        for record in records:
            for port in record["ports"]:
                if port.get("state") == "open":
                    open_ports += 1
                    name = (port.get("service") or {}).get("name")
                    if name:
                        services[name] = services.get(name, 0) + 1
            os_name = (record["os"] or {}).get("name")
            if os_name:
                os_counter[os_name] = os_counter.get(os_name, 0) + 1
        return {
            "total_hosts": len(records),
            "hosts_up": sum(1 for r in records if r["identity"].get("state") == "up"),
            "open_ports": open_ports,
            "unique_services": len(services),
            "top_services": sorted(services.items(), key=lambda x: x[1], reverse=True)[:10],
            "top_os": sorted(os_counter.items(), key=lambda x: x[1], reverse=True)[:10],
        }

    def _html_summary_stats(self, host_ids: List[int]) -> Dict[str, Any]:
        """Same summary aggregates for the streamed path, computed with
        scalar-column queries (chunked) instead of holding the host graph."""
        stats = {
            "total_hosts": len(host_ids), "hosts_up": 0, "open_ports": 0,
            "unique_services": 0, "top_services": [], "top_os": [],
        }
        if not host_ids:
            return stats
        services: Dict[str, int] = {}
        os_counter: Dict[str, int] = {}
        for chunk in _id_chunks(host_ids):
            for state, os_name in (
                self.db.query(models.Host.state, models.Host.os_name)
                .filter(models.Host.id.in_(chunk)).all()
            ):
                if state == "up":
                    stats["hosts_up"] += 1
                if os_name:
                    os_counter[os_name] = os_counter.get(os_name, 0) + 1
            for (svc_name,) in (
                self.db.query(models.Port.service_name)
                .filter(models.Port.host_id.in_(chunk), models.Port.state == "open").all()
            ):
                stats["open_ports"] += 1
                if svc_name:
                    services[svc_name] = services.get(svc_name, 0) + 1
        stats["unique_services"] = len(services)
        stats["top_services"] = sorted(services.items(), key=lambda x: x[1], reverse=True)[:10]
        stats["top_os"] = sorted(os_counter.items(), key=lambda x: x[1], reverse=True)[:10]
        return stats

    def _html_open(
        self, stats: Dict[str, Any], filters: Dict[str, Any],
        report_type: str, findings: List[Dict[str, Any]],
    ) -> str:
        """Everything from <!DOCTYPE> through the opening of the host-dossier
        container — shared by the streaming and non-streaming HTML builders.
        ``_html_tail`` closes it."""
        is_comprehensive = report_type != "inventory"
        css = ReportTemplates.get_css_styles()
        generated_at = datetime.now(timezone.utc).isoformat()
        backend_version = settings.APP_VERSION
        frontend_version = settings.FRONTEND_VERSION
        top_services = stats["top_services"]
        top_os = stats["top_os"]
        max_service_value = max((v for _, v in top_services), default=1)
        max_os_value = max((v for _, v in top_os), default=1)

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
        <div class="section-header">Findings index</div>
        <div class="section-content">
            <p class="muted">Triaged findings across the included hosts. Each finding links to every affected host's dossier below; each dossier links back here.</p>
            {self._html_findings_index(findings)}
        </div>
    </div>"""
        else:
            hotspots_nav = findings_nav = ""
            hotspots_section = findings_section = ""

        if self.report_truncated:
            included = stats["total_hosts"]
            truncation_banner = (
                '<div class="section" style="border-left:4px solid #c0392b;background:#fdecea;'
                'padding:12px 16px;margin:0 0 16px;color:#7b241c;">'
                f'<strong>⚠ Report truncated.</strong> Your filters matched more than '
                f'{included:,} hosts; this report includes only the first {included:,}. '
                'Narrow the filters, or use the CSV inventory export (it streams the full set).</div>'
            )
        else:
            truncation_banner = ""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BlueStick Host Report</title>
    {css}
    <style>
        .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; margin-top: 10px; }}
        .chart-card {{ background: var(--bg-panel-soft); border: 1px solid var(--border); border-radius: 8px; padding: 20px; box-shadow: 0 12px 28px rgba(0,0,0,0.20); }}
        .chart-title {{ margin-bottom: 15px; font-size: 1.1em; font-weight: 600; color: var(--text); }}
        .bar {{ display: flex; align-items: center; margin-bottom: 8px; gap: 12px; }}
        .bar-label {{ flex: 0 0 120px; font-size: 0.85em; color: var(--muted); }}
        .bar-fill {{ flex: 1; height: 10px; border-radius: 999px; background: #0b1118; position: relative; overflow: hidden; border: 1px solid var(--border); }}
        .bar-fill::after {{ content: ''; position: absolute; top: 0; left: 0; height: 100%; width: var(--bar-width, 0%); background: linear-gradient(135deg, var(--accent) 0%, var(--accent-warm) 100%); }}
        .bar-value {{ flex: 0 0 40px; font-size: 0.85em; text-align: right; color: var(--muted); }}
        .filters-list {{ margin-top: 10px; color: var(--muted); font-size: 0.9em; }}
        .filters-list strong {{ color: var(--text); }}
        td.up {{ color: var(--success); font-weight: 600; }}
        td.down {{ color: var(--danger); font-weight: 600; }}
    </style>
</head>
<body>
    <div class="report-header">
        <div class="metadata">
            <div>
                <div class="report-title">BlueStick Host Report</div>
                <div class="report-subtitle">Host-centric security dossiers</div>
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
        <a href="#hosts">Host Dossiers</a>
    </nav>

    {truncation_banner}

    <div class="executive-summary" id="summary">
        <h3>Executive Summary</h3>
        <p><strong>Scope:</strong> This report summarizes {stats['total_hosts']} discovered hosts filtered by the selected criteria. The dataset contains {stats['hosts_up']} hosts currently marked as up and {stats['open_ports']} detected open ports across the sample.</p>
        <p><strong>Service Exposure:</strong> We identified {stats['unique_services']} unique services. Review the Top Services panel below to focus on the most prevalent protocols.</p>
        <p><strong>Usage:</strong> Search the host dossiers below by IP, hostname, CVE, finding, note, or service. Each dossier consolidates everything known about one host.</p>
    </div>

    <div class="section" id="metrics">
        <div class="section-header">Key Metrics</div>
        <div class="section-content">
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">{stats['total_hosts']}</div><div class="stat-label">Total Hosts</div></div>
                <div class="stat-card"><div class="stat-value">{stats['hosts_up']}</div><div class="stat-label">Hosts Up</div></div>
                <div class="stat-card"><div class="stat-value">{stats['open_ports']}</div><div class="stat-label">Open Ports</div></div>
                <div class="stat-card"><div class="stat-value">{stats['unique_services']}</div><div class="stat-label">Unique Services</div></div>
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
        <div class="section-header">Host Dossiers</div>
        <div class="section-content">
            <div class="dossier-controls">
                <input type="text" id="host-search" class="dossier-search" placeholder="Search hosts — IP, hostname, CVE, finding, note, service…" aria-label="Search host dossiers">
                <span id="host-search-count" class="table-hint"></span>
                <button type="button" id="host-search-prev" class="dossier-nav-btn" aria-label="Previous match">↑</button>
                <button type="button" id="host-search-next" class="dossier-nav-btn" aria-label="Next match">↓</button>
            </div>
            <div class="host-dossiers">"""

    def _html_tail(self) -> str:
        """Close the dossier container + host section, then footer + scripts."""
        scripts = ReportTemplates.get_interactive_scripts()
        backend_version = settings.APP_VERSION
        frontend_version = settings.FRONTEND_VERSION
        return f"""</div>
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
    
    @staticmethod
    def _dossier_block(title: str, count: int, body: str, det: str) -> str:
        return (
            f'<details class="dossier-block"{det}><summary>{title} '
            f'<span class="dcount">{count}</span></summary>'
            f'<div class="dossier-block-body">{body}</div></details>'
        )

    def _render_host_dossier(self, record: Dict[str, Any], for_pdf: bool = False) -> str:
        """One host's consolidated dossier section: a summary header + collapsible
        blocks for canonical findings, untriaged scanner observations, execution
        findings, tester summaries, notes, and ports.  Anchored ``#host-{id}`` and
        carrying a lowercased ``data-search`` blob (IP/hostname/site/subnet/CVE/
        finding title/note text/service) for the report-wide host search.
        ``for_pdf`` expands every block so collapsed content isn't dropped."""
        e = html.escape
        nl = chr(10)
        host_id = record["host_id"]
        ident = record["identity"]
        ip = ident.get("ip_address") or ""
        hostname = ident.get("hostname") or ""
        state = ident.get("state") or "unknown"
        scope = record["scope"]
        site = scope.get("site") or "—"
        subnets = ", ".join(s.get("cidr") for s in (scope.get("subnets") or []) if s.get("cidr")) or "—"
        os_name = (record["os"] or {}).get("name") or "—"
        summary = record["dossier_summary"]
        canonical = record["canonical_findings"]
        untriaged = record["untriaged_vulnerabilities"]
        execf = record["execution_findings"]
        tester = record["tester_summaries"]
        notes = record["analyst_context"]["notes"]
        ports = record["ports"]
        det = " open" if for_pdf else ""

        # Searchable blob — one lowercased attribute the JS search matches on.
        terms = [ip, hostname, site, subnets, os_name]
        for cf in canonical:
            terms.append(cf.get("title") or "")
            terms.append((cf.get("source_detail") or {}).get("cve_id") or "")
        for v in untriaged:
            terms += [v.get("title") or "", v.get("cve_id") or ""]
        for n in notes:
            terms.append(n.get("body") or "")
        for p in ports:
            terms.append((p.get("service") or {}).get("name") or "")
        search_blob = e(" ".join(t for t in terms if t).lower())

        fbs = summary["findings_by_severity"]
        finding_chips = " ".join(
            f'<span class="dsev dsev-{e(sev)}">{n} {e(sev)}</span>'
            for sev, n in sorted(fbs.items(), key=lambda kv: self.SEVERITY_ORDER.get(kv[0], 5))
        ) or '<span class="muted">none</span>'
        vbs = summary["vulns_by_severity"]
        vuln_line = " · ".join(f"{vbs.get(k, 0)} {k}" for k in ("critical", "high", "medium", "low", "info") if vbs.get(k)) or "none"

        head = (
            '<div class="dossier-head">'
            f'<div class="dossier-id"><a href="#host-{host_id}" class="anchor-self">#</a> {e(ip)}'
            + (f' <span class="dossier-host">{e(hostname)}</span>' if hostname else '')
            + '</div>'
            '<dl class="dossier-meta">'
            f'<div><dt>State</dt><dd class="state-{e(state.lower())}">{e(state)}</dd></div>'
            f'<div><dt>Site</dt><dd>{e(site)}</dd></div>'
            f'<div><dt>Subnet</dt><dd>{e(subnets)}</dd></div>'
            f'<div><dt>OS</dt><dd>{e(os_name)}</dd></div>'
            '</dl>'
            '<div class="dossier-glance">'
            f'<span>Findings <strong>{summary["active_findings"]}</strong>/{summary["total_findings"]}: {finding_chips}</span>'
            f'<span>Vulns: {e(vuln_line)} · untriaged {summary["untriaged_vulns"]}</span>'
            f'<span>Exec {summary["execution_findings"]} · Tester {summary["tester_summaries"]} · Notes {summary["open_notes"]}/{summary["total_notes"]}</span>'
            '</div>'
            '</div>'
        )

        blocks: List[str] = []

        if canonical:
            items = []
            for cf in canonical:
                d = cf.get("source_detail") or {}
                if d.get("kind") == "scanner":
                    extra = " · ".join(filter(None, [
                        f"CVE {e(d['cve_id'])}" if d.get("cve_id") else "",
                        f"port {d['port_number']}/{e(d.get('protocol') or '')}" if d.get("port_number") else "",
                        e((d.get("solution") or "")[:200]) if d.get("solution") else "",
                    ]))
                elif d.get("kind") == "execution":
                    extra = " · ".join(filter(None, [
                        f"cmd <code>{e((d.get('command') or '')[:160])}</code>" if d.get("command") else "",
                        e((d.get("findings_summary") or "")[:200]) if d.get("findings_summary") else "",
                    ]))
                elif d.get("kind") == "note":
                    extra = e((d.get("body") or "")[:240])
                else:
                    extra = ""
                comments_html = "".join(
                    f'<div class="dcomment"><span class="muted">{e(str(c.get("author") or "—"))}</span> '
                    f'{e(str(c.get("body") or "")).replace(nl, "<br/>")}</div>'
                    for c in (cf.get("comments") or [])
                )
                items.append(
                    '<div class="dfinding"><div class="dfinding-head">'
                    f'<span class="dsev dsev-{e(cf["severity"])}">{e(cf["severity"])}</span> '
                    f'<a href="#finding-{cf["finding_id"]}" class="dfinding-title">{e(cf.get("title") or "untitled")}</a> '
                    f'<span class="dpill">{e(cf.get("host_status") or "open")}</span> '
                    f'<span class="muted">{e(cf.get("source") or "")}'
                    + (f' · {e(cf["owner"])}' if cf.get("owner") else "")
                    + '</span></div>'
                    + (f'<div class="dfinding-detail">{extra}</div>' if extra else "")
                    + comments_html
                    + '</div>'
                )
            blocks.append(self._dossier_block("Canonical findings", len(canonical), "".join(items), det))

        if untriaged:
            rows = "".join(
                f'<tr><td><span class="dsev dsev-{e(v.get("severity") or "unknown")}">{e(v.get("severity") or "unknown")}</span></td>'
                f'<td>{e(v.get("title") or "")}</td><td>{e(v.get("cve_id") or "")}</td>'
                f'<td>{e(str(v.get("port_number") or ""))}/{e(v.get("protocol") or "")}</td>'
                f'<td>{e(v.get("service_name") or "")}</td></tr>'
                for v in untriaged
            )
            table = (
                '<table class="data-table"><thead><tr><th>Severity</th><th>Title</th><th>CVE</th><th>Port</th><th>Service</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>'
                '<p class="muted">Scanner observations not yet promoted to a canonical finding on this host.</p>'
            )
            blocks.append(self._dossier_block("Untriaged scanner observations", len(untriaged), table, det))

        if execf:
            items = []
            for x in execf:
                items.append(
                    '<div class="dfinding"><div class="dfinding-head">'
                    f'<span class="dsev dsev-{e(x.get("severity") or "info")}">{e(x.get("severity") or "—")}</span> '
                    f'<strong>{e(x.get("plan_title") or "")}</strong> '
                    f'<span class="muted">{e(x.get("test_phase") or "")}</span> '
                    + ('<span class="dpill">promoted</span>' if x.get("promoted") else "")
                    + '</div>'
                    + (f'<div class="dfinding-detail">cmd <code>{e((x.get("command") or "")[:160])}</code></div>' if x.get("command") else "")
                    + (f'<div class="dfinding-detail">{e((x.get("findings_summary") or "")[:300])}</div>' if x.get("findings_summary") else "")
                    + '</div>'
                )
            blocks.append(self._dossier_block("Execution findings", len(execf), "".join(items), det))

        if tester:
            items = "".join(
                '<div class="dfinding"><div class="dfinding-head">'
                f'<strong>{e(t.get("plan_title") or "")}</strong> '
                f'<span class="muted">{e(t.get("test_phase") or "")} · {e(str(t.get("status") or ""))}</span></div>'
                f'<div class="dfinding-detail">{e(t.get("findings") or "").replace(nl, "<br/>")}</div></div>'
                for t in tester
            )
            blocks.append(self._dossier_block("Tester summaries", len(tester), items, det))

        if notes:
            items = "".join(
                '<div class="dnote"><div class="dfinding-head">'
                f'<span class="dpill">{e(str(n.get("status") or ""))}</span> '
                f'<span class="muted">{e(str(n.get("note_type") or n.get("type") or ""))}</span></div>'
                f'<div class="dfinding-detail">{e(str(n.get("body") or "")).replace(nl, "<br/>")}</div></div>'
                for n in notes
            )
            blocks.append(self._dossier_block("Notes", len(notes), items, det))

        if ports:
            open_count = sum(1 for p in ports if p.get("state") == "open")
            rows = "".join(
                f'<tr><td>{e(str(p.get("port_number") or ""))}</td><td>{e(p.get("protocol") or "")}</td>'
                f'<td>{e(p.get("state") or "")}</td><td>{e((p.get("service") or {}).get("name") or "")}</td>'
                f'<td>{e((p.get("service") or {}).get("product") or "")}</td>'
                f'<td>{e((p.get("service") or {}).get("version") or "")}</td></tr>'
                for p in sorted(ports, key=lambda x: (x.get("port_number") or 0, x.get("protocol") or ""))
            )
            table = (
                '<table class="data-table"><thead><tr><th>Port</th><th>Proto</th><th>State</th><th>Service</th><th>Product</th><th>Version</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>'
            )
            blocks.append(self._dossier_block("Ports &amp; services", open_count, table, det))

        return (
            f'<section class="host-dossier" id="host-{host_id}" data-search="{search_blob}">'
            f'{head}{"".join(blocks)}</section>'
        )

    def generate_json_report(
        self, hosts: List[models.Host], report_type: str = "comprehensive",
    ) -> Dict[str, Any]:
        """Generate JSON host report.  ``report_type='inventory'`` omits the
        project-wide findings + hotspots roll-ups (host records are unchanged)."""
        is_comprehensive = report_type != "inventory"
        # Host-centric schema: each host record carries its correlated dossier
        # (canonical findings + resolved source, untriaged scanner observations,
        # execution findings, tester summaries, notes, ports) via the shared
        # export record — the same object the bundle/agent-package emit.
        context = self._build_export_context(hosts)
        records = [self._build_host_export_record(host, context, {}) for host in hosts]
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "report_type": "comprehensive" if is_comprehensive else "inventory",
            "summary": {
                "total_hosts": len(hosts),
                "hosts_up": len([h for h in hosts if h.state == 'up']),
                "hosts_down": len([h for h in hosts if h.state == 'down']),
                "total_open_ports": sum(len([p for p in (h.ports or []) if p.state == 'open']) for h in hosts),
                # True when the filter matched more than the in-memory cap and
                # this payload was truncated (use the CSV inventory for the
                # complete set).
                "truncated": self.report_truncated,
                "host_cap": self.MAX_INMEMORY_REPORT_HOSTS,
            },
            "hosts": records,
        }
        if is_comprehensive:
            report_data["findings"] = self._findings_for_report(hosts)
            report_data["hotspots"] = self._build_hotspots()
        return report_data

    def _findings_for_report(self, hosts: List[models.Host]) -> List[Dict[str, Any]]:
        """Findings affecting the report's hosts (thin wrapper over the
        ids-based core so the streamed HTML can drive it without host objects)."""
        return self._findings_for_report_ids([h.id for h in hosts])

    def _findings_for_report_ids(self, host_ids: List[int]) -> List[Dict[str, Any]]:
        """Findings affecting ``host_ids``, severity-ordered — the triaged record
        that rolls up across hosts (note promotions, scanner promotions,
        execution results). Included in every export format so a report carries
        the analyst's conclusions, not just raw scan data."""
        if not host_ids:
            return []
        host_id_set = set(host_ids)
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
                # In-report affected hosts (ip + id) so the HTML index can link
                # each finding to the dossiers that actually appear below.
                "affected": [
                    {"ip": fh.host.ip_address, "host_id": fh.host_id}
                    for fh in f.hosts
                    if fh.host and fh.host_id in host_id_set
                ],
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

    # --- Per-host correlation (the dossier sources) -----------------------
    #
    # Three host-keyed maps, each built from a fixed number of batched queries
    # (no N+1) so they're viable for a chunk of hosts at a time in the streamed
    # HTML and for the capped in-memory formats: canonical findings (with the
    # per-host FindingHost.host_status + resolved source row), execution
    # findings (TestExecutionResult.is_finding, reached via its TestPlanEntry),
    # and tester summaries (TestPlanEntry.findings).

    def _canonical_findings_by_host(
        self, host_ids: List[int]
    ) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, set], set]:
        """``host_id -> [canonical finding dicts]`` with per-host status and the
        resolved source row, plus ``(per-host promoted vuln-id set, global
        promoted exec-result-id set)`` so the dossier can label scanner vulns /
        execution results already represented by a canonical finding.

        Queries: 1 (FindingHost⨝Finding) + ≤1 each to resolve the scanner /
        execution / note source rows + 1 (finding comment threads)."""
        empty: Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, set], set] = ({}, {}, set())
        if not host_ids:
            return empty
        fh_rows = (
            self.db.query(FindingHost)
            .join(Finding, FindingHost.finding_id == Finding.id)
            .options(selectinload(FindingHost.finding).selectinload(Finding.owner))
            .filter(
                FindingHost.host_id.in_(host_ids),
                Finding.project_id == self.project_id,
            )
            .all()
        )
        if not fh_rows:
            return empty
        finding_list = list({fh.finding_id: fh.finding for fh in fh_rows if fh.finding}.values())

        vuln_ids = {f.vuln_id for f in finding_list if f.source == "scanner" and f.vuln_id}
        exec_ids = {f.exec_result_id for f in finding_list if f.source == "execution" and f.exec_result_id}
        note_ids = {f.evidence_annotation_id for f in finding_list if f.source == "note" and f.evidence_annotation_id}

        vuln_map: Dict[int, Vulnerability] = {}
        if vuln_ids:
            for v in (
                self.db.query(Vulnerability).options(selectinload(Vulnerability.port))
                .filter(Vulnerability.id.in_(vuln_ids)).all()
            ):
                vuln_map[v.id] = v
        exec_map: Dict[int, TestExecutionResult] = {}
        if exec_ids:
            for r in self.db.query(TestExecutionResult).filter(TestExecutionResult.id.in_(exec_ids)).all():
                exec_map[r.id] = r
        note_map: Dict[int, models.Annotation] = {}
        if note_ids:
            for a in self.db.query(models.Annotation).filter(models.Annotation.id.in_(note_ids)).all():
                note_map[a.id] = a

        comments_by_finding = self._finding_comments([f.id for f in finding_list])

        # Per-finding dict (shared across the hosts a finding affects), minus
        # the per-host status which is grafted on below.
        base: Dict[int, Dict[str, Any]] = {}
        for f in finding_list:
            detail: Optional[Dict[str, Any]] = None
            if f.source == "scanner" and f.vuln_id in vuln_map:
                v = vuln_map[f.vuln_id]
                detail = {
                    "kind": "scanner",
                    "cve_id": v.cve_id,
                    "description": v.description,
                    "solution": v.solution,
                    "cvss_score": v.cvss_score,
                    "severity": enum_value(v.severity),
                    "port_number": v.port.port_number if v.port else None,
                    "protocol": v.port.protocol if v.port else None,
                    "service_name": v.port.service_name if v.port else None,
                }
            elif f.source == "execution" and f.exec_result_id in exec_map:
                r = exec_map[f.exec_result_id]
                detail = {
                    "kind": "execution",
                    "command": r.command_run,
                    "findings_summary": r.findings_summary,
                    "severity": r.severity,
                    "status": r.status,
                }
            elif f.source == "note" and f.evidence_annotation_id in note_map:
                a = note_map[f.evidence_annotation_id]
                detail = {
                    "kind": "note",
                    "body": a.body or "",
                    "status": enum_value(getattr(a, "status", None)),
                }
            base[f.id] = {
                "finding_id": f.id,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "source": f.source,
                "owner": (f.owner.full_name or f.owner.username) if f.owner else None,
                "vuln_id": f.vuln_id,
                "exec_result_id": f.exec_result_id,
                "source_detail": detail,
                "comments": comments_by_finding.get(f.id, []),
            }

        by_host: Dict[int, List[Dict[str, Any]]] = {}
        promoted_vuln_ids: Dict[int, set] = {}
        promoted_exec_ids: set = {
            f.exec_result_id for f in finding_list if f.source == "execution" and f.exec_result_id
        }
        for fh in fh_rows:
            if not fh.finding:
                continue
            rec = dict(base[fh.finding_id])
            rec["host_status"] = fh.host_status
            by_host.setdefault(fh.host_id, []).append(rec)
            if fh.finding.source == "scanner" and fh.finding.vuln_id:
                promoted_vuln_ids.setdefault(fh.host_id, set()).add(fh.finding.vuln_id)
        for recs in by_host.values():
            recs.sort(key=lambda r: self.SEVERITY_ORDER.get(r["severity"], 5))
        return by_host, promoted_vuln_ids, promoted_exec_ids

    def _execution_findings_by_host(
        self, host_ids: List[int], promoted_exec_ids: set
    ) -> Dict[int, List[Dict[str, Any]]]:
        """``host_id -> [execution findings]`` — every ``TestExecutionResult``
        flagged ``is_finding`` for the host (reached via its ``TestPlanEntry``),
        marked ``promoted`` when already represented by a canonical finding."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        if not host_ids:
            return out
        rows = (
            self.db.query(
                TestExecutionResult,
                TestPlanEntry.host_id,
                TestPlanEntry.test_phase,
                TestPlan.title,
            )
            .join(TestPlanEntry, TestExecutionResult.entry_id == TestPlanEntry.id)
            .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
            .filter(
                TestPlanEntry.host_id.in_(host_ids),
                TestExecutionResult.is_finding.is_(True),
            )
            .all()
        )
        for r, host_id, phase, plan_title in rows:
            out.setdefault(host_id, []).append({
                "exec_result_id": r.id,
                "plan_title": plan_title,
                "test_phase": phase,
                "severity": r.severity,
                "status": r.status,
                "command": r.command_run,
                "findings_summary": r.findings_summary,
                "promoted": r.id in promoted_exec_ids,
                "executed_at": self._iso(r.executed_at),
            })
        return out

    def _tester_summaries_by_host(self, host_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """``host_id -> [tester summaries]`` — the analyst's per-host
        ``TestPlanEntry.findings`` narrative (skipping empty ones)."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        if not host_ids:
            return out
        rows = (
            self.db.query(TestPlanEntry, TestPlan.title)
            .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
            .filter(
                TestPlanEntry.host_id.in_(host_ids),
                TestPlanEntry.findings.isnot(None),
                func.length(func.trim(TestPlanEntry.findings)) > 0,
            )
            .all()
        )
        for entry, plan_title in rows:
            out.setdefault(entry.host_id, []).append({
                "plan_title": plan_title,
                "test_phase": entry.test_phase,
                "status": entry.status,
                "findings": entry.findings,
            })
        return out

    @staticmethod
    def _dossier_summary(
        canonical_findings: List[Dict[str, Any]],
        vuln_summary: Dict[str, int],
        untriaged_count: int,
        execution_findings: List[Dict[str, Any]],
        tester_summaries: List[Dict[str, Any]],
        notes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Per-host roll-up for the dossier header.  Finding severities (5-key
        vocab) are kept SEPARATE from vulnerability severities (6-key incl
        unknown) — the two vocabularies are intentionally distinct and must not
        be merged."""
        findings_by_severity: Dict[str, int] = {}
        active = 0
        for cf in canonical_findings:
            findings_by_severity[cf["severity"]] = findings_by_severity.get(cf["severity"], 0) + 1
            if cf.get("host_status") != FindingHostStatus.REMEDIATED.value:
                active += 1
        open_notes = sum(1 for n in notes if (n.get("status") or "") not in ("resolved",))
        return {
            "active_findings": active,
            "total_findings": len(canonical_findings),
            "findings_by_severity": findings_by_severity,
            "vulns_by_severity": vuln_summary,
            "untriaged_vulns": untriaged_count,
            "execution_findings": len(execution_findings),
            "tester_summaries": len(tester_summaries),
            "open_notes": open_notes,
            "total_notes": len(notes),
        }

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

    def _html_findings_index(self, findings: List[Dict[str, Any]]) -> str:
        """The findings index: a severity-ordered table where each finding is
        anchored ``#finding-{id}`` and its affected in-report hosts link to the
        matching ``#host-{id}`` dossiers (which link back here).  Followed by an
        Evidence gallery (comment threads + embedded screenshots)."""
        if not findings:
            return '<p class="muted">No findings recorded for these hosts.</p>'
        rows = []
        for f in findings:
            affected = f.get("affected") or []
            links = ", ".join(
                f'<a href="#host-{a["host_id"]}">{html.escape(str(a["ip"]))}</a>'
                for a in affected[:8]
            )
            extra = f.get("host_count", len(affected)) - min(len(affected), 8)
            if extra > 0:
                links += f" (+{extra} more)"
            if not links:
                links = '<span class="muted">—</span>'
            rows.append(
                f'<tr id="finding-{f["id"]}">'
                f'<td><span class="dsev dsev-{html.escape(str(f["severity"]))}">{html.escape(str(f["severity"]))}</span></td>'
                f"<td>{html.escape(str(f['title']))}</td>"
                f"<td>{html.escape(str(f['status']))}</td>"
                f"<td>{html.escape(str(f['source']))}</td>"
                f"<td>{html.escape(str(f['owner'] or '—'))}</td>"
                f"<td>{links}</td>"
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
            # The canonical findings collection — counted in the manifest but
            # previously never written.  Each host record carries its
            # ``canonical_findings`` with finding ids that cross-reference here.
            bundle.writestr("findings.json", json.dumps(dataset["findings"], indent=2, default=str))
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
            # vulnerabilities.csv (was misleadingly "findings.csv" — these are
            # scanner vulns), plus the correlation CSVs the dossier surfaces.
            bundle.writestr("vulnerabilities.csv", self._generate_vulnerabilities_csv(dataset["hosts"]))
            bundle.writestr("canonical_findings.csv", self._generate_canonical_findings_csv(dataset["findings"]))
            bundle.writestr("execution_findings.csv", self._generate_execution_findings_csv(dataset["hosts"]))
            bundle.writestr("notes.csv", self._generate_notes_csv(dataset["hosts"]))
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

        # Per-host dossier correlation (canonical findings + their resolved
        # sources, execution findings, tester summaries) — batched, so a chunk
        # of hosts at a time stays viable in the streamed HTML.
        canonical_by_host, promoted_vuln_ids, promoted_exec_ids = self._canonical_findings_by_host(host_ids)
        execution_findings_map = self._execution_findings_by_host(host_ids, promoted_exec_ids)
        tester_summaries_map = self._tester_summaries_by_host(host_ids)

        return {
            "follow_map": follow_map,
            "subnet_map": subnet_map,
            "host_confidence_map": host_confidence_map,
            "port_confidence_map": port_confidence_map,
            "host_conflicts_map": host_conflicts_map,
            "port_conflicts_map": port_conflicts_map,
            "scans": scans,
            "canonical_findings_map": canonical_by_host,
            "promoted_vuln_ids_map": promoted_vuln_ids,
            "execution_findings_map": execution_findings_map,
            "tester_summaries_map": tester_summaries_map,
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

        # Dossier correlation for this host (defaults make the record valid even
        # for a context built without the finding maps — e.g. a future caller).
        canonical_findings = context.get("canonical_findings_map", {}).get(host.id, [])
        execution_findings = context.get("execution_findings_map", {}).get(host.id, [])
        tester_summaries = context.get("tester_summaries_map", {}).get(host.id, [])
        promoted_vuln_ids = context.get("promoted_vuln_ids_map", {}).get(host.id, set())
        untriaged_vulnerabilities = [
            v for v in vulnerabilities if v.get("id") not in promoted_vuln_ids
        ]
        vuln_summary = self._build_vulnerability_summary(vulnerabilities)

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
            "vulnerability_summary": vuln_summary,
            # Untriaged = scanner vulns not yet promoted to a canonical finding
            # on this host (labelled "scanner observation" in the dossier).
            "untriaged_vulnerabilities": untriaged_vulnerabilities,
            "canonical_findings": canonical_findings,
            "execution_findings": execution_findings,
            "tester_summaries": tester_summaries,
            "dossier_summary": self._dossier_summary(
                canonical_findings, vuln_summary, len(untriaged_vulnerabilities),
                execution_findings, tester_summaries, notes,
            ),
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

            if host.get("canonical_findings"):
                lines.extend(["", "#### Canonical Findings",
                              "| Severity | Title | Status | Source | Owner |",
                              "|---|---|---|---|---|"])
                for cf in host["canonical_findings"]:
                    lines.append(
                        f"| {cf.get('severity') or ''} | {(cf.get('title') or '').replace('|', '/')} | "
                        f"{cf.get('host_status') or ''} | {cf.get('source') or ''} | {cf.get('owner') or ''} |"
                    )

            if host.get("execution_findings"):
                lines.extend(["", "#### Execution Findings"])
                for x in host["execution_findings"]:
                    promoted = " (promoted)" if x.get("promoted") else ""
                    lines.append(
                        f"- [{x.get('severity') or '—'}] {x.get('plan_title') or ''} / "
                        f"{x.get('test_phase') or ''}{promoted}: {(x.get('findings_summary') or '').strip()}"
                    )

            if host.get("tester_summaries"):
                lines.extend(["", "#### Tester Summaries"])
                for t in host["tester_summaries"]:
                    lines.append(
                        f"- {t.get('plan_title') or ''} ({t.get('test_phase') or ''}, "
                        f"{t.get('status') or ''}): {(t.get('findings') or '').strip()}"
                    )

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

    def _generate_vulnerabilities_csv(self, hosts: List[Dict[str, Any]]) -> str:
        """Scanner vulnerabilities, one row per host×vuln.  (Renamed from the
        misleading ``findings.csv`` — these are raw scanner observations, not
        the triaged canonical findings, which live in canonical_findings.csv.)"""
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

    def _generate_canonical_findings_csv(self, findings: List[Dict[str, Any]]) -> str:
        """The triaged canonical findings (one row per finding) — severity,
        status, source, owner, and the affected host IPs."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Finding ID", "Severity", "Title", "Status", "Source", "Owner", "Host Count", "Affected Hosts"])
        for f in findings:
            _safe_csv_row(writer, [
                f.get("id"),
                f.get("severity") or "",
                f.get("title") or "",
                f.get("status") or "",
                f.get("source") or "",
                f.get("owner") or "",
                f.get("host_count") or 0,
                ", ".join(f.get("affected_hosts") or []),
            ])
        return output.getvalue()

    def _generate_execution_findings_csv(self, hosts: List[Dict[str, Any]]) -> str:
        """Execution findings (``TestExecutionResult.is_finding``), one row per
        host×result, with the plan, phase, severity, and whether it was promoted
        to a canonical finding."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["IP Address", "Hostname", "Plan", "Phase", "Severity", "Promoted", "Command", "Summary"])
        for host in hosts:
            for x in host.get("execution_findings") or []:
                _safe_csv_row(writer, [
                    host["identity"]["ip_address"],
                    host["identity"].get("hostname") or "",
                    x.get("plan_title") or "",
                    x.get("test_phase") or "",
                    x.get("severity") or "",
                    "yes" if x.get("promoted") else "no",
                    x.get("command") or "",
                    x.get("findings_summary") or "",
                ])
        return output.getvalue()

    def _generate_notes_csv(self, hosts: List[Dict[str, Any]]) -> str:
        """Host notes, one row per note — type, status, author, and body."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["IP Address", "Hostname", "Type", "Status", "Author", "Body"])
        for host in hosts:
            for note in host["analyst_context"].get("notes") or []:
                _safe_csv_row(writer, [
                    host["identity"]["ip_address"],
                    host["identity"].get("hostname") or "",
                    note.get("note_type") or note.get("type") or "",
                    note.get("status") or "",
                    note.get("author") or note.get("created_by") or "",
                    note.get("body") or "",
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

