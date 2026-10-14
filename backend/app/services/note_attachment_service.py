"""Shared image-attachment (evidence) handling for annotation threads.

Notes live on several targets — a host, and now a Finding (the comment/
evidence thread the notes→findings→reports flow refines before a report).
Both attach screenshots the same way, so the security-sensitive bits — the
magic-byte sniff (a renamed/polyglot non-image must not be stored as one),
the 10 MB cap, the 0700/0600 on-disk layout — live here once rather than
being copied per endpoint.
"""
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models

logger = logging.getLogger(__name__)

# Image attachment limits.  Images only (so the report can embed them and the
# browser can render thumbnails); 10 MB cap; magic-byte sniffed so a renamed
# non-image (or a polyglot) can't be stored as one.
_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
_ATTACHMENT_TYPES = {
    "image/png": (b"\x89PNG\r\n\x1a\n", "png"),
    "image/jpeg": (b"\xff\xd8\xff", "jpg"),
    "image/gif": (b"GIF8", "gif"),
    "image/webp": (None, "webp"),  # RIFF....WEBP — checked specially below
}


def _sniff_image(data: bytes, declared_type: str) -> Optional[str]:
    """Return the canonical extension if ``data`` really is the declared image
    type (magic-byte check), else None."""
    spec = _ATTACHMENT_TYPES.get(declared_type)
    if not spec:
        return None
    magic, ext = spec
    if declared_type == "image/webp":
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ext
        return None
    return ext if data.startswith(magic) else None


def _attachments_root() -> Path:
    return Path(settings.UPLOAD_DIR) / "note_attachments"


UNREADABLE_UPLOADS_FIX = "docker compose exec backend chown -R appuser:appgroup /app/uploads"


def require_readable_file(target: Path, what: str) -> None:
    """404 when a stored file is gone; a 500 that NAMES THE CAUSE when it is
    there but the app cannot read it (v2.374.4).

    Stored files are owner-only (0700 dirs / 0600 files, uid 999).  An uploads
    tree copied in from another deployment keeps the copier's ownership, and
    ``Path.exists()`` then RAISES ``PermissionError`` — every note image
    answered a bare 500 that said nothing.  The entrypoint now heals that at
    boot; this is for a copy made while the stack is running."""
    try:
        ok = target.exists() and target.is_file()
    except PermissionError:
        logger.error(
            "%s at %s exists but is not readable by the app user — the uploads "
            "tree is not owned by uid 999 (copied from another deployment?). Fix: %s",
            what, target, UNREADABLE_UPLOADS_FIX,
        )
        raise HTTPException(
            status_code=500,
            detail=f"{what} is on disk but the server cannot read it: the uploads directory is "
                   f"not owned by the app user. An administrator can fix it with: {UNREADABLE_UPLOADS_FIX}",
        )
    if not ok:
        raise HTTPException(status_code=404, detail=f"{what} file missing on disk")


#: Where the images of a project's own report text are kept, under the
#: attachments root: ``report/{project_id}/``.  Not a number, so the
#: reconciler (which reaps note folders) never looks at it.
REPORT_IMAGES_DIR = "report"


def purge_project_report_images(project_id: int) -> None:
    """Remove a DELETED project's report-text images from disk (their rows
    cascaded with the project)."""
    root = _attachments_root() / REPORT_IMAGES_DIR
    try:
        target = root / str(int(project_id))
        target.resolve().relative_to(root.resolve())
    except (TypeError, ValueError, OSError):
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def store_image_attachment(
    db: Session, *, note_id: Optional[int], project_id: int, uploaded_by_id: int,
    file: UploadFile, caption: Optional[str] = None,
) -> "models.NoteAttachment":
    """Validate, store, and record an image attachment for note ``note_id``.

    The caller is responsible for having scoped ``note_id`` to the project
    (so an attachment can't be hung off another project's note via a tampered
    path) — this function only handles the file itself.  Commits the new row.

    ``note_id=None`` (v2.495.0) stores an image of the project's own REPORT
    text instead: no note, kept under ``report/{project_id}/``, marked for
    the report from the start (that is all it is for) and only in a format
    every report can print.
    """
    declared = (file.content_type or "").lower()
    if note_id is None and declared == "image/webp":
        raise HTTPException(status_code=400, detail="Only PNG, JPEG or GIF images can be printed in a report.")
    if declared not in _ATTACHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, GIF, or WebP images are allowed.")
    # Read with a hard cap (one extra byte detects an over-limit file without
    # materialising the whole thing).
    data = file.file.read(_ATTACHMENT_MAX_BYTES + 1)
    return store_image_bytes(
        db, note_id=note_id, project_id=project_id, uploaded_by_id=uploaded_by_id,
        data=data, content_type=declared, filename=file.filename, caption=caption,
    )


