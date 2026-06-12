"""
LLM Provider Service

Encrypted credential storage + a thin client abstraction over OpenAI,
Anthropic, Azure OpenAI, Ollama, and any OpenAI-compatible endpoint.

Security notes:
- API keys are encrypted with Fernet at rest.  The Fernet key is derived
  from ``settings.SECRET_KEY`` via HKDF, so rotating SECRET_KEY
  invalidates stored credentials (by design — a user rotating the app
  secret is also rotating every symmetric key the app holds).
- Decrypted keys are NEVER serialized to API responses.  ``get_provider``
  returns the encrypted blob; the service's ``call_llm`` method
  decrypts internally and feeds the plaintext directly to the HTTP client.
- For Ollama / localhost targets, an API key is optional; the service
  accepts NULL ``api_key_encrypted`` and skips the Authorization header.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from urllib.parse import quote
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_llm import LLMProvider, LLMProviderType
from app.services.url_validator import safe_http_client

logger = logging.getLogger(__name__)


_FERNET_CACHE: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Return a cached Fernet instance for credential encryption at rest.

    Audit finding C4: as of v2.9.7 the encryption key is sourced from
    ``settings.CREDENTIAL_ENCRYPTION_KEY`` rather than directly from
    ``SECRET_KEY``.  The fallback chain in ``config.py`` still uses
    SECRET_KEY if the dedicated key is unset, so existing deployments
    keep working — but operators see a deprecation warning on first
    encrypt/decrypt so the migration to a dedicated key is visible.

    HKDF lets us accept any key length (Fernet needs exactly 32 bytes
    base64-encoded) and the fixed salt+info strings scope the derived
    key to the credential-encryption purpose so it can never be
    confused with the JWT signing key even if they share a source.
    """
    global _FERNET_CACHE
    if _FERNET_CACHE is not None:
        return _FERNET_CACHE

    # Prefer the dedicated key; fall back to SECRET_KEY with a
    # warning.  An unset key in both is a fatal configuration error
    # because encrypting with an empty secret would make the
    # ciphertext trivially recoverable.
    dedicated = getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "")
    fallback = settings.SECRET_KEY or ""
    source = dedicated or fallback
    if not source:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY (or the legacy SECRET_KEY fallback) "
            "must be set in the environment to encrypt LLM provider and "
            "scanner integration credentials. Set it in your .env file."
        )
    if not dedicated and fallback:
        logger.warning(
            "CREDENTIAL_ENCRYPTION_KEY is not set; falling back to SECRET_KEY "
            "for credential encryption. This is deprecated as of v2.9.7 and "
            "will be removed in a future release. Set CREDENTIAL_ENCRYPTION_KEY "
            "to a dedicated 32+ byte secret so rotating the JWT signing key "
            "does not invalidate every stored integration credential."
        )

    secret = source.encode("utf-8")
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"networkmapper-llm-provider-v1",
        info=b"fernet-key-derivation",
    )
    raw = kdf.derive(secret)
    key = base64.urlsafe_b64encode(raw)
    _FERNET_CACHE = Fernet(key)
    return _FERNET_CACHE


