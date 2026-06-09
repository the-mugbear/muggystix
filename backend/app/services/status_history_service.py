"""One status-transition recorder for every *_status_history table.

Foundation phase 3 consolidation: annotation and finding lifecycle audit
trails have the same shape (from_status / to_status / changed_by_id /
summary), so they share one recorder instead of each call site re-rolling
the enum-normalize-and-append logic.
"""
from typing import Any, Optional, Type

from sqlalchemy.orm import Session


def _value(status: Any) -> Optional[str]:
    """Normalize an enum member or string status to its stored string."""
    if status is None:
        return None
    return status.value if hasattr(status, "value") else str(status)


def record_status_transition(
    db: Session,
    *,
    history_model: Type,
    fk_field: str,
    entity_id: int,
    from_status: Any,
    to_status: Any,
    changed_by_id: Optional[int],
    summary: Optional[str] = None,
):
    """Append one transition row (added + flushed, not committed).  Returns
    the row, or ``None`` when from == to (a no-op transition is not recorded).

    ``history_model`` is e.g. ``AnnotationStatusHistory`` or
    ``FindingStatusHistory``; ``fk_field`` is its entity FK column name
    (``"note_id"`` / ``"finding_id"``).
    """
    fv, tv = _value(from_status), _value(to_status)
    if fv == tv:
        return None
    row = history_model(
        **{fk_field: entity_id},
        from_status=fv,
        to_status=tv,
        changed_by_id=changed_by_id,
        summary=summary,
    )
    db.add(row)
    db.flush()
    return row
