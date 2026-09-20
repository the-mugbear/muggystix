"""Upload batches — files uploaded together as one sweep (v2.335.0).

See ``models.ScanBatch``. The Scans page creates operator batches directly
(``POST /scans/batches``); agents name theirs with a ``batch`` label on each
recon upload, resolved here within their recon session.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ScanBatch

MAX_LABEL_LENGTH = 200
BATCH_LOCK_TIMEOUT_MS = 5000


def get_or_create_session_batch(
    db: Session, *, project_id: int, recon_session_id: int, label: str,
) -> ScanBatch:
    """The recon session's batch named ``label``, created on first use.

    Every chunk of a sweep an agent uploads under the same label lands in one
    batch. Two chunks racing to create it are settled by the unique
    (recon_session_id, label) index: the loser re-reads the winner's row.

    **A newly created batch is COMMITTED here, before returning** (v2.368.0).
    It used to ride the caller's transaction, and the caller is an ``async``
    handler that then awaits file I/O. A second chunk with the same new label,
    served by the same worker while the first was suspended in that await,
    blocked synchronously inside Postgres on the unique index — waiting for a
    transaction that could only finish once the event loop ran again, which
    the blocked call prevented. The worker hung for good, ``/health``
    included. Agents upload a sweep's chunks in parallel, so that was the
    ordinary case, not a corner. Committing first makes the wait as long as an
    insert. The cost is that a rejected upload can leave an empty batch —
    the same state an operator's ``POST /scans/batches`` followed by a
    rejected file already produces, and the Scans page already handles.

    ``lock_timeout`` is the backstop: if anything ever holds that index entry
    across an await again, the loser errors out after a few seconds instead of
    freezing the worker.
    """
    label = label.strip()[:MAX_LABEL_LENGTH]

    def _existing():
        return (
            db.query(ScanBatch)
            .filter(ScanBatch.recon_session_id == recon_session_id, ScanBatch.label == label)
            .first()
        )

    batch = _existing()
    if batch is not None:
        return batch
    if db.get_bind().dialect.name == "postgresql":
        # SET LOCAL: scoped to this transaction, gone at the commit below.
        db.execute(text(f"SET LOCAL lock_timeout = '{BATCH_LOCK_TIMEOUT_MS}ms'"))
    try:
        with db.begin_nested():
            batch = ScanBatch(project_id=project_id, recon_session_id=recon_session_id, label=label)
            db.add(batch)
    except IntegrityError:
        batch = _existing()
        if batch is None:  # pragma: no cover — the conflict was something else
            raise
    # Created or lost the race: either way nothing of ours may stay open on
    # that index entry while the caller awaits.
    db.commit()
    return batch
