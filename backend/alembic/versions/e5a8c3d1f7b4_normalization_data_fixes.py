"""Normalization data fixes: unstated exploit flags, Nikto's invented LOW, empty CVE ids

Three corrections to stored rows, each the data form of a rule the parsers
now follow (2.535.0–2.537.0: a field is the tool's statement, and a statement
a tool did not make is never written):

* ``a3f1c7e9d2b4`` set unstated ``exploitable = false`` rows to NULL but kept
  an OpenVAS row whenever its vector was NULL — the exclusion compared a NULL
  vector and NULL logic kept the row out of the update.  The statement is
  repeated here with ``coalesce``, so a database that already ran it is
  corrected (the original revision is corrected too for fresh chains; both
  are idempotent).
* Nikto rates nothing; every native Nikto finding was stored LOW by the
  parser (2.536.0 writes UNKNOWN).  Nikto rows rated LOW that are not a
  catalog check become UNKNOWN.  A wrapper format that stated "low" is
  indistinguishable and is re-rated too; a re-upload records its statement.
* The Nikto parser passed an empty string as a CVE id; it is no id.  Empty
  ``cve_id`` values become NULL (the CVE list, ``vulnerability_cves``,
  skipped them already).

Downgrade: Nikto UNKNOWN rows (no catalog check) back to LOW — what the older
parser wrote; the other two cannot be inverted (nothing recorded which rows
were ``false`` or ``''``) and are left as they are, which is what the older
code reads correctly anyway.

Revision ID: e5a8c3d1f7b4
Revises: d4f7b2c8e1a9
Create Date: 2026-11-06
"""
from alembic import op

revision = "e5a8c3d1f7b4"
down_revision = "d4f7b2c8e1a9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE vulnerabilities
           SET exploitable = NULL
         WHERE exploitable IS FALSE
           AND NOT (source = 'OPENVAS' AND coalesce(cvss_vector, '') ~* '(^|/)E:U(/|$)')
           AND NOT (source = 'NMAP' AND coalesce(plugin_output, '') LIKE 'vulners:%')
        """
    )
    op.execute(
        """
        UPDATE vulnerabilities
           SET severity = 'UNKNOWN'
         WHERE source = 'NIKTO' AND severity = 'LOW' AND check_id IS NULL
        """
    )
    op.execute("UPDATE vulnerabilities SET cve_id = NULL WHERE cve_id IS NOT NULL AND btrim(cve_id) = ''")


def downgrade():
    op.execute(
        """
        UPDATE vulnerabilities
           SET severity = 'LOW'
         WHERE source = 'NIKTO' AND severity = 'UNKNOWN' AND check_id IS NULL
        """
    )
