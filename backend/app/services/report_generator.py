"""ReportGenerator — the host inventory downloads and the systemic briefing.

What it produces:

* the inventory CSV, streamed (``iter_inventory_csv``);
* the inventory JSON, written by the report worker (``write_json_report``);
* the per-host record both the JSON and the agents' ``report-context.ndjson``
  carry (``iter_host_records``);
* the systemic executive briefing (``generate_systemic_executive_html``).

The HTML host report, the agent package and the Markdown bundle were retired
with "Export hosts" (owner, 2026-10-07).

Lives in the service layer so the report worker builds a download WITHOUT
importing the HTTP layer (enforced by ``tests/test_service_router_boundary.py``);
``reports.py`` re-exports ``ReportGenerator``.  No ``app.api.*`` imports: the
host-filter builder is pulled from the ``host_query`` service, not the hosts
router.
"""
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import case, func
from app.db import models
from app.db.models_vulnerability import Vulnerability, enum_value, SEVERITY_KEYS
from app.core.config import settings
from app.services.report_templates import ReportTemplates
from app.services.subnet_insight_service import resolve_host_locations, compute_subnet_insights
from app.services.systemic_insight_service import compute_systemic_insights
from app.services.attention_service import compute_site_attention
from app.services.host_serialization import (
    _serialize_follow, _serialize_note, note_load_options, vulnerability_sort_key,
)
from app.services.host_query import build_filtered_host_query as _build_filtered_host_query
from app.db.models import HostFollow
from app.db.models_confidence import HostConfidence, PortConfidence, ConflictHistory
from app.db.models_findings import Finding, FindingHost, INACTIVE_ENDPOINT_STATES
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.services.csv_utils import safe_csv_row as _safe_csv_row
import io
import csv
import json
import logging
from datetime import datetime, timezone
import html

logger = logging.getLogger(__name__)

# Endpoint states that are not live work on that host — the ONE definition
# lives beside the model (v2.365.0) so the site / subnet summaries share it.
_INACTIVE_ENDPOINT_STATES = INACTIVE_ENDPOINT_STATES

# Systemic spread classification (Phase 1) → report label. Falls back to the
# legacy is_blind_spot boolean when an older/cached snapshot lacks the field.
_SYSTEMIC_SPREAD_LABEL = {
    "estate_wide": "estate-wide",
    "recurring": "recurring",
    "isolated": "isolated",
}


def _id_chunks(ids, size: int = 1000):
    """Yield slices of an id list for chunked ``IN (...)`` queries.

    A chunk of hosts carries its ports too — at ~10 ports/host a whole
    project's port ids as a single ``IN`` list approaches PostgreSQL's
    bind-param ceiling and degrades the query plan toward a seq-scan.
    Chunking keeps each statement bounded.
    """
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