def store_image_bytes(
    db: Session, *, note_id: Optional[int], project_id: int, uploaded_by_id: Optional[int],
    data: bytes, content_type: str, filename: Optional[str], caption: Optional[str] = None,
) -> "models.NoteAttachment":
    """``store_image_attachment`` for bytes already in hand — the ONE writer
    of an image attachment's file and row (an upload, and an issued report's
    frozen image restored for its revision or addendum).  Same checks, same
    layout; commits the new row."""
    declared = (content_type or "").lower()
    for_report = note_id is None
    if for_report and declared == "image/webp":
        raise HTTPException(status_code=400, detail="Only PNG, JPEG or GIF images can be printed in a report.")
    if declared not in _ATTACHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, GIF, or WebP images are allowed.")
    if len(data) > _ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (max 10 MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    ext = _sniff_image(data, declared)
    if not ext:
        raise HTTPException(status_code=400, detail="File content does not match an allowed image type.")

    # Store under uploads/note_attachments/{note_id}/{uuid}.{ext}, 0700/0600.
    folder = f"{REPORT_IMAGES_DIR}/{int(project_id)}" if for_report else str(note_id)
    note_dir = _attachments_root() / folder
    note_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(note_dir, 0o700)
    except OSError:
        pass
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    target = note_dir / stored_name
    with open(target, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    # Sanitised original name for display only (never used as a path).
    display_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or ""))[:255] or f"image.{ext}"

    att = models.NoteAttachment(
        annotation_id=note_id,
        project_id=project_id,
        filename=display_name,
        content_type=declared,
        size_bytes=len(data),
        storage_path=f"{folder}/{stored_name}",
        uploaded_by_id=uploaded_by_id,
        include_in_report=for_report,
        caption=caption,
    )
    db.add(att)
    try:
        db.commit()
    except Exception:
        # Don't leak the file we just wrote if the row didn't persist (R6 —
        # commit-failure orphan).  The reconciler is the backstop; this is the
        # immediate compensation.
        db.rollback()
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    db.refresh(att)
    return att


# --- File lifecycle (R6: don't orphan files on delete / failed commit) -------

def _safe_note_dir(note_id: int) -> Optional[Path]:
    """``note_attachments/{note_id}`` resolved + confined to the root (guards a
    tampered note_id from escaping the attachments tree)."""
    root = _attachments_root()
    try:
        d = (root / str(int(note_id)))
        d.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return d


def purge_note_files(note_id: int) -> None:
    """Remove a note's on-disk attachment dir.  Call AFTER the note's rows are
    deleted (the DB cascade drops the NoteAttachment rows; this drops the files
    they pointed at)."""
    d = _safe_note_dir(note_id)
    if d and d.exists():
        shutil.rmtree(d, ignore_errors=True)


# Grace period so the reconciler never reaps a dir for an in-flight upload (the
# dir exists between mkdir and the row commit).
_RECONCILE_GRACE_SECONDS = 3600


def reconcile_orphan_attachments(db: Session) -> int:
    """Delete attachment dirs with no surviving NoteAttachment row — the backstop
    for commit-failure orphans and any delete path that didn't clean its files.
    Skips dirs younger than the grace window so in-flight uploads are safe."""
    root = _attachments_root()
    if not root.exists():
        return 0
    dirs = [c for c in root.iterdir() if c.is_dir() and c.name.isdigit()]
    if not dirs:
        return 0
    existing = {r[0] for r in db.query(models.NoteAttachment.annotation_id).distinct().all()}
    now = time.time()
    removed = 0
    for c in dirs:
        if int(c.name) in existing:
            continue
        try:
            if now - c.stat().st_mtime <= _RECONCILE_GRACE_SECONDS:
                continue  # possibly an in-flight upload
        except OSError:
            continue
        shutil.rmtree(c, ignore_errors=True)
        removed += 1
    return removed
