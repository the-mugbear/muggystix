"""The upload rows of the Scans page's import history — the Ingestion Results
page's rows until 2.524.0 (moved from ``endpoints/parse_errors`` so
``ingestion_results_service`` can build them for the page and the agents'
read alike — review 2026-10-10, R1b)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class IngestionResultStats(BaseModel):
    hosts_parsed: int = 0
    hosts_up: int = 0
    ports_found: int = 0
    open_ports: int = 0
    services_detected: int = 0


class IngestionResultError(BaseModel):
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    user_message: Optional[str] = None


class IngestionResultItem(BaseModel):
    # NB: this is the INGESTION JOB id, not a ParseError id. The two live in
    # separate tables with independent sequences that overlap heavily (most
    # ingestions succeed, so job ids climb past the dense low range of parse
    # error ids), so passing one where the other is expected does not 404 —
    # it silently returns a DIFFERENT file's error. Use `parse_error_id`
    # below for anything that addresses the ParseError itself.
    id: int
    # The ParseError this job produced, when it produced one. Previously the
    # id was resolved server-side and then dropped, leaving callers to guess
    # with `id` (see /parse-errors/{error_id}).
    parse_error_id: Optional[int] = None
    original_filename: str
    status: str  # queued, processing, completed, failed
    file_size: Optional[int] = None
    tool_name: Optional[str] = None
    scan_type: Optional[str] = None
    scan_id: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    progress: Optional[str] = None
    # v2.351.0 — the format chain: what was detected first, what the
    # operator chose instead (if anything), what actually parsed the file,
    # and the tool they named.  Keys from app/services/format_registry.py,
    # with their labels so the page need not know the registry.
    detected_file_type: Optional[str] = None
    detected_format_label: Optional[str] = None
    format_override: Optional[str] = None
    format_override_label: Optional[str] = None
    final_file_type: Optional[str] = None
    final_format_label: Optional[str] = None
    source_tool: Optional[str] = None
    # v2.354.0 — whether the uploaded bytes are still on disk (so "review
    # the format and retry" / re-process need no re-upload) and until when.
    file_retained: bool = False
    retained_until: Optional[datetime] = None
    # v2.363.0 — "a partial job must stay visibly partial in every list that
    # shows it" (v2.332.0), and this list did not: a truncated file read
    # "Completed".  With what was lost, and whether someone has dismissed it.
    partial: bool = False
    skipped_count: int = 0
    parser_warnings: Optional[str] = None
    dismissed_at: Optional[datetime] = None
    # v2.403.0 — a failed or partial import whose file a LATER job imported
    # cleanly names that job, and is not "needs attention"; and the specific
    # reason a failed job did not import (the parser's own message, not the
    # generic "Failed to parse the file …" sentence).
    superseded_by_job_id: Optional[int] = None
    failure_reason: Optional[str] = None
    # The kind of a failed job's failure (import_attention_service
    # .failure_kind) — "truncated_file" is the one the page acts on: the file
    # was cut short and holds complete records a person may choose to import.
    failure_kind: Optional[str] = None
    # v2.418.0 — how many lines the parser did not interpret, and in how
    # many shapes; the shapes are GET …/ingestion-results/{id}/uninterpreted.
    uninterpreted_total: int = 0
    uninterpreted_distinct: int = 0
    # Stats (populated for completed jobs)
    stats: Optional[IngestionResultStats] = None
    # Error info (populated for failed jobs)
    error: Optional[IngestionResultError] = None
