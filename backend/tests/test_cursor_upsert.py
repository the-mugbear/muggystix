"""The per-(user, project) cursor upsert (`app/db/cursor_upsert.py`).

Its one user is the Operations cursor since the Activity cursor went (review
2026-10-23, R-FE-P1 — `test_migration_drop_activity_cursors.py`); this test
moved here from `test_activity_cursor.py`, whose other test guarded the two
removed routes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.cursor_upsert import upsert_user_project_cursor
from app.db.models import OperationsCursor


def test_cursor_upsert_is_monotonic(db_session, test_project, test_user):
    """CR3-#7 — a late older write must not move the cursor backward."""
    t1 = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
    t0 = t1 - timedelta(hours=1)

    upsert_user_project_cursor(
        db_session, OperationsCursor, user_id=test_user.id, project_id=test_project.id,
        ts_column="last_viewed_at", ts_value=t1,
    )
    # An out-of-order OLDER write lands last — must be ignored.
    upsert_user_project_cursor(
        db_session, OperationsCursor, user_id=test_user.id, project_id=test_project.id,
        ts_column="last_viewed_at", ts_value=t0,
    )
    row = (
        db_session.query(OperationsCursor)
        .filter(OperationsCursor.user_id == test_user.id,
                OperationsCursor.project_id == test_project.id)
        .one()
    )
    assert row.last_viewed_at.replace(tzinfo=None) == t1.replace(tzinfo=None)
