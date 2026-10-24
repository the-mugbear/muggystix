"""Profile pictures (2.530.0) — the ONE writer of a user's picture.

The browser crops and scales (a 256 × 256 square drawn on a canvas, which also
drops a photo's metadata); the server keeps its own checks: PNG or JPEG whose
bytes match their declared type (``image_headers``, which reads the size only
from a real file of that format), at most ``MAX_BYTES``, square, at most
``MAX_SIDE`` a side.

Stored content-named: ``uploads/profile_images/<user id>/<sha256>.<ext>``, the
row's ``profile_image_sha256`` / ``profile_image_type`` naming it.  Per user,
publication and cleanup are serialized under the user's row lock: an upload
whose sha is already the current one writes nothing; otherwise the file is
written (the same bytes again are harmless), the columns set and committed,
and THEN, under the lock again, cleanup deletes every file in the folder that
is not the sha the row now names.  It never deletes "the old file" by name, so
neither an identical upload nor a concurrent replacement can remove the file
in use.

Who sees a picture (owner, 2026-10-24): the person, users who share a project
with them, and global admins.  Anyone else — and an address naming a sha that
is not the current one — gets the same 404.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership
from app.services.image_headers import image_size

MAX_BYTES = 512 * 1024
MAX_SIDE = 1024
MIN_SIDE = 32
_EXT = {"image/png": "png", "image/jpeg": "jpg"}


def _root() -> Path:
    return Path(settings.UPLOAD_DIR) / "profile_images"


def _folder(user_id: int) -> Path:
    return _root() / str(int(user_id))


def picture_path(user: User) -> Optional[Path]:
    if not user.profile_image_sha256 or user.profile_image_type not in _EXT:
        return None
    return _folder(user.id) / f"{user.profile_image_sha256}.{_EXT[user.profile_image_type]}"


def _lock(db: Session, user_id: int) -> User:
    return (
        db.query(User).filter(User.id == user_id).with_for_update().populate_existing().one()
    )


def _cleanup(db: Session, user_id: int) -> None:
    """Under the user's row lock: delete every stored file that is not the
    one the row names.  Commits (releasing the lock)."""
    user = _lock(db, user_id)
    keep = picture_path(user)
    folder = _folder(user_id)
    if folder.is_dir():
        for f in folder.iterdir():
            if keep is None or f.name != keep.name:
                try:
                    f.unlink()
                except OSError:
                    pass
        if keep is None:
            shutil.rmtree(folder, ignore_errors=True)
    db.commit()


def validate(data: bytes, content_type: str) -> str:
    content_type = (content_type or "").lower()
    if content_type not in _EXT:
        raise HTTPException(status_code=400, detail="A profile picture is a PNG or JPEG image.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="A profile picture is at most 512 KB.")
    size = image_size(data, content_type)
    if size is None:
        raise HTTPException(status_code=400, detail="The file is not the image type it says it is.")
    width, height = size
    if width != height:
        raise HTTPException(status_code=400, detail="A profile picture is square.")
    if not MIN_SIDE <= width <= MAX_SIDE:
        raise HTTPException(status_code=400, detail=f"A profile picture is {MIN_SIDE}–{MAX_SIDE} pixels a side.")
    return content_type


def set_picture(db: Session, user_id: int, data: bytes, content_type: str) -> User:
    content_type = validate(data, content_type)
    sha = hashlib.sha256(data).hexdigest()
    user = _lock(db, user_id)
    if user.profile_image_sha256 == sha and user.profile_image_type == content_type:
        db.commit()
        return user
    folder = _folder(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    target = folder / f"{sha}.{_EXT[content_type]}"
    if not target.is_file():
        tmp = folder / f".{sha}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
    user.profile_image_sha256 = sha
    user.profile_image_type = content_type
    db.commit()
    _cleanup(db, user_id)
    db.refresh(user)
    return user


def remove_picture(db: Session, user_id: int) -> bool:
    """True when there was a picture to remove."""
    user = _lock(db, user_id)
    had = user.profile_image_sha256 is not None
    user.profile_image_sha256 = None
    user.profile_image_type = None
    db.commit()
    _cleanup(db, user_id)
    return had


def discard_all(user_id: int) -> None:
    """After a user is deleted: their folder goes too."""
    shutil.rmtree(_folder(user_id), ignore_errors=True)


def may_see(db: Session, viewer: User, owner_id: int) -> bool:
    if viewer.id == owner_id or viewer.role == UserRole.ADMIN:
        return True
    mine = aliased(ProjectMembership)
    theirs = aliased(ProjectMembership)
    return bool(db.scalar(select(exists().where(
        mine.user_id == viewer.id, theirs.user_id == owner_id, mine.project_id == theirs.project_id,
    ))))


def readable_picture(db: Session, viewer: User, owner_id: int, sha: str) -> tuple:
    """``(path, content_type)`` of the picture ``sha`` of user ``owner_id`` for
    ``viewer``, or a 404 — the same for "no such user", "not theirs to see",
    "not the current picture" and "gone from storage"."""
    not_found = HTTPException(status_code=404, detail="Picture not found")
    owner = db.get(User, owner_id)
    if owner is None or owner.profile_image_sha256 != sha or not may_see(db, viewer, owner_id):
        raise not_found
    path = picture_path(owner)
    if path is None or not path.is_file():
        raise not_found
    return path, owner.profile_image_type
