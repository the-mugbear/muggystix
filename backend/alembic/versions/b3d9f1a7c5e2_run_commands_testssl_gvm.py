"""Run commands for testssl, gvm-cli and gvm-tools in the tool catalogue

Owner, 2026-10-22: three tools whose output BlueStick parses shipped with no
"Run for BlueStick" text.  ``testssl`` gets the invocation UPLOAD_FORMATS gives
(``--jsonfile out.json``); ``gvm-cli`` and ``gvm-tools`` get a note only — the
report is exported as XML from Greenbone, there is no one command line.

Seeding is additive and never overwrites (``tool_registry_service.seed_registry``),
so the seed edit alone reaches only a new installation; this revision gives
existing rows the same text.  Like ``c9a4e70b5d18`` it touches only what is
still as shipped: a row whose ``run_command`` / ``run_note`` is no longer NULL
keeps what it holds.

**Upgrade**: sets ``tool_registry.run_command`` of ``testssl`` and ``run_note``
of ``gvm-cli`` / ``gvm-tools``, each only where that column is NULL.

**Downgrade**: sets them back to NULL only where they still equal what the
upgrade wrote.

Revision ID: b3d9f1a7c5e2
Revises: e8c4a2f6d1b9
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa


revision = "b3d9f1a7c5e2"
down_revision = "e8c4a2f6d1b9"
branch_labels = None
depends_on = None


# Frozen copies of the seed's text (app/data/tool_registry_seed.json): a
# revision never reads the application, which moves on after it.
TESTSSL_RUN_COMMAND = "testssl --jsonfile out.json https://target:443"
GVM_RUN_NOTE = (
    "BlueStick reads the finished report exported as XML — the Greenbone web interface's "
    "report download (XML format), or gvm-cli … --xml '<get_reports report_id=\"<id>\" "
    "details=\"1\" ignore_pagination=\"1\"/>' > report.xml. Upload that file."
)

# (tool, column, text)
_WRITES = (
    ("testssl", "run_command", TESTSSL_RUN_COMMAND),
    ("gvm-cli", "run_note", GVM_RUN_NOTE),
    ("gvm-tools", "run_note", GVM_RUN_NOTE),
)


def upgrade() -> None:
    conn = op.get_bind()
    for name, column, value in _WRITES:
        conn.execute(
            sa.text(f"UPDATE tool_registry SET {column} = :value WHERE name = :name AND {column} IS NULL"),
            {"value": value, "name": name},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for name, column, value in _WRITES:
        conn.execute(
            sa.text(f"UPDATE tool_registry SET {column} = NULL WHERE name = :name AND {column} = :value"),
            {"value": value, "name": name},
        )