class ReportGenerator:
    # Derived from the canonical SEVERITY_KEYS (critical=0 … unknown=5) so the
    # sort ordering has one source shared with the host serializer.
    SEVERITY_ORDER = {sev: i for i, sev in enumerate(SEVERITY_KEYS)}

    def __init__(self, db: Session, current_user, project_id: int = None):
        self.db = db
        self.current_user = current_user
        self.project_id = project_id
        # Lazily-computed, report-lifetime caches so the host→site/subnet map
        # and the hotspots roll-up are each built at most once per report even
        # though more than one section reads them.
        self._host_loc_cache: Optional[Dict[int, Dict[str, Any]]] = None
        self._hotspots_cache: Optional[Dict[str, Any]] = None
        self._systemic_cache: Optional[Dict[str, Any]] = None

    # --- Site / subnet enrichment (shared by the CSV and the record) ------

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

    def _filtered_host_id_query(self, filters: Dict[str, Any]):
        """Just the matching host ids — the cheap driver for both downloads
        (ints only; the per-chunk hydrate loads the heavy relationships).

        ``filters`` must be ``build_filtered_host_query`` kwargs — the routes
        derive it from ``HostFilterParams.as_builder_kwargs()`` and it is
        splatted in, so a new filter dimension can never be silently dropped
        from a download (the bug that let exports include more hosts than the
        visible list).  Splatting (not per-key ``.get()``) is what guarantees
        no drift.
        """
        return (
            _build_filtered_host_query(
                self.db, self.current_user, **filters, project_id=self.project_id,
            )
            .with_entities(models.Host.id)
            .distinct()
        )

    def iter_inventory_csv(self, filters: Dict[str, Any], chunk_size: int = None):
        """Yield the Host Inventory CSV incrementally over a chunked cursor —
        every matching host, bounded memory (one chunk hydrated at a time).

        Header first, then rows in id-ordered chunks.
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
        selectin-loaded by ``iter_inventory_csv``)."""
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
    # (severity counts, SMB signing, tags, recency).
    INVENTORY_CSV_HEADER = [
        'IP Address', 'Hostname', 'State', 'Site', 'Subnet',
        'OS Name', 'OS Family', 'OS Type', 'OS Accuracy', 'SMB Signing',
        'Open Ports', 'Total Ports', 'Services',
        'Critical', 'High', 'Medium', 'Low',
        # Triaged-record columns (the host record rolled up to counts) so the
        # inventory can be filtered/sorted on what was actually concluded, not
        # just raw scan vulns: active canonical findings, critical findings,
        # test results that found something (evidence records with outcome
        # "finding"; "Execution Findings" until v2.442.0) and scanner vulns not
        # yet triaged.  No "Open Notes" (review 2026-10-01 N2): notes lost
        # their status in v2.446.0, so that column counted every note — the
        # same number as "Notes" below under a label that said otherwise.
        'Active Findings', 'Critical Findings', 'Test Findings',
        'Untriaged Vulns',
        'Tags', 'Notes', 'Last Seen', 'Scan File', 'Scan Date',
    ]

    def _judged_vuln_ids(self, host_ids: List[int]) -> Dict[int, set]:
        """``host_id -> ids of its scanner rows a finding covers ON THAT HOST``
        — the app's one "judged" rule (``observation_judged_on_host``: the
        promoted row itself OR the same issue key).  The exports subtracted
        only ``Finding.vuln_id``, one row of one host, so host B's row of an
        issue promoted from host A read "Untriaged scanner observation" here
        while the inspector, Oversight and the scanner-observations view
        called it judged (review 2026-09-23 R7)."""
        from app.services.engagement_metrics_service import observation_judged_on_host

        out: Dict[int, set] = {}
        for chunk in _id_chunks(host_ids):
            for host_id, vuln_id in (
                self.db.query(Vulnerability.host_id, Vulnerability.id)
                .join(models.Host, models.Host.id == Vulnerability.host_id)
                .filter(Vulnerability.host_id.in_(chunk), observation_judged_on_host())
                .all()
            ):
                out.setdefault(host_id, set()).add(vuln_id)
        return out

    def _finding_rows_on_hosts(self, host_ids: List[int]) -> Dict[Tuple[int, int], List[int]]:
        """``(host_id, finding_id) -> ids of THAT host's scanner rows the
        finding covers`` — the judged rule above, keeping which finding.

        ``Finding.vuln_id`` is the one row a finding was first promoted from,
        usually another host's, so an agent joining a host's
        ``canonical_findings`` to its ``vulnerabilities`` by it found nothing
        (prod feedback 2026-10-07)."""
        from app.db.models_findings import FindingSource
        from sqlalchemy import and_, or_

        out: Dict[Tuple[int, int], List[int]] = {}
        for chunk in _id_chunks(host_ids):
            for host_id, finding_id, vuln_id in (
                self.db.query(Vulnerability.host_id, Finding.id, Vulnerability.id)
                .join(FindingHost, FindingHost.host_id == Vulnerability.host_id)
                .join(Finding, Finding.id == FindingHost.finding_id)
                .filter(
                    Vulnerability.host_id.in_(chunk),
                    Finding.project_id == self.project_id,
                    Finding.source == FindingSource.SCANNER.value,
                    or_(
                        Finding.vuln_id == Vulnerability.id,
                        and_(Vulnerability.issue_key.isnot(None), Finding.dedup_key == Vulnerability.issue_key),
                    ),
                )
                .order_by(Vulnerability.id)
                .all()
            ):
                out.setdefault((host_id, finding_id), []).append(vuln_id)
        return out

    def _inventory_finding_counts(self, host_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """``host_id -> {active, critical, exec, promoted_vuln_ids}`` via batched
        GROUP-BY queries — the counts the streaming inventory CSV needs without
        building a full host record per row."""
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
                if host_status not in _INACTIVE_ENDPOINT_STATES:
                    d["active"] += 1
                    # Beside "Active Findings", on the same rule: a critical
                    # dismissed or remediated on this host is not one of its
                    # critical findings.
                    if severity == "critical":
                        d["critical"] += 1
            for host_id, vuln_ids in self._judged_vuln_ids(chunk).items():
                _slot(host_id)["promoted_vuln_ids"] |= vuln_ids
            for host_id, count in (
                self.db.query(EvidenceRecord.host_id, func.count(EvidenceRecord.id))
                .filter(
                    EvidenceRecord.host_id.in_(chunk),
                    EvidenceRecord.project_id == self.project_id,
                    EvidenceRecord.outcome == "finding",
                )
                .group_by(EvidenceRecord.host_id)
                .all()
            ):
                _slot(host_id)["exec"] = count
        return out

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
                untriaged_vulns,
                self._host_tags(host),
                len(host.notes or []),
                last_seen_str,
                scan_filename,
                scan_date_str
            ])

        return output.getvalue()
    
    @staticmethod
    def _record_eager_options():
        """Eager loads the full per-host record needs."""
        return (
            selectinload(models.Host.ports).selectinload(models.Port.scripts),
            selectinload(models.Host.host_scripts),
            selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
            selectinload(models.Host.last_updated_scan),
            *note_load_options(selectinload(models.Host.notes)),
            selectinload(models.Host.vulnerabilities).selectinload(Vulnerability.port),
            selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
        )

    def iter_host_records(self, host_id_query, chunk_size: int = None):
        """Yield the full per-host record DICT for each host in
        ``host_id_query`` (a query/subquery yielding already project- and
        filter-scoped host ids), streamed in id-ordered chunks.

        This is the SAME correlated record the JSON download carries
        (identity, ports, findings, notes, discoveries, canonical/execution
        findings, provenance, tags, review state) — exposed so a terminal-side
        agent can stream it to a file and populate a report template. There is
        **no host cap**: coverage must be complete for a report, and
        it's safe because only one ``chunk_size`` slice is hydrated at a time
        (``expunge_all`` after each), so a tens-of-thousands-host project streams
        in bounded memory. The caller downloads it to disk, never into context.
        """
        host_ids = [row[0] for row in host_id_query.order_by(models.Host.id).all()]
        for records in self.iter_host_record_chunks(host_ids, chunk_size):
            yield from records

    def iter_host_record_chunks(self, host_ids: List[int], chunk_size: int = None):
        """Yield the records of one chunk of ``host_ids`` at a time (in the
        order given).  One chunk is hydrated at a time and released after the
        caller resumes, so a whole-engagement download stays at ~one chunk of
        memory."""
        chunk_size = chunk_size or settings.REPORT_STREAM_CHUNK
        for start in range(0, len(host_ids), chunk_size):
            chunk_ids = host_ids[start:start + chunk_size]
            hosts = (
                self.db.query(models.Host)
                .filter(models.Host.id.in_(chunk_ids))
                .options(*self._record_eager_options())
                .all()
            )
            order = {hid: i for i, hid in enumerate(chunk_ids)}
            hosts.sort(key=lambda h: order.get(h.id, 0))
            context = self._build_export_context(hosts)
            yield [self._build_host_export_record(host, context) for host in hosts]
            # Release the chunk's ORM objects so peak memory stays ~one chunk.
            self.db.expunge_all()

    # --- The JSON download, written to a file (review 2026-09-23 B-Ops-6).
    # It used to be built in memory and capped at 2,000 hosts, so an 80k-host
    # project could not be exported whole.  It streams every matching host in
    # chunks straight to the artifact; nothing is capped.

    def _matching_host_ids(self, filters: Dict[str, Any]) -> List[int]:
        return [row[0] for row in self._filtered_host_id_query(filters).order_by(models.Host.id).all()]

    def write_json_report(self, filters: Dict[str, Any], report_type: str, out) -> int:
        """Write the JSON download for every matching host to the binary file
        ``out``; returns the host count.  Each host record is the shared
        record (``_build_host_export_record``), one per line inside
        ``hosts``; ``summary`` follows them because it is counted while they
        stream.  ``report_type='inventory'`` omits the project-wide findings,
        hotspots and systemic roll-ups."""
        is_comprehensive = report_type != "inventory"
        host_ids = self._matching_host_ids(filters)
        write = lambda text: out.write(text.encode("utf-8"))  # noqa: E731
        write("{\n")
        write(f'  "generated_at": {json.dumps(datetime.now().isoformat())},\n')
        write(f'  "report_type": {json.dumps("comprehensive" if is_comprehensive else "inventory")},\n')
        write('  "hosts": [')
        up = down = open_ports = count = 0
        for records in self.iter_host_record_chunks(host_ids):
            for record in records:
                write(("\n    " if count == 0 else ",\n    ") + json.dumps(record, default=str))
                count += 1
                state = (record.get("identity") or {}).get("state")
                up += state == "up"
                down += state == "down"
                open_ports += sum(1 for p in record.get("ports") or [] if p.get("state") == "open")
        write("\n  ],\n" if count else "],\n")
        summary = {
            "total_hosts": count, "hosts_up": up, "hosts_down": down, "total_open_ports": open_ports,
            # Kept for readers of the capped format this once was: nothing is
            # capped.
            "truncated": False, "host_cap": None,
        }
        tail: Dict[str, Any] = {"summary": summary}
        if is_comprehensive:
            tail["findings"] = self._findings_for_report_ids(host_ids)
            tail["hotspots"] = self._build_hotspots()
            tail["systemic"] = self._build_systemic()
        body = json.dumps(tail, indent=2, default=str)
        write(body[1:].lstrip("\n"))  # the tail's keys continue the open object
        return count

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

    def _findings_for_report_ids(self, host_ids: List[int]) -> List[Dict[str, Any]]:
        """Findings affecting ``host_ids``, severity-ordered — the triaged record
        that rolls up across hosts (note promotions, scanner promotions,
        test results).  The comprehensive JSON's ``findings`` and the
        drafter's input (``report_draft_service``), so both carry the
        analyst's conclusions, not just raw scan data."""
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
                # The affected hosts that are IN this download (ip + id) — the
                # join from a finding to the ``hosts`` records beside it.
                "affected": [
                    {"ip": fh.host.ip_address, "host_id": fh.host_id}
                    for fh in f.hosts
                    if fh.host and fh.host_id in host_id_set
                ],
                "vuln_id": f.vuln_id,
                # The note thread this finding was promoted from — its image
                # attachments are the finding's visual evidence.  Just the int
                # (the drafter resolves it to image captions).
                "evidence_annotation_id": f.evidence_annotation_id,
                # The finding's own comment/evidence thread (repro steps,
                # rationale, discussion the analyst added while refining it).
                "comments": comments_by_finding.get(f.id, []),
            }
            for f in findings
        ]
        out.sort(key=lambda x: self.SEVERITY_ORDER.get(x["severity"], 5))
        return out

    def _finding_comments(self, finding_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Map finding id → its comment thread (author + body, oldest-first) —
        the discussion/repro/rationale analysts add on the Findings page."""
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

    # --- Per-host correlation (the record's sources) ----------------------
    #
    # Three host-keyed maps, each built from a fixed number of batched queries
    # (no N+1) so they're viable for a chunk of hosts at a time: canonical
    # findings (with the
    # per-host FindingHost.host_status + resolved source row), test findings
    # (evidence records whose outcome is "finding") and tester summaries
    # (HostTest.tester_summary).  The dataset keys keep the name
    # ``execution_findings``; until v2.442.0 they were test-plan execution
    # results.

    def _canonical_findings_by_host(
        self, host_ids: List[int]
    ) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, set], set]:
        """``host_id -> [canonical finding dicts]`` with per-host status and the
        resolved source row, plus ``(per-host promoted vuln-id set, global
        promoted evidence-id set)`` so the record can label scanner vulns /
        test results already represented by a canonical finding.

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
        note_ids = {f.evidence_annotation_id for f in finding_list if f.source == "note" and f.evidence_annotation_id}

        vuln_map: Dict[int, Vulnerability] = {}
        if vuln_ids:
            for v in (
                self.db.query(Vulnerability).options(selectinload(Vulnerability.port))
                .filter(Vulnerability.id.in_(vuln_ids)).all()
            ):
                vuln_map[v.id] = v
        # The evidence behind each finding (EvidenceRecord.finding_id is the
        # link; the earliest record is the one the finding was raised from).
        evidence_map: Dict[int, EvidenceRecord] = {}
        promoted_evidence_ids: set = set()
        for r in (
            self.db.query(EvidenceRecord)
            .filter(
                EvidenceRecord.project_id == self.project_id,
                EvidenceRecord.finding_id.in_([f.id for f in finding_list]),
            )
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
            .all()
        ):
            evidence_map.setdefault(r.finding_id, r)
            promoted_evidence_ids.add(r.id)
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
            elif f.source == "execution" and f.id in evidence_map:
                r = evidence_map[f.id]
                detail = {
                    "kind": "execution",
                    "tool": r.tool,
                    "command": r.command,
                    "findings_summary": r.summary,
                    "outcome": r.outcome,
                }
            elif f.source == "note" and f.evidence_annotation_id in note_map:
                a = note_map[f.evidence_annotation_id]
                detail = {
                    "kind": "note",
                    "body": a.body or "",
                }
            base[f.id] = {
                "finding_id": f.id,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "source": f.source,
                "owner": (f.owner.full_name or f.owner.username) if f.owner else None,
                "vuln_id": f.vuln_id,
                "source_detail": detail,
                "comments": comments_by_finding.get(f.id, []),
            }

        by_host: Dict[int, List[Dict[str, Any]]] = {}
        promoted_vuln_ids: Dict[int, set] = {}
        rows_on_host = self._finding_rows_on_hosts(host_ids)
        for fh in fh_rows:
            if not fh.finding:
                continue
            rec = dict(base[fh.finding_id])
            rec["host_status"] = fh.host_status
            # THIS host's scanner rows the finding covers — the join to the
            # record's ``vulnerabilities[].id``.  ``vuln_id`` above is the row
            # the finding was first promoted from, on whichever host that was.
            rec["vulnerability_ids"] = rows_on_host.get((fh.host_id, fh.finding_id), [])
            by_host.setdefault(fh.host_id, []).append(rec)
        # The app's judged rule, not just each finding's own vuln_id.
        promoted_vuln_ids.update(self._judged_vuln_ids(host_ids))
        for recs in by_host.values():
            recs.sort(key=lambda r: self.SEVERITY_ORDER.get(r["severity"], 5))
        return by_host, promoted_vuln_ids, promoted_evidence_ids

    def _execution_findings_by_host(
        self, host_ids: List[int], promoted_evidence_ids: set
    ) -> Dict[int, List[Dict[str, Any]]]:
        """``host_id -> [test findings]`` — every evidence record on the host
        whose outcome is ``finding``, with the label of the test it answers
        (when it answers one), marked ``promoted`` when a canonical finding
        already cites it."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        if not host_ids:
            return out
        rows = (
            self.db.query(EvidenceRecord, HostTest.label)
            .outerjoin(HostTest, HostTest.id == EvidenceRecord.host_test_id)
            .filter(
                EvidenceRecord.host_id.in_(host_ids),
                EvidenceRecord.project_id == self.project_id,
                EvidenceRecord.outcome == "finding",
            )
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
            .all()
        )
        for r, label in rows:
            out.setdefault(r.host_id, []).append({
                "evidence_id": r.id,
                "host_test_id": r.host_test_id,
                "label": label,
                "tool": r.tool,
                "command": r.command,
                "findings_summary": r.summary,
                "promoted": r.id in promoted_evidence_ids,
                "executed_at": self._iso(r.executed_at or r.created_at),
            })
        return out

    def _names_by_host(self, host_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Names observed at each host's address (v2.323.0), for the record's
        identity block: ``[{fqdn, current}]`` sorted current-first then by
        name.  ``current`` uses the one binding rule from dns_name_service so
        the report cannot disagree with the inventory."""
        if not host_ids:
            return {}
        from sqlalchemy import func as _func
        from app.services.dns_name_service import current_binding_condition

        r, n, h = models.DNSRecord, models.DNSName, models.Host
        rows = (
            self.db.query(
                h.id, n.fqdn,
                _func.max(case((current_binding_condition(r), 1), else_=0)).label("current"),
            )
            .join(r, r.value == h.ip_address)
            .join(n, n.id == r.name_id)
            .filter(
                h.id.in_(host_ids),
                r.project_id == h.project_id,
                r.record_type.in_(models.DNS_ADDRESS_VALUED_TYPES),
                n.kind == "fqdn",
            )
            .group_by(h.id, n.fqdn)
            .all()
        )
        out: Dict[int, List[Dict[str, Any]]] = {}
        for hid, fqdn, current in rows:
            out.setdefault(hid, []).append({"fqdn": fqdn, "current": bool(current)})
        for entries in out.values():
            entries.sort(key=lambda e: (not e["current"], e["fqdn"]))
        return out

    def _tester_summaries_by_host(self, host_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """``host_id -> [tester summaries]`` — what the analyst wrote on each
        of the host's tests (``HostTest.tester_summary``, skipping empty ones)."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        if not host_ids:
            return out
        rows = (
            self.db.query(HostTest)
            .filter(
                HostTest.host_id.in_(host_ids),
                HostTest.project_id == self.project_id,
                HostTest.tester_summary.isnot(None),
                func.length(func.trim(HostTest.tester_summary)) > 0,
            )
            .order_by(HostTest.id)
            .all()
        )
        for t in rows:
            out.setdefault(t.host_id, []).append({
                "host_test_id": t.id,
                "label": t.label,
                "tool": t.tool,
                "description": t.description,
                "status": t.status,
                "findings": t.tester_summary,
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
        """Per-host roll-up (the record's ``dossier_summary``; the key keeps
        its name).  Finding severities (5-key
        vocab) are kept SEPARATE from vulnerability severities (6-key incl
        unknown) — the two vocabularies are intentionally distinct and must not
        be merged."""
        findings_by_severity: Dict[str, int] = {}
        active = 0
        for cf in canonical_findings:
            findings_by_severity[cf["severity"]] = findings_by_severity.get(cf["severity"], 0) + 1
            if cf.get("host_status") not in _INACTIVE_ENDPOINT_STATES:
                active += 1
        return {
            "active_findings": active,
            "total_findings": len(canonical_findings),
            "findings_by_severity": findings_by_severity,
            "vulns_by_severity": vuln_summary,
            "untriaged_vulns": untriaged_count,
            "execution_findings": len(execution_findings),
            "tester_summaries": len(tester_summaries),
            "total_notes": len(notes),
        }

    # --- Site / subnet hotspots (the JSON and the briefing) ----------------

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

    def _generate_hotspots_html(self, site: Optional[str] = None) -> str:
        """HTML fragment: a Sites table + a Subnets table, worst-first.
        ``site`` keeps only that site's row and its subnets — the briefing a
        site owner takes into their own meeting."""
        data = self._build_hotspots()
        parts: List[str] = []

        sites = data["sites"]
        subnets_all = data["subnets"]
        if site:
            sites = [s for s in sites if (s.get("site") or "") == site]
            subnets_all = [s for s in subnets_all if (s.get("site") or "") == site]
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

        subnets = subnets_all
        if data["subnets_adopted"] and subnets:
            rows = []
            for s in subnets:
                site_name = "—" if not s.get("site") else s["site"]
                tier = "—" if s.get("criticality_tier") is None else f"T{s['criticality_tier']}"
                sev = s["exposure"]["by_severity"]
                hy = s["hygiene"]
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(s.get('cidr', '')))}</td>"
                    f"<td>{html.escape(str(site_name))}</td>"
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

    # --- Systemic insights (the JSON and the briefing) ---------------------

    def _build_systemic(self) -> Dict[str, Any]:
        """Cross-sectional systemic insights (estate blind spots / conditions /
        segment outliers / diagnostic profiles), reusing the live service so the
        report agrees with the /insights/systemic dashboard.  Cached for the
        report's life."""
        if self._systemic_cache is None:
            if not self.project_id:
                self._systemic_cache = {"adopted": False}
            else:
                self._systemic_cache = compute_systemic_insights(self.db, self.project_id)
        return self._systemic_cache

    def _generate_systemic_html(self, site: Optional[str] = None) -> str:
        """HTML fragment: estate blind spots + systemic conditions + segment
        outliers + diagnostic profiles, worst-first.  ``site`` narrows the
        per-subnet sections (outliers, profiles) to that site; the estate-wide
        blind spots and conditions are by definition not per-site and stay."""
        data = self._build_systemic()
        if not data.get("adopted"):
            return '<p class="muted">No scoped subnets — define a scope to surface systemic patterns across the estate.</p>'
        blind = data.get("blind_spots") or []
        conditions = data.get("conditions") or []
        outliers = data.get("segment_outliers") or []
        profiles = data.get("diagnostic_profiles") or []
        if site:
            outliers = [o for o in outliers if (o.get("site") or "") == site]
            profiles = [d for d in profiles if (d.get("site") or "") == site]
        if not blind and not conditions:
            return '<p class="muted">No weakness recurs widely enough across the in-scope estate to suggest a shared cause.</p>'

        parts: List[str] = []

        if blind:
            items = []
            for b in blind:
                pct = round((b.get("host_fraction") or 0) * 100)
                items.append(
                    "<li>"
                    f"<strong>{html.escape(str(b.get('label', '')))}</strong>"
                    f" — {b.get('affected_hosts', 0)} hosts ({pct}%), "
                    f"{b.get('subnet_spread', 0)} subnets, {b.get('site_spread', 0)} sites. "
                    f"{html.escape(str(b.get('recommended_action', '')))}"
                    "</li>"
                )
            parts.append(
                "<h4>Estate blind spots</h4>"
                '<p class="muted">Weaknesses spanning most of the estate — likely an organisational gap.</p>'
                f"<ul>{''.join(items)}</ul>"
            )

        if conditions:
            rows = []
            for c in conditions:
                pct = round((c.get("host_fraction") or 0) * 100)
                scope = _SYSTEMIC_SPREAD_LABEL.get(
                    c.get("classification"),
                    "estate-wide" if c.get("is_blind_spot") else "localised",
                )
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(c.get('label', '')))}</td>"
                    f"<td>{c.get('affected_hosts', 0)} ({pct}%)</td>"
                    f"<td>{c.get('subnet_spread', 0)}</td>"
                    f"<td>{c.get('site_spread', 0)}</td>"
                    f"<td>{c.get('systemic_score', 0)}</td>"
                    f"<td>{scope}</td>"
                    f"<td>{html.escape(str(c.get('recommended_action', '')))}</td>"
                    "</tr>"
                )
            parts.append(
                "<h4>Systemic conditions</h4>"
                '<table class="data-table"><thead><tr>'
                "<th>Condition</th><th>Hosts</th><th>Subnets</th><th>Sites</th>"
                "<th>Score</th><th>Scope</th><th>Recommended action</th>"
                f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )

        if outliers:
            rows = []
            for o in outliers:
                conds = ", ".join(o.get("conditions") or []) or "—"
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(o.get('cidr', '')))}</td>"
                    f"<td>{html.escape(str(o.get('site') or '—'))}</td>"
                    f"<td>{o.get('host_count', 0)}</td>"
                    f"<td>{o.get('times_median', 0)}× median</td>"
                    f"<td>{html.escape(conds)}</td>"
                    "</tr>"
                )
            parts.append(
                "<h4>Segment outliers</h4>"
                '<p class="muted">Subnets whose issue density is well above the estate median.</p>'
                '<table class="data-table"><thead><tr>'
                "<th>Subnet</th><th>Site</th><th>Hosts</th><th>Density</th><th>Conditions</th>"
                f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )

        if profiles:
            rows = []
            for d in profiles:
                conds = ", ".join(d.get("conditions") or []) or "—"
                rc = d.get("root_cause") or {}
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(d.get('cidr', '')))}</td>"
                    f"<td>{html.escape(str(d.get('site') or '—'))}</td>"
                    f"<td>{html.escape(conds)}</td>"
                    f"<td>{html.escape(str(rc.get('kind', '')))}: {html.escape(str(rc.get('text', '')))}</td>"
                    "</tr>"
                )
            parts.append(
                "<h4>Diagnostic profiles</h4>"
                '<p class="muted">Per-subnet co-occurrence signature → the question it raises (a lead to check, not a conclusion).</p>'
                '<table class="data-table"><thead><tr>'
                "<th>Subnet</th><th>Site</th><th>Conditions</th><th>Worth checking</th>"
                f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )

        return "".join(parts)

    def generate_systemic_executive_html(self, site: Optional[str] = None) -> str:
        """Standalone, lightweight executive systemic report — estate summary +
        blind spots + conditions + outliers + profiles, plus the site/subnet
        hotspots, and nothing per host.

        Purpose-built as a self-contained HTML file for sharing at a high-level
        meeting.  Bounded systemic payload, so it renders synchronously.

        ``site`` (a site name) scopes the per-site sections — hotspots, segment
        outliers, diagnostic profiles — to that site so the briefing matches the
        posture context it was created from.  Estate-wide patterns stay
        estate-wide: a site briefing still says what the whole estate suffers."""
        data = self._build_systemic()
        site_note = (
            f'<div class="report-subtitle">Scoped to site: {html.escape(site)}</div>' if site else ""
        )
        css = ReportTemplates.get_css_styles()
        generated_at = datetime.now(timezone.utc)
        estate = data.get("estate") or {}
        if data.get("adopted"):
            summary = (
                f"<div class=\"stats-grid\">"
                f"<div class=\"stat-card\"><div class=\"stat-value\">{estate.get('hosts_in_scope', 0)}</div><div class=\"stat-label\">Hosts in scope</div></div>"
                f"<div class=\"stat-card\"><div class=\"stat-value\">{estate.get('subnets', 0)}</div><div class=\"stat-label\">Subnets</div></div>"
                f"<div class=\"stat-card\"><div class=\"stat-value\">{estate.get('sites', 0)}</div><div class=\"stat-label\">Sites</div></div>"
                f"<div class=\"stat-card\"><div class=\"stat-value\">{estate.get('blind_spot_count', 0)}</div><div class=\"stat-label\">Estate blind spots</div></div>"
                f"</div>"
            )
        else:
            summary = '<p class="muted">No scoped subnets yet.</p>'

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BlueStick Systemic Insights</title>
    {css}
