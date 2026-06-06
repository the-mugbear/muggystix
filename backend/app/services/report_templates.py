import base64
import json
from datetime import datetime
from typing import Dict, Any, List

from app.core.config import settings
from app.services.subnet_calculator import SubnetCalculator

class ReportTemplates:
    """Professional report templates for BlueStick exports"""
    
    @staticmethod
    def get_css_styles() -> str:
        """Professional CSS styling for HTML reports"""
        return """
        <style>
            :root {
                color-scheme: dark;
                --bg: #0d1117;
                --bg-elevated: #141a22;
                --bg-panel: #19212b;
                --bg-panel-soft: #202a36;
                --text: #edf2f7;
                --muted: #a6b3c2;
                --subtle: #7d8a99;
                --border: #2d3847;
                --accent: #21c7a8;
                --accent-strong: #4fd1c5;
                --accent-warm: #f5b84b;
                --danger: #ff6b7a;
                --warning: #f7c948;
                --success: #38c172;
                --info: #63b3ed;
                --shadow: 0 18px 50px rgba(0, 0, 0, 0.32);
            }

            * {
                box-sizing: border-box;
            }

            html {
                /* Anchor-link jumps don't land under the sticky .report-nav */
                scroll-padding-top: 5rem;
            }

            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                margin: 0;
                padding: 28px;
                background:
                    radial-gradient(circle at top left, rgba(33, 199, 168, 0.16), transparent 34rem),
                    linear-gradient(135deg, #0d1117 0%, #121822 50%, #16151c 100%);
                color: var(--text);
            }

            a {
                color: var(--accent-strong);
            }
            
            .report-header {
                background:
                    linear-gradient(135deg, rgba(33, 199, 168, 0.22) 0%, rgba(245, 184, 75, 0.10) 100%),
                    var(--bg-elevated);
                color: var(--text);
                padding: 32px;
                border-radius: 8px;
                margin-bottom: 22px;
                box-shadow: var(--shadow);
                border: 1px solid rgba(79, 209, 197, 0.26);
            }
            
            .report-title {
                font-size: clamp(2rem, 5vw, 3.1rem);
                font-weight: 700;
                margin-bottom: 10px;
                line-height: 1.05;
            }
            
            .report-subtitle {
                font-size: 1.2em;
                color: var(--muted);
                margin-bottom: 0;
            }

            .report-nav {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 0 0 22px;
                padding: 12px;
                border: 1px solid var(--border);
                border-radius: 8px;
                background: rgba(20, 26, 34, 0.78);
                position: sticky;
                top: 0;
                z-index: 10;
                backdrop-filter: blur(10px);
            }

            .report-nav a {
                display: inline-flex;
                align-items: center;
                min-height: 34px;
                padding: 6px 12px;
                border-radius: 6px;
                background: var(--bg-panel-soft);
                border: 1px solid var(--border);
                color: var(--text);
                text-decoration: none;
                font-weight: 600;
                font-size: 0.9em;
            }

            .report-nav a:hover {
                border-color: var(--accent);
                color: var(--accent-strong);
            }

            .report-nav a:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
            }
            
            .executive-summary {
                background: linear-gradient(135deg, rgba(33, 199, 168, 0.10), rgba(245, 184, 75, 0.06)), var(--bg-panel);
                border-left: 5px solid var(--accent);
                padding: 25px;
                margin-bottom: 22px;
                border-radius: 8px;
                box-shadow: var(--shadow);
                border-top: 1px solid var(--border);
                border-right: 1px solid var(--border);
                border-bottom: 1px solid var(--border);
            }
            
            .section {
                background: var(--bg-panel);
                margin-bottom: 22px;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: var(--shadow);
                border: 1px solid var(--border);
            }
            
            .section-header {
                background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
                padding: 20px;
                border-bottom: 1px solid var(--border);
                font-size: 1.4em;
                font-weight: 600;
                color: var(--text);
            }
            
            .section-content {
                padding: 25px;
            }
            
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .stat-card {
                background: linear-gradient(135deg, rgba(33, 199, 168, 0.12), rgba(255,255,255,0.03));
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 20px;
                text-align: center;
                transition: transform 0.2s ease;
            }
            
            .stat-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 14px 30px rgba(0,0,0,0.28);
                border-color: rgba(33, 199, 168, 0.55);
            }
            
            .stat-value {
                font-size: 2.2em;
                font-weight: 700;
                color: var(--accent-strong);
                margin-bottom: 5px;
            }
            
            .stat-label {
                color: var(--muted);
                font-size: 0.9em;
                text-transform: uppercase;
                font-weight: 600;
                letter-spacing: 0;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: var(--bg-panel);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 10px 28px rgba(0,0,0,0.20);
                border: 1px solid var(--border);
            }

            .interactive-table-wrapper {
                display: flex;
                flex-direction: column;
                gap: 12px;
            }

            .table-controls {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
            }

            .table-search {
                padding: 9px 14px;
                border: 1px solid var(--border);
                border-radius: 6px;
                font-size: 0.95em;
                min-width: 220px;
                background: var(--bg);
                color: var(--text);
                box-shadow: inset 0 1px 2px rgba(0,0,0,0.22);
            }

            .table-search:focus {
                outline: none;
                border-color: var(--accent);
                box-shadow: 0 0 0 0.2rem rgba(33,199,168,0.20);
            }

            .table-hint {
                font-size: 0.85em;
                color: var(--muted);
                display: flex;
                align-items: center;
                gap: 6px;
            }

            .table-hint::before {
                content: '\u2139';
                font-size: 1em;
                color: var(--info);
            }
            
            th {
                background: linear-gradient(135deg, #202a36 0%, #263241 100%);
                color: var(--text);
                font-weight: 600;
                padding: 15px 12px;
                text-align: left;
                font-size: 0.9em;
                text-transform: uppercase;
                letter-spacing: 0;
                border-bottom: 1px solid var(--border);
            }

            th.sortable {
                cursor: pointer;
                position: relative;
                user-select: none;
            }

            th.sortable::after {
                content: '\u25B4\u25BE';
                position: absolute;
                right: 10px;
                font-size: 0.65em;
                opacity: 0.3;
            }

            th.sortable.sorted-asc::after {
                content: '\u25B2';
                opacity: 0.9;
            }

            th.sortable.sorted-desc::after {
                content: '\u25BC';
                opacity: 0.9;
            }

            /* The sort toggle is rendered as a real <button> inside the
               <th> so keyboard users can activate it with Enter/Space
               and screen readers announce it as a button.  Reset the
               native button chrome so it looks like the surrounding
               header cell. */
            th.sortable button.sort-toggle {
                background: transparent;
                border: 0;
                padding: 0;
                margin: 0;
                color: inherit;
                font: inherit;
                text-align: inherit;
                text-transform: inherit;
                letter-spacing: inherit;
                cursor: inherit;
                width: 100%;
                display: inline-flex;
                align-items: center;
                gap: 6px;
            }

            th.sortable button.sort-toggle:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
                border-radius: 4px;
            }

            td {
                padding: 12px;
                border-bottom: 1px solid var(--border);
                vertical-align: top;
            }
            
            tr:nth-child(even) {
                background-color: rgba(255,255,255,0.05);
            }
            
            tr:hover {
                background-color: rgba(33, 199, 168, 0.08);
                transition: background-color 0.2s ease;
            }
            
            code, pre {
                font-family: 'Cascadia Code', 'SFMono-Regular', Consolas, monospace;
            }

            code {
                color: #d6bcfa;
            }

            pre {
                background: #080b10;
                color: #d9e2ec;
                border: 1px solid var(--border);
                border-radius: 6px;
                padding: 12px;
                overflow-x: auto;
            }

            .risk-critical { background-color: rgba(255, 107, 122, 0.16); color: #ffb3bc; }
            .risk-high { background-color: rgba(245, 184, 75, 0.16); color: #ffd58a; }
            .risk-medium { background-color: rgba(99, 179, 237, 0.16); color: #a8d8ff; }
            .risk-low { background-color: rgba(56, 193, 114, 0.16); color: #9ae6b4; }
            .risk-unknown { background-color: rgba(166, 179, 194, 0.10); color: var(--muted); }
            
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.8em;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0;
            }
            
            .badge-success { background-color: var(--success); color: #06140c; }
            .badge-warning { background-color: var(--warning); color: #1f1600; }
            .badge-danger { background-color: var(--danger); color: #210308; }
            .badge-info { background-color: var(--info); color: #06111d; }
            .badge-secondary { background-color: #526070; color: white; }
            
            .recommendations {
                background: rgba(245, 184, 75, 0.10);
                border: 1px solid rgba(245, 184, 75, 0.38);
                border-radius: 8px;
                padding: 20px;
                margin: 20px 0;
            }
            
            .recommendations h4 {
                color: #ffd58a;
                margin-bottom: 15px;
            }
            
            .recommendation-item {
                background: var(--bg-panel);
                border-left: 4px solid var(--accent-warm);
                padding: 15px;
                margin: 10px 0;
                border-radius: 0 5px 5px 0;
            }
            
            .out-of-scope {
                background-color: rgba(255, 107, 122, 0.13);
                border-left: 4px solid var(--danger);
            }

            .host-execution-card {
                margin-bottom: 24px;
                border: 1px solid var(--border);
                border-radius: 8px;
                overflow: hidden;
                background: var(--bg-panel-soft);
            }

            .host-execution-header {
                padding: 14px 16px;
                background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(33,199,168,0.06));
                border-bottom: 1px solid var(--border);
            }

            .host-execution-title {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
            }

            .host-execution-body {
                padding: 14px 16px;
            }

            .report-pill-row {
                display: flex;
                gap: 6px;
                flex-wrap: wrap;
            }

            .report-pill {
                background: #526070;
                color: white;
                padding: 3px 9px;
                border-radius: 999px;
                font-size: 0.8em;
                font-weight: 600;
            }

            .report-pill-info {
                background: var(--info);
                color: #06111d;
            }

            .report-pill-dark {
                background: #2f3b4a;
                color: var(--text);
            }

            .sanity-panel,
            .finding-panel {
                margin: 10px 0;
                padding: 12px;
                background: rgba(13, 17, 23, 0.55);
                border-left: 4px solid var(--accent);
                border-radius: 0 6px 6px 0;
            }

            .sanity-panel.failed {
                border-left-color: var(--danger);
            }

            .sanity-panel.missing {
                border-left-color: var(--warning);
            }

            .finding-panel {
                border-left-color: var(--accent-warm);
            }

            .muted-text {
                color: var(--muted);
            }
            
            .footer {
                text-align: center;
                padding: 30px;
                color: var(--muted);
                font-size: 0.9em;
                border-top: 1px solid var(--border);
                margin-top: 50px;
            }
            
            .logo {
                float: right;
                max-height: 60px;
                margin-left: 20px;
            }

            .metadata {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
                font-size: 0.9em;
                opacity: 0.9;
            }

            .version-tag {
                font-size: 0.85em;
                color: var(--muted);
                margin-top: 6px;
                letter-spacing: 0;
            }
            
            .chart-placeholder {
                background: var(--bg-panel-soft);
                border: 2px dashed var(--border);
                border-radius: 8px;
                padding: 40px;
                text-align: center;
                color: var(--muted);
                margin: 20px 0;
            }
            
            @media print {
                :root {
                    color-scheme: light;
                    --bg: #ffffff;
                    --bg-elevated: #ffffff;
                    --bg-panel: #ffffff;
                    --bg-panel-soft: #f4f6f8;
                    --text: #1f2933;
                    --muted: #52606d;
                    --border: #d9e2ec;
                }
                body { background: white; color: var(--text); padding: 12px; }
                .section, .executive-summary { page-break-inside: avoid; box-shadow: none; }
                .report-header { background: #ffffff !important; color: var(--text); box-shadow: none; }
                .report-nav { display: none; }
                .stat-card:hover { transform: none; }
                tr:hover { background-color: transparent; }
                /* Override the dark <pre>/<code> theme so raw output prints
                   as dark text on light paper instead of soaking up toner. */
                pre, code {
                    background: #f4f6f8 !important;
                    color: #1f2933 !important;
                    border-color: #d9e2ec !important;
                }
            }
            
            @media (max-width: 768px) {
                .stats-grid { grid-template-columns: 1fr; }
                .metadata { flex-direction: column; align-items: flex-start; }
                .logo { float: none; margin: 10px 0; }
            }
        </style>
        """

    @staticmethod
    def get_interactive_scripts() -> str:
        """Client-side helpers for table filtering and sorting"""
        return """
        <script>
            (function() {
                const initializeTables = () => {
                    document.querySelectorAll('.interactive-table-wrapper').forEach((wrapper) => {
                        const table = wrapper.querySelector('table.interactive-table');
                        if (!table) {
                            return;
                        }
                        const tbody = table.querySelector('tbody');
                        if (!tbody) {
                            return;
                        }

                        const headers = Array.from(table.querySelectorAll('thead th'));
                        const staticRows = Array.from(tbody.querySelectorAll('tr[data-static-row="true"]'));
                        const dynamicRows = () => Array.from(tbody.querySelectorAll('tr')).filter((row) => row.getAttribute('data-static-row') !== 'true');

                        const searchInput = wrapper.querySelector('.table-search');
                        if (searchInput) {
                            searchInput.addEventListener('input', (event) => {
                                applyFilter(dynamicRows(), staticRows, event.target.value || '');
                            });
                        }

                        headers.forEach((header, index) => {
                            header.classList.add('sortable');
                            // aria-sort communicates the current sort state to
                            // assistive tech.  Initial value is 'none' so screen
                            // readers know the column is sortable but not yet
                            // sorted.
                            header.setAttribute('aria-sort', 'none');

                            // Wrap the existing header content in a real
                            // <button> so keyboard users get Enter/Space
                            // activation for free and screen readers announce
                            // it as an interactive control.  Idempotent — if a
                            // sort-toggle button already wraps the content
                            // (re-init), skip the DOM rewrite.
                            let btn = header.querySelector('button.sort-toggle');
                            if (!btn) {
                                btn = document.createElement('button');
                                btn.type = 'button';
                                btn.className = 'sort-toggle';
                                while (header.firstChild) {
                                    btn.appendChild(header.firstChild);
                                }
                                header.appendChild(btn);
                            }

                            btn.addEventListener('click', () => {
                                const current = header.getAttribute('aria-sort') || 'none';
                                const next = current === 'ascending' ? 'descending' : 'ascending';

                                headers.forEach((h) => {
                                    h.setAttribute('aria-sort', 'none');
                                    h.classList.remove('sorted-asc', 'sorted-desc');
                                });

                                header.setAttribute('aria-sort', next);
                                header.classList.add(next === 'ascending' ? 'sorted-asc' : 'sorted-desc');

                                const rows = dynamicRows();
                                rows.sort((a, b) => {
                                    const aValue = toComparable(a.children[index] ? a.children[index].innerText : '');
                                    const bValue = toComparable(b.children[index] ? b.children[index].innerText : '');

                                    let comparison = 0;
                                    if (aValue.type === 'number' && bValue.type === 'number') {
                                        comparison = aValue.value - bValue.value;
                                    } else {
                                        comparison = aValue.value.localeCompare(bValue.value, undefined, { numeric: true, sensitivity: 'base' });
                                    }

                                    return next === 'ascending' ? comparison : -comparison;
                                });

                                rows.forEach((row) => tbody.appendChild(row));
                                staticRows.forEach((row) => tbody.appendChild(row));
                            });
                        });

                        applyFilter(dynamicRows(), staticRows, searchInput ? searchInput.value || '' : '');
                    });
                };

                const toComparable = (raw) => {
                    const value = (raw || '').trim();
                    const numeric = parseFloat(value.replace(/[^0-9.-]/g, ''));
                    if (!Number.isNaN(numeric) && /^-?\\d/.test(value)) {
                        return { type: 'number', value: numeric };
                    }
                    return { type: 'string', value: value.toLowerCase() };
                };

                const applyFilter = (rows, staticRows, term) => {
                    const query = term.trim().toLowerCase();
                    rows.forEach((row) => {
                        const text = row.innerText.toLowerCase();
                        row.style.display = text.includes(query) ? '' : 'none';
                    });

                    if (staticRows.length) {
                        const display = query ? 'none' : '';
                        staticRows.forEach((row) => {
                            row.style.display = display;
                        });
                    }
                };

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', initializeTables);
                } else {
                    initializeTables();
                }
            })();
        </script>
        """

    @staticmethod
    def generate_executive_summary(report_data: Dict[str, Any]) -> str:
        """Generate executive summary based on report data"""
        report_type = report_data.get('report_type', '')

        if report_type == 'scope_report':
            return ReportTemplates._generate_scope_executive_summary(report_data)
        elif report_type == 'scan_report':
            return ReportTemplates._generate_scan_executive_summary(report_data)
        elif report_type == 'out_of_scope_findings':
            return ReportTemplates._generate_out_of_scope_summary(report_data)
        elif report_type == 'test_plan_execution':
            return ReportTemplates._generate_execution_executive_summary(report_data)
        else:
            return "This report provides a comprehensive analysis of network discovery results."
    
    @staticmethod
    def _generate_scope_executive_summary(data: Dict[str, Any]) -> str:
        """Generate executive summary for scope reports"""
        stats = data.get('statistics', {})
        scope = data.get('scope', {})
        
        # Calculate subnet metrics if available
        subnet_metrics = []
        for subnet_data in scope.get('subnets', []):
            cidr = subnet_data.get('cidr', '')
            if cidr:
                metrics = SubnetCalculator.calculate_subnet_metrics(cidr)
                metrics['cidr'] = cidr
                subnet_metrics.append(metrics)
        
        aggregates = SubnetCalculator.calculate_scope_aggregates([
            {
                'total_addresses': m['total_addresses'], 
                'usable_addresses': m['usable_addresses'],
                'discovered_hosts': 0,  # We'll update this with actual data
                'utilization_percentage': 0,
                'risk_level': 'unknown'
            } 
            for m in subnet_metrics
        ])
        
        summary = f"""
        <div class="executive-summary">
            <h3>Executive Summary</h3>
            <p><strong>Scope:</strong> {scope.get('name', 'Unknown')} contains {stats.get('total_subnets', 0)} 
            subnet(s) with a total address space of {aggregates.get('total_usable_addresses', 0):,} usable IP addresses.</p>
            
            <p><strong>Discovery Results:</strong> Network scanning discovered {stats.get('total_hosts', 0)} 
            active hosts across {stats.get('total_scans', 0)} scan(s), indicating network utilization and 
            potential security exposure points.</p>
            
            <p><strong>Web Services:</strong> {stats.get('total_eyewitness_results', 0)} web services were 
            identified and catalogued, providing insight into web-based attack surfaces.</p>
            
            <p><strong>Out-of-Scope Findings:</strong> {stats.get('out_of_scope_hosts', 0)} hosts were 
            discovered outside the defined scope, requiring investigation to ensure comprehensive coverage.</p>
            
            <p><strong>Security Implications:</strong> Each discovered host represents a potential attack vector. 
            Priority should be given to securing exposed services and ensuring proper network segmentation.</p>
        </div>
        """
        return summary
    
    @staticmethod
    def _generate_scan_executive_summary(data: Dict[str, Any]) -> str:
        """Generate executive summary for scan reports"""
        scan = data.get('scan', {})
        hosts = data.get('hosts', [])
        
        open_ports_count = sum(len([p for p in host.get('ports', []) if p.get('state') == 'open']) for host in hosts)
        
        summary = f"""
        <div class="executive-summary">
            <h3>Executive Summary</h3>
            <p><strong>Scan Overview:</strong> This {scan.get('tool_name', 'network')} scan 
            ({scan.get('filename', 'N/A')}) discovered {len(hosts)} active hosts with a total of 
            {open_ports_count} open ports across the target network.</p>
            
            <p><strong>Security Exposure:</strong> Each open port represents a potential entry point for 
            attackers. Critical services should be reviewed for necessity, proper configuration, and access controls.</p>
            
            <p><strong>Risk Assessment:</strong> Hosts with multiple open ports or common attack vectors 
            (SSH, RDP, web services) require immediate security review and hardening measures.</p>
        </div>
        """
        return summary
    
    @staticmethod
    def _generate_out_of_scope_summary(data: Dict[str, Any]) -> str:
        """Generate executive summary for out-of-scope reports"""
        total_findings = data.get('total_out_of_scope_hosts', 0)
        by_tool = data.get('findings_by_tool', {})
        
        summary = f"""
        <div class="executive-summary">
            <h3>Executive Summary</h3>
            <p><strong>Scope Verification:</strong> {total_findings} hosts were discovered outside 
            the defined project scope during network reconnaissance activities.</p>
            
            <p><strong>Discovery Sources:</strong> These findings originate from {len(by_tool)} different 
            scanning tools, indicating comprehensive coverage may have extended beyond intended boundaries.</p>
            
            <p><strong>Action Required:</strong> Out-of-scope discoveries should be reviewed to determine 
            if scope expansion is needed or if scanning parameters require adjustment for future assessments.</p>
        </div>
        """
        return summary
    
    @staticmethod
    def generate_recommendations(report_data: Dict[str, Any]) -> str:
        """Generate security recommendations based on report data"""
        recommendations = []
        
        # Generic network security recommendations
        recommendations.extend([
            "Implement network segmentation to limit lateral movement",
            "Regularly update and patch all discovered systems",
            "Deploy intrusion detection systems for continuous monitoring",
            "Conduct regular vulnerability assessments",
            "Implement proper access controls and authentication"
        ])
        
        # Report-specific recommendations
        if report_data.get('report_type') == 'scope_report':
            hosts = report_data.get('hosts', [])
            if any('22' in str(p.get('port_number', '')) for host in hosts for p in host.get('ports', [])):
                recommendations.append("Review SSH access and implement key-based authentication")
            if any('80' in str(p.get('port_number', '')) or '443' in str(p.get('port_number', '')) 
                   for host in hosts for p in host.get('ports', [])):
                recommendations.append("Audit web applications for security vulnerabilities")
        
        html = '<div class="recommendations" id="recommendations"><h4>Security Recommendations</h4>'
        for i, rec in enumerate(recommendations[:8], 1):  # Limit to top 8
            html += f'<div class="recommendation-item">{i}. {rec}</div>'
        html += '</div>'
        
        return html
    
    @staticmethod
    def generate_professional_html_report(data: Dict[str, Any]) -> str:
        """Generate a comprehensive professional HTML report"""
        report_type = data.get('report_type', 'Network Report')
        generated_at = data.get('generated_at', datetime.utcnow().isoformat())
        backend_version = data.get('app_version', settings.APP_VERSION)
        frontend_version = data.get('frontend_version', settings.FRONTEND_VERSION)

        # Header with logo and metadata
        header_section = f"""
        <div class="report-header">
            <div class="metadata">
                <div>
                    <div class="report-title">BlueStick Security Report</div>
                    <div class="report-subtitle">{report_type.replace('_', ' ').title()}</div>
                    <div class="version-tag">Backend v{backend_version} | Frontend v{frontend_version}</div>
                </div>
                <div>
                    <strong>Generated:</strong> {datetime.fromisoformat(generated_at.replace('Z', '')).strftime('%B %d, %Y at %I:%M %p')}<br>
                    <strong>Report ID:</strong> NM-{datetime.utcnow().strftime('%Y%m%d')}-{abs(hash(str(data)))%10000:04d}
                </div>
            </div>
        </div>
        """
        
        # Executive Summary
        executive_summary = ReportTemplates.generate_executive_summary(data).replace(
            '<div class="executive-summary">',
            '<div class="executive-summary" id="summary">',
            1,
        )
        
        # Statistics Overview
        stats_section = ReportTemplates._generate_stats_section(data)
        
        # Main Content Sections
        content_sections = ReportTemplates._generate_content_sections(data)
        
        # Recommendations
        recommendations = ReportTemplates.generate_recommendations(data)

        # Footer
        footer = f"""
        <div class="footer">
            <p>This report was generated by BlueStick - Professional Network Discovery Platform</p>
            <p>Platform Versions: Backend v{backend_version} | Frontend v{frontend_version}</p>
            <p>© {datetime.utcnow().year} BlueStick. For questions about this report, 
            contact your security team or system administrator.</p>
        </div>
        """

        # Client-side enhancements
        scripts = ReportTemplates.get_interactive_scripts()

        # Build the sticky nav conditionally: only link to sections that
        # were actually rendered. Some report types skip metrics (e.g.
        # out-of-scope findings carry no statistics block) or
        # recommendations, and a dead anchor makes the report feel
        # broken. The executive summary is always present, so #summary
        # is the only unconditional link.
        nav_links = ['<a href="#summary">Summary</a>']
        if stats_section:
            nav_links.append('<a href="#metrics">Metrics</a>')
        if content_sections:
            nav_links.append('<a href="#details">Details</a>')
        if recommendations:
            nav_links.append('<a href="#recommendations">Recommendations</a>')
        report_nav = (
            '<nav class="report-nav" aria-label="Report sections">\n'
            + '\n'.join(f'                {link}' for link in nav_links)
            + '\n            </nav>'
        )

        # Complete HTML
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>BlueStick Report - {report_type.replace('_', ' ').title()}</title>
            {ReportTemplates.get_css_styles()}
        </head>
        <body>
            {header_section}
            {report_nav}
            {executive_summary}
            {stats_section}
            {content_sections}
            {recommendations}
            {footer}
            {scripts}
        </body>
        </html>
        """

        return html
    
    @staticmethod
    def _generate_stats_section(data: Dict[str, Any]) -> str:
        """Generate statistics overview section"""
        stats = data.get('statistics', {})

        if not stats:
            return ""

        stats_cards = []

        # Common stats for all report types
        if 'total_hosts' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["total_hosts"]}</div><div class="stat-label">Discovered Hosts</div></div>')
        if 'total_scans' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["total_scans"]}</div><div class="stat-label">Scans Analyzed</div></div>')
        if 'total_subnets' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["total_subnets"]}</div><div class="stat-label">Network Subnets</div></div>')
        if 'out_of_scope_hosts' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["out_of_scope_hosts"]}</div><div class="stat-label">Out-of-Scope</div></div>')

        # Test plan execution stats
        if 'total_entries' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["total_entries"]}</div><div class="stat-label">Hosts in Plan</div></div>')
        if 'tests_executed' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["tests_executed"]}</div><div class="stat-label">Tests Executed</div></div>')
        if 'tests_skipped' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["tests_skipped"]}</div><div class="stat-label">Tests Skipped</div></div>')
        if 'tests_failed' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["tests_failed"]}</div><div class="stat-label">Tests Failed</div></div>')
        if 'total_findings' in stats:
            stats_cards.append(f'<div class="stat-card"><div class="stat-value">{stats["total_findings"]}</div><div class="stat-label">Total Findings</div></div>')
        if 'sanity_checks_failed' in stats and stats.get('sanity_checks_failed'):
            stats_cards.append(f'<div class="stat-card" style="border-color:var(--danger)"><div class="stat-value" style="color:var(--danger)">{stats["sanity_checks_failed"]}</div><div class="stat-label">Failed Sanity Checks</div></div>')
        
        if stats_cards:
            return f"""
            <div class="section" id="metrics">
                <div class="section-header">Key Metrics</div>
                <div class="section-content">
                    <div class="stats-grid">
                        {"".join(stats_cards)}
                    </div>
                </div>
            </div>
            """
        return ""
    
    @staticmethod
    def _generate_content_sections(data: Dict[str, Any]) -> str:
        """Generate main content sections based on report type"""
        report_type = data.get('report_type', '')
        
        if report_type == 'scope_report':
            content = ReportTemplates._generate_scope_content(data)
        elif report_type == 'scan_report':
            content = ReportTemplates._generate_scan_content(data)
        elif report_type == 'out_of_scope_findings':
            content = ReportTemplates._generate_out_of_scope_content(data)
        elif report_type == 'test_plan_execution':
            content = ReportTemplates._generate_execution_content(data)
        else:
            return ""
        if not content:
            return ""
        if 'id="details"' not in content:
            # Anchor the nav's "Details" link at the first real .section
            # element rather than a detached, zero-height placeholder div
            # — the previous approach (<div id="details"></div>{content})
            # made the nav land at an invisible anchor before the section
            # header, which read as broken navigation.
            first_section_marker = '<div class="section">'
            if first_section_marker in content:
                content = content.replace(
                    first_section_marker,
                    '<div class="section" id="details">',
                    1,
                )
            else:
                content = f'<div id="details"></div>{content}'
        return content
    
    @staticmethod
    def _generate_scope_content(data: Dict[str, Any]) -> str:
        """Generate scope-specific content sections"""
        content = ""
        
        # Subnet Information
        scope = data.get('scope', {})
        if scope.get('subnets'):
            content += """
            <div class="section">
                <div class="section-header">🌐 Network Subnets</div>
                <div class="section-content">
                    <div class="interactive-table-wrapper">
                        <div class="table-controls">
                            <input type="text" class="table-search" placeholder="Filter subnets..." aria-label="Filter subnet rows">
                            <span class="table-hint">Click column headers to sort</span>
                        </div>
                        <table class="interactive-table">
                            <thead>
                                <tr>
                                    <th>CIDR Block</th>
                                    <th>Description</th>
                                    <th>Address Space</th>
                                    <th>Network Type</th>
                                </tr>
                            </thead>
                            <tbody>
            """
            
            for subnet in scope['subnets']:
                metrics = SubnetCalculator.calculate_subnet_metrics(subnet.get('cidr', ''))
                content += f"""
                <tr>
                    <td><code>{subnet.get('cidr', 'N/A')}</code></td>
                    <td>{subnet.get('description', 'No description')}</td>
                    <td>{metrics['usable_addresses']:,} usable ({metrics['total_addresses']:,} total)</td>
                    <td>{'Private' if metrics['is_private'] else 'Public'}</td>
                </tr>
                """
            
            content += "</tbody></table></div></div></div>"
        
        # Host Information
        hosts = data.get('hosts', [])
        if hosts:
            content += ReportTemplates._generate_hosts_table(hosts)
        
        # Out-of-scope hosts
        oos_hosts = data.get('out_of_scope_hosts', [])
        if oos_hosts:
            content += ReportTemplates._generate_out_of_scope_table(oos_hosts)
        
        return content
    
    @staticmethod
    def _generate_scan_content(data: Dict[str, Any]) -> str:
        """Generate scan-specific content sections"""
        content = ""

        # Scan Information — every field is scanner/operator-controlled
        # (filename can come from a manual upload, command_line is the
        # raw scanner invocation, tool_name is parsed metadata), so each
        # value MUST be HTML-escaped before interpolation to keep an
        # exported HTML report from executing scanner-supplied markup
        # when an analyst opens it.
        scan = data.get('scan', {})
        esc = ReportTemplates._escape_html
        content += f"""
        <div class="section">
            <div class="section-header">🔍 Scan Details</div>
            <div class="section-content">
                <table>
                    <tr><td><strong>Filename:</strong></td><td>{esc(scan.get('filename', 'N/A'))}</td></tr>
                    <tr><td><strong>Tool:</strong></td><td>{esc(scan.get('tool_name', 'N/A'))}</td></tr>
                    <tr><td><strong>Scan Type:</strong></td><td>{esc(scan.get('scan_type', 'N/A'))}</td></tr>
                    <tr><td><strong>Command Line:</strong></td><td><code>{esc(scan.get('command_line', 'N/A'))}</code></td></tr>
                    <tr><td><strong>Created:</strong></td><td>{esc(scan.get('created_at', 'N/A'))}</td></tr>
                </table>
            </div>
        </div>
        """

        # Host Results
        hosts = data.get('hosts', [])
        if hosts:
            content += ReportTemplates._generate_hosts_table(hosts)

        return content
    
    @staticmethod
    def _generate_out_of_scope_content(data: Dict[str, Any]) -> str:
        """Generate out-of-scope findings content"""
        findings_by_tool = data.get('findings_by_tool', {})
        esc = ReportTemplates._escape_html

        content = ""
        for tool, findings in findings_by_tool.items():
            if findings:
                # `tool` originates in scanner output (parsed source key).
                # Escape both the displayed title-cased form and the
                # aria-label form so neither can break out of HTML.
                tool_label_safe = esc(tool.title())
                tool_aria_safe = esc(tool)
                content += f"""
                <div class="section">
                    <div class="section-header">🔍 {tool_label_safe} Findings</div>
                    <div class="section-content">
                        <div class="interactive-table-wrapper">
                            <div class="table-controls">
                                <input type='text' class='table-search' placeholder='Filter findings...' aria-label='Filter {tool_aria_safe} out-of-scope findings'>
                                <span class="table-hint">Click column headers to sort</span>
                            </div>
                            <table class="interactive-table">
                            <thead>
                                <tr>
                                    <th>IP Address</th>
                                    <th>Hostname</th>
                                    <th>Ports</th>
                                    <th>Reason</th>
                                    <th>Found Date</th>
                                </tr>
                            </thead>
                            <tbody>
                """

                for finding in findings:
                    # ports_info is a JSON-stringified blob; JSON STRING
                    # VALUES can carry attacker-controlled chars (an
                    # nmap-banner-grabbed `<svg onload=alert(1)>` lands
                    # verbatim in the JSON), so escape the whole thing.
                    ports_info = (
                        json.dumps(finding.get('ports', {}))
                        if finding.get('ports')
                        else 'None'
                    )
                    found_at_raw = finding.get('found_at')
                    found_at = found_at_raw[:10] if found_at_raw else 'N/A'
                    content += f"""
                    <tr class="out-of-scope">
                        <td><code>{esc(finding.get('ip_address', 'N/A'))}</code></td>
                        <td>{esc(finding.get('hostname', 'N/A'))}</td>
                        <td><small>{esc(ports_info)}</small></td>
                        <td>{esc(finding.get('reason', 'N/A'))}</td>
                        <td>{esc(found_at)}</td>
                    </tr>
                    """

                content += "</tbody></table></div></div></div>"

        return content
    
    @staticmethod
    def _generate_hosts_table(hosts: List[Dict]) -> str:
        """Generate hosts table section"""
        if not hosts:
            return ""
        
        content = """
        <div class="section">
            <div class="section-header">Discovered Hosts</div>
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
                            <th>Operating System</th>
                            <th>Open Ports</th>
                            <th>Services</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        
        esc = ReportTemplates._escape_html
        for host in hosts[:50]:  # Limit to first 50 hosts for readability
            open_ports = [p for p in host.get('ports', []) if p.get('state') == 'open']
            ports_str = ', '.join([f"{p.get('port_number', '')}/{p.get('protocol', '')}"
                                  for p in open_ports[:10]])  # Limit ports display

            services_str = ', '.join([p.get('service_name', 'unknown')
                                    for p in open_ports[:5] if p.get('service_name')])

            # Hostname / os_name / service_name are all scanner-derived
            # and can carry markup; escape everything before
            # interpolation to keep exported HTML reports XSS-safe.
            content += f"""
            <tr>
                <td><code>{esc(host.get('ip_address', 'N/A'))}</code></td>
                <td>{esc(host.get('hostname', 'N/A'))}</td>
                <td>{esc(host.get('os_name', 'Unknown'))}</td>
                <td><small>{esc(ports_str)}</small></td>
                <td><small>{esc(services_str)}</small></td>
            </tr>
            """
        
        if len(hosts) > 50:
            content += f"<tr data-static-row='true'><td colspan='5'><em>... and {len(hosts) - 50} more hosts</em></td></tr>"
        
        content += "</tbody></table></div></div></div>"
        return content
    
    @staticmethod
    def _generate_out_of_scope_table(oos_hosts: List[Dict]) -> str:
        """Generate out-of-scope hosts table"""
        if not oos_hosts:
            return ""
        
        content = """
        <div class="section">
            <div class="section-header">⚠️ Out-of-Scope Hosts</div>
            <div class="section-content">
                <div class="interactive-table-wrapper">
                    <div class="table-controls">
                        <input type="text" class="table-search" placeholder="Filter out-of-scope hosts..." aria-label="Filter out-of-scope hosts">
                        <span class="table-hint">Click column headers to sort</span>
                    </div>
                    <table class="interactive-table">
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Hostname</th>
                            <th>Tool Source</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        
        esc = ReportTemplates._escape_html
        for host in oos_hosts:
            content += f"""
            <tr class="out-of-scope">
                <td><code>{esc(host.get('ip_address', 'N/A'))}</code></td>
                <td>{esc(host.get('hostname', 'N/A'))}</td>
                <td>{esc(host.get('tool_source', 'N/A'))}</td>
                <td>{esc(host.get('reason', 'N/A'))}</td>
            </tr>
            """

        content += "</tbody></table></div></div></div>"
        return content

    # ------------------------------------------------------------------
    # Test Plan Execution Report
    # ------------------------------------------------------------------

    @staticmethod
    def _escape_html(value: Any) -> str:
        """Minimal HTML escaping for user-supplied text in the template.

        We deliberately avoid pulling in markupsafe / jinja2 just for this
        template — the rest of ``report_templates.py`` builds f-strings the
        same way, and test-plan findings are analyst-authored text that
        shouldn't contain hostile content.  This is a defense-in-depth
        escape for <, >, &, and quotes so accidentally-pasted HTML in a
        findings field doesn't blow up the report layout.
        """
        if value is None:
            return ""
        s = str(value)
        return (
            s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;')
             .replace("'", '&#x27;')
        )

    @staticmethod
    def _generate_execution_executive_summary(data: Dict[str, Any]) -> str:
        """Executive summary for a test plan execution report."""
        plan = data.get('plan', {}) or {}
        session = data.get('session', {}) or {}
        stats = data.get('statistics', {}) or {}
        findings_by_sev = stats.get('findings_by_severity') or {}

        severity_text_parts = []
        for sev in ('critical', 'high', 'medium', 'low', 'info'):
            if findings_by_sev.get(sev):
                severity_text_parts.append(f"{findings_by_sev[sev]} {sev}")
        severity_text = ", ".join(severity_text_parts) if severity_text_parts else "none"

        sc_run = stats.get('sanity_checks_run', 0)
        sc_failed = stats.get('sanity_checks_failed', 0)
        sanity_text = (
            f"All {sc_run} sanity checks passed"
            if sc_run and not sc_failed
            else (
                f"{sc_failed} of {sc_run} sanity checks failed — review the per-host sections below"
                if sc_run
                else "No sanity checks were recorded"
            )
        )

        session_status = session.get('status', 'unknown')
        plan_title = ReportTemplates._escape_html(plan.get('title') or 'Untitled plan')

        return f"""
        <div class="executive-summary">
            <h3>Executive Summary</h3>
            <p><strong>Test Plan:</strong> {plan_title} (v{plan.get('version', '?')},
            plan #{plan.get('id', '?')}) — execution session #{session.get('id', '?')}
            (<em>{ReportTemplates._escape_html(session_status)}</em>).</p>

            <p><strong>Execution Results:</strong> {stats.get('tests_executed', 0)} tests were executed,
            {stats.get('tests_skipped', 0)} skipped, {stats.get('tests_failed', 0)} failed across
            {stats.get('total_entries', 0)} host(s) in this plan.</p>

            <p><strong>Findings:</strong> {stats.get('total_findings', 0)} total findings
            ({ReportTemplates._escape_html(severity_text)}).</p>

            <p><strong>Target Verification:</strong> {ReportTemplates._escape_html(sanity_text)}.</p>
        </div>
        """

    @staticmethod
    def _generate_execution_content(data: Dict[str, Any]) -> str:
        """Per-host content sections for a test plan execution report.

        Each entry renders as a panel with the sanity check result, the
        list of executed tests (with command + output + severity), and
        the host-level findings summary.  Uses the existing ``.section``
        styling so this report drops into the same visual language.
        """
        entries = data.get('entries', []) or []
        if not entries:
            return """
            <div class="section" id="details">
                <div class="section-header">Per-Host Execution Detail</div>
                <div class="section-content"><p>No per-host results were recorded for this session.</p></div>
            </div>
            """

        severity_color = {
            'critical': 'var(--danger)', 'high': '#f97316', 'medium': 'var(--warning)',
            'low': 'var(--success)', 'info': 'var(--info)', 'none': '#526070',
        }

        esc = ReportTemplates._escape_html
        parts: List[str] = []
        parts.append("""
        <div class="section" id="details">
            <div class="section-header">Per-Host Execution Detail</div>
            <div class="section-content">
        """)

        for e in entries:
            sc = e.get('sanity_check')
            if sc:
                sc_passed = sc.get('passed')
                sc_badge_color = 'var(--success)' if sc_passed else 'var(--danger)'
                sc_badge_text = 'passed' if sc_passed else 'FAILED'
                sc_panel_class = 'sanity-panel' if sc_passed else 'sanity-panel failed'
                sc_html = f"""
                <div class="{sc_panel_class}">
                    <strong>Sanity Check:</strong>
                    <span style="color:{sc_badge_color};font-weight:700">[{sc_badge_text}]</span>
                    via {esc(sc.get('method'))} — target {esc(sc.get('target_ip'))}
                    {f"(port {sc.get('port_checked')})" if sc.get('port_checked') else ""}<br/>
                    <small>
                    {f"source_ip={esc(sc.get('source_ip'))}" if sc.get('source_ip') else ""}
                    {f" · dns={esc(sc.get('dns_result'))}" if sc.get('dns_result') else ""}
                    {f" · expected={esc(sc.get('expected_value'))}" if sc.get('expected_value') else ""}
                    {f" · actual={esc(sc.get('actual_value'))}" if sc.get('actual_value') else ""}
                    </small>
                    {f'<div><em>{esc(sc.get("details"))}</em></div>' if sc.get('details') else ''}
                </div>
                """
            else:
                sc_html = (
                    '<div class="sanity-panel missing"><strong>Sanity Check:</strong> '
                    'not recorded for this host.</div>'
                )

            # Test results rows
            results = e.get('results', []) or []
            results_html = ""
            if results:
                rows = []
                for r in results:
                    sev = (r.get('severity') or 'none').lower()
                    color = severity_color.get(sev, '#526070')
                    finding_badge = (
                        f'<span style="background:{color};color:white;padding:2px 8px;'
                        f'border-radius:10px;font-size:0.8em;font-weight:700;">'
                        f'{esc(sev.upper())}</span>'
                        if r.get('is_finding') else ''
                    )
                    raw = r.get('raw_output') or ''
                    if len(raw) > 2000:
                        raw = raw[:2000] + '\n... [truncated]'
                    command = r.get('command_run') or '(no command recorded)'
                    rows.append(f"""
                    <tr>
                        <td><code>#{r.get('test_index', '?')}</code></td>
                        <td><code>{esc(r.get('status'))}</code></td>
                        <td>{finding_badge}</td>
                        <td>
                            <div><strong>Command:</strong> <code>{esc(command)}</code></div>
                            {f'<div style="margin-top:4px;"><strong>Summary:</strong> {esc(r.get("findings_summary"))}</div>' if r.get('findings_summary') else ''}
                            {f'<details style="margin-top:6px;"><summary>Raw output</summary><pre>{esc(raw)}</pre></details>' if raw else ''}
                        </td>
                    </tr>
                    """)
                results_html = f"""
                <table>
                    <thead>
                        <tr>
                            <th>Test</th>
                            <th>Status</th>
                            <th>Finding</th>
                            <th>Detail</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(rows)}
                    </tbody>
                </table>
                """
            else:
                results_html = (
                    '<div class="muted-text" style="margin:8px 0;">'
                    '<em>No test results recorded for this host.</em></div>'
                )

            findings_block = ""
            if e.get('findings'):
                findings_block = (
                    f'<div class="finding-panel"><strong>Host-Level Findings:</strong><br/>'
                    f'{esc(e.get("findings"))}</div>'
                )

            host_header_ip = esc(e.get('host_ip') or 'unknown')
            host_header_hostname = esc(e.get('host_hostname') or '')
            priority = esc(e.get('priority') or '')
            phase = esc(e.get('test_phase') or '')
            status = esc(e.get('status') or '')
            worst = e.get('worst_finding_severity')
            worst_color = severity_color.get(worst, '#526070') if worst else None

            parts.append(f"""
            <div class="host-execution-card">
                <div class="host-execution-header">
                    <div class="host-execution-title">
                        <div>
                            <strong style="font-size:1.1em;"><code>{host_header_ip}</code></strong>
                            {f'&nbsp;<span class="muted-text">({host_header_hostname})</span>' if host_header_hostname else ''}
                        </div>
                        <div class="report-pill-row">
                            <span class="report-pill">priority: {priority}</span>
                            <span class="report-pill report-pill-info">{phase}</span>
                            <span class="report-pill report-pill-dark">{status}</span>
                            {f'<span class="report-pill" style="background:{worst_color};font-weight:700;">worst finding: {esc(worst)}</span>' if worst else ''}
                        </div>
                    </div>
                </div>
                <div class="host-execution-body">
                    {sc_html}
                    {results_html}
                    {findings_block}
                </div>
            </div>
            """)

        parts.append("</div></div>")
        return "".join(parts)
