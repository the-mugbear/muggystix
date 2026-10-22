"""The test schema is the one production runs (backlog H-b, R-nit, 2026-10-22).

``tests/conftest.py`` builds the suite's database with ``alembic upgrade
head``. Until then it was ``create_all()`` from the models, and every
constraint a revision created by hand was missing from the tests: the one
active key per agent session, the one-target CHECK on notes and the three DNS
observation identities among them — a test could write what production
refuses and pass.

Pinned here:

* the schema is at the chain's head (``alembic_version``);
* each of those constraints exists in the test database AND refuses what it
  exists to refuse;
* each is declared on its model too, so ``alembic check`` compares it (it was
  excluded from the comparison while it lived in the migrations only).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import models
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="the chain-built schema is Postgres")

BACKEND_ROOT = Path(__file__).resolve().parents[1]

PARTIAL_UNIQUES = {
    "uq_api_key_agent_session_active": "api_keys",
    "uq_dns_record_scan_observation": "dns_records",
    "uq_dns_record_import_observation": "dns_records",
    "uq_dns_record_evidence_observation": "dns_records",
}


def _head() -> str:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_the_test_database_is_at_the_chains_head(db_session):
    assert db_session.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()


def test_a_database_built_from_nothing_owes_no_data_repair(db_session):
    """What a new installation sees: revision d4a7c1e3f6b8 records the repairs
    of its day as not needed on a database with no hosts. (create_all never
    ran a revision's data step, so the suite had never seen this.)"""
    from app.services import data_repair_service as svc

    rows = db_session.execute(text("SELECT name, mode FROM data_repairs ORDER BY name")).all()
    assert rows == [("misconfig_backfill", "not_needed"), ("netexec_results_repair", "not_needed")]
    assert [r.name for r in svc.pending(db_session)] == [
        r.name for r in svc.KNOWN_REPAIRS if r.name not in {"misconfig_backfill", "netexec_results_repair"}
    ]


@pytest.mark.parametrize("name,table", sorted(PARTIAL_UNIQUES.items()))
def test_each_partial_unique_is_in_the_test_database_and_on_its_model(db_session, name, table):
    indexdef = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": name},
    ).scalar()
    assert indexdef is not None, f"{name} is missing from the test schema"
    assert indexdef.startswith("CREATE UNIQUE INDEX") and " WHERE " in indexdef

    model_table = models.Base.metadata.tables[table]
    declared = {ix.name: ix for ix in model_table.indexes}
    assert name in declared, f"{name} is not declared on the {table} model"
    assert declared[name].unique


def test_the_one_target_check_is_in_the_test_database_and_on_its_model(db_session):
    found = db_session.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_annotations_exactly_one_target'")
    ).scalar()
    assert found is not None and "num_nonnulls" in found
    names = {c.name for c in models.Base.metadata.tables["annotations"].constraints}
    assert "ck_annotations_exactly_one_target" in names


def test_a_note_with_no_target_is_refused(db_session):
    with pytest.raises(IntegrityError, match="ck_annotations_exactly_one_target"):
        with db_session.begin_nested():
            db_session.execute(text("INSERT INTO annotations (body, pinned, actor_type) VALUES ('x', false, 'user')"))


def test_a_note_with_two_targets_is_refused(db_session, test_project):
    from app.db.models import Scope

    scope = Scope(project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    with pytest.raises(IntegrityError, match="ck_annotations_exactly_one_target"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO annotations (body, pinned, actor_type, project_id, scope_id) "
                    "VALUES ('x', false, 'user', :p, :s)"
                ),
                {"p": test_project.id, "s": scope.id},
            )


def test_a_second_active_key_for_one_agent_session_is_refused(client, db_session, test_project):
    """Through the real start route, then a hand-written second active key."""
    from app.db.models_auth import APIKey

    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "schema"})
    assert started.status_code == 201, started.text
    key = db_session.query(APIKey).filter(APIKey.is_active.is_(True), APIKey.agent_session_id.isnot(None)).first()
    assert key is not None
    with pytest.raises(IntegrityError, match="uq_api_key_agent_session_active"):
        with db_session.begin_nested():
            db_session.add(APIKey(
                name="second", key_hash="different-hash", key_prefix="bs_x",
                agent_id=key.agent_id, agent_session_id=key.agent_session_id, is_active=True,
            ))
            db_session.flush()


def test_the_same_dns_answer_twice_in_one_scan_is_refused(db_session, test_project):
    from app.db.models import DNSName, DNSRecord, Scan

    scan = Scan(project_id=test_project.id, filename="dns.txt")
    name = DNSName(project_id=test_project.id, fqdn="www.example.test")
    db_session.add_all([scan, name])
    db_session.flush()

    def answer(record_type="A", **extra):
        return DNSRecord(
            project_id=test_project.id, name_id=name.id, domain="www.example.test",
            record_type=record_type, value="10.0.0.1", **extra,
        )

    db_session.add(answer(scan_id=scan.id))
    db_session.flush()
    # resolver_name NULL both times: COALESCE makes the two the same identity.
    with pytest.raises(IntegrityError, match="uq_dns_record_scan_observation"):
        with db_session.begin_nested():
            db_session.add(answer(scan_id=scan.id))
            db_session.flush()

    db_session.add(answer(record_type="IMPORT"))
    db_session.flush()
    with pytest.raises(IntegrityError, match="uq_dns_record_import_observation"):
        with db_session.begin_nested():
            db_session.add(answer(record_type="IMPORT"))
            db_session.flush()
