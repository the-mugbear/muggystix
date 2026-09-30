"""Agent evidence records and agent proposals (v2.436.0).

Two halves of one rule the user set for agent writes:

* an agent's change to what the team has CONCLUDED, or to what the client
  report says — a finding's report text, a new finding, promoting or
  dismissing a scanner observation, an endpoint's status — is a PROPOSAL that
  a person accepts (then may edit) or rejects (:class:`AgentProposal`);
* what the agent DID and SAW is recorded directly and never changes: "I ran X
  against Y and got Z" (:class:`EvidenceRecord`), without a test plan.
  Proposals may cite it.

Scan uploads, feedback, notes, host corrections, review status and plan /
execution writes stay direct, attributed to the session.
"""
from __future__ import annotations

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class EvidenceOutcome(str, enum.Enum):
    """What the agent's check found, in its own words' category."""
    FINDING = "finding"            # it demonstrated an issue
    NO_FINDING = "no_finding"      # it ran and the issue was not there
    INCONCLUSIVE = "inconclusive"  # it ran; the result does not decide it
    FAILED = "failed"              # it could not run (tool error, unreachable)
    INFO = "info"                  # context, not a test (a banner, a listing)


class EvidenceRecord(Base):
    """What an agent ran against a host and what came back.  Immutable: no
    update or delete path (a project's deletion cascades).  The raw output is
    a file (``raw_output_path``) with a short inline preview."""
    __tablename__ = "evidence_records"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    finding_id = Column(Integer, ForeignKey("findings.id", ondelete="SET NULL"), nullable=True, index=True)
    finding_host_id = Column(Integer, ForeignKey("finding_hosts.id", ondelete="SET NULL"), nullable=True)

    tool = Column(String(100), nullable=False)
    command = Column(Text, nullable=True)
    outcome = Column(String(20), nullable=False)
    summary = Column(Text, nullable=False)
    raw_output_path = Column(String(500), nullable=True)
    raw_output_bytes = Column(Integer, nullable=True)
    raw_output_preview = Column(Text, nullable=True)
    observed_ip = Column(String(45), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    agent_session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    agent_model = Column(String(100), nullable=True)
    agent_client = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    host = relationship("Host")
    recorded_by = relationship("User", foreign_keys=[recorded_by_user_id])


class ProposalKind(str, enum.Enum):
    FINDING_TEXT = "finding_text"
    FINDING_CREATE = "finding_create"
    OBSERVATION_PROMOTE = "observation_promote"
    OBSERVATION_DISMISS = "observation_dismiss"
    ENDPOINT_STATUS = "endpoint_status"


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    # Another proposal for the same field was accepted; kept for comparison,
    # no longer "needs review".
    SUPERSEDED = "superseded"


class ProposalSource(str, enum.Enum):
    AGENT = "agent"          # an agent session over MCP / curl
    LLM_DRAFT = "llm_draft"  # the in-app "Draft empty sections" (the user's own LLM provider)


class AgentProposal(Base):
    """A change an agent (or the in-app drafter) proposes; a person decides.

    ``payload`` holds the change: ``{"value": …}`` for one report-text field
    (``field`` names it — several proposals per field are kept so output from
    different models can be compared); the promote / dismiss parameters; the
    new finding; the endpoint's new status.  Accepting runs the same service
    call a person's click makes, as the person who accepts it."""
    __tablename__ = "agent_proposals"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default=ProposalStatus.PENDING.value,
                    server_default=ProposalStatus.PENDING.value)
    source = Column(String(20), nullable=False, default=ProposalSource.AGENT.value,
                    server_default=ProposalSource.AGENT.value)

    finding_id = Column(Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=True, index=True)
    vulnerability_id = Column(Integer, ForeignKey("vulnerabilities.id", ondelete="CASCADE"), nullable=True, index=True)
    finding_host_id = Column(Integer, ForeignKey("finding_hosts.id", ondelete="CASCADE"), nullable=True)
    field = Column(String(40), nullable=True)
    payload = Column(JSON, nullable=False)
    rationale = Column(Text, nullable=True)
    evidence_ids = Column(JSON, nullable=True)

    agent_session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    proposed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    agent_model = Column(String(100), nullable=True)
    agent_client = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    decided_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_note = Column(Text, nullable=True)
    # The finding an accepted create / promote produced or joined.
    result_finding_id = Column(Integer, ForeignKey("findings.id", ondelete="SET NULL"), nullable=True)
    # Why the last accept attempt failed (the target changed underneath); the
    # proposal stays pending.
    error = Column(Text, nullable=True)

    proposed_by = relationship("User", foreign_keys=[proposed_by_user_id])
    decided_by = relationship("User", foreign_keys=[decided_by_user_id])

    __table_args__ = (
        Index("idx_agent_proposals_project_status", "project_id", "status"),
    )
