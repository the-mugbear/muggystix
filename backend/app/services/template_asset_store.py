"""Uploaded report-template images (v2.431.0).

A template's own images (logo, cover art, the Word styles file) were only ever
installed by copying them into ``report-templates/<name>/`` on the server —
a folder mounted READ-ONLY into the backend and report worker.  Uploading from
the Reports page writes them here instead:

    <UPLOAD_DIR>/template_assets/<template>/<asset id><ext>   the file
    <UPLOAD_DIR>/template_assets/<template>/<asset id>.json   who, when, size

The metadata file is written last and removed first, so a file without one is
never used.  Each upload writes its own temporary files (``.<asset id>.<random>
.tmp``) and publishes file and metadata under a per-asset ``flock``
(``.<asset id>.lock``, shared by every API worker; ``remove`` takes it too) —
neither is ever listed as an upload.  ``uploads/`` is already backed up (``backup-db.sh``) and carried
across upgrades (``upgrade-instance.sh``).

Templates are shared by every project, so an upload is instance-wide branding:
global administrators only (enforced by the endpoint).  The renderer places an
upload at the asset's declared path in its private copy of the template
(``quarto_render._place_overrides``) — it wins over a server-installed file —
and ``report_template_service.fingerprint`` includes it, so an issued report
refuses to re-render with a different image (a revision, as for any template
change).

Validation reads what the bytes ARE, never the name or the browser's type:

* PNG / JPEG — the signature, and the pixel size from the header (no image
  library decodes anything); the type must match the declared path's
  extension, since the Word pipeline and pandoc go by it.  SVG, GIF and WebP
  are not uploadable (an SVG would carry script into the HTML report).
* DOCX — a real Word document (``[Content_Types].xml``, ``word/document.xml``,
  ``word/styles.xml``), no macro part, no member path escaping the archive,
  and a bounded uncompressed size (no zip bomb).
* Size — the asset's ``max_bytes`` (default 5 MB for an image, 10 MB for a
  Word file); pixels — the asset's ``min_width`` / ``min_height``, and at most
  50 megapixels; an ``aspect`` that differs by more than 10 % is a warning.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import struct
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from app.core.config import settings

_TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ASSET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_PIXELS = 50_000_000
# An uncompressed DOCX larger than this is refused (a zip bomb, or not a
# styles file).
MAX_DOCX_UNCOMPRESSED = 100 * 1024 * 1024
_EXT = {"png": ".png", "jpeg": ".jpg", "docx": ".docx"}
_KIND_LABEL = {"png": "PNG image", "jpeg": "JPEG image", "docx": "Word document (.docx)"}


class AssetUploadError(ValueError):
    """An upload that is refused; the message is safe to show the operator."""


def root() -> Path:
    return Path(settings.UPLOAD_DIR) / "template_assets"


def _folder(template_name: str) -> Path:
    if not _TEMPLATE_NAME.match(template_name or ""):
        raise AssetUploadError("Not a template name.")
    return root() / template_name


def _paths(template_name: str, asset_id: str, kind: str) -> Tuple[Path, Path]:
    if not _ASSET_ID.match(asset_id or ""):
        raise AssetUploadError("Not an asset id.")
    folder = _folder(template_name)
    return folder / f"{asset_id}{_EXT[kind]}", folder / f"{asset_id}.json"


# ---------------------------------------------------------------------------
# Reading what is uploaded
# ---------------------------------------------------------------------------

def uploads(template_name: str) -> Dict[str, dict]:
    """{asset id: metadata} for every complete upload of the template (the
    metadata carries ``file``, the stored file's absolute path)."""
    try:
        folder = _folder(template_name)
    except AssetUploadError:
        return {}
    if not folder.is_dir():
        return {}
    out: Dict[str, dict] = {}
    for meta_path in sorted(folder.glob("*.json")):
        asset_id = meta_path.stem
        if not _ASSET_ID.match(asset_id):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = meta.get("kind")
        if kind not in _EXT:
            continue
        stored = folder / f"{asset_id}{_EXT[kind]}"
        if stored.is_file() and not stored.is_symlink():
            out[asset_id] = {**meta, "file": stored}
    return out


def overrides(template_name: str) -> Dict[str, Path]:
    """{asset id: file} — what the renderer places over the template's own."""
    return {asset_id: meta["file"] for asset_id, meta in uploads(template_name).items()}


def public_info(meta: dict) -> dict:
    """The metadata shown on the Reports page (no server path)."""
    return {k: meta.get(k) for k in (
        "kind", "size", "width", "height", "sha256", "original_filename", "uploaded_at", "uploaded_by",
    )}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _png_size(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    # A complete file ends with the IEND chunk (a truncated upload does not).
    if data[-8:-4] != b"IEND":
        return None
    return struct.unpack(">II", data[16:24])


# Start-of-frame markers carry the frame's size (not DHT C4, JPG C8, DAC CC).
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 4 or data[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return None
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def _check_docx(data: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AssetUploadError("This is not a Word document (.docx): it is not a valid archive.")
    with archive:
        infos = archive.infolist()
        names = {i.filename for i in infos}
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise AssetUploadError("This Word document contains a file path outside itself and is refused.")
        if sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED:
            raise AssetUploadError("This Word document expands to more than 100 MB and is refused.")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise AssetUploadError("This Word document contains macros; save it as a plain .docx without them.")
        for required in ("[Content_Types].xml", "word/document.xml", "word/styles.xml"):
            if required not in names:
                raise AssetUploadError(
                    f"This is not a Word styles file: {required} is missing. Start from a copy of the "
                    "template's reference.docx, change its styles in Word, and save it as .docx."
                )
        try:
            content_types = archive.read("[Content_Types].xml")
        except (KeyError, zipfile.BadZipFile, RuntimeError):
            raise AssetUploadError("This Word document could not be read.")
        if b"wordprocessingml.document.main" not in content_types:
            raise AssetUploadError(
                "This is not a Word document (.docx) — a template (.dotx) or macro-enabled file is not accepted."
            )


def _aspect_warning(asset: dict, width: int, height: int) -> Optional[str]:
    aspect = asset.get("aspect")
    if not aspect:
        return None
    w, h = (float(x) for x in aspect.split(":"))
    wanted, actual = w / h, width / height
    if abs(actual - wanted) / wanted > 0.10:
        return (
            f"The image is {width} × {height} px (about {actual:.2f}:1); this place is shaped "
            f"{aspect} ({wanted:.2f}:1), so it will be scaled to fit with space around it."
        )
    return None


def validate(asset: dict, data: bytes) -> Tuple[dict, List[str]]:
    """``(facts, warnings)`` for an upload to ``asset`` (a ``template_assets``
    entry), or ``AssetUploadError`` saying what is wrong and what is wanted."""
    kind = asset.get("kind") or ""
    if not asset.get("uploadable") or kind not in _EXT:
        raise AssetUploadError(
            f"{asset.get('label') or asset.get('id')} cannot be uploaded here (it is a "
            f"{kind or 'different'} file); an administrator installs it on the server."
        )
    if not data:
        raise AssetUploadError("The file is empty.")
    max_bytes = int(asset.get("max_bytes") or 0)
    if max_bytes and len(data) > max_bytes:
        raise AssetUploadError(
            f"The file is {len(data) / 1048576:.1f} MB; {asset.get('label')} can be at most "
            f"{max_bytes / 1048576:.1f} MB."
        )
    facts = {"kind": kind, "size": len(data), "width": None, "height": None,
             "sha256": hashlib.sha256(data).hexdigest()}
    warnings: List[str] = []
    if kind == "docx":
        _check_docx(data)
        return facts, warnings

    size = _png_size(data) if kind == "png" else _jpeg_size(data)
    if size is None:
        other = _jpeg_size(data) if kind == "png" else _png_size(data)
        found = f" (it is a {'JPEG' if kind == 'png' else 'PNG'} image)" if other else ""
        raise AssetUploadError(
            f"{asset.get('label')} must be a {_KIND_LABEL[kind]}{found}; this file is not a complete "
            f"{_KIND_LABEL[kind]}."
        )
    width, height = size
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise AssetUploadError(f"The image is {width} × {height} px; at most 50 megapixels are accepted.")
    min_w, min_h = asset.get("min_width"), asset.get("min_height")
    if (min_w and width < min_w) or (min_h and height < min_h):
        raise AssetUploadError(
            f"The image is {width} × {height} px; {asset.get('label')} needs at least "
            f"{min_w or 1} × {min_h or 1} px to print sharply."
        )
    warning = _aspect_warning(asset, width, height)
    if warning:
        warnings.append(warning)
    facts.update(width=width, height=height)
    return facts, warnings


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _clean_filename(name: Optional[str]) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    return "".join(ch for ch in base if ch.isprintable())[:120]


def save(template_name: str, asset: dict, data: bytes, *, uploaded_by: str,
         original_filename: Optional[str] = None) -> Tuple[dict, List[str]]:
    """Validate and store an upload (replacing a previous one), atomically:
    the file first, the metadata that makes it count last."""
    facts, warnings = validate(asset, data)
    stored, meta_path = _paths(template_name, asset["id"], facts["kind"])
    stored.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        **facts,
        "original_filename": _clean_filename(original_filename),
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    # Each upload writes temporary files of its OWN (a fixed `<name>.tmp` was
    # shared by two workers uploading the same asset: one renamed it away and
    # the other's rename failed, or one's metadata was paired with the
    # other's bytes), then publishes both under the asset's lock.
    tmp_file = _write_private(stored.parent, asset["id"], data)
    tmp_meta = None
    try:
        tmp_meta = _write_private(stored.parent, asset["id"], json.dumps(meta).encode("utf-8"))
        with _publication_lock(template_name, asset["id"]):
            os.replace(tmp_file, stored)
            os.replace(tmp_meta, meta_path)
    finally:
        tmp_file.unlink(missing_ok=True)  # only still there when it failed
        if tmp_meta is not None:
            tmp_meta.unlink(missing_ok=True)
    return meta, warnings


def _write_private(folder: Path, asset_id: str, data: bytes) -> Path:
    """Write ``data`` to a new file no other upload can name.  The name starts
    with a dot and ends ``.tmp``, so ``uploads`` (``*.json`` with an asset-id
    stem) never lists it; created with the process umask, as the published
    file always was (the report worker reads it)."""
    path = folder / f".{asset_id}.{uuid.uuid4().hex}.tmp"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@contextmanager
def _publication_lock(template_name: str, asset_id: str) -> Iterator[None]:
    """One writer at a time per (template, asset), across PROCESSES: several
    API workers share the uploads volume, so the lock is ``flock`` on
    ``.<asset id>.lock`` in the asset's folder.  Held across the file AND its
    metadata in ``save`` and across ``remove``.  The lock file is never
    removed (unlinking a lock file another process is waiting on gives two
    holders) and is never an asset: ``uploads`` reads only ``*.json``."""
    if not _ASSET_ID.match(asset_id or ""):
        raise AssetUploadError("Not an asset id.")
    lock_path = _folder(template_name) / f".{asset_id}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def remove(template_name: str, asset_id: str) -> Optional[dict]:
    """Remove an upload; its metadata, or None when there was none."""
    if uploads(template_name).get(asset_id) is None:
        return None
    with _publication_lock(template_name, asset_id):
        # Read again under the lock: an upload may have replaced it (even
        # with another kind of file) since the check above.
        meta = uploads(template_name).get(asset_id)
        if meta is None:
            return None
        _stored, meta_path = _paths(template_name, asset_id, meta["kind"])
        meta_path.unlink(missing_ok=True)  # first: from here on it is not used
        Path(meta["file"]).unlink(missing_ok=True)
        return meta
