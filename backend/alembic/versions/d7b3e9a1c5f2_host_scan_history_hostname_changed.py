"""host_scan_history: add hostname_changed (did this scan change the host's display name)

Owner, 2026-10-11: "Add display names changed."  The number of hosts whose
display name an import changed was a figure inside the DNS import's result
sentence and nowhere else: nothing a reader could open, and nothing at all for
the other tools.  It becomes a fact on the row that already says what a scan
did to a host, beside ``host_created``.

**Upgrade.**

``host_scan_history.hostname_changed BOOLEAN NOT NULL DEFAULT false``: true
when THAT scan changed ``hosts_v2.hostname`` on a host that already existed
(an empty name filled, or a name replaced under the display-name precedence
rule).  It is written at import by the two writers of the row.

* Every existing row reads ``false``.  Earlier imports are NOT back-filled:
  the name a host carried before a scan is not kept anywhere, so which scans
  renamed which hosts cannot be derived.  (Owner: "Leave it, I can reimport if
  needed" — a file imported again records it; a scan imported before this
  revision shows no renamed figure.)
* No row is rewritten and no index is built.  On PostgreSQL 11 and later (16
  here) an ``ADD COLUMN`` with a constant default is a catalogue change only:
  the table is not rewritten, so the step does not grow with its size
  (hundreds of thousands of rows on a large installation) and holds its lock
  for an instant.
* No index on purpose: the Scans figures already read every history row of
  the scans on the page (``idx_host_scan_history_scan_discovered``), and the
  Hosts filter reaches a host's rows by ``uq_host_scan (host_id, scan_id)``.

**Downgrade.**

The column is dropped.  What goes with it: which scans changed which hosts'
display names, for every import made on this revision.  The names themselves
(``hosts_v2.hostname``, ``host_scan_history.hostname_at_scan``) are untouched.
Upgrading again brings the column back with every row ``false``.

One transaction; a failure leaves the database at ``a4f8c2e6b0d1``.

Revision ID: d7b3e9a1c5f2
Revises: a4f8c2e6b0d1
"""
import sqlalchemy as sa
from alembic import op

revision = "d7b3e9a1c5f2"
down_revision = "a4f8c2e6b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host_scan_history",
        sa.Column(
            "hostname_changed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("host_scan_history", "hostname_changed")
