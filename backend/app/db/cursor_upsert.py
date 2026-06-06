"""Race-safe per-(user, project) cursor upsert (review #9).

Two first-time ``mark-seen`` requests (across tabs/devices) can both observe
no cursor row, then one violates the ``(user_id, project_id)`` unique
constraint.  This does an atomic upsert instead: PostgreSQL ``INSERT … ON
CONFLICT DO UPDATE``; on other backends (the SQLite test fallback) a
portable update-or-insert.  Commits.
"""
from typing import Any, Type

from sqlalchemy.orm import Session


def upsert_user_project_cursor(
    db: Session,
    model: Type[Any],
    *,
    user_id: int,
    project_id: int,
    ts_column: str,
    ts_value,
) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(model.__table__)
            .values(user_id=user_id, project_id=project_id, **{ts_column: ts_value})
            .on_conflict_do_update(
                index_elements=["user_id", "project_id"],
                set_={ts_column: ts_value},
            )
        )
        db.execute(stmt)
    else:
        updated = (
            db.query(model)
            .filter(model.user_id == user_id, model.project_id == project_id)
            .update({getattr(model, ts_column): ts_value}, synchronize_session=False)
        )
        if not updated:
            db.add(model(user_id=user_id, project_id=project_id, **{ts_column: ts_value}))
    db.commit()
