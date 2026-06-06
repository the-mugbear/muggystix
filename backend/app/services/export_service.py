import json
import csv
import io
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import models
from app.core.config import settings
from app.services.csv_utils import safe_csv_row
from app.services.dns_service import DNSService
from app.services.report_templates import ReportTemplates
from app.services.subnet_calculator import SubnetCalculator
import logging

logger = logging.getLogger(__name__)

class ExportService:
    def __init__(self, db: Session):
        self.db = db
        self.dns_service = DNSService(db)

    def export_scope_report(self, scope_id: int, format_type: str = 'json') -> Dict[str, Any]:
        """Export comprehensive report for a scope"""
        scope = self.db.query(models.Scope).filter(models.Scope.id == scope_id).first()
        if not scope:
            raise ValueError(f"Scope with ID {scope_id} not found")
        
        # Gather all data for the scope
        report_data = self._gather_scope_data(scope)
        
        # Enhance scope data with subnet calculations
        report_data = self._enhance_scope_data_with_calculations(report_data)
        
        if format_type.lower() == 'json':
            return self._format_json_report(report_data)
        elif format_type.lower() == 'csv':
            return self._format_csv_report(report_data)
        elif format_type.lower() == 'html':
            return self._format_html_report(report_data)
        else:
            raise ValueError(f"Unsupported export format: {format_type}")

    def export_scan_report(self, scan_id: int, format_type: str = 'json') -> Dict[str, Any]:
        """Export report for a specific scan"""
        scan = self.db.query(models.Scan).filter(models.Scan.id == scan_id).first()
        if not scan:
            raise ValueError(f"Scan with ID {scan_id} not found")
        
        report_data = self._gather_scan_data(scan)
        
        if format_type.lower() == 'json':
            return self._format_json_report(report_data)
        elif format_type.lower() == 'csv':
            return self._format_csv_report(report_data)
        elif format_type.lower() == 'html':
            return self._format_html_report(report_data)
        else:
            raise ValueError(f"Unsupported export format: {format_type}")

    def export_out_of_scope_report(self, format_type: str = 'json') -> Dict[str, Any]:
        """Export report of all out-of-scope findings"""
        out_of_scope_hosts = self.db.query(models.OutOfScopeHost).all()
        
        report_data = {
            'report_type': 'out_of_scope_findings',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'app_version': settings.APP_VERSION,
            'frontend_version': settings.FRONTEND_VERSION,
            'total_out_of_scope_hosts': len(out_of_scope_hosts),
            'findings': []
        }
        
        # Group by tool source
        by_tool = {}
        for host in out_of_scope_hosts:
            tool = host.tool_source or 'unknown'
            if tool not in by_tool:
                by_tool[tool] = []
            
            finding = {
                'ip_address': host.ip_address,
                'hostname': host.hostname,
                'ports': host.ports or {},
                'reason': host.reason,
                'found_at': host.created_at.isoformat() if host.created_at else None,
                'scan_id': host.scan_id
            }
            
            # Try to get scan information
            scan = self.db.query(models.Scan).filter(models.Scan.id == host.scan_id).first()
            if scan:
                finding['scan_filename'] = scan.filename
                finding['scan_type'] = scan.scan_type
                finding['tool_name'] = scan.tool_name
            
            by_tool[tool].append(finding)
        
        report_data['findings_by_tool'] = by_tool
        report_data['findings'] = [finding for findings in by_tool.values() for finding in findings]
        
        if format_type.lower() == 'json':
            return self._format_json_report(report_data)
        elif format_type.lower() == 'csv':
            return self._format_csv_report(report_data)
        elif format_type.lower() == 'html':
            return self._format_html_report(report_data)
        else:
            raise ValueError(f"Unsupported export format: {format_type}")

    def _gather_scope_data(self, scope: models.Scope) -> Dict[str, Any]:
        """Gather all data related to a scope"""
        from sqlalchemy.orm import joinedload, selectinload

        # Get all subnet mappings for this scope.  Eager-load Subnet
        # so the ``mapping.subnet.cidr`` access in the host loop
        # below doesn't fire a separate SELECT per mapping (5k mappings
        # on a real scope = 5k extra queries).
        mappings = (
            self.db.query(models.HostSubnetMapping)
            .options(joinedload(models.HostSubnetMapping.subnet))
            .join(models.Subnet)
            .filter(models.Subnet.scope_id == scope.id)
            .all()
        )

        # Get unique hosts with ports eager-loaded.  Without
        # ``selectinload``, ``host.ports`` in the per-host loop fires
        # one lazy-load query per host.
        host_ids = list(set([mapping.host_id for mapping in mappings]))
        hosts = (
            self.db.query(models.Host)
            .options(selectinload(models.Host.ports))
            .filter(models.Host.id.in_(host_ids))
            .all()
            if host_ids
            else []
        )

        # Get scan information.  Host is dedup'd by IP — it has no scan_id,
        # only ``last_updated_scan_id`` — so we resolve "every scan that
        # observed a host in this scope" via HostScanHistory, not by reading
        # a (non-existent) Host.scan_id.  Using last_updated_scan_id here
        # would silently exclude every prior scan that touched the same
        # host, producing incomplete scope reports.
        if host_ids:
            scan_ids = [
                row[0] for row in self.db.query(models.HostScanHistory.scan_id)
                .filter(models.HostScanHistory.host_id.in_(host_ids))
                .distinct()
                .all()
            ]
        else:
            scan_ids = []
        scans = self.db.query(models.Scan).filter(models.Scan.id.in_(scan_ids)).all() if scan_ids else []
        
        # Get Eyewitness results for these scans
        eyewitness_results = self.db.query(models.EyewitnessResult).filter(
            models.EyewitnessResult.scan_id.in_(scan_ids)
        ).all() if scan_ids else []
        
        # Get out-of-scope hosts from these scans
        out_of_scope = self.db.query(models.OutOfScopeHost).filter(
            models.OutOfScopeHost.scan_id.in_(scan_ids)
        ).all() if scan_ids else []
        
        report_data = {
            'report_type': 'scope_report',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'app_version': settings.APP_VERSION,
            'frontend_version': settings.FRONTEND_VERSION,
            'scope': {
                'id': scope.id,
                'name': scope.name,
                'description': scope.description,
                'created_at': scope.created_at.isoformat() if scope.created_at else None,
                'subnets': [
                    {
                        'id': subnet.id,
                        'cidr': subnet.cidr,
                        'description': subnet.description,
                        'created_at': subnet.created_at.isoformat() if subnet.created_at else None
                    }
                    for subnet in scope.subnets
                ]
            },
            'statistics': {
                'total_subnets': len(scope.subnets),
                'total_hosts': len(hosts),
                'total_scans': len(scans),
                'total_eyewitness_results': len(eyewitness_results),
                'out_of_scope_hosts': len(out_of_scope)
            },
            'scans': [],
            'hosts': [],
            'eyewitness_results': [],
            'out_of_scope_hosts': []
        }
        
        # Add scan details
        for scan in scans:
            scan_data = {
                'id': scan.id,
                'filename': scan.filename,
                'scan_type': scan.scan_type,
                'tool_name': scan.tool_name,
                'created_at': scan.created_at.isoformat() if scan.created_at else None,
                'command_line': scan.command_line,
                'version': scan.version
            }
            report_data['scans'].append(scan_data)
        
        # Batch-fetch DNS records for every hostname we'll need below.
        # The old code did ``get_stored_dns_records(host.hostname)``
        # inside the per-host loop — one query per host.  One IN-list
        # query is cheaper at any host count > 1 and dominates at scope
        # scale.  Also pre-index subnet mappings by host_id so the
        # subnet-list comprehension stops re-scanning ``mappings`` per
        # host (O(H·M) → O(H+M)).
        hostnames = [h.hostname for h in hosts if h.hostname]
        dns_records_by_hostname: Dict[str, List[models.DNSRecord]] = {}
        if hostnames:
            project_id = getattr(self.dns_service, "project_id", None)
            dns_query = self.db.query(models.DNSRecord).filter(
                models.DNSRecord.domain.in_(hostnames)
            )
            if project_id is not None:
                dns_query = dns_query.filter(models.DNSRecord.project_id == project_id)
            for record in dns_query.all():
                dns_records_by_hostname.setdefault(record.domain, []).append(record)

        subnets_by_host_id: Dict[int, List[str]] = {}
        for mapping in mappings:
            subnets_by_host_id.setdefault(mapping.host_id, []).append(mapping.subnet.cidr)

        # Add host details with DNS information
        for host in hosts:
            dns_records = dns_records_by_hostname.get(host.hostname, []) if host.hostname else []

            # Get ports
            ports = [
                {
                    'port_number': port.port_number,
                    'protocol': port.protocol,
                    'state': port.state,
                    'service_name': port.service_name,
                    'service_product': port.service_product,
                    'service_version': port.service_version
                }
                for port in host.ports
            ]

            host_subnets = subnets_by_host_id.get(host.id, [])
            
            host_data = {
                'id': host.id,
                'ip_address': host.ip_address,
                'hostname': host.hostname,
                'state': host.state,
                'os_name': host.os_name,
                'os_family': host.os_family,
                'scan_id': host.last_updated_scan_id,
                'ports': ports,
                'subnets': host_subnets,
                'dns_records': [
                    {
                        'domain': record.domain,
                        'record_type': record.record_type,
                        'value': record.value,
                        'ttl': record.ttl
                    }
                    for record in dns_records
                ]
            }
            report_data['hosts'].append(host_data)
        
        # Add Eyewitness results
        for result in eyewitness_results:
            result_data = {
                'id': result.id,
                'url': result.url,
                'ip_address': result.ip_address,
                'port': result.port,
                'title': result.title,
                'server_header': result.server_header,
                'response_code': result.response_code,
                'screenshot_path': result.screenshot_path,
                'scan_id': result.scan_id
            }
            report_data['eyewitness_results'].append(result_data)
        
        # Add out-of-scope hosts
        for oos_host in out_of_scope:
            oos_data = {
                'ip_address': oos_host.ip_address,
                'hostname': oos_host.hostname,
                'ports': oos_host.ports,
                'tool_source': oos_host.tool_source,
                'reason': oos_host.reason,
                'scan_id': oos_host.scan_id,
                'created_at': oos_host.created_at.isoformat() if oos_host.created_at else None
            }
            report_data['out_of_scope_hosts'].append(oos_data)
        
        return report_data

    def _gather_scan_data(self, scan: models.Scan) -> Dict[str, Any]:
        """Gather all data for a specific scan"""
        report_data = {
            'report_type': 'scan_report',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'app_version': settings.APP_VERSION,
            'frontend_version': settings.FRONTEND_VERSION,
            'scan': {
                'id': scan.id,
                'filename': scan.filename,
                'scan_type': scan.scan_type,
                'tool_name': scan.tool_name,
                'created_at': scan.created_at.isoformat() if scan.created_at else None,
                'command_line': scan.command_line,
                'version': scan.version
            },
            'hosts': [],
            'eyewitness_results': [],
            'out_of_scope_hosts': []
        }
        
        # Batch-fetch DNS records the same way as _gather_scope_data —
        # one IN-list query beats one-per-host on the inner loop.
        scan_hosts = list(scan.hosts)
        scan_hostnames = [h.hostname for h in scan_hosts if h.hostname]
        scan_dns_by_hostname: Dict[str, List[models.DNSRecord]] = {}
        if scan_hostnames:
            project_id = getattr(self.dns_service, "project_id", None)
            scan_dns_query = self.db.query(models.DNSRecord).filter(
                models.DNSRecord.domain.in_(scan_hostnames)
            )
            if project_id is not None:
                scan_dns_query = scan_dns_query.filter(models.DNSRecord.project_id == project_id)
            for record in scan_dns_query.all():
                scan_dns_by_hostname.setdefault(record.domain, []).append(record)

        # Add host data (similar to scope report but for single scan)
        for host in scan_hosts:
            dns_records = scan_dns_by_hostname.get(host.hostname, []) if host.hostname else []

            ports = [
                {
                    'port_number': port.port_number,
                    'protocol': port.protocol,
                    'state': port.state,
                    'service_name': port.service_name,
                    'service_product': port.service_product,
                    'service_version': port.service_version
                }
                for port in host.ports
            ]
            
            host_data = {
                'id': host.id,
                'ip_address': host.ip_address,
                'hostname': host.hostname,
                'state': host.state,
                'os_name': host.os_name,
                'ports': ports,
                'dns_records': [
                    {
                        'domain': record.domain,
                        'record_type': record.record_type,
                        'value': record.value
                    }
                    for record in dns_records
                ]
            }
            report_data['hosts'].append(host_data)
        
        # Add Eyewitness results
        for result in scan.eyewitness_results:
            result_data = {
                'url': result.url,
                'ip_address': result.ip_address,
                'port': result.port,
                'title': result.title,
                'response_code': result.response_code
            }
            report_data['eyewitness_results'].append(result_data)
        
        # Add out-of-scope hosts
        out_of_scope_hosts = self.db.query(models.OutOfScopeHost).filter(
            models.OutOfScopeHost.scan_id == scan.id
        ).all()
        
        for oos_host in out_of_scope_hosts:
            oos_data = {
                'ip_address': oos_host.ip_address,
                'hostname': oos_host.hostname,
                'ports': oos_host.ports,
                'tool_source': oos_host.tool_source,
                'reason': oos_host.reason
            }
            report_data['out_of_scope_hosts'].append(oos_data)
        
        return report_data

    def _format_json_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format data as JSON"""
        return {
            'content_type': 'application/json',
            'data': data,
            'filename': f"{data['report_type']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        }

    def _format_csv_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format data as CSV"""
        output = io.StringIO()
        
        # v2.86.4 — every cell now flows through ``safe_csv_row`` so a
        # scanner-supplied hostname / agent-recorded command starting
        # with ``=`` / ``+`` / ``-`` / ``@`` / tab / CR is rendered as
        # text instead of being evaluated as a spreadsheet formula on
        # open.  Pre-fix this path called ``writer.writerow`` directly,
        # bypassing the guard already in reports.py.
        if data['report_type'] == 'scope_report' or data['report_type'] == 'scan_report':
            # Create CSV for hosts
            writer = csv.writer(output)
            # Static header — no untrusted values.
            writer.writerow([
                'IP Address', 'Hostname', 'State', 'OS', 'Open Ports',
                'Subnets', 'DNS Records', 'Scan ID'
            ])

            for host in data.get('hosts', []):
                ports_str = '; '.join([f"{p['port_number']}/{p['protocol']}" for p in host['ports']])
                subnets_str = '; '.join(host.get('subnets', []))
                dns_str = '; '.join([f"{r['record_type']}: {r['value']}" for r in host['dns_records']])

                safe_csv_row(writer, [
                    host['ip_address'],
                    host['hostname'] or '',
                    host['state'] or '',
                    host['os_name'] or '',
                    ports_str,
                    subnets_str,
                    dns_str,
                    host['scan_id']
                ])

        elif data['report_type'] == 'out_of_scope_findings':
            writer = csv.writer(output)
            writer.writerow([
                'IP Address', 'Hostname', 'Tool Source', 'Reason', 'Ports', 'Found At'
            ])

            for finding in data.get('findings', []):
                ports_str = json.dumps(finding['ports']) if finding['ports'] else ''
                safe_csv_row(writer, [
                    finding['ip_address'],
                    finding['hostname'] or '',
                    finding.get('tool_name', ''),
                    finding['reason'] or '',
                    ports_str,
                    finding['found_at'] or ''
                ])

        elif data['report_type'] == 'test_plan_execution':
            writer = csv.writer(output)
            writer.writerow([
                'Host IP', 'Hostname', 'Priority', 'Phase', 'Entry Status',
                'Test Index', 'Test Status', 'Severity', 'Is Finding',
                'Command', 'Findings Summary', 'Executed At',
            ])
            for e in data.get('entries', []):
                results = e.get('results', [])
                if not results:
                    safe_csv_row(writer, [
                        e.get('host_ip') or '',
                        e.get('host_hostname') or '',
                        e.get('priority') or '',
                        e.get('test_phase') or '',
                        e.get('status') or '',
                        '', '', '', '', '', '', '',
                    ])
                    continue
                for r in results:
                    safe_csv_row(writer, [
                        e.get('host_ip') or '',
                        e.get('host_hostname') or '',
                        e.get('priority') or '',
                        e.get('test_phase') or '',
                        e.get('status') or '',
                        r.get('test_index', ''),
                        r.get('status') or '',
                        r.get('severity') or '',
                        'yes' if r.get('is_finding') else 'no',
                        (r.get('command_run') or '').replace('\n', ' '),
                        (r.get('findings_summary') or '').replace('\n', ' '),
                        r.get('executed_at') or '',
                    ])

        return {
            'content_type': 'text/csv',
            'data': output.getvalue(),
            'filename': f"{data['report_type']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        }

    def _format_html_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format data as professional HTML report"""
        html_content = ReportTemplates.generate_professional_html_report(data)
        
        return {
            'content_type': 'text/html',
            'data': html_content,
            'filename': f"BlueStick_{data['report_type']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.html"
        }

    def _enhance_scope_data_with_calculations(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enhance scope report data with subnet calculations"""
        scope = report_data.get('scope', {})
        subnets = scope.get('subnets', [])
        
        # Calculate enhanced metrics for each subnet
        enhanced_subnets = []
        for subnet in subnets:
            cidr = subnet.get('cidr', '')
            if cidr:
                # Get subnet metrics
                metrics = SubnetCalculator.calculate_subnet_metrics(cidr)
                
                # Count hosts in this subnet (simplified)
                discovered_hosts = len([h for h in report_data.get('hosts', []) 
                                      if self._ip_in_subnet(h.get('ip_address', ''), cidr)])
                
                utilization = SubnetCalculator.calculate_utilization_percentage(discovered_hosts, cidr)
                risk_info = SubnetCalculator.get_subnet_risk_level(utilization, discovered_hosts)
                
                enhanced_subnet = subnet.copy()
                enhanced_subnet.update({
                    'total_addresses': metrics['total_addresses'],
                    'usable_addresses': metrics['usable_addresses'],
                    'discovered_hosts': discovered_hosts,
                    'utilization_percentage': utilization,
                    'risk_level': risk_info['risk_level'],
                    'risk_description': risk_info['risk_description'],
                    'is_private': metrics['is_private']
                })
                enhanced_subnets.append(enhanced_subnet)
        
        # Update scope with enhanced subnet data
        report_data['scope']['subnets'] = enhanced_subnets
        
        # Calculate scope-level aggregates
        if enhanced_subnets:
            aggregates = SubnetCalculator.calculate_scope_aggregates([
                {
                    'total_addresses': s['total_addresses'],
                    'usable_addresses': s['usable_addresses'],
                    'discovered_hosts': s['discovered_hosts'],
                    'utilization_percentage': s['utilization_percentage'],
                    'risk_level': s['risk_level']
                }
                for s in enhanced_subnets
            ])
            report_data['scope_aggregates'] = aggregates
        
        return report_data
    
    def _ip_in_subnet(self, ip: str, cidr: str) -> bool:
        """Simple check if IP is in subnet (basic implementation)"""
        try:
            import ipaddress
            return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
        except (ipaddress.AddressValueError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Test Plan Execution Report
    # ------------------------------------------------------------------

    def export_test_plan_execution_report(
        self,
        plan_id: int,
        session_id: Optional[int] = None,
        format_type: str = 'json',
    ) -> Dict[str, Any]:
        """Export a report for a test plan execution session.

        If ``session_id`` is None, the most-recently-started session for
        the plan is used.  Supported formats: json, csv, html, pdf.
        """
        from app.db.models_agent import (
            TestPlan, TestPlanEntry, ExecutionSession,
            TestExecutionResult, HostSanityCheck,
        )

        plan = self.db.query(TestPlan).filter(TestPlan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Test plan {plan_id} not found")

        if session_id is not None:
            session = (
                self.db.query(ExecutionSession)
                .filter(
                    ExecutionSession.id == session_id,
                    ExecutionSession.test_plan_id == plan_id,
                )
                .first()
            )
            if not session:
                raise ValueError(
                    f"Execution session {session_id} not found for plan {plan_id}"
                )
        else:
            session = (
                self.db.query(ExecutionSession)
                .filter(ExecutionSession.test_plan_id == plan_id)
                .order_by(ExecutionSession.started_at.desc())
                .first()
            )
            if not session:
                raise ValueError(
                    f"No execution sessions exist for plan {plan_id} — "
                    f"cannot generate an execution report."
                )

        report_data = self._gather_test_plan_execution_data(plan, session)

        fmt = format_type.lower()
        if fmt == 'json':
            return self._format_json_report(report_data)
        if fmt == 'csv':
            return self._format_csv_report(report_data)
        if fmt == 'html':
            return self._format_html_report(report_data)
        if fmt == 'pdf':
            return self._format_pdf_report(report_data)
        raise ValueError(f"Unsupported export format: {format_type}")

    def _gather_test_plan_execution_data(
        self,
        plan,                 # TestPlan (imported lazily by caller)
        session,              # ExecutionSession
    ) -> Dict[str, Any]:
        """Assemble the full execution-report payload.

        Produces a single dict passed to both the HTML template and JSON/CSV
        formatters.  Groups per-test results and sanity checks by entry so
        the template can render each host as a standalone section.
        """
        from app.db.models_agent import (
            TestPlanEntry, TestExecutionResult, HostSanityCheck,
        )
        from sqlalchemy.orm import joinedload

        # Code review #refactor-3: eager-load the related host so the
        # per-entry loop below doesn't fire one SELECT per entry when
        # it reaches ``host.ip_address`` / ``host.hostname`` / etc.
        # On large plans the old behavior was O(entries) host queries
        # under a single report render.
        entries = (
            self.db.query(TestPlanEntry)
            .options(joinedload(TestPlanEntry.host))
            .filter(TestPlanEntry.test_plan_id == plan.id)
            .all()
        )
        entry_map = {e.id: e for e in entries}

        results = (
            self.db.query(TestExecutionResult)
            .filter(TestExecutionResult.execution_session_id == session.id)
            .order_by(
                TestExecutionResult.entry_id,
                TestExecutionResult.test_index,
            )
            .all()
        )
        sanity_checks = (
            self.db.query(HostSanityCheck)
            .filter(HostSanityCheck.execution_session_id == session.id)
            .all()
        )

        severity_priority = {
            'critical': 0, 'high': 1, 'medium': 2,
            'low': 3, 'info': 4, 'none': 5,
        }
        findings_by_severity: Dict[str, int] = {}
        tests_executed = 0
        tests_skipped = 0
        tests_failed = 0

        # Cap raw_output in the rendered report so a multi-MB nmap / ffuf
        # dump can't blow up PDF/HTML generation or the JSON response body.
        # The full text is still in the DB — this only trims the view.
        RAW_OUTPUT_MAX = 16 * 1024  # 16 KB per test result

        def _trim_raw(text: Optional[str]) -> Optional[str]:
            if not text:
                return text
            if len(text) <= RAW_OUTPUT_MAX:
                return text
            head = text[:RAW_OUTPUT_MAX]
            omitted = len(text) - RAW_OUTPUT_MAX
            return f"{head}\n… [{omitted} bytes truncated in report view] …"

        results_by_entry: Dict[int, List[Dict[str, Any]]] = {}
        for r in results:
            results_by_entry.setdefault(r.entry_id, []).append({
                'test_index': r.test_index,
                'status': r.status,
                'command_run': r.command_run,
                'raw_output': _trim_raw(r.raw_output),
                'findings_summary': r.findings_summary,
                'severity': r.severity,
                'is_finding': bool(r.is_finding),
                'executed_at': r.executed_at.isoformat() if r.executed_at else None,
            })
            if r.status == 'executed':
                tests_executed += 1
            elif r.status == 'skipped':
                tests_skipped += 1
            elif r.status == 'failed':
                tests_failed += 1
            if r.is_finding and r.severity:
                sev = (r.severity or 'none').lower()
                findings_by_severity[sev] = findings_by_severity.get(sev, 0) + 1

        sanity_by_entry: Dict[int, Dict[str, Any]] = {}
        for sc in sanity_checks:
            sanity_by_entry[sc.entry_id] = {
                'method': sc.method,
                'target_ip': sc.target_ip,
                'port_checked': sc.port_checked,
                'expected_value': sc.expected_value,
                'actual_value': sc.actual_value,
                'source_ip': sc.source_ip,
                'dns_result': sc.dns_result,
                'passed': bool(sc.passed),
                'details': sc.details,
                'checked_at': sc.checked_at.isoformat() if sc.checked_at else None,
            }

        # Build per-host entry payloads sorted by severity-of-findings first,
        # then by priority (critical → info).  Hosts with findings float to
        # the top of the report so readers see the important stuff first.
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
        entry_payloads: List[Dict[str, Any]] = []
        for e in entries:
            host = e.host
            e_results = results_by_entry.get(e.id, [])
            worst_sev = None
            for r in e_results:
                if r['is_finding'] and r['severity']:
                    rs = (r['severity'] or 'none').lower()
                    if worst_sev is None or severity_priority.get(rs, 99) < severity_priority.get(worst_sev, 99):
                        worst_sev = rs
            entry_payloads.append({
                'entry_id': e.id,
                'host_id': e.host_id,
                'host_ip': host.ip_address if host else None,
                'host_hostname': host.hostname if host else None,
                'host_os': getattr(host, 'os_name', None) if host else None,
                'priority': e.priority,
                'test_phase': e.test_phase,
                'status': e.status,
                'proposed_tests': e.proposed_tests or [],
                'rationale': e.rationale,
                'findings': e.findings,
                'notes': e.notes,
                'worst_finding_severity': worst_sev,
                'results': e_results,
                'sanity_check': sanity_by_entry.get(e.id),
            })

        def _sort_key(p):
            wf = severity_priority.get(p.get('worst_finding_severity') or 'none', 99)
            pr = priority_order.get(p.get('priority') or 'info', 99)
            return (wf, pr)

        entry_payloads.sort(key=_sort_key)

        statistics = {
            'total_entries': len(entries),
            'total_results': len(results),
            'tests_executed': tests_executed,
            'tests_skipped': tests_skipped,
            'tests_failed': tests_failed,
            'total_findings': sum(findings_by_severity.values()),
            'findings_by_severity': findings_by_severity,
            'sanity_checks_run': len(sanity_checks),
            'sanity_checks_failed': sum(1 for v in sanity_by_entry.values() if not v['passed']),
        }

        return {
            'report_type': 'test_plan_execution',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'app_version': settings.APP_VERSION,
            'frontend_version': settings.FRONTEND_VERSION,
            'plan': {
                'id': plan.id,
                'version': plan.version,
                'title': plan.title,
                'description': plan.description,
                'status': plan.status,
                'project_id': plan.project_id,
                'created_at': plan.created_at.isoformat() if plan.created_at else None,
                'approved_at': plan.approved_at.isoformat() if plan.approved_at else None,
                'completed_at': plan.completed_at.isoformat() if plan.completed_at else None,
            },
            'session': {
                'id': session.id,
                'status': session.status,
                'started_at': session.started_at.isoformat() if session.started_at else None,
                'completed_at': session.completed_at.isoformat() if session.completed_at else None,
            },
            'statistics': statistics,
            'entries': entry_payloads,
        }

    def _format_pdf_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Render the HTML report to PDF via WeasyPrint.

        WeasyPrint is imported lazily so the rest of the export surface
        keeps working even if the library (or its system deps) are absent
        — callers get a clean 501 with the missing-dep message instead of
        a module-load error at import time.
        """
        try:
            from weasyprint import HTML
        except Exception as exc:
            raise RuntimeError(
                "PDF export requires WeasyPrint. Install with `pip install "
                "weasyprint` and ensure libpango/libcairo are available. "
                f"Underlying error: {exc}"
            )

        html_content = ReportTemplates.generate_professional_html_report(data)
        pdf_bytes = HTML(string=html_content).write_pdf()
        return {
            'content_type': 'application/pdf',
            'data': pdf_bytes,
            'filename': (
                f"BlueStick_{data['report_type']}_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
            ),
        }
