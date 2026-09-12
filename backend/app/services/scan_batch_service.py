"""Upload batches — files uploaded together as one sweep (v2.335.0).

See ``models.ScanBatch``. The Scans page creates operator batches directly
(``POST /scans/batches``); agents name theirs with a ``batch`` label on each
recon upload, resolved here within their recon session.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ScanBatch

MAX_LABEL_LENGTH = 200


def get_or_create_session_batch(
    db: Session, *, project_id: int, recon_session_id: int, label: str,
) -> ScanBatch:
    """The recon session's batch named ``label``, created on first use.

    Every chunk of a sweep an agent uploads under the same label lands in one
    batch. Two chunks racing to create it are settled by the unique
    (recon_session_id, label) index: the loser re-reads the winner's row.
    Added to the caller's transaction, which commits it with the upload.
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
    try:
        with db.begin_nested():
            batch = ScanBatch(project_id=project_id, recon_session_id=recon_session_id, label=label)
            db.add(batch)
    except IntegrityError:
        batch = _existing()
        if batch is None:  # pragma: no cover — the conflict was something else
            raise
    return batch
