"""A finding's exposure follows the HOST's state, not only the finding's (v2.365.0).

Code review 2026-09-19, finding 5.  Since v2.360.0 a false-positive dismissal is
about ONE host: the finding stays confirmed for the others.  The site and subnet
summaries filtered ``Finding.status`` and never ``FindingHost.host_status``, so
the dismissed (or remediated) host went on adding its site / subnet to the
finding's exposure — and the summaries disagreed with the reports, which
already used the right rule.  Pins:

* one confirmed finding on two sites, one endpoint dismissed → only the live
  site carries it, in both summaries;
* a remediated endpoint is treated the same way;
* the rule is one definition, shared with the report code.
"""
from app.db import models
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, INACTIVE_ENDPOINT_STATES,
)
from app.services import report_generator
from app.services.attention_service import compute_site_attention
from app.services.subnet_correlation import SubnetCorrelationService
from app.services.subnet_insight_service import compute_subnet_insights


def _seed(db, project, dismissed_state):
    pid = project.id
    scope = models.Scope(project_id=pid, name="s")
    db.add(scope)
    db.flush()
    for cidr, site in (("10.50.1.0/24", "East"), ("10.50.2.0/24", "West")):
        db.add(models.Subnet(scope_id=scope.id, cidr=cidr, site=site))
    live = models.Host(project_id=pid, ip_address="10.50.1.5", state="up")
    dismissed = models.Host(project_id=pid, ip_address="10.50.2.5", state="up")
    db.add_all([live, dismissed])
    db.flush()

    finding = Finding(project_id=pid, title="Outdated OpenSSH", severity="critical",
                      status="confirmed", source="scanner")
    db.add(finding)
    db.flush()
    db.add_all([
        FindingHost(finding_id=finding.id, host_id=live.id, host_status=FindingHostStatus.OPEN.value),
        FindingHost(finding_id=finding.id, host_id=dismissed.id, host_status=dismissed_state),
    ])
    db.commit()
    SubnetCorrelationService(db).correlate_all_hosts_to_subnets(project_id=pid)
    return finding


def _site_findings(db, pid):
    return {s["site"]: s["exposure"]["active_findings"] for s in compute_site_attention(db, pid)["sites"]}


def _subnet_findings(db, pid):
    rows = compute_subnet_insights(db, pid, limit=None)["subnets"]
    return {r["cidr"]: r["exposure"]["active_findings"] for r in rows}


def test_a_host_only_false_positive_stops_counting_for_its_site_and_subnet(db_session, test_project):
    _seed(db_session, test_project, FindingHostStatus.FALSE_POSITIVE.value)
    assert _site_findings(db_session, test_project.id) == {"East": 1, "West": 0}
    assert _subnet_findings(db_session, test_project.id) == {"10.50.1.0/24": 1, "10.50.2.0/24": 0}


def test_a_remediated_endpoint_stops_counting_too(db_session, test_project):
    _seed(db_session, test_project, FindingHostStatus.REMEDIATED.value)
    assert _site_findings(db_session, test_project.id) == {"East": 1, "West": 0}
    assert _subnet_findings(db_session, test_project.id) == {"10.50.1.0/24": 1, "10.50.2.0/24": 0}


def test_an_endpoint_still_in_retest_keeps_counting(db_session, test_project):
    _seed(db_session, test_project, FindingHostStatus.RETEST.value)
    assert _site_findings(db_session, test_project.id) == {"East": 1, "West": 1}


def test_the_rule_is_one_definition_shared_with_reports():
    assert report_generator._INACTIVE_ENDPOINT_STATES is INACTIVE_ENDPOINT_STATES
    assert INACTIVE_ENDPOINT_STATES == {"remediated", "false_positive"}
