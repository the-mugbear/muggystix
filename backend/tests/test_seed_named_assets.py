"""The named-asset demo seed's --reset removes ONLY what the seed created.

Review of d069516: reset deleted every name / scope domain in the project and
any host matching the fixture IPs, whoever created them.  Ownership is now a
per-run manifest (ids that appeared during the run + display names changed on
pre-existing hosts); this plants unrelated rows before AND after seeding and
checks they survive, while everything seeded is gone and changed display
names are restored.

Skipped when the scripts directory isn't reachable (the test container mounts
only backend/ unless scripts/ is mounted too).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from app.db import models
from app.db.models_findings import Finding
from app.db.models_agent import TestPlan


def _load_seed_module():
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "seed_named_assets.py",
        Path("/app/scripts/seed_named_assets.py"),
    ]
    for path in candidates:
        if path.exists():
            spec = importlib.util.spec_from_file_location("seed_named_assets", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    pytest.skip("scripts/seed_named_assets.py not reachable from this checkout")


def test_reset_removes_only_seeded_rows(db_session, test_project, test_user, tmp_path):
    seed = _load_seed_module()
    svc = seed.names

    # A scope + a few internal hosts the seed will bind names to (it changes
    # their display names, which reset must restore).
    scope = models.Scope(project_id=test_project.id, name="default")
    db_session.add(scope)
    hosts = []
    for i in range(3):
        h = models.Host(ip_address=f"10.10.0.{10 + i}", project_id=test_project.id, state="up",
                        hostname=f"pre-existing-{i}", hostname_source="scanner")
        db_session.add(h)
        hosts.append(h)
    # Unrelated operator data planted BEFORE seeding — including a host on the
    # seed's own LB address, which the seed must reuse, not own.
    pre_lb = models.Host(ip_address=seed.LB_IP, project_id=test_project.id, state="up",
                         hostname="operator-lb", hostname_source="operator")
    db_session.add(pre_lb)
    db_session.flush()
    svc.import_names(db_session, project_id=test_project.id, raw_names=["operator.example.net"], created_by_id=None)
    svc.upsert_scope_domains(db_session, scope, [("operator.example.net", False, "mine")])
    db_session.commit()
    operator_name_id = db_session.query(models.DNSName).filter_by(fqdn="operator.example.net").one().id
    operator_domain_id = db_session.query(models.ScopeDomain).filter_by(domain="operator.example.net").one().id

    seed.seed(db_session, test_project, test_user, str(tmp_path))
    manifest = Path(seed._manifest_path(test_project, str(tmp_path)))
    assert manifest.exists()
    assert db_session.query(models.DNSName).filter_by(project_id=test_project.id).count() > 10
    assert db_session.query(TestPlan).filter_by(project_id=test_project.id).count() == 1

    # Unrelated data planted AFTER seeding.
    svc.import_names(db_session, project_id=test_project.id, raw_names=["later.example.net"], created_by_id=None)
    db_session.commit()
    later_name_id = db_session.query(models.DNSName).filter_by(fqdn="later.example.net").one().id

    seed.reset(db_session, test_project, str(tmp_path))

    survivors = {n.fqdn for n in db_session.query(models.DNSName).filter_by(project_id=test_project.id).all()}
    assert survivors == {"operator.example.net", "later.example.net"}
    assert db_session.get(models.DNSName, operator_name_id) is not None
    assert db_session.get(models.DNSName, later_name_id) is not None
    assert db_session.get(models.ScopeDomain, operator_domain_id) is not None
    assert db_session.query(models.ScopeDomain).count() == 1
    # The pre-existing LB host was reused, not owned: it survives with its
    # operator display name intact (operator outranks PTR).
    lb = db_session.get(models.Host, pre_lb.id)
    assert lb is not None and (lb.hostname, lb.hostname_source) == ("operator-lb", "operator")
    # The seed's own old-LB host and everything it created are gone.
    assert db_session.query(models.Host).filter_by(ip_address=seed.OLD_LB_IP).count() == 0
    assert db_session.query(TestPlan).filter_by(project_id=test_project.id).count() == 0
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 0
    assert db_session.query(models.WebInterface).filter_by(project_id=test_project.id).count() == 0
    assert db_session.query(models.DNSRecord).filter(
        models.DNSRecord.project_id == test_project.id,
        models.DNSRecord.record_type != "IMPORT",
    ).count() == 0
    # Display names the seed changed on pre-existing internal hosts are restored.
    for i, h in enumerate(hosts):
        db_session.refresh(h)
        assert (h.hostname, h.hostname_source) == (f"pre-existing-{i}", "scanner")
    assert not manifest.exists()


def test_reset_refuses_without_a_manifest(db_session, test_project, tmp_path):
    seed = _load_seed_module()
    with pytest.raises(SystemExit, match="no manifest"):
        seed.reset(db_session, test_project, str(tmp_path))


def test_reset_refuses_when_seeded_name_gained_a_foreign_reference(db_session, test_project, test_user, tmp_path):
    seed = _load_seed_module()
    scope = models.Scope(project_id=test_project.id, name="default")
    db_session.add(scope)
    for i in range(3):
        db_session.add(models.Host(ip_address=f"10.10.0.{10 + i}", project_id=test_project.id, state="up"))
    db_session.commit()
    seed.seed(db_session, test_project, test_user, str(tmp_path))
    # An operator later promotes a finding onto a seeded vhost — a row the
    # seed did not create references a seeded name.
    from app.services.finding_service import FindingService
    lb = db_session.query(models.Host).filter_by(ip_address=seed.LB_IP).one()
    portal = db_session.query(models.DNSName).filter_by(fqdn="portal.example-corp.com").one()
    f = FindingService(db_session).create_finding(project_id=test_project.id, title="operator's", severity="low", actor_id=None)
    FindingService(db_session).restore_endpoint(finding=f, host_id=lb.id, name_id=portal.id)
    db_session.commit()
    with pytest.raises(SystemExit, match="referenced by rows this script did not create"):
        seed.reset(db_session, test_project, str(tmp_path))
