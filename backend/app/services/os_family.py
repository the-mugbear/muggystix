"""The OS family an OS name belongs to, in nmap's vocabulary (v2.421.0).

Only nmap's ``<osclass osfamily=…>`` supplied ``Host.os_family``.  Nessus
(``operating-system``), NetExec's SMB banner and operator corrections give a
name only, so on a Nessus-heavy inventory most hosts had a name and no family
(production, 2026-09-26: 43,358 of 51,132) — blank in the inspector's OS line,
in search by family and in the exports' OS Family column.

A family derived here fills a blank; it never replaces one a scanner gave.
The names are nmap's own ``osfamily`` values, so derived and scanned families
group together.  An unrecognised name has no family rather than a guess.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

# First match wins: specific products before the generic words they contain
# ("Cisco NX-OS" before "IOS", "Citrix NetScaler" before "Linux").
_RULES: Sequence[Tuple[str, str]] = (
    (r"\bnx-?os\b", "NX-OS"),
    (r"\bios[ -]?xe\b|\bios[ -]?xr\b|\bcisco ios\b", "IOS"),
    (r"adaptive security appliance|\bcisco asa\b|\basa\s*\d", "ASA"),
    (r"netscaler|citrix adc", "NetScaler"),
    (r"\besxi?\b|vmware esx", "ESXi"),
    (r"\bjunos\b", "JUNOS"),
    (r"\bfortios\b|fortigate", "FortiOS"),
    (r"\bpan-?os\b|palo alto", "PAN-OS"),
    (r"\brouteros\b|mikrotik", "RouterOS"),
    (r"\bdata ontap\b|\bontap\b", "Data ONTAP"),
    (r"\bqts\b|qnap", "QTS"),
    (r"\bandroid\b", "Android"),
    (r"\bios\b.*\b(iphone|ipad)\b|\biphone os\b|\bipados\b", "iOS"),
    (r"\bmac ?os\b|\bos x\b|\bdarwin\b", "macOS"),
    (r"\bwindows\b|\bmicrosoft\b", "Windows"),
    # NetExec's SMB banner before v2.421.0 lost its leading "Windows":
    # "Server 2019 Standard 17763", "10 Build 19041 x64".
    (r"^server 20\d\d\b|^(?:7|8|8\.1|10|11|vista|xp)\b.*\bbuild \d{4,5}\b", "Windows"),
    (r"\bfreebsd\b|\bpfsense\b|\bopnsense\b|\btruenas\b", "FreeBSD"),
    (r"\bopenbsd\b", "OpenBSD"),
    (r"\bnetbsd\b", "NetBSD"),
    (r"\bsolaris\b|\bsunos\b", "Solaris"),
    (r"\baix\b", "AIX"),
    (r"\bhp-?ux\b", "HP-UX"),
    (r"\blinux\b|\bubuntu\b|\bdebian\b|\bcentos\b|\bred ?hat\b|\brhel\b|\bfedora\b|\bsuse\b"
     r"|\balmalinux\b|\brocky\b|\boracle linux\b|\bamazon linux\b|\bkali\b|\bgentoo\b"
     r"|\barch linux\b|\balpine\b|\braspbian\b", "Linux"),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), family) for pattern, family in _RULES)


def os_family_from_name(os_name: Optional[str]) -> Optional[str]:
    """The family ``os_name`` belongs to, or None when it is not recognised.

    Nessus often lists several candidates, one per line ("Microsoft Windows
    Server 2019\\nMicrosoft Windows 10"); they share a family when the scan was
    confident enough to name them, and the first line decides."""
    if not os_name:
        return None
    first = str(os_name).strip().splitlines()[0] if str(os_name).strip() else ""
    for pattern, family in _COMPILED:
        if pattern.search(first):
            return family
    return None
