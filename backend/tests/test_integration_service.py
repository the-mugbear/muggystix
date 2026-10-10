"""Contract tests for IntegrationService (the installation's scanner list).

Integrations belong to the installation (2.482.0, revision ``c8f2d5a9e3b1``):
no row is a user's or a project's.  ``test_user_isolation`` and
``test_list_for_user_project_filter`` pinned the per-user / per-project model
that went; what they guarded is now the opposite statement,
``test_one_list_for_everyone_whoever_configured_it``.
"""

from __future__ import annotations

import json

from app.db.models_auth import AuditLog, User, UserRole
from app.services.integration_service import (
    CREDENTIALS_SHARED_ACTION,
    IntegrationService,
    describe_for_agent,
    share_credentials_with_agent,
)


def _user(db, user_id: int, name: str) -> User:
    user = User(
        id=user_id, username=name, hashed_password="x",
        role=UserRole.MEMBER, is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _create(svc: IntegrationService, **over):
    values = dict(
        created_by_id=None, name="X", integration_type="nessus", base_url=None,
        secret="s1", secret2="s2", extra_config=None,
    )
    values.update(over)
    return svc.create(**values)


class TestIntegrationCrud:
    def test_create_encrypts_both_secrets(self, db_session, test_user):
        svc = IntegrationService(db_session)
        row = _create(
            svc, created_by_id=test_user.id, name="Work Nessus",
            base_url="https://nessus.example.com:8834",
            secret="access-key-plain", secret2="secret-key-plain",
        )
        # At-rest: both encrypted, neither equals plaintext
        assert row.secret_encrypted is not None
        assert row.secret2_encrypted is not None
        assert "access-key-plain" not in row.secret_encrypted
        assert "secret-key-plain" not in row.secret2_encrypted
        assert row.created_by_id == test_user.id

    def test_one_list_for_everyone_whoever_configured_it(self, db_session, test_user):
        """Two people configured a scanner under the SAME name: both rows are
        kept (no unique name), and there is one list with both in it."""
        other = _user(db_session, 97, "other-integration-user")
        svc = IntegrationService(db_session)
        mine = _create(svc, created_by_id=test_user.id, name="Nessus")
        theirs = _create(svc, created_by_id=other.id, name="Nessus")
        nobodys = _create(svc, created_by_id=None, name="Generic", integration_type="generic_api")

        assert {r.id for r in svc.list_all()} == {mine.id, theirs.id, nobodys.id}
        assert svc.get(theirs.id).id == theirs.id

    def test_active_only_leaves_out_a_disabled_one(self, db_session):
        svc = IntegrationService(db_session)
        on = _create(svc, name="On")
        off = _create(svc, name="Off", is_active=False)
        assert [r.id for r in svc.list_all(active_only=True)] == [on.id]
        assert {r.id for r in svc.list_all()} == {on.id, off.id}

    def test_update_and_delete_reach_a_row_someone_else_created(self, db_session, test_user):
        other = _user(db_session, 98, "configured-it")
        svc = IntegrationService(db_session)
        row = _create(svc, created_by_id=other.id, name="Theirs")
        svc.update(integration_id=row.id, name="Renamed")
        db_session.refresh(row)
        assert row.name == "Renamed" and row.created_by_id == other.id
        svc.delete(row.id)
        assert svc.get(row.id) is None

    def test_clear_secret(self, db_session):
        svc = IntegrationService(db_session)
        row = _create(svc, secret="to-be-wiped", secret2="also-wiped")
        assert row.secret_encrypted is not None

        svc.update(integration_id=row.id, clear_secret=True, clear_secret2=True)
        db_session.refresh(row)
        assert row.secret_encrypted is None
        assert row.secret2_encrypted is None


class TestWhatAnAgentIsGiven:
    def test_the_description_carries_no_secret(self, db_session):
        row = _create(
            IntegrationService(db_session), name="Burp", integration_type="burp",
            base_url="http://127.0.0.1:1337", secret="burp-key-plain", secret2=None,
            extra_config={"scan_policy": "fast"},
        )
        described = describe_for_agent(row)
        assert described == {
            "id": row.id, "name": "Burp", "integration_type": "burp",
            "base_url": "http://127.0.0.1:1337", "extra_config": {"scan_policy": "fast"},
        }
        assert "burp-key-plain" not in json.dumps(described)

    def test_credentials_are_named_by_what_they_are(self, db_session):
        svc = IntegrationService(db_session)
        expected = {
            "nessus": {"access_key": "one-plain", "secret_key": "two-plain"},
            "openvas": {"username": "one-plain", "password": "two-plain"},
            "nuclei": {"pdcp_token": "one-plain"},
            "burp": {"api_key": "one-plain"},
            "generic_api": {"secret": "one-plain"},
        }
        for itype, credentials in expected.items():
            row = _create(svc, name=itype, integration_type=itype, secret="one-plain", secret2="two-plain")
            shared = share_credentials_with_agent(
                db_session, row, operator_id=None, project_id=None, agent_session_id=None,
            )
            assert shared["credentials"] == credentials, itype

    def test_sharing_stages_an_audit_row_without_a_secret(self, db_session, test_user, test_project):
        row = _create(
            IntegrationService(db_session), name="Work Nessus",
            secret="access-key-plain", secret2="secret-key-plain",
        )
        share_credentials_with_agent(
            db_session, row, operator_id=test_user.id, project_id=test_project.id,
            agent_session_id=None,
        )
        db_session.commit()
        audit = db_session.query(AuditLog).filter_by(action=CREDENTIALS_SHARED_ACTION).one()
        assert audit.user_id == test_user.id
        assert (audit.resource_type, audit.resource_id) == ("integration", str(row.id))
        assert audit.details["integration_name"] == "Work Nessus"
        assert audit.details["project_id"] == test_project.id
        stored = json.dumps(audit.details)
        assert "access-key-plain" not in stored and "secret-key-plain" not in stored