def encrypt_secret(plaintext: str) -> str:
    """Return a Fernet ciphertext (base64 str) for the given plaintext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    """Return the plaintext for a stored Fernet ciphertext, or None.

    Returns ``None`` if the ciphertext is missing or was encrypted with
    a different key (caller can then re-prompt the user for the secret).
    """
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Failed to decrypt LLM provider credential — key rotated?")
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class LLMProviderService:
    def __init__(self, db: Session):
        self.db = db

    def list_for_user(self, user_id: int) -> List[LLMProvider]:
        return (
            self.db.query(LLMProvider)
            .filter(LLMProvider.user_id == user_id)
            .order_by(LLMProvider.is_default.desc(), LLMProvider.name)
            .all()
        )

    def get(self, provider_id: int, user_id: int) -> Optional[LLMProvider]:
        return (
            self.db.query(LLMProvider)
            .filter(LLMProvider.id == provider_id, LLMProvider.user_id == user_id)
            .first()
        )

    def get_default(self, user_id: int) -> Optional[LLMProvider]:
        return (
            self.db.query(LLMProvider)
            .filter(LLMProvider.user_id == user_id, LLMProvider.is_default.is_(True))
            .first()
        )

    def create(
        self,
        *,
        user_id: int,
        name: str,
        provider_type: str,
        base_url: Optional[str],
        model_id: Optional[str],
        api_key_plaintext: Optional[str],
        extra_config: Optional[Dict[str, Any]],
        is_default: bool,
    ) -> LLMProvider:
        if provider_type not in {p.value for p in LLMProviderType}:
            raise ValueError(f"Unknown provider_type {provider_type!r}")
        self._normalize_default(user_id, is_default)
        row = LLMProvider(
            user_id=user_id,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            model_id=model_id,
            api_key_encrypted=encrypt_secret(api_key_plaintext) if api_key_plaintext else None,
            extra_config=json.dumps(extra_config) if extra_config else None,
            is_default=bool(is_default),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(
        self,
        *,
        provider_id: int,
        user_id: int,
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        model_id: Optional[str] = None,
        api_key_plaintext: Optional[str] = None,
        clear_api_key: bool = False,
        extra_config: Optional[Dict[str, Any]] = None,
        is_default: Optional[bool] = None,
    ) -> LLMProvider:
        row = self.get(provider_id, user_id)
        if not row:
            raise ValueError("Provider not found")
        if name is not None:
            row.name = name
        if base_url is not None:
            row.base_url = base_url
        if model_id is not None:
            row.model_id = model_id
        if clear_api_key:
            row.api_key_encrypted = None
        elif api_key_plaintext:
            row.api_key_encrypted = encrypt_secret(api_key_plaintext)
        if extra_config is not None:
            row.extra_config = json.dumps(extra_config)
        if is_default is not None:
            if is_default:
                self._normalize_default(user_id, True, except_id=row.id)
            row.is_default = bool(is_default)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, provider_id: int, user_id: int) -> None:
        row = self.get(provider_id, user_id)
        if not row:
            raise ValueError("Provider not found")
        self.db.delete(row)
        self.db.commit()

    def _normalize_default(
        self,
        user_id: int,
        incoming_default: bool,
        except_id: Optional[int] = None,
    ) -> None:
        if not incoming_default:
            return
        q = self.db.query(LLMProvider).filter(
            LLMProvider.user_id == user_id,
            LLMProvider.is_default.is_(True),
        )
        if except_id is not None:
            q = q.filter(LLMProvider.id != except_id)
        q.update({"is_default": False}, synchronize_session=False)


# ---------------------------------------------------------------------------
# Connection test — verifies credentials before saving / at user request
# ---------------------------------------------------------------------------

def test_connection(provider: LLMProvider) -> Dict[str, Any]:
    """Issue a lightweight request to the provider to verify connectivity.

    Does NOT generate a full completion — just a model-list probe where
    the provider supports it, or a minimal completion otherwise.  Uses
    ``httpx`` (already a dependency) rather than pulling in
    provider-specific SDKs, to keep the server image small and the
    failure modes easy to debug.
    """
    import httpx

    api_key = decrypt_secret(provider.api_key_encrypted)
    ptype = provider.provider_type
    # Ollama is the only provider we let point at loopback / LAN hosts.
    allow_private = ptype == LLMProviderType.OLLAMA.value

    try:
        with safe_http_client(allow_private=allow_private, timeout=15.0) as client:
            if ptype == LLMProviderType.OPENAI.value or ptype == LLMProviderType.OPENAI_COMPATIBLE.value:
                url = (provider.base_url or "https://api.openai.com").rstrip("/") + "/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                r = client.get(url, headers=headers)
                r.raise_for_status()
                return {"ok": True, "detail": "Model list fetched successfully."}
            if ptype == LLMProviderType.ANTHROPIC.value:
                url = (provider.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
                headers = {
                    "x-api-key": api_key or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                body = {
                    "model": provider.model_id or "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                }
                r = client.post(url, headers=headers, json=body)
                r.raise_for_status()
                return {"ok": True, "detail": "Anthropic endpoint responded."}
            if ptype == LLMProviderType.AZURE_OPENAI.value:
                if not provider.base_url:
                    return {"ok": False, "detail": "Azure OpenAI requires a base URL."}
                url = provider.base_url.rstrip("/") + "/openai/models?api-version=2024-02-01"
                headers = {"api-key": api_key or ""}
                r = client.get(url, headers=headers)
                r.raise_for_status()
                return {"ok": True, "detail": "Azure OpenAI deployment reachable."}
            if ptype == LLMProviderType.OLLAMA.value:
                base = provider.base_url or "http://localhost:11434"
                url = base.rstrip("/") + "/api/tags"
                r = client.get(url)
                r.raise_for_status()
                tags = r.json().get("models", [])
                return {
                    "ok": True,
                    "detail": f"Ollama reachable — {len(tags)} local model(s) loaded.",
                }
            return {"ok": False, "detail": f"Unknown provider type: {ptype}"}
    except httpx.HTTPStatusError as exc:
        # Log the upstream response body server-side rather than reflecting
        # it to the API caller — some provider error bodies echo request
        # metadata (and an upstream that echoed the auth header would leak
        # it).  The caller gets the status code, which is all it needs.
        logger.warning(
            "LLM provider test_connection got HTTP %s: %s",
            exc.response.status_code, exc.response.text[:500],
        )
        return {"ok": False, "detail": f"Provider returned HTTP {exc.response.status_code}."}
    except httpx.RequestError as exc:
        return {"ok": False, "detail": f"Request failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM provider test_connection failed")
        return {"ok": False, "detail": f"Unexpected error: {exc}"}


# ---------------------------------------------------------------------------
# Chat completion — used by in-app agent runtime (Feature 7d)
# ---------------------------------------------------------------------------
#
# Each supported provider is described by an LLMAdapter row in _ADAPTERS.
# chat_completion() looks up the adapter for the configured provider_type,
# builds the request, POSTs, and parses the response — no provider-specific
# branches in the dispatch path.  Adding a new provider is one row plus its
# build_request / parse_response closures, not a fifth copy-paste branch.
#
# Conventions:
#   * `build_request` returns ``(url, headers, body)`` — all the transport-
#     specific shaping lives there.
#   * `parse_response` extracts the assistant's plaintext message from the
#     provider's JSON shape.  It MUST return ``""`` on any missing field
#     rather than raise — `chat_completion` already returns the full ``raw``
#     payload so callers debugging an empty completion can inspect what the
#     provider actually sent back.
#   * `required_provider_fields` lists `LLMProvider` attributes that must be
#     truthy to call this provider (Azure needs both base_url and model_id).


def _prepend_system_message(
    system: Optional[str], messages: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """Inline a system prompt as a leading {role: system} message.  Used by
    every provider whose chat API takes a single messages array (OpenAI,
    Ollama, Azure).  Anthropic uses a separate top-level `system` field
    and skips this helper."""
    if not system:
        return list(messages)
    return [{"role": "system", "content": system}] + list(messages)


def _build_openai_request(
    *, provider, api_key, system, messages, max_tokens, temperature
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    base = (provider.base_url or "https://api.openai.com").rstrip("/")
    url = f"{base}/v1/chat/completions"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": provider.model_id or "gpt-4o-mini",
        "messages": _prepend_system_message(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    return url, headers, body


def _build_anthropic_request(
    *, provider, api_key, system, messages, max_tokens, temperature
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    base = (provider.base_url or "https://api.anthropic.com").rstrip("/")
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key": api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: Dict[str, Any] = {
        "model": provider.model_id or "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": list(messages),
    }
    if system:
        body["system"] = system
    return url, headers, body


def _build_ollama_request(
    *, provider, api_key, system, messages, max_tokens, temperature
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    base = (provider.base_url or "http://localhost:11434").rstrip("/")
    url = f"{base}/api/chat"
    body = {
        "model": provider.model_id or "llama3",
        "messages": _prepend_system_message(system, messages),
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "stream": False,
    }
    return url, {}, body


def _build_azure_request(
    *, provider, api_key, system, messages, max_tokens, temperature
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    # required_provider_fields enforced upstream; this assertion makes the
    # narrowing obvious to type checkers.
    base = provider.base_url.rstrip("/")
    # URL-encode model_id (user-supplied) so a value containing ``/`` or
    # ``?`` can't reshape the path/query.  Host is pinned by base_url
    # (validated via safe_http_client), so this is robustness, not SSRF.
    url = f"{base}/openai/deployments/{quote(provider.model_id, safe='')}/chat/completions?api-version=2024-02-01"
    headers = {"api-key": api_key or "", "Content-Type": "application/json"}
    body = {
        "messages": _prepend_system_message(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    return url, headers, body


def _parse_openai_response(data: Dict[str, Any]) -> str:
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def _parse_anthropic_response(data: Dict[str, Any]) -> str:
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _parse_ollama_response(data: Dict[str, Any]) -> str:
    return (data.get("message") or {}).get("content", "") or ""


@dataclass(frozen=True)
class LLMAdapter:
    """Static description of one provider's transport + response shape."""
    build_request: Callable[..., Tuple[str, Dict[str, str], Dict[str, Any]]]
    parse_response: Callable[[Dict[str, Any]], str]
    timeout_seconds: float = 60.0
    allow_private: bool = False
    required_provider_fields: Tuple[str, ...] = ()


