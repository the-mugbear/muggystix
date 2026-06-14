"""Systemic insights in the report exports.

The systemic analysis (estate blind spots / conditions / outliers / profiles)
is reachable two ways through the export layer: as a section of the
comprehensive report (JSON / HTML / markdown) and as the standalone executive
HTML export (``GET /reports/systemic.html``).  These pin both.
"""
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
    gen = _gen(db_session, test_project.id, test_user.id)
    hosts = db_session.query(models.Host).filter_by(project_id=test_project.id).all()
    data = gen.generate_json_report(hosts, report_type="comprehensive")
    assert "systemic" in data
    assert data["systemic"]["adopted"] is True
    assert any(b["key"] == "eol_os" for b in data["systemic"]["blind_spots"])
    # Inventory reports omit the project-wide roll-ups.
    inv = _gen(db_session, test_project.id, test_user.id).generate_json_report(hosts, report_type="inventory")
    assert "systemic" not in inv


def test_systemic_markdown_lines(db_session, test_project, test_user):
    _estate_with_eol_blind_spot(db_session, test_project.id)
    lines = _gen(db_session, test_project.id, test_user.id)._systemic_markdown_lines()
    md = "\n".join(lines)
    assert "## Systemic Insights" in md
    assert "End-of-life operating systems" in md


def test_executive_html_not_adopted_without_scope(db_session, test_project, test_user):
    """No scoped subnets → the export renders the onboarding state, not a crash."""
    html_doc = _gen(db_session, test_project.id, test_user.id).generate_systemic_executive_html()
    assert "No scoped subnets" in html_doc
