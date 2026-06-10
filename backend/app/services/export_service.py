import json
import csv
import io
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from app.core.config import settings
from app.services.csv_utils import safe_csv_row
from app.services.report_templates import ReportTemplates
import logging

logger = logging.getLogger(__name__)

class ExportService:
    def __init__(self, db: Session):
        self.db = db

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
