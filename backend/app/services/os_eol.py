"""Catalog of end-of-life / unsupported operating systems.

The subnet-insights "hygiene" lens treats a host running an OS past its
vendor support date as a management signal — nobody is patching it because
the vendor stopped shipping patches.  We only have the OS as a free-text
string (``Host.os_name`` / ``os_family``, populated by nmap / netexec /
nessus), so detection is pattern-based against that string.

Design rules (mirrors ports_of_interest.py — a flat, reviewable catalog):
  * **Conservative.** Only flag an OS we can confidently call EOL from the
    string alone.  A false "EOL" badge erodes trust faster than a missed
    one, so when the version is ambiguous we do NOT flag.
  * **Current.** ``eol_date`` is the real vendor end-of-support date; it is
    informational (shown to the operator), not used for matching, so the
    catalog stays correct as the wall-clock advances without code changes.
  * **Ordered.** ``match_eol_os`` returns the FIRST pattern that matches, so
    list more-specific patterns before broader ones.

Patterns are matched case-insensitively as regular expressions against the
OS string.  Word boundaries guard against version-number bleed (``7`` must
not match ``7000``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class EolOs:
    pattern: re.Pattern
    label: str          # canonical product name for display
    eol_date: str       # ISO date vendor support ended (informational)
    note: str           # one-line "why this matters"


def _p(rx: str) -> re.Pattern:
    return re.compile(rx, re.IGNORECASE)


# Ordered most-specific → broadest.  Each entry's pattern is matched against
# the raw OS string; the first hit wins.
EOL_CATALOG: List[EolOs] = [
    # --- Windows client ----------------------------------------------------
    # Match only genuinely-old NT (3.x / 4.x) and the literal "2000".  The NT
    # version number is NOT the marketing year: modern Windows 10/11 report
    # as "Windows NT 10.0" (and 7/8.1 as NT 6.x) in SMB and HTTP Server
    # headers, so a bare ``nt`` match flagged current Windows as EOL-2010.
    EolOs(_p(r"windows\s*(2000|nt\s*[34]\b)"), "Windows 2000 / NT 3.x–4.x", "2010-07-13",
          "Windows 2000 / NT 4.x are long unsupported (modern 'NT 10.0' strings are not matched)."),
    EolOs(_p(r"windows\s*xp"), "Windows XP", "2014-04-08",
          "Unsupported since 2014; trivially exploitable, common ransomware target."),
    EolOs(_p(r"windows\s*vista"), "Windows Vista", "2017-04-11",
          "Unsupported since 2017."),
    EolOs(_p(r"windows\s*7\b"), "Windows 7", "2020-01-14",
          "Mainstream + extended support ended Jan 2020 (ESU also lapsed)."),
    EolOs(_p(r"windows\s*8(\.1)?\b"), "Windows 8 / 8.1", "2023-01-10",
          "Windows 8.1 extended support ended Jan 2023; 8.0 ended 2016."),
    # LTSC/LTSB editions have much longer lifecycles (e.g. Win10 LTSC 2021 →
    # 2027), so exempt them — the conservative contract is "don't flag unless
    # confident", and a generic Win10 string is, but an LTSC one is not.
    EolOs(_p(r"windows\s*10\b(?!.*lts[cb])"), "Windows 10", "2025-10-14",
          "End of support Oct 2025 — patches require paid ESU (LTSC/LTSB exempt)."),
    # --- Windows Server ----------------------------------------------------
    EolOs(_p(r"windows\s*server\s*2003"), "Windows Server 2003", "2015-07-14",
          "Unsupported since 2015."),
    EolOs(_p(r"windows\s*server\s*2008"), "Windows Server 2008 / R2", "2020-01-14",
          "Extended support ended Jan 2020 (ESU also lapsed)."),
    EolOs(_p(r"windows\s*server\s*2012"), "Windows Server 2012 / R2", "2023-10-10",
          "Extended support ended Oct 2023."),
    # --- Unix / Linux ------------------------------------------------------
    # nmap reports kernels like "Linux 2.6.32".  2.x and early 3.x are long
    # past upstream support; 4.x+ we leave alone (can't tell distro EOL from
    # the kernel string alone).
    EolOs(_p(r"linux\s*2\.\d"), "Linux kernel 2.x", "2016-10-01",
          "Linux 2.x kernels are long past upstream maintenance."),
    EolOs(_p(r"linux\s*3\.[0-9]\b"), "Linux kernel 3.0–3.9", "2017-09-01",
          "Early 3.x kernels are past upstream maintenance."),
    EolOs(_p(r"\bcentos\s*[5-7]\b"), "CentOS 5–7", "2024-06-30",
          "CentOS 5/6/7 are all past end-of-life (7 ended Jun 2024)."),
    EolOs(_p(r"\bsolaris\s*(8|9|10)\b"), "Solaris 8–10", "2021-01-31",
          "Legacy Solaris releases past extended support."),
    EolOs(_p(r"\b(esxi|vmware)\s*(5\.|6\.0|6\.5)"), "VMware ESXi ≤ 6.5", "2022-10-15",
          "ESXi 5.x/6.0/6.5 are past end of general support."),
]


def match_eol_os(os_name: Optional[str]) -> Optional[EolOs]:
    """Return the catalog entry for an EOL OS string, or None.

    Accepts the raw ``Host.os_name`` (or any OS-bearing string).  Returns
    None for null/empty input and for anything not confidently EOL.
    """
    if not os_name:
        return None
    text = os_name.strip()
    if not text:
        return None
    for entry in EOL_CATALOG:
        if entry.pattern.search(text):
            return entry
    return None
