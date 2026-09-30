"""Retire the "agent on rails" schema (v2.433.0)

The agent feature used to run on rails: approved tools → a recon run → a
drafted plan a human approved → an execution run.  Operators now drive their
agents and agents execute their own plans, so:

* test plans lose their approval states and columns.  ``proposed`` and
  ``approved`` plans become ``draft`` (a draft is executable now);
  ``rejected`` becomes ``archived``; an ``approved`` entry becomes
  ``proposed`` (not tested yet).  ``rejection_reason`` — already reused as the
  archive reason — is renamed ``archive_reason``.
* the tool registry is a catalogue: ``approved`` rows become ``reference``.
* target checks are evidence, not a gate: ``test_execution_results``
  loses ``sanity_override_reason`` (v2.433.1).
* an execution run can only be continued by the session that opened it, so
  open runs whose session has ended (and runs from before sessions existed)
  become ``abandoned``, results kept (v2.433.1).
* recon runs are gone.  An agent reads a scope (``/agent/scopes/{id}/…``),
  runs its scanners and uploads the output to its agent session.
  ``ingestion_jobs`` and ``scan_batches`` gain ``agent_session_id``
  (backfilled from the run), an agent batch's label is unique per agent
  session, and ``recon_sessions`` is dropped with every column pointing at it
  (ingestion jobs, scan batches, agent API calls, agent feedback, and a plan's
  ``source_recon_session_id`` — plans sourced from a run become
  ``unspecified``).

Revision ID: c7d2e9f4a1b6
Revises: b2f8d6e0a3c5
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op


revision = "c7d2e9f4a1b6"
down_revision = "b2f8d6e0a3c5"
branch_labels = None
depends_on = None


def upgrade():
    # --- test plans ---------------------------------------------------------
    op.execute("UPDATE test_plans SET status = 'draft' WHERE status IN ('proposed', 'approved')")
    op.execute("UPDATE test_plans SET status = 'archived' WHERE status = 'rejected'")
    op.execute("UPDATE test_plan_entries SET status = 'proposed' WHERE status = 'approved'")
    op.drop_constraint("test_plans_approved_by_id_fkey", "test_plans", type_="foreignkey")
    op.drop_constraint("test_plans_rejected_by_id_fkey", "test_plans", type_="foreignkey")
    op.drop_column("test_plans", "approved_by_id")
    op.drop_column("test_plans", "approved_at")
    op.drop_column("test_plans", "rejected_by_id")
    op.drop_column("test_plans", "rejected_at")
    op.alter_column("test_plans", "rejection_reason", new_column_name="archive_reason")

    # --- runs stranded by an ended session ----------------------------------
    # Only the session that opened a run can continue it, so an open run
    # whose session has ended can never move again (it read as "left open"
    # and "blocked" for good).  Ending a session now abandons its runs; this
    # does the same for the ones already stranded.  Results are kept.
    op.execute(
        "UPDATE execution_sessions e SET status = 'abandoned', "
        "completed_at = COALESCE(e.completed_at, s.completed_at, now()) "
        "FROM agent_sessions s WHERE e.agent_session_id = s.id "
        "AND s.status = 'ended' AND e.status IN ('active', 'paused')"
    )
    # Runs from before sessions existed have no session to continue them.
    op.execute(
        "UPDATE execution_sessions SET status = 'abandoned', "
        "completed_at = COALESCE(completed_at, now()) "
        "WHERE agent_session_id IS NULL AND status IN ('active', 'paused')"
    )

    # --- target checks are evidence, not a gate -----------------------------
    # Nothing writes the override reason any more (there is no gate to
    # override).  Testbed deployment: the stored reasons are not kept.
    op.drop_index(
        "ix_test_execution_results_sanity_override_reason",
        table_name="test_execution_results",
    )
    op.drop_column("test_execution_results", "sanity_override_reason")

    # --- tool registry ------------------------------------------------------
    op.execute("UPDATE tool_registry SET status = 'reference' WHERE status = 'approved'")

    # --- uploads belong to the agent session --------------------------------
    op.add_column("ingestion_jobs", sa.Column("agent_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ingestion_jobs_agent_session_id", "ingestion_jobs", "agent_sessions",
        ["agent_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_ingestion_jobs_agent_session_id", "ingestion_jobs", ["agent_session_id"])
    op.execute(
        "UPDATE ingestion_jobs j SET agent_session_id = r.agent_session_id "
        "FROM recon_sessions r WHERE j.recon_session_id = r.id"
    )

    op.add_column("scan_batches", sa.Column("agent_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_scan_batches_agent_session_id", "scan_batches", "agent_sessions",
        ["agent_session_id"], ["id"], ondelete="SET NULL",
    )
    # Backfill the first batch per (session, label) only: two runs of one
    # session may have used the same label, and the new unique index would
    # refuse both.  The others stay listed as operator-less batches.
    op.execute(
        "UPDATE scan_batches b SET agent_session_id = x.agent_session_id "
        "FROM (SELECT DISTINCT ON (r.agent_session_id, sb.label) sb.id, r.agent_session_id "
        "      FROM scan_batches sb JOIN recon_sessions r ON r.id = sb.recon_session_id "
        "      WHERE r.agent_session_id IS NOT NULL "
        "      ORDER BY r.agent_session_id, sb.label, sb.id) x "
        "WHERE b.id = x.id"
    )
    op.drop_index("uq_scan_batches_session_label", table_name="scan_batches")
    op.create_index(
        "uq_scan_batches_agent_session_label", "scan_batches", ["agent_session_id", "label"],
        unique=True, postgresql_where=sa.text("agent_session_id IS NOT NULL"),
    )

    # --- recon runs are gone ------------------------------------------------
    op.execute(
        "UPDATE test_plans SET source_kind = 'unspecified' WHERE source_kind = 'recon_session'"
    )
    op.drop_index("idx_test_plan_source_recon", table_name="test_plans")
    op.drop_constraint("fk_test_plans_source_recon_session", "test_plans", type_="foreignkey")
    op.drop_column("test_plans", "source_recon_session_id")

    op.drop_index("ix_agent_feedback_recon_session_id", table_name="agent_feedback")
    op.drop_constraint("fk_agent_feedback_recon_session", "agent_feedback", type_="foreignkey")
    op.drop_column("agent_feedback", "recon_session_id")

    op.drop_index("idx_agent_api_call_recon_created", table_name="agent_api_calls")
    op.drop_index("ix_agent_api_calls_recon_session_id", table_name="agent_api_calls")
    op.drop_constraint("agent_api_calls_recon_session_id_fkey", "agent_api_calls", type_="foreignkey")
    op.drop_column("agent_api_calls", "recon_session_id")

    op.drop_index("ix_ingestion_jobs_recon_session_id", table_name="ingestion_jobs")
    op.drop_constraint("ingestion_jobs_recon_session_id_fkey", "ingestion_jobs", type_="foreignkey")
    op.drop_column("ingestion_jobs", "recon_session_id")

    op.drop_constraint("scan_batches_recon_session_id_fkey", "scan_batches", type_="foreignkey")
    op.drop_column("scan_batches", "recon_session_id")

    op.drop_table("recon_sessions")


def downgrade():
    # Recon runs: the table and its links come back empty — the runs
    # themselves are not recoverable.
    op.create_table(
        "recon_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("uploads_submitted", sa.Integer(), nullable=False),
        sa.Column("scans_ingested", sa.Integer(), nullable=False),
        sa.Column("hosts_discovered", sa.Integer(), nullable=False),
        sa.Column("ports_discovered", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("environment", sa.JSON(), nullable=True),
        sa.Column("environment_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "environment_probed_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL",
                          name="fk_recon_sessions_environment_probed_by_user_id"),
            nullable=True,
        ),
        sa.Column("environment_probed_from_ip", sa.String(45), nullable=True),
        sa.Column("generated_by_model", sa.String(100), nullable=True),
        sa.Column("generated_by_tool", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column(
            "agent_session_id", sa.Integer(),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE",
                          name="fk_recon_sessions_agent_session_id"),
            nullable=True,
        ),
    )
    for name, cols in (
        ("idx_recon_session_project", ["project_id"]),
        ("idx_recon_session_scope", ["scope_id"]),
        ("idx_recon_session_status", ["status"]),
        ("ix_recon_sessions_agent_session_id", ["agent_session_id"]),
        ("ix_recon_sessions_id", ["id"]),
        ("ix_recon_sessions_project_id", ["project_id"]),
        ("ix_recon_sessions_scope_id", ["scope_id"]),
    ):
        op.create_index(name, "recon_sessions", cols)

    op.add_column("scan_batches", sa.Column("recon_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "scan_batches_recon_session_id_fkey", "scan_batches", "recon_sessions",
        ["recon_session_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("ingestion_jobs", sa.Column("recon_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "ingestion_jobs_recon_session_id_fkey", "ingestion_jobs", "recon_sessions",
        ["recon_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_ingestion_jobs_recon_session_id", "ingestion_jobs", ["recon_session_id"])
    op.add_column("agent_api_calls", sa.Column("recon_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_api_calls_recon_session_id_fkey", "agent_api_calls", "recon_sessions",
        ["recon_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_agent_api_calls_recon_session_id", "agent_api_calls", ["recon_session_id"])
    op.create_index(
        "idx_agent_api_call_recon_created", "agent_api_calls", ["recon_session_id", "created_at"],
    )
    op.add_column("agent_feedback", sa.Column("recon_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_feedback_recon_session", "agent_feedback", "recon_sessions",
        ["recon_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_agent_feedback_recon_session_id", "agent_feedback", ["recon_session_id"])
    op.add_column("test_plans", sa.Column("source_recon_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_test_plans_source_recon_session", "test_plans", "recon_sessions",
        ["source_recon_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("idx_test_plan_source_recon", "test_plans", ["source_recon_session_id"])

    # Uploads lose their agent-session attribution here: with no recon runs
    # to map them back to, uploads made under this revision read as operator
    # uploads after a downgrade.  Runs abandoned on upgrade stay abandoned.
    op.drop_index("uq_scan_batches_agent_session_label", table_name="scan_batches")
    op.create_index(
        "uq_scan_batches_session_label", "scan_batches", ["recon_session_id", "label"],
        unique=True, postgresql_where=sa.text("recon_session_id IS NOT NULL"),
    )
    op.drop_constraint("fk_scan_batches_agent_session_id", "scan_batches", type_="foreignkey")
    op.drop_column("scan_batches", "agent_session_id")
    op.drop_index("ix_ingestion_jobs_agent_session_id", table_name="ingestion_jobs")
    op.drop_constraint("fk_ingestion_jobs_agent_session_id", "ingestion_jobs", type_="foreignkey")
    op.drop_column("ingestion_jobs", "agent_session_id")

    op.add_column(
        "test_execution_results",
        sa.Column("sanity_override_reason", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_test_execution_results_sanity_override_reason",
        "test_execution_results", ["sanity_override_reason"],
    )

    # Approval states are not restored: nothing distinguishes a plan that was
    # approved from one that never was once both are drafts.
    op.alter_column("test_plans", "archive_reason", new_column_name="rejection_reason")
    op.add_column("test_plans", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_plans", sa.Column("rejected_by_id", sa.Integer(), nullable=True))
    op.add_column("test_plans", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_plans", sa.Column("approved_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "test_plans_rejected_by_id_fkey", "test_plans", "users",
        ["rejected_by_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "test_plans_approved_by_id_fkey", "test_plans", "users",
        ["approved_by_id"], ["id"], ondelete="SET NULL",
    )
