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
loader (which asks ``iter_document`` here when its document does not load).

**A file that ends early** (owner decisions 2026-10-11; the rule and the
instruction that keeps such a file live in ``cut_short.py``).  What this
module knows is how to tell, for JSON:

* A JSON **document** — an array, or an object holding the array — whose
  file stops before the document is closed.  It is told apart from invalid
  content by WHERE the reader fails, not by the library's wording: yajl
  raises ``IncompleteJSONError`` for every error, and "premature EOF" only
  for some cuts (a cut inside ``tru``, an escape or a multi-byte character
  reads as a "lexical error").  A failure reported only after the input has
  been read to its end means every byte was acceptable and more were
  expected: the file is a prefix of a document.  A failure before that is
  invalid content.
* **JSON lines** whose LAST line is cut: it does not decode, the file does
  not end with a line ending after it, and the line is the beginning of a
  JSON value by the same test (``is_cut_last_line``).  A line that does not
  decode anywhere else — or a last line that is simply not JSON — is invalid
  content: skipped and counted, as it always was.

Either way the reader reports it through ``cut_short.settle_cut``, which
raises ``TruncatedFile`` or, when the import keeps what a cut-short file
holds, returns — and the records simply end: the caller received the
complete records and nothing of the one the cut fell in.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Iterable, Iterator, List, Optional, Sequence, Union

import ijson

from app.parsers.cut_short import EndTracking, reject_line, settle_cut

logger = logging.getLogger(__name__)


_DEFAULT_STREAM_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
_PEEK_BYTES = 64 * 1024