</head>
<body>
    <div class="report-header">
        <div class="metadata">
            <div>
                <div class="report-title">Systemic Insights</div>
                <div class="report-subtitle">Estate-wide weakness patterns — executive summary</div>
                {site_note}
                <div class="version-tag">Backend v{settings.APP_VERSION} | Frontend v{settings.FRONTEND_VERSION}</div>
            </div>
            <div><strong>Generated:</strong> {generated_at.strftime('%B %d, %Y at %I:%M %p')} UTC</div>
        </div>
    </div>
    <div class="section">
        <div class="section-header">Estate</div>
        <div class="section-content">{summary}</div>
    </div>
    <div class="section">
        <div class="section-header">Systemic patterns</div>
        <div class="section-content">{self._generate_systemic_html(site=site)}</div>
    </div>
    <div class="section">
        <div class="section-header">Site &amp; Subnet Hotspots</div>
        <div class="section-content">{self._generate_hotspots_html(site=site)}</div>
    </div>
</body>
</html>"""

    # --- The per-host record (the JSON's ``hosts`` and the agents'
    # ``report-context.ndjson``) ---------------------------------------------

    def _build_export_context(self, hosts: List[models.Host]) -> Dict[str, Any]:
        """What a chunk of hosts' records need, in batched queries."""
        host_ids = [host.id for host in hosts]
        port_ids = [port.id for host in hosts for port in (host.ports or [])]

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

        # Per-host correlation (canonical findings + their resolved sources,
        # test findings, tester summaries) — batched, so a chunk of hosts at
        # a time stays viable.
        canonical_by_host, promoted_vuln_ids, promoted_evidence_ids = self._canonical_findings_by_host(host_ids)
        execution_findings_map = self._execution_findings_by_host(host_ids, promoted_evidence_ids)
        tester_summaries_map = self._tester_summaries_by_host(host_ids)
        names_map = self._names_by_host(host_ids)

        # v2.328.0 — the third coverage state, from the ONE shared derivation
        # (scope_coverage / host_query use the same predicate): hosts in no
        # declared subnet that an in-scope name currently resolves to.
        name_reachable_ids: set = set()
        if host_ids and self.project_id:
            from app.services.dns_name_service import host_reachable_via_in_scope_name_condition
            for chunk_start in range(0, len(host_ids), 1000):
                chunk = host_ids[chunk_start:chunk_start + 1000]
                name_reachable_ids.update(
                    hid for (hid,) in self.db.query(models.Host.id)
                    .filter(
                        models.Host.id.in_(chunk),
                        host_reachable_via_in_scope_name_condition(self.project_id),
                    )
                    .all()
                )

        return {
            "follow_map": follow_map,
            "subnet_map": subnet_map,
            "name_reachable_ids": name_reachable_ids,
            "host_confidence_map": host_confidence_map,
            "port_confidence_map": port_confidence_map,
            "host_conflicts_map": host_conflicts_map,
            "port_conflicts_map": port_conflicts_map,
            "canonical_findings_map": canonical_by_host,
            "promoted_vuln_ids_map": promoted_vuln_ids,
            "execution_findings_map": execution_findings_map,
            "names_map": names_map,
            "tester_summaries_map": tester_summaries_map,
        }

    def _build_host_export_record(self, host: models.Host, context: Dict[str, Any]) -> Dict[str, Any]:
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
            for vuln in sorted(list(host.vulnerabilities or []), key=vulnerability_sort_key)
        ]
        subnet_entries = context["subnet_map"].get(host.id, [])
        # The one host→site rule (nearest site-bearing subnet).
        primary_site = self._host_site(host.id) or None
        follow_record = context["follow_map"].get(host.id)

        # Correlation for this host (defaults make the record valid even for a
        # context built without the finding maps — e.g. a future caller).
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
                # v2.323.0 — every name observed at this address (the display
                # hostname is one of many behind a load balancer).  Each entry:
                # {fqdn, current} where current = the name resolves here NOW.
                "names": context.get("names_map", {}).get(host.id, []),
                "state": host.state,
                "state_reason": host.state_reason,
            },
            "scope": {
                # Three states (v2.328.0): subnet-in-scope, reachable only via
                # an in-scope name, or out of scope.  ``in_scope`` keeps its
                # subnet meaning; ``via_name`` is the third state and is
                # never counted as in_scope (name scope does not confer subnet
                # scope); ``out_of_scope`` is neither.
                "in_scope": bool(subnet_entries),
                "via_name": (not subnet_entries) and host.id in context.get("name_reachable_ids", set()),
                "out_of_scope": not subnet_entries and host.id not in context.get("name_reachable_ids", set()),
                "status": (
                    "in_scope" if subnet_entries
                    else "via_name" if host.id in context.get("name_reachable_ids", set())
                    else "out_of_scope"
                ),
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
                self._serialize_port_for_export(host.id, port, context)
                for port in sorted(
                    list(host.ports or []),
                    key=lambda item: (item.port_number, item.protocol),
                )
            ],
            "host_scripts": [
                self._serialize_host_script_for_export(host.id, script)
                for script in sorted(
                    list(host.host_scripts or []),
                    key=lambda item: (item.script_id, item.id),
                )
            ],
            "vulnerabilities": vulnerabilities,
            "vulnerability_summary": vuln_summary,
            # Untriaged = scanner vulns not yet promoted to a canonical finding
            # on this host.
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
    ) -> Dict[str, Any]:
        scripts = [
            self._serialize_port_script_for_export(host_id, port.port_number, port.protocol, script)
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
    ) -> Dict[str, Any]:
        payload = {
            "script_id": script.script_id,
            "scan_id": script.scan_id,
            "first_seen": self._iso(script.first_seen),
            "last_seen": self._iso(script.last_seen),
        }
        if script.output:
            # ``output_ref`` named the script's output FILE inside the zip
            # bundles, which were retired with "Export hosts".  The record
            # never carried the output itself; the key is kept so the JSON and
            # ``report-context.ndjson`` are unchanged — it says "this script
            # had output", it no longer points at anything.
            payload["output_ref"] = f"artifacts/hosts/{host_id}/ports/{port_number}-{protocol}/{script.script_id}.txt"
        return payload

    def _serialize_host_script_for_export(
        self,
        host_id: int,
        script: models.HostScript,
    ) -> Dict[str, Any]:
        payload = {
            "script_id": script.script_id,
            "scan_id": script.scan_id,
            "first_seen": self._iso(script.first_seen),
            "last_seen": self._iso(script.last_seen),
        }
        if script.output:
            # See ``_serialize_port_script_for_export``.
            payload["output_ref"] = f"artifacts/hosts/{host_id}/host_scripts/{script.script_id}.txt"
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
            # v2.390.0 — the per-host scanner evidence, which the export
            # (like the inspector) had left out.
            "plugin_output": vuln.plugin_output,
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

