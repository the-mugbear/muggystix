"""vulnerabilities.cvss_version — which CVSS a row's score and vector are

Scores of CVSS 2.0, 3.x and 4.0 are not one scale, yet one ``cvss_score``
column held them all with nothing to tell them apart.  The version is
derived from the stored vector in one place (``cvss_service.cvss_version_of``)
on every write; this revision adds the column and backfills it from the
vectors already stored with the same rule: a ``CVSS:3.0/`` / ``CVSS:3.1/`` /
``CVSS:4.0/`` prefix names the version; a bare or ``CVSS2#`` vector that
starts with ``AV:`` (parentheses allowed) is 2.0; anything else stays NULL.

Revision ID: b7d2e4f8a1c3
Revises: a3f1c7e9d2b4
Create Date: 2026-11-06
"""
import sqlalchemy as sa
from alembic import op

revision = "b7d2e4f8a1c3"
down_revision = "a3f1c7e9d2b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vulnerabilities", sa.Column("cvss_version", sa.String(length=4), nullable=True))
    op.execute(
        r"""
        UPDATE vulnerabilities
           SET cvss_version = CASE
                 WHEN cvss_vector ~ '^\s*CVSS:3\.1/' THEN '3.1'
                 WHEN cvss_vector ~ '^\s*CVSS:3\.0/' THEN '3.0'
                 WHEN cvss_vector ~ '^\s*CVSS:4\.0/' THEN '4.0'
                 WHEN cvss_vector ~* '^\s*(CVSS2#)?\(?AV:' THEN '2.0'
                 ELSE NULL
               END
         WHERE cvss_vector IS NOT NULL AND btrim(cvss_vector) <> ''
        """
    )


def downgrade():
    op.drop_column("vulnerabilities", "cvss_version")
