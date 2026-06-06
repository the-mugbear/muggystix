"""
LLM Provider Credentials

Per-user configurations for talking to hosted or self-hosted LLM
providers (OpenAI, Anthropic, Azure OpenAI, Ollama, etc).  API keys
are stored **encrypted at rest** with a Fernet key derived from the
app's SECRET_KEY — see ``llm_provider_service.encrypt_secret``.
"""

import enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class LLMProviderType(str, enum.Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"
    # Escape hatch: anything else that speaks the OpenAI chat-completions
    # protocol (e.g. vLLM, llama.cpp server, Together, Groq).  The user
    # supplies the base URL + model id.
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMProvider(Base):
    """One configured LLM provider owned by a user.

    Multiple providers per user are supported (e.g. OpenAI for planning,
    Ollama for local-only tasks).  At most one can be flagged as the
    default; the service layer enforces single-default on insert/update.
    """
    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(100), nullable=False)
    provider_type = Column(String(32), nullable=False)
    base_url = Column(String(255), nullable=True)  # e.g. http://localhost:11434 for Ollama
    model_id = Column(String(128), nullable=True)   # default model for this provider
    api_key_encrypted = Column(Text, nullable=True)  # Fernet ciphertext; NULL for Ollama local
    extra_config = Column(Text, nullable=True)       # JSON blob for provider-specific knobs
    is_default = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_llm_provider_user_name"),
    )
