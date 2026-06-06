"""vulnerabilities.references: backfill Python-repr strings into JSON

Closes finding #7 from the second code review.  Pre-fix the Nessus
ingestion path wrote ``references`` via ``str(references)``, which
produces Python repr with single quotes (``"['url1', 'url2']"``) —
not valid JSON.  The schema validator silently returned ``[]`` on
parse failure, so every CVE link written before this fix was
discarded at the API boundary.  The application code is now
``json.dumps``; this migration converts the existing rows so they
round-trip too.

Strategy per row:

  1. Try ``json.loads`` — if it parses, leave the value as-is.
  2. Otherwise try ``ast.literal_eval``; if the result is a list of
     strings, write back ``json.dumps`` of that list.
  3. Otherwise leave the row alone (malformed beyond repair —
     surfaces in logs but doesn't fail the migration).

Idempotent: re-running is a no-op because step 1 covers everything
the first run wrote.

Revision ID: a4f2e8d10c39
Revises: e7b3c845f192
Create Date: 2026-06-04
"""
from __future__ import annotations

import ast
import json
import logging

from alembic import op
import sqlalchemy as sa


revision = "a4f2e8d10c39"
down_revision = "e7b3c845f192"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            'SELECT id, "references" FROM vulnerabilities '
            'WHERE "references" IS NOT NULL AND "references" <> \'\''
        )
    ).fetchall()

    converted = 0
    skipped = 0
    malformed = 0
    for row in rows:
        vid, raw = row[0], row[1]
        try:
            json.loads(raw)
            skipped += 1
            continue
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            malformed += 1
            continue
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            malformed += 1
            continue
        bind.execute(
            sa.text('UPDATE vulnerabilities SET "references" = :v WHERE id = :id'),
            {"v": json.dumps(parsed), "id": vid},
        )
        converted += 1

    logger.info(
        "vuln-references backfill: %d converted, %d already-json, %d malformed",
        converted, skipped, malformed,
    )


def downgrade() -> None:
    # No-op: the JSON form is a strict improvement over Python-repr, and
    # the schema validator accepts only JSON anyway.  Downgrading would
    # not reproduce the bug-restoring shape; leave the data alone.
    pass
