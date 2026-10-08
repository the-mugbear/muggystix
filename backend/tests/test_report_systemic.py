"""Systemic insights in the downloads.

The systemic analysis (estate blind spots / conditions / outliers / profiles)
is reachable two ways through the export layer: as the ``systemic`` key of
the comprehensive inventory JSON and as the standalone executive briefing
(``GET /reports/systemic.html``).  These pin both, and that the briefing —
the one HTML document this layer still writes — escapes what people typed.
"""
import io
import json
from unittest.mock import MagicMock

from app.db import models
from app.db.models import Scope, Subnet, Site, HostSubnetMapping
from app.api.v1.endpoints.reports import ReportGenerator


def _gen(db, project_id, user_id):
    return ReportGenerator(db=db, current_user=MagicMock(id=user_id), project_id=project_id)


def _estate_with_eol_blind_spot(db, project_id):
    """EOL OS on one host in each of two sites → an estate blind spot."""
    scope = Scope(project_id=project_id, name="scope")
    db.add(scope)
    s1 = Site(project_id=project_id, name="HQ", criticality_tier=1)
    s2 = Site(project_id=project_id, name="Branch", criticality_tier=3)
    db.add_all([s1, s2])
    db.flush()
    sn_a = Subnet(scope_id=scope.id, cidr="10.1.1.0/24", site="HQ", site_id=s1.id)
    sn_b = Subnet(scope_id=scope.id, cidr="10.2.2.0/24", site="Branch", site_id=s2.id)
    db.add_all([sn_a, sn_b])
    db.flush()

    def host(ip, subnet, os_name):
        h = models.Host(project_id=project_id, ip_address=ip, state="up", os_name=os_name)
        db.add(h)
        db.flush()
        db.add(HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
        return h

    host("10.1.1.1", sn_a, "Windows XP Professional")  # EOL
    host("10.1.1.2", sn_a, "Ubuntu")
    host("10.2.2.1", sn_b, "Windows 7")                # EOL
    host("10.2.2.2", sn_b, "Ubuntu")
    db.flush()


def test_executive_html_contains_blind_spot(db_session, test_project, test_user):
    _estate_with_eol_blind_spot(db_session, test_project.id)
    html_doc = _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html()
    assert "Systemic Insights" in html_doc
    assert "Estate blind spots" in html_doc
    assert "End-of-life operating systems" in html_doc
    # Estate summary surfaces the in-scope host count.
    assert "Hosts in scope" in html_doc


def test_comprehensive_json_includes_systemic(db_session, test_project, test_user):
    _estate_with_eol_blind_spot(db_session, test_project.id)
    def json_report(report_type):
        out = io.BytesIO()
        _gen(db_session, test_project.id, test_user.id).write_json_report({}, report_type, out)
        return json.loads(out.getvalue())

    data = json_report("comprehensive")
    assert "systemic" in data
    assert data["systemic"]["adopted"] is True
    assert any(b["key"] == "eol_os" for b in data["systemic"]["blind_spots"])
    # Inventory reports omit the project-wide roll-ups.
    assert "systemic" not in json_report("inventory")


def test_executive_html_escapes_site_names_and_the_site_filter(db_session, test_project, test_user):
    """A site's name is typed by a person and printed into the briefing's
    hotspot tables; the ``site=`` filter is printed into its header.  Both
    must arrive as text.

    Replaces ``test_report_templates_escaping.py``, whose two tests were about
    the retired HTML host report (its nav anchors, its sortable-table script)
    and never about escaping; this is the guard the file's name promised, on
    the HTML that is left."""
    hostile = '<script>alert(1)</script>'
    scope = Scope(project_id=test_project.id, name="scope")
    db_session.add(scope)
    site = Site(project_id=test_project.id, name=hostile, criticality_tier=1)
    db_session.add(site)
    db_session.flush()
    subnet = Subnet(scope_id=scope.id, cidr="10.9.9.0/24", site=hostile, site_id=site.id)
    db_session.add(subnet)
    db_session.flush()
    host = models.Host(project_id=test_project.id, ip_address="10.9.9.1", state="up", os_name="Windows XP")
    db_session.add(host)
    db_session.flush()
    db_session.add(HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
    db_session.flush()

    for html_doc in (
        _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html(),
        _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html(site=hostile),
    ):
        assert "<script>" not in html_doc
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_doc
    # The briefing makes no request and runs nothing: no script, no external
    # stylesheet, font or image.
    assert "<script" not in html_doc and "src=" not in html_doc and "<link" not in html_doc
    assert "Scoped to site: &lt;script&gt;" in html_doc


def test_executive_html_not_adopted_without_scope(db_session, test_project, test_user):
    """No scoped subnets → the export renders the onboarding state, not a crash."""
    html_doc = _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html()
    assert "No scoped subnets" in html_doc


def test_executive_html_site_filter_scopes_hotspots(db_session, test_project, test_user):
    """``site=`` narrows the per-site sections (hotspots, outliers, profiles)
    to that site; estate-wide blind spots stay estate-wide."""
    _estate_with_eol_blind_spot(db_session, test_project.id)
    gen = _gen(db_session, test_project.id, test_user.id)
    whole = gen.generate_systemic_executive_html()
    assert "HQ" in whole and "Branch" in whole and "Scoped to site" not in whole

    hq = _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html(site="HQ")
    assert "Scoped to site: HQ" in hq
    assert "End-of-life operating systems" in hq          # estate pattern retained
    hotspots = hq.split("Site &amp; Subnet Hotspots", 1)[1]
    assert "HQ" in hotspots and "10.1.1.0/24" in hotspots
    assert "Branch" not in hotspots and "10.2.2.0/24" not in hotspots


def test_systemic_html_endpoint_accepts_site(client, db_session, test_project):
    _estate_with_eol_blind_spot(db_session, test_project.id)
    r = client.get(f"/api/v1/projects/{test_project.id}/reports/systemic.html?site=HQ")
    assert r.status_code == 200, r.text
    assert "Scoped to site: HQ" in r.text
    assert 'filename=systemic_insights_HQ_' in r.headers["content-disposition"]
