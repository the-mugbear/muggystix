"""Regression tests for the shared HTML report template generators.

Covers the v2.80.1 security fix: every scanner/operator-derived value
interpolated into ``ReportTemplates._generate_*`` HTML must be escaped
via ``_escape_html`` so an exported HTML report can't execute
attacker-supplied markup when an analyst opens it in their browser.

Also covers the nav-link conditionality (no dead ``#metrics`` link when
no statistics section is rendered) and the ``id="details"`` placement
fix (the anchor must land on the first real ``.section`` rather than a
detached empty div).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from app.services.report_templates import ReportTemplates


XSS = '<script>alert("x")</script>'
ESCAPED = '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;'


def _assert_no_raw_script(html: str, label: str) -> None:
    """Every variant of a raw <script> tag must be gone from the output.

    Allowed: an escaped form (``&lt;script&gt;``). Disallowed: any
    appearance of the literal ``<script>`` sequence (case-insensitive,
    with optional whitespace) anywhere in the rendered HTML.
    """
    assert not re.search(r'<\s*script', html, re.IGNORECASE), (
        f"{label}: a literal <script> tag survived escaping — HTML report XSS regression"
    )


class TestScanContentEscaping:
    def test_scan_metadata_fields_escaped(self):
        html = ReportTemplates._generate_scan_content({
            'scan': {
                'filename': XSS,
                'tool_name': XSS,
                'scan_type': XSS,
                'command_line': XSS,
                'created_at': XSS,
            },
            'hosts': [],
        })
        _assert_no_raw_script(html, '_generate_scan_content')
        # The escaped form should appear at least once (proves escaping ran)
        assert ESCAPED in html


class TestScopeContentEscaping:
    def test_subnet_description_escaped(self):
        html = ReportTemplates._generate_scope_content({
            'scope': {
                'subnets': [
                    # cidr is a valid network so SubnetCalculator doesn't bail;
                    # description is operator free-text — the injection vector
                    # this path previously interpolated without escaping.
                    {'cidr': '10.0.0.0/24', 'description': XSS},
                ],
            },
            'hosts': [],
            'out_of_scope_hosts': [],
        })
        _assert_no_raw_script(html, '_generate_scope_content')
        assert ESCAPED in html

    def test_scope_executive_summary_name_escaped(self):
        html = ReportTemplates._generate_scope_executive_summary({
            'scope': {'name': XSS, 'subnets': []},
            'statistics': {'total_subnets': 1, 'total_hosts': 2, 'total_scans': 1},
        })
        _assert_no_raw_script(html, '_generate_scope_executive_summary')
        assert ESCAPED in html


class TestOutOfScopeContentEscaping:
    def test_finding_fields_escaped(self):
        html = ReportTemplates._generate_out_of_scope_content({
            'findings_by_tool': {
                # The tool key itself is rendered into the section
                # header + the aria-label; both must be escaped.
                XSS: [
                    {
                        'ip_address': XSS,
                        'hostname': XSS,
                        # ports gets JSON-stringified; a malicious
                        # service banner inside the JSON would land
                        # verbatim in the cell without escaping.
                        'ports': {'80/tcp': XSS},
                        'reason': XSS,
                        'found_at': f'{XSS}1234',
                    }
                ],
            },
        })
        _assert_no_raw_script(html, '_generate_out_of_scope_content')


class TestHostsTableEscaping:
    def test_host_row_fields_escaped(self):
        html = ReportTemplates._generate_hosts_table([
            {
                'ip_address': XSS,
                'hostname': XSS,
                'os_name': XSS,
                'ports': [
                    {
                        'state': 'open',
                        'port_number': '80',
                        'protocol': 'tcp',
                        # Scanner-derived service names can carry markup
                        # from banner-grabs; must be escaped in the
                        # joined services string too.
                        'service_name': XSS,
                    }
                ],
            }
        ])
        _assert_no_raw_script(html, '_generate_hosts_table')
        assert ESCAPED in html


class TestOutOfScopeTableEscaping:
    def test_oos_host_row_fields_escaped(self):
        html = ReportTemplates._generate_out_of_scope_table([
            {
                'ip_address': XSS,
                'hostname': XSS,
                'tool_source': XSS,
                'reason': XSS,
            }
        ])
        _assert_no_raw_script(html, '_generate_out_of_scope_table')
        assert ESCAPED in html


class TestNavConditionality:
    """Nav links must only point at sections that were actually rendered.

    Previously the shared report nav unconditionally included
    ``<a href="#metrics">``, which made out-of-scope reports (no
    statistics block) navigate to a missing target.
    """

    def test_out_of_scope_report_omits_metrics_link(self):
        # An out-of-scope report payload with no `statistics` key MUST
        # NOT render the Metrics nav link (no stats section is generated).
        html = ReportTemplates.generate_professional_html_report({
            'report_type': 'out_of_scope_findings',
            'findings_by_tool': {
                'nmap': [
                    {
                        'ip_address': '10.0.0.5',
                        'hostname': 'host.example',
                        'ports': {'80/tcp': 'http'},
                        'reason': 'subnet not in scope',
                        'found_at': '2026-05-29',
                    }
                ],
            },
        })
        # The summary anchor is always present.
        assert '<a href="#summary">Summary</a>' in html
        # The metrics anchor must NOT appear when no stats section was rendered.
        assert '<a href="#metrics">' not in html

    def test_report_with_statistics_includes_metrics_link(self):
        html = ReportTemplates.generate_professional_html_report({
            'report_type': 'scan_report',
            'statistics': {'total_hosts': 12, 'total_scans': 1},
            'scan': {
                'filename': 'scan.xml',
                'tool_name': 'nmap',
                'scan_type': '-sS',
                'command_line': 'nmap -sS 10.0.0.0/24',
                'created_at': '2026-05-29',
            },
            'hosts': [],
        })
        assert '<a href="#metrics">' in html
        # Every href="#X" must have a matching id="X" somewhere in the doc.
        for anchor in re.findall(r'href="#([^"]+)"', html):
            assert f'id="{anchor}"' in html, (
                f"nav anchor #{anchor} has no matching section id"
            )


class TestSortableHeaderAccessibility:
    """The sortable-header script must be operable by keyboard and
    announce its state to screen readers.  Pre-fix, the headers were
    plain `<th>` elements with a click listener: mouse-only, with no
    `aria-sort` updates, so keyboard and screen-reader users could
    neither discover nor operate the sort.
    """

    def test_scripts_wrap_headers_in_button_and_update_aria_sort(self):
        scripts = ReportTemplates.get_interactive_scripts()
        # The implementation must wrap each sortable <th>'s content in a
        # real <button> so Enter/Space activation is native.
        assert "createElement('button')" in scripts, (
            "sortable headers must be wrapped in a <button> for keyboard access"
        )
        # aria-sort must be initialised + updated to the WAI-ARIA values.
        assert "setAttribute('aria-sort'" in scripts
        assert "'ascending'" in scripts
        assert "'descending'" in scripts
        # And the old mouse-only data-sort attribute is gone.
        assert "data-sort" not in scripts, (
            "data-sort was replaced by aria-sort; stale references would silently regress a11y"
        )


class TestHostHtmlReportNav:
    """The host HTML report (rendered by `ReportGenerator.generate_html_report`
    in reports.py) must include the sticky nav with anchors that match
    real section ids and the interactive table wrapper that wires up
    sorting + filtering.
    """

    def test_host_report_includes_nav_with_matching_anchors(self):
        # Stub a minimal Host model instance — only the attributes the
        # generator reads.  We deliberately use MagicMock with explicit
        # attribute assignment (not spec=) so the generator's getattr
        # access doesn't trip the spec.
        port = MagicMock(
            state='open', service_name='http', port_number=80, protocol='tcp'
        )
        host = MagicMock(state='up', os_name='Linux', ports=[port])

        # Late import — keeps top-of-file imports independent of the
        # FastAPI endpoint module's transitive imports.
        from app.api.v1.endpoints.reports import ReportGenerator

        gen = ReportGenerator(db=MagicMock(), current_user=MagicMock(), project_id=1)
        html = gen.generate_html_report([host], filters={})

        # Sticky nav present.
        assert 'class="report-nav"' in html
        # The section ids the dark-mode rework introduced.
        for expected in ('summary', 'metrics', 'exposure', 'hosts'):
            assert f'id="{expected}"' in html, f"section id='{expected}' missing"
        # Every nav href="#X" has a matching id="X" in the document.
        for anchor in re.findall(r'href="#([^"]+)"', html):
            assert f'id="{anchor}"' in html, (
                f"host-report nav anchor #{anchor} has no matching section id"
            )
        # The host table is wrapped in the interactive-table-wrapper so
        # the shared script can wire up filter + sort.
        assert 'interactive-table-wrapper' in html


class TestDetailsAnchorPlacement:
    """The ``#details`` anchor must land on the first real ``.section``
    instead of a detached empty placeholder div."""

    def test_details_id_lives_on_first_section(self):
        content = ReportTemplates._generate_content_sections({
            'report_type': 'scan_report',
            'scan': {
                'filename': 'scan.xml',
                'tool_name': 'nmap',
                'scan_type': '-sS',
                'command_line': 'nmap -sS 10.0.0.0/24',
                'created_at': '2026-05-29',
            },
            'hosts': [],
        })
        # The first .section opens with id="details" — no detached
        # <div id="details"></div> placeholder.
        assert '<div class="section" id="details">' in content, (
            "id='details' should be merged onto the first .section, not a detached div"
        )
        assert '<div id="details"></div>' not in content, (
            "detached empty placeholder div should not be used"
        )
