"""What an unsuccessful import still asks of someone (v2.403.0).

An import that FAILED, or finished PARTIAL, needs attention until someone
dismisses it — unless the same file has since been imported cleanly.  An
operator who fixed a parser, or chose the right format, and uploaded the file
again (a new job, same bytes) has dealt with the failure; the old row kept
reading "4 imports need attention" on /scans and Ingestion Results while every
one of the four files was in the inventory.

**Superseded**: a failed or partial job whose file — the same project, the same
``content_sha256`` — was later imported by a job that completed and was not
partial.  It is not "needs attention" anywhere (``blocked_import_condition``
is the one definition every count and list uses), it says which job imported
it, and it can be dismissed in bulk.

Also here: the SPECIFIC reason a job failed.  The job's own message is the
parse-error service's generic sentence ("Failed to parse the file 'x'. The
file format may not be supported…") while the cause the parser gave ("SMBMap
parser found 0 hosts in x") sits on the ParseError row.  Every list shows the
cause; the generic sentence only when nothing more specific was recorded.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session, aliased

from app.db import models

# How long a reason line may be; the full text stays on the parse error.
_REASON_MAX = 300
# "(psycopg2.errors.StringDataRightTruncation) value too long …" — a driver
# error's class prefix is noise, and its "[SQL: …]" lines follow the first.
_EXC_PREFIX = re.compile(r"^\((?:[A-Za-z_][\w]*\.)*[A-Za-z_]\w*\)\s*")


def unsuccessful_import_condition():
    """Failed, or completed partial — an import that did not fully land."""
    Job = models.IngestionJob
    return or_(Job.status == "failed", and_(Job.status == "completed", Job.partial.is_(True)))


def _later_clean_import(Job, Later):
    return and_(
        Later.project_id == Job.project_id,
        Later.content_sha256 == Job.content_sha256,
        Later.id > Job.id,
        Later.status == "completed",
        Later.partial.is_(False),
    )


def superseded_import_condition():
    """A failed or partial job whose file a LATER job of the project imported
    cleanly.  Correlated on ``models.IngestionJob`` — use it in a query over
    that table."""
    Job = models.IngestionJob
    Later = aliased(models.IngestionJob)
    return and_(
        Job.content_sha256.isnot(None),
        unsuccessful_import_condition(),
        exists().where(_later_clean_import(Job, Later)),
    )


def superseding_job_ids(db: Session, jobs: Iterable[models.IngestionJob]) -> Dict[int, int]:
    """``{job_id: id of the first later job that imported the same file}``
    for the superseded ones among ``jobs``, in one query."""
    candidates = [
        j for j in jobs
        if j.content_sha256
        and (j.status == "failed" or (j.status == "completed" and bool(j.partial)))
    ]
    if not candidates:
        return {}
    Job = models.IngestionJob
    rows = (
        db.query(Job.id, Job.project_id, Job.content_sha256)
        .filter(
            Job.content_sha256.in_({j.content_sha256 for j in candidates}),
            Job.project_id.in_({j.project_id for j in candidates}),
            Job.status == "completed",
            Job.partial.is_(False),
        )
        .order_by(Job.id)
        .all()
    )
    later: Dict[tuple, List[int]] = {}
    for rid, pid, sha in rows:
        later.setdefault((pid, sha), []).append(rid)
    out: Dict[int, int] = {}
    for j in candidates:
        nxt = next((rid for rid in later.get((j.project_id, j.content_sha256), []) if rid > j.id), None)
        if nxt is not None:
            out[j.id] = nxt
    return out


def _first_line(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for line in str(text).strip().splitlines():
        line = _EXC_PREFIX.sub("", line.strip()).strip()
        if line:
            return line if len(line) <= _REASON_MAX else line[: _REASON_MAX - 1] + "…"
    return None


# A database driver's error is a parser bug, not something the operator can
# act on from its wording ("value too long for type character varying(200)").
# The reason line says what happened in plain terms; the raw text stays in the
# parse error's details.
_DB_ERROR = re.compile(r"^\((?:psycopg2|sqlalchemy|sqlite3)[\w.]*\)")
_DB_ERROR_REASONS = (
    ("StringDataRightTruncation", "A value in this file was longer than BlueStick stores — a parser bug; re-import after updating"),
    ("NumericValueOutOfRange", "A number in this file was out of the range BlueStick stores — a parser bug; re-import after updating"),
    ("UniqueViolation", "The import collided with a record already stored — a parser bug; re-import after updating"),
)
_DB_ERROR_GENERIC = "The import failed while storing its results — a parser bug; see Details"


def _plain_reason(text: Optional[str]) -> Optional[str]:
    # psycopg2 raises this one as a bare ValueError, with no driver prefix.
    if text and "cannot contain NUL (0x00)" in str(text):
        return ("The file contains NUL bytes (binary content, or a UTF-16 capture), which "
                "BlueStick could not store — NetExec reads them since 2.420.0; re-import after updating")
    if text and _DB_ERROR.match(str(text).strip()):
        head = str(text).strip().splitlines()[0]
        return next((msg for cls, msg in _DB_ERROR_REASONS if cls in head), _DB_ERROR_GENERIC)
    return _first_line(text)


def failure_reason(job: models.IngestionJob, parse_error: Optional[models.ParseError]) -> Optional[str]:
    """The most specific one-line reason a FAILED job did not import: the
    parser's own message from its ParseError, else the job's message (a
    discard, an expiry, an unexpected processing failure).  A database
    driver error is put in plain words.  ``None`` for a job that did not fail."""
    if job.status != "failed":
        return None
    specific = _plain_reason(parse_error.error_message) if parse_error is not None else None
    return specific or _plain_reason(job.error_message) or _first_line(job.message)


def annotate_jobs(db: Session, jobs: List[models.IngestionJob]) -> List[models.IngestionJob]:
    """Set ``superseded_by_job_id`` and ``failure_reason`` on each job (plain
    attributes, read by ``IngestionJobSchema``).  Two queries for any number
    of jobs."""
    if not jobs:
        return jobs
    superseded = superseding_job_ids(db, jobs)
    pe_ids = {j.parse_error_id for j in jobs if j.parse_error_id is not None and j.status == "failed"}
    parse_errors = (
        {pe.id: pe for pe in db.query(models.ParseError).filter(models.ParseError.id.in_(pe_ids)).all()}
        if pe_ids else {}
    )
    for j in jobs:
        j.superseded_by_job_id = superseded.get(j.id)
        j.failure_reason = failure_reason(j, parse_errors.get(j.parse_error_id))
    return jobs
