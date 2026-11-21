"""a web interface's technologies as rows; what the call log is asked about a session, on the session

Review 2026-11-20 (T1, T6).

**``web_interface_technologies``** — one row per technology a web interface
was reported with (``web_interface_id``, ``host_id``, ``name``).  The JSON
array ``web_interfaces.technologies`` was filtered (``tech:``), unnested (the
Hosts facet, the query suggestions) and bucketed in Python (the technology
monoculture), each with its own idea of which rows count; a value that is
filtered and grouped across hosts is a typed column.  The array stays on the
row as what the tool wrote; nothing reads it across hosts any more.

**``web_interfaces.technologies_stated``** — the tool said what technologies
it saw here (possibly none).  "The latest observation of a URL wins" needs to
know which rows are observations of technologies at all; that was read off
the JSON column being non-NULL.

Back-filled from the existing rows: a row whose ``technologies`` is not SQL
NULL is stated; every string of an array becomes a row (trimmed, cut at 160
characters as the monoculture count always cut it, once per interface), for
interfaces that have a host.  Nothing is refused.

The expression index ``ix_trgm_web_technologies`` (over the JSON cast to
text) goes; ``ix_trgm_web_technology_name`` (trigram, over the name) replaces
it.

**``agent_sessions.first_call_at`` / ``via_mcp``** — when the session's first
authenticated call arrived and whether any arrived through the MCP transport:
two facts that never change once set, which the sessions list re-read from
every call row of every listed session.  Back-filled from the call log as it
stands (calls older than the log's retention are gone, as they were for the
list).

**Downgrade.**  Drops the table, the columns and the new index, and builds
the old expression index again.  The JSON arrays were never changed.

Revision ID: d2e8b0f4a6c3
Revises: c1d7a9e3b5f2
Create Date: 2026-11-21
"""
import sqlalchemy as sa
from alembic import op

revision = "d2e8b0f4a6c3"
down_revision = "c1d7a9e3b5f2"
branch_labels = None
depends_on = None

_OLD_INDEX = "ix_trgm_web_technologies"
_NEW_INDEX = "ix_trgm_web_technology_name"


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"

    op.add_column("web_interfaces", sa.Column(
        "technologies_stated", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.execute("UPDATE web_interfaces SET technologies_stated = true WHERE technologies IS NOT NULL")

    op.create_table(
        "web_interface_technologies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "web_interface_id", sa.Integer(),
            sa.ForeignKey("web_interfaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.UniqueConstraint("web_interface_id", "name", name="uq_web_interface_technology"),
    )
    op.create_index("ix_web_interface_technologies_host_id", "web_interface_technologies", ["host_id"])
    if postgres:
        op.execute(
            """
            INSERT INTO web_interface_technologies (web_interface_id, host_id, name)
            SELECT DISTINCT w.id, w.host_id, left(btrim(t.name), 160)
            FROM web_interfaces w
            CROSS JOIN LATERAL json_array_elements_text(
                CASE WHEN json_typeof(w.technologies) = 'array' THEN w.technologies ELSE '[]'::json END
            ) AS t(name)
            WHERE w.host_id IS NOT NULL AND w.technologies IS NOT NULL AND btrim(t.name) <> ''
            """
        )
        op.execute(f"DROP INDEX IF EXISTS {_OLD_INDEX}")
        op.execute(
            f"CREATE INDEX {_NEW_INDEX} ON web_interface_technologies USING gin (name gin_trgm_ops)"
        )

    op.add_column("agent_sessions", sa.Column("first_call_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_sessions", sa.Column(
        "via_mcp", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.execute(
        """
        UPDATE agent_sessions s
        SET first_call_at = c.first_at, via_mcp = c.mcp
        FROM (
            SELECT agent_session_id, min(created_at) AS first_at, coalesce(bool_or(via_mcp), false) AS mcp
            FROM agent_api_calls WHERE agent_session_id IS NOT NULL GROUP BY agent_session_id
        ) c
        WHERE c.agent_session_id = s.id
        """
    )


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    op.drop_column("agent_sessions", "via_mcp")
    op.drop_column("agent_sessions", "first_call_at")
    if postgres:
        op.execute(f"DROP INDEX IF EXISTS {_NEW_INDEX}")
    op.drop_index("ix_web_interface_technologies_host_id", table_name="web_interface_technologies")
    op.drop_table("web_interface_technologies")
    op.drop_column("web_interfaces", "technologies_stated")
    if postgres:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_OLD_INDEX} "
            "ON web_interfaces USING gin ((technologies::text) gin_trgm_ops)"
        )
