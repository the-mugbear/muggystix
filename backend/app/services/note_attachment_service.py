"""Shared image-attachment (evidence) handling for annotation threads.

Notes live on several targets — a host, and now a Finding (the comment/
evidence thread the notes→findings→reports flow refines before a report).
Both attach screenshots the same way, so the security-sensitive bits — the
magic-byte sniff (a renamed/polyglot non-image must not be stored as one),
the 10 MB cap, the 0700/0600 on-disk layout — live here once rather than
being copied per endpoint.
"""
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models

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


def store_image_attachment(
    db: Session, *, note_id: int, project_id: int, uploaded_by_id: int,
    file: UploadFile,
) -> "models.NoteAttachment":
    """Validate, store, and record an image attachment for note ``note_id``.

    The caller is responsible for having scoped ``note_id`` to the project
    (so an attachment can't be hung off another project's note via a tampered
    path) — this function only handles the file itself.  Commits the new row.
    """
    declared = (file.content_type or "").lower()
    if declared not in _ATTACHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, GIF, or WebP images are allowed.")

    # Read with a hard cap (one extra byte detects an over-limit file without
    # materialising the whole thing).
    data = file.file.read(_ATTACHMENT_MAX_BYTES + 1)
    if len(data) > _ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (max 10 MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    ext = _sniff_image(data, declared)
    if not ext:
        raise HTTPException(status_code=400, detail="File content does not match an allowed image type.")

    # Store under uploads/note_attachments/{note_id}/{uuid}.{ext}, 0700/0600.
    note_dir = _attachments_root() / str(note_id)
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
    display_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(file.filename or ""))[:255] or f"image.{ext}"

    att = models.NoteAttachment(
        annotation_id=note_id,
        project_id=project_id,
        filename=display_name,
        content_type=declared,
        size_bytes=len(data),
        storage_path=f"{note_id}/{stored_name}",
        uploaded_by_id=uploaded_by_id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return att
