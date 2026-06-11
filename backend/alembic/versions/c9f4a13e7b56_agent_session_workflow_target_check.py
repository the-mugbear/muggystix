"""enforce the agent_sessions workflow/target invariant

Revision ID: c9f4a13e7b56
Revises: b8e1f37a92c4
Create Date: 2026-06-11 07:10:00.000000

R5 contract step: with the write paths now populating AgentSession (and the
drift backfilled by b8e1f37a92c4), enforce the base table's workflow/target
invariant at the DB — a plan-generation/execution session must carry a
plan_id, a recon session a scope_id, an assist session neither.

The CHECK constrains only KNOWN workflows; an unrecognised workflow passes it
and is rejected at the auth layer (get_current_agent fails closed), so we don't
make that defence-in-depth path unstorable (and the fail-closed test still
exercises it).  Verified against live data before adding: zero violations.

Deliberately NOT in this step (the broader contract phase): enforcing
agent_session_id NOT NULL on the detail tables / a key→session CHECK, and
dropping the legacy api_keys scope FKs — those need coordinated test-fixture
updates and auth-code changes.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c9f4a13e7b56'
down_revision: Union[str, None] = 'b8e1f37a92c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK = (
    "(workflow NOT IN ('execution','plan_generation') OR plan_id IS NOT NULL) "
    "AND (workflow <> 'recon' OR scope_id IS NOT NULL) "
    "AND (workflow <> 'assist' OR (plan_id IS NULL AND scope_id IS NULL))"
)


def upgrade() -> None:
    op.create_check_constraint("ck_agent_sessions_workflow_target", "agent_sessions", _CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_agent_sessions_workflow_target", "agent_sessions", type_="check")
