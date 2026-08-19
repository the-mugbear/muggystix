"""merge the duplicate testssl / testssl.sh registry rows

v2.277.0 shipped a seed carrying both `testssl` (the name the recon catalogue
offers, and therefore the name a reported command is matched against) and
`testssl.sh` (the name the old frontend catalogue documented). They are one
tool; the split was a naming mismatch, not a missing entry.

The seed was corrected in 2.278.0, but seeding is deliberately additive — it
inserts what is missing and never overwrites — so a deployment that already
seeded the 2.277.0 file keeps both rows forever, showing the same tool twice on
the reference page. A shipped-seed mistake is exactly the case additive seeding
cannot fix, which is what this migration is for.

Both statements are guarded on the row still matching what we shipped: an
operator who has edited either row keeps their edit and the migration leaves
them alone. That is the same promise the seeder makes, applied to a correction.

Revision ID: c9a4e70b5d18
Revises: b8d3f61c470a
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "c9a4e70b5d18"
down_revision = "b8d3f61c470a"
branch_labels = None
depends_on = None


_SHIPPED_DOT_SH_DESCRIPTION = (
    "Specialized TLS and SSL auditing script that enumerates supported "
    "protocols, ciphers, certificates, and common HTTPS misconfigurations. "
    "A strong follow-up when scans report weak TLS settings."
)

_SHIPPED_TESTSSL_DESCRIPTION = (
    "TLS/SSL configuration scanner: reports protocol versions, cipher suites, "
    "certificate validity and expiry, and known TLS vulnerabilities for a "
    "service. Use it to confirm weak-crypto findings that a port scan only "
    "hints at."
)

_MERGED_DESCRIPTION = (
    "Specialized TLS and SSL auditing script that enumerates supported "
    "protocols, ciphers, certificates, and common HTTPS misconfigurations. "
    "A strong follow-up when scans report weak TLS settings. Invoked as "
    "testssl.sh; the recon catalogue and this registry key it as `testssl`, "
    "which is the name a reported command is matched against."
)


def upgrade() -> None:
    conn = op.get_bind()
    # Adopt the human-facing prose the reference page carried, onto the row the
    # catalogue actually keys off — but only if nobody has rewritten it.
    conn.execute(
        sa.text(
            """
            UPDATE tool_registry
               SET description = :merged,
                   ports = '443, 8443, 9443'
             WHERE name = 'testssl'
               AND description = :shipped
            """
        ),
        {"merged": _MERGED_DESCRIPTION, "shipped": _SHIPPED_TESTSSL_DESCRIPTION},
    )
    # Drop the duplicate, unless an operator has made it their own (edited the
    # prose, or approved/declined it — either is a decision worth keeping, and
    # a leftover row is far less costly than discarding one).
    conn.execute(
        sa.text(
            """
            DELETE FROM tool_registry
             WHERE name = 'testssl.sh'
               AND status = 'reference'
               AND description = :shipped
            """
        ),
        {"shipped": _SHIPPED_DOT_SH_DESCRIPTION},
    )


def downgrade() -> None:
    # Deliberately not restored: re-creating a duplicate row for a tool that
    # exists under its canonical name would recreate the defect, and the seed
    # file no longer contains it. The description edit is left as-is for the
    # same reason — it is strictly more accurate.
    pass
