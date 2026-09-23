"""Regression tests for the shared HTML report template generators.

The per-report escaping tests for the scope / scan / out-of-scope report
types went with those types in v2.395.0 (they were never produced); the
test plan execution report's escaping is pinned in
``test_execution_report.py``.

Covers the nav-link conditionality (no dead ``#metrics`` link when
no statistics section is rendered) and the ``id="details"`` placement
fix (the anchor must land on the first real ``.section`` rather than a
detached empty div).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from app.services.report_templates import ReportTemplates


class TestNavConditionality:
    """Nav links must only point at sections that were actually rendered.

    Previously the shared report nav unconditionally included
    ``<a href="#metrics">``, which navigated to a missing target when no
    statistics block was rendered.  The test plan execution report is the
    only report this template renders.
    """

    def test_report_without_statistics_omits_metrics_link(self):
        html = ReportTemplates.generate_professional_html_report({
            'report_type': 'test_plan_execution',
            'plan': {'title': 'Plan'},
            'entries': [],
        })
        # The summary anchor is always present.
        assert '<a href="#summary">Summary</a>' in html
        # The metrics anchor must NOT appear when no stats section was rendered.
        assert '<a href="#metrics">' not in html

    def test_report_with_statistics_includes_metrics_link(self):
        html = ReportTemplates.generate_professional_html_report({
            'report_type': 'test_plan_execution',
            'plan': {'title': 'Plan'},
            'statistics': {'total_entries': 3, 'tests_executed': 5},
            'entries': [],
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

    def test_host_report_includes_nav_with_matching_anchors(self, db_session, test_project):
        # The host-dossier report builds its correlated record from the DB, so
        # this needs a real session + host (not a MagicMock).
        from app.db import models
        from app.api.v1.endpoints.reports import ReportGenerator

        host = models.Host(project_id=test_project.id, ip_address="10.20.0.9", state="up", os_name="Linux")
        db_session.add(host)
        db_session.flush()

        # current_user.id must be a real int — the export context queries
        # HostFollow.user_id == current_user.id.
        gen = ReportGenerator(db=db_session, current_user=MagicMock(id=1), project_id=test_project.id)
        html = gen.generate_html_report([host], filters={})

        # Sticky nav present.
        assert 'class="report-nav"' in html
        # The section ids the host-dossier rework uses.
        for expected in ('summary', 'metrics', 'exposure', 'hosts'):
            assert f'id="{expected}"' in html, f"section id='{expected}' missing"
        # Every nav href="#X" has a matching id="X" in the document.
        for anchor in re.findall(r'href="#([^"]+)"', html):
            assert f'id="{anchor}"' in html, (
                f"host-report nav anchor #{anchor} has no matching section id"
            )
        # The host renders as a dossier section anchored #host-{id}.
        assert f'id="host-{host.id}"' in html
        assert 'host-dossiers' in html


class TestDetailsAnchorPlacement:
    """The ``#details`` anchor must land on the first real ``.section``
    instead of a detached empty placeholder div."""

    def test_details_id_lives_on_first_section(self):
        content = ReportTemplates._generate_content_sections({
            'report_type': 'test_plan_execution',
            'entries': [],
        })
        # The first .section opens with id="details" — no detached
        # <div id="details"></div> placeholder.
        assert '<div class="section" id="details">' in content, (
            "id='details' should be merged onto the first .section, not a detached div"
        )
        assert '<div id="details"></div>' not in content, (
            "detached empty placeholder div should not be used"
        )
