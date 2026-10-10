"""Contract tests for the server-side prompt sanitizer.

Text posted to ``/llm-providers/{id}/complete`` (and what the report drafter
sends) goes to a hosted provider, so a BlueStick agent key and the well-known
third-party token formats are replaced first.

The labelled-bullet rule (``- Access key: `…```) and its tests went with
``agent_prompt_service._integration_block`` in 2.482.0: the session prompt no
longer prints a scanner integration's credentials, so there is no such bullet
to redact.  That the prompt carries none is pinned where the prompt is built
(``test_scanner_integrations_agent.py``).
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


class TestThirdPartyTokenRedaction:
    """The well-known token formats, wherever they sit in the text.  Each
    value is assembled here from a prefix and filler: none is a real token."""

    @pytest.mark.parametrize(
        "token",
        [
            "AKIA" + "A1" * 8,                       # AWS access key id
            "sk-" + "a1B2" * 6,                      # OpenAI-style secret key
            "ghp_" + "a1B2" * 6,                     # GitHub token
            "xoxb-" + "1234-abcd-5678",              # Slack token
            "AIza" + "a1B2_" * 7,                    # Google API key
            ".".join(["eyJ" + "a1B2" * 3] * 3),      # JSON Web Token
        ],
        ids=["aws", "openai", "github", "slack", "google", "jwt"],
    )
    def test_token_is_replaced(self, token):
        out = sanitize_for_llm(f"The scanner printed {token} in its banner.")
        assert token not in out
        assert "[REDACTED" in out
        assert "in its banner." in out

    def test_bearer_credential_is_replaced_and_the_scheme_kept(self):
        credential = "a1B2c3D4" * 4
        out = sanitize_for_llm(f"Authorization: Bearer {credential}")
        assert credential not in out
        assert "Bearer [REDACTED" in out


def test_a_labelled_bullet_is_ordinary_text():
    """The session prompt no longer prints credentials as labelled bullets, so
    the rule that redacted them by label is gone: a bullet that happens to
    carry such a label (a finding quoting a default password, say) reaches the
    drafter as written."""
    text = "- Password: `admin` was accepted on the device's login page"
    assert sanitize_for_llm(text) == text


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
            "and again bare: nm_agent_yyyyyyyyyyyyyyyyyyyyyyyy\n"
        )
        once = sanitize_for_llm(prompt)
        twice = sanitize_for_llm(once)
        assert once == twice
