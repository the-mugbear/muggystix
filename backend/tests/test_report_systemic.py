"""Systemic insights in the downloads.

The systemic analysis (estate blind spots / conditions / outliers / profiles)
is the ``systemic`` key of the comprehensive inventory JSON.  The standalone
systemic briefing (``/reports/systemic.html``) that also printed it went with
the Posture briefing (2026-10-22); its tests moved to
``test_posture_briefing.py`` (escaping, no outbound reference, the
non-ASCII download name, the no-scope state).
"""
import io
import json
from unittest.mock import MagicMock

from app.db import models
from app.db.models import Scope, Subnet, Site, HostSubnetMapping
from app.api.v1.endpoints.reports import ReportGenerator


def _gen(db, project_id, user_id):
    return ReportGenerator(db=db, current_user=MagicMock(id=user_id), project_id=project_id)


def estate_with_eol_blind_spot(db, project_id, *, site_names=("HQ", "Branch")):
    """EOL OS on one host in each of two sites → an estate blind spot."""
    scope = Scope(project_id=project_id)
    db.add(scope)
    s1 = Site(project_id=project_id, name=site_names[0], criticality_tier=1)
    s2 = Site(project_id=project_id, name=site_names[1], criticality_tier=3)
    db.add_all([s1, s2])
    db.flush()
    sn_a = Subnet(scope_id=scope.id, cidr="10.1.1.0/24", site=s1.name, site_id=s1.id)
    sn_b = Subnet(scope_id=scope.id, cidr="10.2.2.0/24", site=s2.name, site_id=s2.id)
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


def test_comprehensive_json_includes_systemic(db_session, test_project, test_user):
    estate_with_eol_blind_spot(db_session, test_project.id)
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
