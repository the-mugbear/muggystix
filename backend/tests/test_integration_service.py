"""Contract tests for IntegrationService (scanner credential store)."""

from __future__ import annotations

import pytest

from app.services.integration_service import (
    IntegrationService,
    decrypt_integration,
)


class TestIntegrationCrud:
    def test_create_encrypts_both_secrets(self, db_session, test_user, test_project):
        svc = IntegrationService(db_session)
        row = svc.create(
            user_id=test_user.id,
            name="Work Nessus",
            integration_type="nessus",
            project_id=test_project.id,
            base_url="https://nessus.example.com:8834",
            secret="access-key-plain",
            secret2="secret-key-plain",
            extra_config=None,
            is_active=True,
        )
        # At-rest: both encrypted, neither equals plaintext
        assert row.secret_encrypted is not None
        assert row.secret2_encrypted is not None
        assert "access-key-plain" not in row.secret_encrypted
        assert "secret-key-plain" not in row.secret2_encrypted

        # Decrypted: recovered via the helper
        decrypted = decrypt_integration(row)
        assert decrypted["secret"] == "access-key-plain"
        assert decrypted["secret2"] == "secret-key-plain"

    def test_list_for_user_project_filter(self, db_session, test_user, test_project):
        """A project-scoped integration + a user-global integration
        should both appear when listing for that project."""
        svc = IntegrationService(db_session)
        # User-global (project_id=None)
        global_row = svc.create(
            user_id=test_user.id,
            name="Global",
            integration_type="generic_api",
            project_id=None,
            base_url=None,
            secret="g1",
            secret2=None,
            extra_config=None,
        )
        # Project-scoped
        scoped = svc.create(
            user_id=test_user.id,
            name="Scoped",
            integration_type="nessus",
            project_id=test_project.id,
            base_url=None,
            secret="s1",
            secret2="s2",
            extra_config=None,
        )

        rows = svc.list_for_user(test_user.id, project_id=test_project.id)
        ids = {r.id for r in rows}
        assert global_row.id in ids
        assert scoped.id in ids

    def test_clear_secret(self, db_session, test_user):
        svc = IntegrationService(db_session)
        row = svc.create(
            user_id=test_user.id,
            name="X",
            integration_type="nessus",
            project_id=None,
            base_url=None,
            secret="to-be-wiped",
            secret2="also-wiped",
            extra_config=None,
        )
        assert row.secret_encrypted is not None

        svc.update(
            integration_id=row.id,
            user_id=test_user.id,
            clear_secret=True,
            clear_secret2=True,
        )
        db_session.refresh(row)
        assert row.secret_encrypted is None
        assert row.secret2_encrypted is None

    def test_user_isolation(self, db_session, test_user):
        from app.db.models_auth import User, UserRole
        other = User(
            id=97,
            username="other-integration-user",
            hashed_password="x",
            role=UserRole.MEMBER,  # v2.65.0 — was ANALYST pre-binary-collapse
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        svc = IntegrationService(db_session)
        row = svc.create(
            user_id=test_user.id,
            name="Mine",
            integration_type="nessus",
            project_id=None,
            base_url=None,
            secret="s",
            secret2="s2",
            extra_config=None,
        )
        # Another user gets None when fetching by id
        assert svc.get(row.id, other.id) is None
        # And sees nothing in their list
        assert svc.list_for_user(other.id) == []

    def test_decrypt_integration_structure(self, db_session, test_user):
        svc = IntegrationService(db_session)
        row = svc.create(
            user_id=test_user.id,
            name="Burp",
            integration_type="burp",
            project_id=None,
            base_url="http://127.0.0.1:1337",
            secret="burp-key",
            secret2=None,
            extra_config={"scan_policy": "fast"},
        )
        decrypted = decrypt_integration(row)
        assert decrypted["integration_type"] == "burp"
        assert decrypted["name"] == "Burp"
        assert decrypted["base_url"] == "http://127.0.0.1:1337"
        assert decrypted["secret"] == "burp-key"
        assert decrypted["secret2"] is None
        assert decrypted["extra_config"] == {"scan_policy": "fast"}
        assert decrypted["is_active"] is True
