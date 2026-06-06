"""Streaming JSON helper for scanner-output parsers.

Several parsers (naabu, amass, httpx, nikto, smbmap, dirbuster,
eyewitness) historically loaded entire JSON files with ``handle.read()``
+ ``json.loads()`` before iterating.  A 200 MB scanner export expands
to ~1-2 GB of Python objects; with several concurrent uploads (the
worker is multi-process), this is enough to OOM the box.

``iter_json_records`` picks the right strategy automatically:

* Below ``threshold_bytes`` (default 50 MB), it reads the file eagerly
  with ``json.loads`` — fast and simple for the common case.
* At or above the threshold, it streams with ``ijson``, peeking at the
  first 64 KiB to detect the top-level shape and pick the correct
  ``ijson`` prefix.  JSONL files are streamed line by line.

Callers receive a uniform iterable of ``dict`` records — the iterate
loop they write doesn't change with file size, only the underlying
memory profile.

Non-dict records are skipped silently; the parsers all want dicts.

Note: this helper does NOT cover the netexec ``{<ip>: {<share>: …}}``
shape — that file's structure can't be streamed as a flat record
iterator without restructuring its parser, so it stays on its own
loader.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable, Iterator, List, Optional, Sequence

import ijson

logger = logging.getLogger(__name__)


_DEFAULT_STREAM_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
_PEEK_BYTES = 64 * 1024


def iter_json_records(
    file_path: str,
    *,
    array_keys: Sequence[str] = (),
    tool_label: str = "JSON",
    threshold_bytes: int = _DEFAULT_STREAM_THRESHOLD_BYTES,
) -> Iterable[dict]:
    """Iterate dict records from a scanner JSON/JSONL file.

    Args:
        file_path: Path on disk.
        array_keys: When the top level is a JSON object, these keys are
            tried in order to find the array of records.  Example:
            ``("results", "vulnerabilities", "findings")``.
        tool_label: Prefix for error messages (``"Naabu JSON"``,
            ``"Nikto JSON"``, …) so the operator sees which file
            failed.
        threshold_bytes: Files at/above this size are streamed; below,
            they're loaded eagerly.

    Returns:
        For small files, a fully materialised ``list[dict]``.  For
        large files, a lazy iterator — but callers iterate either the
        same way (``for record in iter_json_records(...)``).

    Raises:
        ValueError on malformed JSON or on streaming a top-level
        object whose array key isn't found in the peek window.
    """
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        file_size = 0

    if file_size >= threshold_bytes:
        return _stream_records(file_path, array_keys, tool_label)
    return _load_records_eager(file_path, array_keys, tool_label)


def _load_records_eager(
    file_path: str, array_keys: Sequence[str], tool_label: str,
) -> List[dict]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        raw = handle.read().strip()
    if not raw:
        return []
    if raw.startswith("[") or raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # JSON top-level didn't parse — try treating as JSONL.
            payload = None
        if payload is not None:
            return list(_extract_from_payload(payload, array_keys))
    return list(_iter_jsonl_lines(raw.splitlines()))


def _extract_from_payload(payload, array_keys: Sequence[str]) -> Iterator[dict]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in array_keys:
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return
        # Single-object payload — yield as one record so single-target
        # exports (e.g. httpx --json against one URL) still parse.
        yield payload


def _iter_jsonl_lines(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _stream_records(
    file_path: str, array_keys: Sequence[str], tool_label: str,
) -> Iterator[dict]:
    with open(file_path, "rb") as handle:
        head = handle.read(_PEEK_BYTES)
    head_stripped = head.lstrip()

    if head_stripped.startswith(b"["):
        prefix: Optional[str] = "item"
    elif head_stripped.startswith(b"{"):
        prefix = _detect_array_prefix(head_stripped, array_keys)
        if prefix is None:
            # RV-7 — a leading '{' is ambiguous: it's EITHER a single
            # top-level object carrying a known array key, OR JSONL (one
            # object per line, which is exactly what dnsx -json emits and
            # ALWAYS starts with '{').  Pre-fix the latter raised here once
            # the file passed the streaming threshold.  If the first line
            # is itself a complete JSON object, treat the file as JSONL.
            if _looks_like_jsonl(head):
                yield from _stream_jsonl_file(file_path)
                return
            raise ValueError(
                f"{tool_label} exceeds streaming threshold but no recognised "
                f"top-level array key was found in the first {_PEEK_BYTES} bytes "
                f"(expected one of: {', '.join(array_keys) or '[no keys configured]'})."
            )
    else:
        # No leading `[` or `{` — assume JSONL and stream by line.
        yield from _stream_jsonl_file(file_path)
        return

    with open(file_path, "rb") as handle:
        try:
            for item in ijson.items(handle, prefix):
                if isinstance(item, dict):
                    yield item
        except ijson.JSONError as exc:
            raise ValueError(
                f"Invalid or truncated {tool_label} during streaming: {exc}"
            ) from exc


def _looks_like_jsonl(head: bytes) -> bool:
    """RV-7 — True when the first non-empty line of the peek window is a
    complete JSON object, i.e. the file is JSONL (object-per-line) rather
    than one pretty-printed top-level object (whose first line is just
    ``{``).  Used to disambiguate a leading ``{`` that carries no known
    top-level array key."""
    text = head.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            return isinstance(json.loads(s), dict)
        except json.JSONDecodeError:
            return False
    return False


def _stream_jsonl_file(file_path: str) -> Iterator[dict]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _detect_array_prefix(head: bytes, array_keys: Sequence[str]) -> Optional[str]:
    """Find the first ``array_keys`` member that appears as a JSON
    array opener in the peek window — return the corresponding ``ijson``
    prefix (``"<key>.item"``) so the streaming items() call yields the
    array elements directly.
    """
    for key in array_keys:
        pattern = re.compile(
            rb'"' + re.escape(key.encode()) + rb'"\s*:\s*\[',
            re.DOTALL,
        )
        if pattern.search(head):
            return f"{key}.item"
    return None