def iter_json_records(
    file_path: str,
    *,
    array_keys: Sequence[str] = (),
    tool_label: str = "JSON",
    threshold_bytes: Optional[int] = None,
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
            they're loaded eagerly.  None = the module's default, read at
            the call (no parser passes one, so a test can move it).

    Returns:
        For small files, a fully materialised ``list[dict]``.  For
        large files, a lazy iterator — but callers iterate either the
        same way (``for record in iter_json_records(...)``).

    Raises:
        ValueError on malformed JSON or on streaming a top-level
        object whose array key isn't found in the peek window;
        ``TruncatedFile`` (a ValueError) when the file ends early —
        at once for a small file, at the cut for a streamed one.
    """
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        file_size = 0

    if threshold_bytes is None:
        threshold_bytes = _DEFAULT_STREAM_THRESHOLD_BYTES
    if file_size >= threshold_bytes:
        return _stream_records(file_path, array_keys, tool_label)
    return _load_records_eager(file_path, array_keys, tool_label)


def _load_records_eager(
    file_path: str, array_keys: Sequence[str], tool_label: str,
) -> List[dict]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
    raw = text.strip()
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
        # Not one JSON value.  JSON lines are read line by line below, as
        # they always were.  Anything else is a document that did not parse:
        # if that is because the file ENDS EARLY, say so (below the
        # streaming threshold it used to fall to the line reader, which kept
        # whichever lines happened to decode, or failed with "no parseable
        # records").  Invalid for another reason: the line reader, as before.
        head = raw[:_PEEK_BYTES].encode("utf-8", errors="ignore")
        if not _looks_like_jsonl(head):
            kept = _records_before_the_cut(file_path, head, array_keys, tool_label)
            if kept is not None:
                return kept
    return list(_iter_jsonl_lines(json_lines(text), tool_label))


def _records_before_the_cut(
    file_path: str, head: bytes, array_keys: Sequence[str], tool_label: str,
) -> Optional[List[dict]]:
    """For a small document ``json.loads`` refused: its complete records when
    the file ends early and the import keeps them; ``TruncatedFile`` when it
    ends early and the import does not; ``None`` when the document is invalid
    for any other reason."""
    if head.lstrip().startswith(b"["):
        prefix = "item"
    else:
        # No known array key: the object itself is the one record, and a cut
        # object is no record.
        prefix = _detect_array_prefix(head, array_keys) or ""
    records: List[dict] = []
    try:
        # ``floats``: this file is below the streaming threshold, so a
        # complete one would have been read by ``json.loads`` — the records
        # before the cut carry the same number types.
        for record in _document_items(file_path, prefix, tool_label, floats=True):
            records.append(record)
    except _EndsEarly as cut:
        settle_cut(cut.read, label=tool_label, cause=cut.__cause__)
        return records
    except ValueError:
        return None
    return None  # the stream reader accepts what json.loads refused: leave it to the line reader


class _EndsEarly(Exception):
    """The input ended before its JSON document did, ``read`` items in."""

    def __init__(self, read: int):
        super().__init__(read)
        self.read = read


def _document_items(
    source: Union[str, bytes], prefix: str, tool_label: str,
    *, pairs: bool = False, dicts_only: bool = True, floats: bool = False,
) -> Iterator:
    """The items of one JSON document (a path, or its bytes), streamed: the
    values under ``prefix``, or with ``pairs`` the ``(key, value)`` members of
    the object there.  ``floats``: non-integer numbers as ``float``, as
    ``json.loads`` reads them (the streaming default is ``Decimal``).  Raises
    ``_EndsEarly`` when the input ends before the document does and a plain
    ``ValueError`` for invalid content (see the module docstring for how the
    two are told apart)."""
    read = 0
    raw = io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb")
    with raw:
        handle = EndTracking(raw)
        try:
            items = (
                _top_level_members(handle, floats) if pairs
                else ijson.items(handle, prefix, use_float=floats)
            )
            for item in items:
                if pairs or not dicts_only or isinstance(item, dict):
                    read += 1
                    yield item
        except ijson.JSONError as exc:
            if handle.ended:
                raise _EndsEarly(read) from exc
            raise ValueError(f"Invalid {tool_label} during streaming: {exc}") from exc


def _top_level_members(handle, floats: bool) -> Iterator:
    """``(key, value)`` for each member of the top-level object, handed over
    as soon as its value is closed.  (``ijson.kvitems`` holds a member back
    until the NEXT key arrives, so a file that stops right after a complete
    member would lose it.)"""
    key, builder, depth = None, None, 0
    for _prefix, event, value in ijson.parse(handle, use_float=floats):
        if depth == 1 and event == "map_key":
            key, builder = value, ijson.ObjectBuilder()
            continue
        if event in ("start_map", "start_array"):
            depth += 1
        elif event in ("end_map", "end_array"):
            depth -= 1
        if builder is not None and depth >= 1:
            builder.event(event, value)
            if depth == 1:
                yield key, builder.value
                builder = None


def iter_document(
    source: Union[str, bytes], prefix: str, tool_label: str,
    *, pairs: bool = False, dicts_only: bool = True, floats: bool = False, unit: str = "record",
) -> Iterator:
    """For a parser with its own loader: the complete items of a JSON
    document, by the shared rule.  A document that ends early is settled in
    the one place (``TruncatedFile``, or the items end there when the import
    keeps them); invalid content is a ``ValueError``."""
    try:
        yield from _document_items(
            source, prefix, tool_label, pairs=pairs, dicts_only=dicts_only, floats=floats,
        )
    except _EndsEarly as cut:
        settle_cut(cut.read, label=tool_label, unit=unit, cause=cut.__cause__)


def ends_early(text: str) -> bool:
    """True when ``text`` is the beginning of a JSON value that stops before
    its end — the reader accepts every byte and then wants more."""
    if not text.strip():
        return False
    handle = EndTracking(io.BytesIO(text.encode("utf-8", errors="ignore")))
    try:
        for _event in ijson.basic_parse(handle):
            pass
    except ijson.JSONError:
        return handle.ended
    return False


def file_ends_early(file_path: str) -> bool:
    """True when the file is the beginning of ONE JSON document that stops
    before its end (the same test as ``ends_early``, streamed)."""
    with open(file_path, "rb") as raw:
        handle = EndTracking(raw)
        try:
            for _event in ijson.basic_parse(handle):
                pass
        except ijson.JSONError:
            return handle.ended
    return False


def json_lines(text: str) -> List[str]:
    """``text``'s lines WITH their endings, split where a JSON-lines writer
    ends a line — ``\\n`` (``\\r\\n`` and ``\\r`` read as it, as a file opened
    in text mode reads them, so the streamed and the in-memory reader agree).
    Never ``str.splitlines``: it also splits at U+0085, U+2028, U+2029 and
    other characters that are legal RAW inside a JSON string, so a page
    title holding one made a valid line two broken ones — the first read as
    the file's cut last line (review 2026-10-14, H6)."""
    return io.StringIO(text, newline=None).readlines()


def is_cut_last_line(line: str) -> bool:
    """For a JSON-lines reader, given a line that did not decode WITH its
    line ending as read: is it the file's last line, cut short?  Only a
    file's last line can lack a line ending."""
    return not line.endswith(("\n", "\r")) and ends_early(line)


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


def _iter_jsonl_lines(lines: Iterable[str], tool_label: str) -> Iterator[dict]:
    """The dict records of JSON lines (each line WITH its ending).  A line
    that does not decode is skipped and counted — except the file's last
    line when it was cut short, which ends the records the settled way."""
    read = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if is_cut_last_line(line):
                settle_cut(read, label=tool_label, in_lines=True, cause=exc)
                return
            reject_line()
            continue
        if isinstance(obj, dict):
            read += 1
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
                yield from _stream_jsonl_file(file_path, tool_label)
                return
            raise ValueError(
                f"{tool_label} exceeds streaming threshold but no recognised "
                f"top-level array key was found in the first {_PEEK_BYTES} bytes "
                f"(expected one of: {', '.join(array_keys) or '[no keys configured]'})."
            )
    else:
        # No leading `[` or `{` — assume JSONL and stream by line.
        yield from _stream_jsonl_file(file_path, tool_label)
        return

    # Raises at a cut unless this import keeps what a cut-short file holds;
    # then the records simply end there.
    yield from iter_document(file_path, prefix, tool_label)


def _looks_like_jsonl(head: bytes) -> bool:
    """RV-7 — True when the first non-empty line of the peek window is a
    complete JSON object, i.e. the file is JSONL (object-per-line) rather
    than one pretty-printed top-level object (whose first line is just
    ``{``).  Used to disambiguate a leading ``{`` that carries no known
    top-level array key."""
    text = head.decode("utf-8", errors="ignore")
    for line in json_lines(text):
        s = line.strip()
        if not s:
            continue
        try:
            return isinstance(json.loads(s), dict)
        except json.JSONDecodeError:
            return False
    return False


def _stream_jsonl_file(file_path: str, tool_label: str) -> Iterator[dict]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        yield from _iter_jsonl_lines(handle, tool_label)


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
