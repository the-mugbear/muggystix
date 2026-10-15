"""Contract tests for LLMProviderService encryption + CRUD.

Focus areas:
  - Encryption roundtrip (secret in → ciphertext at rest → plaintext out)
  - Fernet fallback behaviour when CREDENTIAL_ENCRYPTION_KEY is unset
  - Default-provider invariant (at most one per user)
  - Project-independent (LLM providers are user-scoped, not project-scoped)
"""

from __future__ import annotations

import pytest

from app.services.llm_provider_service import (
    LLMProviderService,
    encrypt_secret,
    decrypt_secret,
)


class TestEncryption:
    def test_roundtrip(self):
        plaintext = "sk-abcdef1234567890"
        cipher = encrypt_secret(plaintext)
        assert cipher != plaintext, "ciphertext must not equal plaintext"
        assert decrypt_secret(cipher) == plaintext

    def test_empty_string_passes_through(self):
        assert encrypt_secret("") == ""

    def test_decrypt_none_returns_none(self):
        assert decrypt_secret(None) is None
        assert decrypt_secret("") is None

    def test_decrypt_garbage_returns_none(self):
        """A ciphertext encrypted with a different key should return
        None rather than raising — caller can re-prompt the user."""
        assert decrypt_secret("not-a-valid-fernet-token") is None


def test_the_advice_to_set_the_key_keeps_stored_credentials(monkeypatch):
    """Diagnostics bundle 7 (2026-10-15): the boot warning said to set "a
    dedicated" CREDENTIAL_ENCRYPTION_KEY.  Only one key is tried on read, so
    on an installation with stored credentials a NEW value makes them all
    unreadable.  What the warning says now is pinned to what is true: the
    current SECRET_KEY's value keeps them; another value does not."""
    from app import startup
    from app.services import llm_provider_service as svc

    def with_keys(dedicated, secret):
        monkeypatch.setattr(svc.settings, "CREDENTIAL_ENCRYPTION_KEY", dedicated, raising=False)
        monkeypatch.setattr(svc.settings, "SECRET_KEY", secret)
        monkeypatch.setattr(svc, "_FERNET_CACHE", None)

    with_keys("", "the-current-secret-key-value-0123456789")       # the fallback, as deployed
    stored = encrypt_secret("sk-stored-credential")
    with_keys("the-current-secret-key-value-0123456789", "a-rotated-signing-key")   # as advised, then rotated
    assert decrypt_secret(stored) == "sk-stored-credential"
    with_keys("a-brand-new-dedicated-key-0123456789", "the-current-secret-key-value-0123456789")
    assert decrypt_secret(stored) is None                          # what "a dedicated key" did

    # The app's loggers do not propagate to pytest's capture: read the call.
    said = []
    monkeypatch.setattr(startup.logger, "warning", lambda message, *args: said.append(message % args if args else message))
    monkeypatch.setenv("SECRET_KEY", "the-current-secret-key-value-0123456789")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    startup.check_credential_encryption_key()
    assert len(said) == 1 and "SAME value SECRET_KEY has now" in said[0] and "unreadable" in said[0]
    assert "dedicated" not in said[0]


class TestProviderCrud:
    def test_create_and_get(self, db_session, test_user):
        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="Work OpenAI",
            provider_type="openai",
            base_url="https://api.openai.com",
            model_id="gpt-4o-mini",
            api_key_plaintext="sk-test-key",
            extra_config=None,
            is_default=True,
        )
        assert provider.id is not None
        assert provider.api_key_encrypted is not None
        assert "sk-test-key" not in (provider.api_key_encrypted or "")

        fetched = svc.get(provider.id, test_user.id)
        assert fetched is not None
        assert fetched.name == "Work OpenAI"
        # The encrypted column stays encrypted at rest
        assert fetched.api_key_encrypted == provider.api_key_encrypted
        # But the decrypt helper recovers plaintext
        assert decrypt_secret(fetched.api_key_encrypted) == "sk-test-key"

    def test_user_isolation(self, db_session, test_user):
        """Providers created by one user are invisible to another."""
        from app.db.models_auth import User, UserRole
        other = User(
            id=99,
            username="other-user",
            hashed_password="x",
            role=UserRole.MEMBER,  # v2.65.0 — was ANALYST pre-binary-collapse
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="Private",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="sk-private",
            extra_config=None,
            is_default=False,
        )
        # Get from other user's context returns None (not the provider)
        assert svc.get(provider.id, other.id) is None
        # Their list is empty
        assert svc.list_for_user(other.id) == []
        # Original user still sees it
        assert svc.get(provider.id, test_user.id) is not None

    def test_default_provider_single_per_user(self, db_session, test_user):
        """Setting a second provider as default demotes the first."""
        svc = LLMProviderService(db_session)
        first = svc.create(
            user_id=test_user.id,
            name="A",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="k1",
            extra_config=None,
            is_default=True,
        )
        second = svc.create(
            user_id=test_user.id,
            name="B",
            provider_type="anthropic",
            base_url=None,
            model_id=None,
            api_key_plaintext="k2",
            extra_config=None,
            is_default=True,
        )
        db_session.refresh(first)
        db_session.refresh(second)
        assert first.is_default is False
        assert second.is_default is True

    def test_update_replaces_api_key(self, db_session, test_user):
        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="X",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="original",
            extra_config=None,
            is_default=False,
        )
        original_cipher = provider.api_key_encrypted

        svc.update(
            provider_id=provider.id,
            user_id=test_user.id,
            api_key_plaintext="rotated",
        )
        db_session.refresh(provider)
        assert provider.api_key_encrypted != original_cipher
        assert decrypt_secret(provider.api_key_encrypted) == "rotated"

    def test_update_clear_api_key(self, db_session, test_user):
        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="X",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="to-be-cleared",
            extra_config=None,
            is_default=False,
        )
        assert provider.api_key_encrypted is not None

        svc.update(
            provider_id=provider.id,
            user_id=test_user.id,
            clear_api_key=True,
        )
        db_session.refresh(provider)
        assert provider.api_key_encrypted is None

    def test_delete(self, db_session, test_user):
        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="X",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="k",
            extra_config=None,
            is_default=False,
        )
        svc.delete(provider.id, test_user.id)
        assert svc.get(provider.id, test_user.id) is None

    def test_delete_wrong_user_raises(self, db_session, test_user):
        from app.db.models_auth import User, UserRole
        other = User(
            id=98,
            username="attacker",
            hashed_password="x",
            role=UserRole.MEMBER,  # v2.65.0 — was ANALYST pre-binary-collapse
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        svc = LLMProviderService(db_session)
        provider = svc.create(
            user_id=test_user.id,
            name="X",
            provider_type="openai",
            base_url=None,
            model_id=None,
            api_key_plaintext="k",
            extra_config=None,
            is_default=False,
        )
        with pytest.raises(ValueError, match="not found"):
            svc.delete(provider.id, other.id)