_ADAPTERS: Dict[str, LLMAdapter] = {
    LLMProviderType.OPENAI.value: LLMAdapter(
        build_request=_build_openai_request,
        parse_response=_parse_openai_response,
    ),
    LLMProviderType.OPENAI_COMPATIBLE.value: LLMAdapter(
        build_request=_build_openai_request,
        parse_response=_parse_openai_response,
    ),
    LLMProviderType.ANTHROPIC.value: LLMAdapter(
        build_request=_build_anthropic_request,
        parse_response=_parse_anthropic_response,
    ),
    LLMProviderType.OLLAMA.value: LLMAdapter(
        build_request=_build_ollama_request,
        parse_response=_parse_ollama_response,
        timeout_seconds=120.0,  # local llamas are slow
        allow_private=True,     # localhost/LAN by design
    ),
    LLMProviderType.AZURE_OPENAI.value: LLMAdapter(
        build_request=_build_azure_request,
        parse_response=_parse_openai_response,
        required_provider_fields=("base_url", "model_id"),
    ),
}


def chat_completion(
    provider: LLMProvider,
    *,
    system: Optional[str],
    messages: List[Dict[str, str]],
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    """Send a chat completion to the configured provider.

    Returns ``{"content": str, "raw": dict}`` where ``content`` is the
    assistant's text response and ``raw`` is the provider's full
    response body (for logging / debugging).  Raises
    ``RuntimeError`` on transport or API errors — the caller is
    expected to surface these to the user rather than silently retry.
    """
    adapter = _ADAPTERS.get(provider.provider_type)
    if adapter is None:
        raise RuntimeError(f"Unsupported provider type: {provider.provider_type}")

    for required_field in adapter.required_provider_fields:
        if not getattr(provider, required_field, None):
            raise RuntimeError(
                f"{provider.provider_type} requires {required_field} to be set."
            )

    api_key = decrypt_secret(provider.api_key_encrypted)
    url, headers, body = adapter.build_request(
        provider=provider,
        api_key=api_key,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    try:
        with safe_http_client(
            allow_private=adapter.allow_private, timeout=adapter.timeout_seconds,
        ) as client:
            r = client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        # Mirror test_connection: log the upstream body server-side (it may echo
        # request metadata / auth) and raise the contract's RuntimeError with
        # only the status code, never the raw provider body.
        logger.warning(
            "LLM chat_completion got HTTP %s: %s",
            exc.response.status_code, exc.response.text[:500],
        )
        raise RuntimeError(
            f"{provider.provider_type} provider returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"{provider.provider_type} request failed: {exc}") from exc
    except ValueError as exc:  # non-JSON body from r.json()
        raise RuntimeError(
            f"{provider.provider_type} returned a non-JSON response."
        ) from exc

    return {"content": adapter.parse_response(data), "raw": data}
