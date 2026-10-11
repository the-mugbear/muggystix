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

**A document that ends early** (owner decision 2026-10-11).  A JSON document
— an array, or an object holding the array — whose file stops before the
document is closed raises ``TruncatedJSONFile``, at any size, and says how
many complete records precede the cut.  It is told apart from invalid content
by WHERE the reader fails, not by the library's wording: yajl raises
``IncompleteJSONError`` for every error, and "premature EOF" only for some
cuts (a cut inside ``tru``, an escape or a multi-byte character reads as a
"lexical error").  A failure reported only after the file has been read to
its end means every byte was acceptable and more were expected: the file is
a prefix of a document.  A failure before that is invalid content.

When the import was asked to keep what a cut-short file holds
(``begin_read(keep_truncated=True)``), the reader stops at the cut instead of
raising: the caller receives the complete records and nothing of the record
the cut fell in (the library only ever yields a closed record).  Invalid
content always raises.  A JSON-lines file is not a document: an undecodable
line is skipped and counted, as before.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar, Token
from typing import Iterable, Iterator, List, NamedTuple, Optional, Sequence

import ijson

logger = logging.getLogger(__name__)


_DEFAULT_STREAM_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
_PEEK_BYTES = 64 * 1024


class TruncatedJSONFile(ValueError):
    """The file ends before its JSON document is closed.

    ``records_read`` is how many complete records precede the cut; the record
    the cut fell in is not among them.  A ``ValueError`` so a caller that
    treats every reader failure alike still does."""

    def __init__(self, tool_label: str, records_read: int):
        self.tool_label = tool_label
        self.records_read = records_read
        if records_read:
            text = (
                f"This {tool_label} file was cut short: it ends after {records_read:,} complete "
                f"record{'s' if records_read != 1 else ''}, before the end of the document."
            )
        else:
            text = (
                f"This {tool_label} file was cut short before its first record was complete: "
                "there is nothing in it to import."
            )
        super().__init__(text)


class ReadReport(NamedTuple):
    """What the shared reader met during one parse."""
    # v2.419.0 (review 2026-09-25 H4) — JSON LINES that did not decode.  They
    # are skipped here, below every parser, so no import could count them: a
    # truncated JSONL file imported as complete.
    rejected_lines: int
    # The document ended early (None = it did not).  Set whether the reader
    # raised it or, asked to keep, stopped there.
    cut_short: Optional[TruncatedJSONFile]


class _ParseRead:
    __slots__ = ("rejected_lines", "keep_truncated", "cut_short")

    def __init__(self, keep_truncated: bool):
        self.rejected_lines = 0
        self.keep_truncated = keep_truncated
        self.cut_short: Optional[TruncatedJSONFile] = None


# The ingestion service opens one around each parser run: the reader is
# below every parser, and eleven of them call it.
_parse_read: ContextVar[Optional[_ParseRead]] = ContextVar("json_parse_read", default=None)


def begin_read(*, keep_truncated: bool = False) -> Token:
    """Start the report for the parse about to run.  ``keep_truncated``: a
    document that ends early ends the records instead of raising."""
    return _parse_read.set(_ParseRead(bool(keep_truncated)))


def end_read(token: Token) -> ReadReport:
    """What the reader met since ``begin_read``."""
    state = _parse_read.get()
    _parse_read.reset(token)
    if state is None:
        return ReadReport(0, None)
    return ReadReport(state.rejected_lines, state.cut_short)


def _reject_line() -> None:
    state = _parse_read.get()
    if state is not None:
        state.rejected_lines += 1


def _settle_cut(cut: TruncatedJSONFile) -> None:
    """The document ended early: note it on the parse's report, then raise —
    unless this parse keeps what a cut-short file holds AND it holds
    something.  With nothing complete before the cut there is nothing to
    keep, whatever was asked."""
    state = _parse_read.get()
    if state is not None:
        state.cut_short = cut
    if cut.records_read == 0 or state is None or not state.keep_truncated:
        raise cut
    logger.warning("%s — keeping the records before the cut, as the import asked", cut)


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
        ``TruncatedJSONFile`` (a ValueError) when the document ends early —
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
    return list(_iter_jsonl_lines(raw.splitlines()))


def _records_before_the_cut(
    file_path: str, head: bytes, array_keys: Sequence[str], tool_label: str,
) -> Optional[List[dict]]:
    """For a small document ``json.loads`` refused: its complete records when
    the file ends early and the import keeps them; ``TruncatedJSONFile`` when
    it ends early and the import does not; ``None`` when the document is
    invalid for any other reason."""
    if head.lstrip().startswith(b"["):
        prefix = "item"
    else:
        # No known array key: the object itself is the one record, and a cut
        # object is no record.
        prefix = _detect_array_prefix(head, array_keys) or ""
    records: List[dict] = []
    try:
        for record in _document_records(file_path, prefix, tool_label):
            records.append(record)
    except TruncatedJSONFile as cut:
        _settle_cut(cut)
        return records
    except ValueError:
        return None
    return None  # the stream reader accepts what json.loads refused: leave it to the line reader


class _EndTracking:
    """A binary handle that remembers having been read to its end."""

    def __init__(self, handle):
        self._handle = handle
        self.ended = False

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        # ijson opens with ``read(0)`` to learn whether the handle yields
        # bytes: an empty answer to a request for nothing is not the end.
        if not data and size != 0:
            self.ended = True
        return data


def _document_records(file_path: str, prefix: str, tool_label: str) -> Iterator[dict]:
    """The dict records of one JSON document, streamed.  Raises
    ``TruncatedJSONFile`` when the file ends before the document does and a
    plain ``ValueError`` for invalid content (see the module docstring for
    how the two are told apart)."""
    read = 0
    with open(file_path, "rb") as raw:
        handle = _EndTracking(raw)
        try:
            for item in ijson.items(handle, prefix):
                if isinstance(item, dict):
                    read += 1
                    yield item
        except ijson.JSONError as exc:
            if handle.ended:
                raise TruncatedJSONFile(tool_label, read) from exc
            raise ValueError(f"Invalid {tool_label} during streaming: {exc}") from exc


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
            _reject_line()
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

    try:
        yield from _document_records(file_path, prefix, tool_label)
    except TruncatedJSONFile as cut:
        # Raises unless this import keeps what a cut-short file holds; then
        # the records simply end here.
        _settle_cut(cut)


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
                _reject_line()
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
