"""client reports: the executive summary follows the project

Owner, 2026-10-14: "the executive summary seems to belong to a draft and
needs to be surfaced at the project level so the user doesn't have to copy
and paste the executive summary between drafts and released reports."  The
executive summary becomes an engagement detail like the others
(``report_defaults.DETAIL_KEYS``): the project's report defaults hold one
(``report_profiles.executive_summary``), and a full report prints it unless
it overrides it (``reports.settings["executive_summary"]``).  An addendum's
"Summary of changes" is always its own (``report_defaults.OWN_IN_ADDENDUM``).

**Upgrade.**  Adds ``report_profiles.executive_summary`` (empty).  Each DRAFT
whose ``reports.executive_summary`` holds text gets it as its override
(``settings["executive_summary"]``) — so it prints exactly what it printed.
Issued and superseded reports keep theirs in their frozen dataset
(``snapshot.dataset.executive_summary``), which is what they print and what a
revision carries.  Then DROPS ``reports.executive_summary``.

**Downgrade.**  Re-adds ``reports.executive_summary``: a draft's override (the
key removed from its settings), else — a full report — the project's executive
summary; an issued or superseded report's from its frozen dataset.  Then drops
``report_profiles.executive_summary``.  What a draft prints is unchanged at
that moment, but it no longer follows the project.

One transaction; a failure leaves the database at ``b7d2e5f9a1c3``.

Revision ID: c9e4a7b2d6f1
Revises: b7d2e5f9a1c3
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "c9e4a7b2d6f1"
down_revision = "b7d2e5f9a1c3"
branch_labels = None
depends_on = None

KEY = "executive_summary"


def _clean(value):
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _loads(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _write(conn, report_id, settings):
    conn.execute(
        sa.text("UPDATE reports SET settings = CAST(:s AS json) WHERE id = :id"),
        {"s": json.dumps(settings), "id": report_id},
    )


def upgrade() -> None:
    op.add_column("report_profiles", sa.Column(KEY, sa.Text(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, settings, executive_summary FROM reports WHERE status = 'draft' ORDER BY id"
    )).all()
    for report_id, raw, summary in rows:
        text = _clean(summary)
        if text is None:
            continue
        settings = _loads(raw)
        settings = dict(settings) if isinstance(settings, dict) else {}
        settings[KEY] = text
        _write(conn, report_id, settings)
    op.drop_column("reports", KEY)


def downgrade() -> None:
    op.add_column("reports", sa.Column(KEY, sa.Text(), nullable=True))
    conn = op.get_bind()
    profiles = {
        project_id: _clean(text)
        for project_id, text in conn.execute(sa.text(f"SELECT project_id, {KEY} FROM report_profiles")).all()
    }
    for report_id, project_id, kind, status, raw, snapshot in conn.execute(sa.text(
        "SELECT id, project_id, kind, status, settings, snapshot FROM reports ORDER BY id"
    )).all():
        if status == "draft":
            settings = _loads(raw)
            settings = dict(settings) if isinstance(settings, dict) else {}
            own = _clean(settings.pop(KEY, None))
            text = own if own is not None or kind == "addendum" else profiles.get(project_id)
            if KEY in (_loads(raw) or {}):
                _write(conn, report_id, settings)
        else:
            dataset = ((_loads(snapshot) or {}).get("dataset") or {})
            text = _clean(dataset.get(KEY))
        if text is not None:
            conn.execute(sa.text(f"UPDATE reports SET {KEY} = :t WHERE id = :id"), {"t": text, "id": report_id})
    op.drop_column("report_profiles", KEY)
