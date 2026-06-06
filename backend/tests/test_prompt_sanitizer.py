"""Contract tests for the server-side prompt sanitizer.

Mirrors ``frontend/src/utils/promptSanitizer.ts`` at the pattern
level.  The invariant these tests enforce is that every shape the
v2.9.1 ``_integration_block`` template emits must be matched by the
sanitizer, so a direct POST to ``/llm-providers/{id}/complete``
(bypassing the frontend) can never leak a credential.

If a test here fails because you changed the bullet shape in
``agent_prompt_service._integration_block``, the correct fix is to
update **both** ``prompt_sanitizer.py`` and
``frontend/src/utils/promptSanitizer.ts`` to match — not to relax the
tests.
"""

from __future__ import annotations

import pytest

from app.services.prompt_sanitizer import sanitize_for_llm


class TestApiKeyRedaction:
    """Every shape the agent instruction block emits the key in."""

    def test_x_api_key_header_line_stripped(self):
        prompt = "X-API-Key: nm_agent_abcdefghijklmnopqrstuvwxyz"
        out = sanitize_for_llm(prompt)
        assert "nm_agent_abcdefghijklmnopqrstuvwxyz" not in out
        assert "[REDACTED" in out

    def test_x_api_key_inside_markdown_code_block(self):
        prompt = (
            "```\n"
            "X-API-Key: nm_agent_abcdefghijklmnopqrstuvwxyz_1234\n"
            "```\n"
        )
        out = sanitize_for_llm(prompt)
        assert "nm_agent_abcdefghijklmnopqrstuvwxyz_1234" not in out
        # Fence must still be present so the LLM can reason about structure
        assert "```" in out

    def test_bare_token_outside_header(self):
        """Defense in depth: catch tokens that appear in free text."""
        prompt = "If you need to authenticate, use nm_agent_qwertyuiopasdfghjklz_zzz as your key."
        out = sanitize_for_llm(prompt)
        assert "nm_agent_qwertyuiopasdfghjklz_zzz" not in out

    def test_short_pseudo_token_not_touched(self):
        """Don't redact strings that merely start with nm_agent_ but are
        too short to be real keys (prompt_version placeholders, etc)."""
        prompt = "The PROMPT_VERSION is 1.0.0, not nm_agent_short."
        out = sanitize_for_llm(prompt)
        assert "nm_agent_short" in out  # 11 chars, below 20-char minimum

    def test_multiple_keys_on_multiple_lines(self):
        prompt = (
            "X-API-Key: nm_agent_aaaaaaaaaaaaaaaaaaaa\n"
            "Some explanation text\n"
            "X-API-Key: nm_agent_bbbbbbbbbbbbbbbbbbbb\n"
        )
        out = sanitize_for_llm(prompt)
        assert "nm_agent_aaaaaaaaaaaaaaaaaaaa" not in out
        assert "nm_agent_bbbbbbbbbbbbbbbbbbbb" not in out


class TestIntegrationCredentialRedaction:
    """Each bullet shape emitted by _integration_block in agent_prompt_service."""

    @pytest.mark.parametrize(
        "label",
        [
            "Access key",
            "Secret key",
            "Password",
            "Username",
            "API key",
            "PDCP token",
            "Secret",
        ],
    )
    def test_credential_bullet_value_stripped(self, label):
        prompt = f"- {label}: `actual_secret_value_here`"
        out = sanitize_for_llm(prompt)
        assert "actual_secret_value_here" not in out
        # Label should still be visible so the LLM can reason about structure
        assert label in out
        assert "[REDACTED" in out

    def test_nessus_full_integration_block(self):
        """End-to-end: the Nessus block shape from _integration_block."""
        prompt = (
            "- **Nessus — `Work Nessus`**\n"
            "  - URL: `https://nessus.example.com:8834`\n"
            "  - Access key: `abc123accesskey`\n"
            "  - Secret key: `xyz789secretkey`\n"
            "  - Guidance: launch a policy scan via the Nessus REST API\n"
        )
        out = sanitize_for_llm(prompt)
        assert "abc123accesskey" not in out
        assert "xyz789secretkey" not in out
        # URL is not a secret; should survive.  Only the labeled
        # credential values get stripped.
        assert "nessus.example.com" in out
        assert "Guidance" in out

    def test_case_insensitive_labels(self):
        """The _integration_block template uses mixed case; sanitizer
        must match regardless."""
        prompt = "  - SECRET KEY: `mixedcase_secret`"
        out = sanitize_for_llm(prompt)
        assert "mixedcase_secret" not in out


class TestEdgeCases:
    def test_empty_string_passes_through(self):
        assert sanitize_for_llm("") == ""

    def test_none_passes_through(self):
        assert sanitize_for_llm(None) is None

    def test_non_matching_content_unchanged(self):
        prompt = "Fetch the context endpoint and summarize the results."
        assert sanitize_for_llm(prompt) == prompt

    def test_idempotent(self):
        """Running the sanitizer twice should produce the same output."""
        prompt = (
            "X-API-Key: nm_agent_zzzzzzzzzzzzzzzzzzzz\n"
            "- Access key: `leaked`\n"
        )
        once = sanitize_for_llm(prompt)
        twice = sanitize_for_llm(once)
        assert once == twice
