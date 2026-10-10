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

One row per tool now; the reference page renders all of them.

v2.433.0 — the registry is a CATALOGUE, not agent policy.  It used to carry an
``approved`` status that agents were told was the only set they could run
without asking; that allowlist was part of the retired "agent on rails" model
(the operator drives their agent now, and the server never enforced it
anyway).  ``status`` now says only whether a row is in the catalogue, an
agent's suggestion awaiting a curator, or a suggestion declined.

``ingestible`` is an **engineering** fact — does BlueStick have a parser for its
output?  It is independent of whether the tool is catalogued: a tool can be
worth running without BlueStick understanding a word of its output (execution
records evidence text; it does not ingest scanner files).
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
    UniqueConstraint,
    func,
)

from app.db.session import Base


# Catalogue states — none of them is a permission.
TOOL_REFERENCE = "reference"    # in the catalogue
TOOL_SUGGESTED = "suggested"    # an agent proposed adding it; awaiting a curator
TOOL_REJECTED = "rejected"      # suggestion declined — kept so it isn't re-proposed forever


class ToolRegistryEntry(Base):
    """One tool: what it is, what it is for, and whether BlueStick parses it."""

    __tablename__ = "tool_registry"

    id = Column(Integer, primary_key=True)
    # The binary as invoked — the join key against a reported `command_run`.
    name = Column(String(64), nullable=False, index=True)

    # --- human knowledge (the reference page renders these) ---
    description = Column(Text, nullable=False)
    category = Column(String(64), nullable=False, index=True)
    ports = Column(String(64), nullable=True)
    install = Column(String(255), nullable=True)
    url = Column(String(500), nullable=True)
    kali = Column(Boolean, nullable=False, server_default="false")

    # --- catalogue state ---
    status = Column(String(16), nullable=False, server_default=TOOL_REFERENCE, index=True)
    # Recon phases this tool belongs to (discovery/service_probe/web/dns/smb/
    # credentialed).  Empty for tools with no agent role.
    phases = Column(JSON, nullable=True)
    # Whether running it can disturb a target (exploit checks, brute force,
    # heavy crawling) — advice an operator reads, not a gate.
    intrusive = Column(Boolean, nullable=True)
    requires_privileges = Column(Boolean, nullable=True)
    output_format = Column(String(16), nullable=True)
    # For a tool whose output BlueStick parses: the invocation that writes a
    # file it can ingest (`<target>` and list files are placeholders), and one
    # line about what to upload.  Reference text the page offers to copy —
    # like every column here, never a permission or an instruction to run.
    # `tests/test_tool_command_consistency.py` pins the seed's commands to the
    # extensions the parsers accept.
    run_command = Column(Text, nullable=True)
    run_note = Column(Text, nullable=True)

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
        # `name` carries a plain btree index PLUS a separate named UNIQUE
        # constraint in the DB (both from the seed migration). Model them
        # explicitly so metadata matches the DB — `unique=True` on the column
        # would instead make the ix_ index itself unique and drop the
        # constraint, which reads to autogenerate as drift.
        UniqueConstraint("name", name="uq_tool_registry_name"),
        Index("idx_tool_registry_status_name", "status", "name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ToolRegistryEntry {self.name} status={self.status}>"
