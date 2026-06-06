"""Race-safe per-(user, project) cursor upsert (review #9).

Two first-time ``mark-seen`` requests (across tabs/devices) can both observe
no cursor row, then one violates the ``(user_id, project_id)`` unique
constraint.  This does an atomic upsert instead: PostgreSQL ``INSERT … ON
CONFLICT DO UPDATE``; on other backends (the SQLite test fallback) a
portable update-or-insert.  Commits.
"""
from typing import Any, Type

from sqlalchemy import func
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
    """Atomic AND monotonic cursor upsert (reviews #9 + #7).

    The timestamp only ever moves FORWARD: ``GREATEST(existing, incoming)``
    on the conflict path, and a ``> existing`` guard on the fallback path,
    so an older request that lands last can't rewind the cursor and
    resurface already-acknowledged activity.
    """
    col = getattr(model, ts_column)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(model.__table__).values(
            user_id=user_id, project_id=project_id, **{ts_column: ts_value},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "project_id"],
            set_={ts_column: func.greatest(
                getattr(stmt.excluded, ts_column), model.__table__.c[ts_column],
            )},
        )
        db.execute(stmt)
    else:
        updated = (
            db.query(model)
            .filter(
                model.user_id == user_id,
                model.project_id == project_id,
                # Monotonic: only advance; NULL (never seen) also advances.
                (col.is_(None)) | (col < ts_value),
            )
            .update({col: ts_value}, synchronize_session=False)
        )
        if not updated:
            # Either no row yet, or the existing cursor is already newer.
            exists = (
                db.query(model)
                .filter(model.user_id == user_id, model.project_id == project_id)
                .first()
            )
            if exists is None:
                db.add(model(user_id=user_id, project_id=project_id, **{ts_column: ts_value}))
    db.commit()
