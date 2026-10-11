"""A file that was cut short — the one place that says so, for every format.

Owner decision 2026-10-11: "if it's possible to see that the … format ended
abruptly let's ask the user if they wish to import the partial for all scans
where it's possible."  One rule for every import format in which a premature
end can be DETECTED:

* without the instruction the import FAILS as cut short (``TruncatedFile``;
  the dispatcher records ``parse_errors.error_type = truncated_file`` and the
  failed-import cleanup removes what the attempt wrote);
* with it (``job.options.keep_truncated``, one attempt's, set only by
  ``staged_import_service.queue_job``) the reader stops cleanly at the cut,
  the parser writes every COMPLETE record, and the import completes partial.

Every reader reports a cut the same way — ``settle_cut(records_read, …)`` —
and never decides anything itself: the call raises, or returns when this
import keeps what the file holds.  The instruction reaches the readers
through the one ContextVar here, which the dispatcher opens around each
parser run (``begin_read`` / ``end_read``); no parser takes it as an
argument.

**Cut short vs invalid** is decided by WHERE a reader fails, never by a
library's exception type, code or wording (yajl words some cuts as lexical
errors; libxml2 reports a cut with dozens of different codes): a failure
reported only after the file was read to its end means every byte was
acceptable and more were expected.  ``EndTracking`` is that observation for a
reader that pulls from a handle (ijson, expat).  libxml2's push parser defers
some checks to the end of input, so for XML the question is put to expat
(``xml_cut_short``), whose reader fails on an invalid byte when it is fed.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import NamedTuple, Optional
from xml.parsers import expat

logger = logging.getLogger(__name__)


class TruncatedFile(ValueError):
    """The file ends before it is complete.

    ``records_read`` is how many complete records precede the cut (``unit``
    names them: a record, a host); the record the cut fell in is not among
    them.  ``in_lines``: the file is one record per line and the last line is
    the cut one.  A ``ValueError`` so a caller that treats every reader
    failure alike still does."""

    def __init__(self, label: str, records_read: int, *, unit: str = "record", in_lines: bool = False):
        self.label = label
        self.records_read = records_read
        self.unit = unit
        self.in_lines = in_lines
        if records_read:
            where = "part-way through the line that follows" if in_lines else "before the end of the document"
            text = (
                f"This {label} file was cut short: it ends after {records_read:,} complete "
                f"{unit}{'s' if records_read != 1 else ''}, {where}."
            )
        else:
            text = (
                f"This {label} file was cut short before its first {unit} was complete: "
                "there is nothing in it to import."
            )
        super().__init__(text)

    @property
    def kept(self) -> str:
        """The sentence for an import that went on with what the file holds."""
        those = f"That {self.unit} was" if self.records_read == 1 else f"Those {self.unit}s were"
        return f"{self} {those} imported; anything the tool wrote after that point is not in the file."


class ReadReport(NamedTuple):
    """What the readers met during one parse."""
    # v2.419.0 (review 2026-09-25 H4) — JSON LINES that did not decode.  They
    # are skipped below every parser, so no import could count them.
    rejected_lines: int
    # The file ended early (None = it did not).  Set whether the reader
    # raised it or, asked to keep, stopped there.
    cut_short: Optional[TruncatedFile]


class _ParseRead:
    __slots__ = ("rejected_lines", "keep_truncated", "label", "cut_short")

    def __init__(self, keep_truncated: bool, label: Optional[str]):
        self.rejected_lines = 0
        self.keep_truncated = keep_truncated
        self.label = label
        self.cut_short: Optional[TruncatedFile] = None


# The ingestion service opens one around each parser run.
_parse_read: ContextVar[Optional[_ParseRead]] = ContextVar("parse_read", default=None)


def begin_read(*, keep_truncated: bool = False, label: Optional[str] = None) -> Token:
    """Start the report for the parse about to run.  ``keep_truncated``: a
    file that ends early ends the records instead of failing the import.
    ``label``: the format's name for the sentence (``format_registry``); a
    reader's own label is used when none is given."""
    return _parse_read.set(_ParseRead(bool(keep_truncated), label))


def end_read(token: Token) -> ReadReport:
    """What the readers met since ``begin_read``."""
    state = _parse_read.get()
    _parse_read.reset(token)
    if state is None:
        return ReadReport(0, None)
    return ReadReport(state.rejected_lines, state.cut_short)


def reject_line() -> None:
    """One JSON line that did not decode and was skipped."""
    state = _parse_read.get()
    if state is not None:
        state.rejected_lines += 1


def settle_cut(
    records_read: int,
    *,
    label: str,
    unit: str = "record",
    in_lines: bool = False,
    cause: Optional[BaseException] = None,
) -> TruncatedFile:
    """The file ended early after ``records_read`` complete records: note it
    on the parse's report, then RAISE — unless this parse keeps what a
    cut-short file holds AND it holds something, in which case the cut is
    returned and the reader simply stops.  With nothing complete before the
    cut there is nothing to keep, whatever was asked.

    ``label`` is the reader's own name for the format, used when the
    dispatcher gave none (a parser run outside a job)."""
    state = _parse_read.get()
    cut = TruncatedFile(
        (state.label if state is not None and state.label else label),
        records_read, unit=unit, in_lines=in_lines,
    )
    if state is not None:
        state.cut_short = cut
    if records_read == 0 or state is None or not state.keep_truncated:
        raise cut from cause
    logger.warning("%s — keeping what precedes the cut, as the import asked", cut)
    return cut


class EndTracking:
    """A binary handle that remembers having been read to its end."""

    def __init__(self, handle):
        self._handle = handle
        self.ended = False

    @classmethod
    def open(cls, file_path: str) -> "EndTracking":
        return cls(open(file_path, "rb"))

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        # ijson opens with ``read(0)`` to learn whether the handle yields
        # bytes: an empty answer to a request for nothing is not the end.
        if not data and size != 0:
            self.ended = True
        return data

    def close(self) -> None:
        self._handle.close()


class _NotAPlainDocument(Exception):
    """The document declares entities: not something to reason about here."""


def _refuse_entities(*_args) -> None:
    raise _NotAPlainDocument()


_XML_CHUNK = 1024 * 1024


def xml_cut_short(source: EndTracking, file_path: str) -> bool:
    """An XML reader failed on ``file_path``, which it was pulling through
    ``source``: did the file simply END (True), or is its content invalid?

    Cut short means both: the reader failed only after it had been handed the
    whole file, and every byte of the file is acceptable XML that then wants
    more.  The second half is asked of expat, fed the file again: it raises on
    an invalid byte at the feed that holds it and reports "no element found" /
    "unclosed token" / "partial character" only when told the input is over.
    (lxml alone cannot answer: libxml2 keeps text it has not finished with, so
    a bare ``&`` near the end of a file is reported at the end of input like
    a cut.)  A document that declares entities is answered False — never
    expanded here."""
    if not source.ended:
        return False
    parser = expat.ParserCreate()
    parser.EntityDeclHandler = _refuse_entities
    try:
        with open(file_path, "rb") as handle:
            while True:
                chunk = handle.read(_XML_CHUNK)
                if not chunk:
                    break
                parser.Parse(chunk, False)
    except (expat.ExpatError, _NotAPlainDocument):
        return False
    try:
        parser.Parse(b"", True)
    except expat.ExpatError:
        return True
    return False  # expat reads it whole: whatever stopped the reader, it was not the end
