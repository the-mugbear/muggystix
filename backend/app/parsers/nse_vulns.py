"""NSE vulnerability results in nmap XML (v2.413.0).

Two structured shapes, taken from nmap's own source (master, 2026-09-25):

* **The vulns library** (``nselib/vulns.lua``), used by the ``vuln`` category
  (smb-vuln-ms17-010, ssl-heartbleed, http-vuln-*…).  ``Report:make_output``
  stores each vulnerability under its CVE id (else its first id) as a table
  with ``title``, ``state``, ``ids`` ("CVE:CVE-…" strings), ``scores``
  (type → value), ``description``, ``disclosure``, ``refs``…  NOT VULNERABLE
  entries are left out unless ``vulns.showall`` is set.  The risk factor is
  only in the text report ("Risk factor: High").
* **vulners** (``scripts/vulners.nse``): one table per CPE, each entry
  ``{id, cvss, type, is_exploit}`` — a version match, not a test.

Pure functions over the ``<script>`` element, so they are tested on nmap's
shapes without a database.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from lxml import etree

# vulns.lua STATE_MSG, except NOT VULNERABLE and UNKNOWN (unable to test).
VULNERABLE_STATES = ("VULNERABLE", "LIKELY VULNERABLE", "VULNERABLE (DoS)", "VULNERABLE (Exploitable)")
_CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
_RISK = re.compile(r"Risk factor:\s*(\w+)", re.IGNORECASE)


def _elem(table: etree._Element, key: str) -> Optional[str]:
    found = table.find(f"elem[@key='{key}']")
    return found.text.strip() if found is not None and found.text else None


def _list(table: etree._Element, key: str) -> List[str]:
    sub = table.find(f"table[@key='{key}']")
    if sub is None:
        return []
    return [e.text.strip() for e in sub.iter("elem") if e.text and e.text.strip()]


def _risk_after(text: str, title: str) -> Optional[str]:
    """The text report's "Risk factor:" for this vulnerability — the first
    one after its title (a script may report several)."""
    at = text.find(title) if title else -1
    match = _RISK.search(text, at if at >= 0 else 0)
    return match.group(1).lower() if match else None


def vulns_lib_results(script: etree._Element) -> List[Dict]:
    """Vulnerabilities a vulns-library script reports as (likely) vulnerable."""
    out = []
    text = script.get("output") or ""
    for table in script.findall("table"):
        state = _elem(table, "state")
        title = _elem(table, "title")
        if not state or not title or state.upper() not in VULNERABLE_STATES:
            continue
        ids = _list(table, "ids")
        cves = [m.group(0).upper() for i in ids for m in [_CVE.search(i)] if m]
        scores = {}
        sub = table.find("table[@key='scores']")
        if sub is not None:
            for e in sub.findall("elem"):
                try:
                    scores[e.get("key") or ""] = float(e.text)
                except (TypeError, ValueError):
                    pass
        out.append({
            "key": table.get("key") or title,
            "title": title,
            "state": state,
            "ids": ids,
            "cves": cves,
            "cvss": max(scores.values()) if scores else None,
            "risk": _risk_after(text, title),
            "description": "\n".join(_list(table, "description")) or None,
            "disclosure": _elem(table, "disclosure"),
            "refs": _list(table, "refs"),
        })
    return out


def vulners_results(script: etree._Element) -> List[Dict]:
    """CVE entries vulners matched to the service's CPE."""
    out = []
    for cpe_table in script.findall("table"):
        cpe = cpe_table.get("key") or ""
        for entry in cpe_table.findall("table"):
            vid = _elem(entry, "id")
            if not vid or (_elem(entry, "type") or "").lower() != "cve":
                continue
            try:
                cvss = float(_elem(entry, "cvss") or "")
            except ValueError:
                cvss = None
            out.append({
                "cpe": cpe,
                "id": vid.upper(),
                "cvss": cvss,
                "is_exploit": (_elem(entry, "is_exploit") or "").lower() == "true",
            })
    return out


_VNC_NO_AUTH = re.compile(r'security types:.*?\bnone\b', re.IGNORECASE | re.DOTALL)


def script_text_check(script_id: str, output: str) -> Optional[str]:
    """The catalog check a script's TEXT output reports (v2.412.0; shared
    with the backfill, which has only the stored text since v2.414.0)."""
    text = output or ''
    if script_id == 'vnc-info' and _VNC_NO_AUTH.search(text):
        return 'vnc_no_auth'
    if script_id == 'ftp-anon' and 'anonymous ftp login allowed' in text.lower():
        return 'ftp_anonymous'
    if script_id == 'smb-protocols' and ('NT LM 0.12' in text or 'SMBv1' in text):
        return 'smbv1_enabled'
    return None


def cpe_product(cpe: str) -> str:
    """"cpe:/a:openbsd:openssh:7.4" → "openssh 7.4"."""
    parts = cpe.split(":")
    return " ".join(p for p in parts[3:5] if p) or cpe
