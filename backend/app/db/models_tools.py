"""The tool registry — one source of truth for every tool BlueStick knows about.

Before v2.277.0 there were two lists that could not see each other:

* ``ToolReference.tsx`` — 61 curated entries in the frontend, the human-facing
  knowledge repo (what a tool is for, how to install it, where to read more).
* ``build_tool_catalog()`` — 11 tools in the backend, the only list that could
  gate anything, since it is the one an agent is handed.

They had already drifted: ``testssl`` was agent-usable with no human entry, so
an agent could be told to run a tool the reference page never mentioned.  More
importantly, any "is this an approved tool?" rule built on the backend list
would have rejected tools the app itself recommends.

One row per tool now, with two views over it: the reference page renders all of
them, and the agent catalog is the ``approved`` subset.

Two properties that look alike and must not be merged
-----------------------------------------------------
``status`` is a **policy** fact — may an agent run this?  ``ingestible`` is an
**engineering** fact — does BlueStick have a parser for its output?  They are
independent: 22 tools have parsers while 11 are agent-approved, and a tool can
be perfectly safe to run without BlueStick understanding a word of its output
(execution records evidence text; it does not ingest scanner files).  Fusing
them would mean either no tool can be approved until someone writes a parser —
stalling the vetting loop this table exists to enable — or approving tools whose
upload then fails.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)

from app.db.session import Base


# Policy states.  Deliberately one field rather than a status *and* an
# `approved` boolean, which could contradict each other.
TOOL_APPROVED = "approved"      # agents may run it
TOOL_REFERENCE = "reference"    # documented for humans, not offered to agents
TOOL_SUGGESTED = "suggested"    # an agent asked for it; awaiting vetting
TOOL_REJECTED = "rejected"      # vetted and declined — kept so it isn't re-proposed forever


class ToolRegistryEntry(Base):
    """One tool: what it is (for humans) and whether an agent may run it."""

    __tablename__ = "tool_registry"

    id = Column(Integer, primary_key=True)
    # The binary as invoked — the join key against a reported `command_run`.
    name = Column(String(64), nullable=False, unique=True, index=True)

    # --- human knowledge (the reference page renders these) ---
    description = Column(Text, nullable=False)
    category = Column(String(64), nullable=False, index=True)
    ports = Column(String(64), nullable=True)
    install = Column(String(255), nullable=True)
    url = Column(String(500), nullable=True)
    kali = Column(Boolean, nullable=False, server_default="false")

    # --- policy ---
    status = Column(String(16), nullable=False, server_default=TOOL_REFERENCE, index=True)
    # Recon phases this tool belongs to (discovery/service_probe/web/dns/smb/
    # credentialed).  Empty for tools with no agent role.
    phases = Column(JSON, nullable=True)
    # `intrusive=False` means safe enough to run without per-command approval
    # escalation — the discriminator the auto-approve rules key off.
    intrusive = Column(Boolean, nullable=True)
    requires_privileges = Column(Boolean, nullable=True)
    output_format = Column(String(16), nullable=True)

    # --- engineering ---
    ingestible = Column(Boolean, nullable=False, server_default="false")

    # --- provenance of a suggestion ---
    # Free text from the agent: what it wanted the tool for. Kept so a human
    # vetting the suggestion can see the case for it rather than just a name.
    suggested_rationale = Column(Text, nullable=True)
    suggested_by_agent_id = Column(Integer, nullable=True)
    # No FK on purpose: a suggestion should outlive the agent, key, and project
    # that proposed it — vetting happens later, often after the session is gone.
    suggested_in_project_id = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_tool_registry_status_name", "status", "name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ToolRegistryEntry {self.name} status={self.status}>"
