"""System-level operational metrics (admin only).

Global, not project-scoped — queue health is a property of the deployment, not
of a single project. Currently exposes the durable job-queue snapshot
(ingestion + report) for monitoring/ops; a `curl`-able JSON shape, no Prometheus
infra required.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_role
from app.db.models_auth import User
from app.db.session import get_db
from app.services.queue_metrics_service import queue_metrics

router = APIRouter()


@router.get("/queue-metrics", summary="Durable job-queue operational metrics")
def get_queue_metrics(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
) -> dict:
    """Queue depth, oldest-queued age, stale (reapable) in-flight count, failed
    backlog, and last-hour throughput + mean processing time, for both the
    ingestion and report queues."""
    return queue_metrics(db)
