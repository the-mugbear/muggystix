"""The client report's scope: printed in the report, or delivered beside it (v2.441.0).

A project with thousands of scoped networks printed them all as one table that
ran for pages in the middle of the report.  Each template now has a cutoff
(``template.json``: ``scope_inline_max`` networks, ``scope_domains_inline_max``
domains, default 25).  At or under it the report lists the scope as before.
Over it, the report gives the totals and a per-site summary and names a file
that carries the complete list, with its SHA-256, so the client can confirm
that the list they received is the one the report refers to.

The file is built from the report's own dataset: a draft's live scope, an
issued report's frozen snapshot.  Its bytes are deterministic, so an issued
report's fingerprint and file never change afterwards.
"""
from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import re
from collections import defaultdict
from typing import Dict, List, Optional

#: The cutoff when a template does not set one: about one page of the table.
DEFAULT_INLINE_MAX = 25
#: Sites shown in the report's summary table; the rest are one "Other sites" row.
SITE_ROWS = 15
OTHER_SITES = "Other sites"
NO_SITE = "No site"

#: A spreadsheet runs a cell that starts with one of these as a formula (CSV
#: injection).  Scope text is typed by people, so such a cell gets a leading
#: apostrophe, which spreadsheets show as text and do not evaluate.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_SLUG = re.compile(r"[^a-z0-9]+")


def _addresses(cidr: str) -> Optional[ipaddress._BaseNetwork]:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None


def summarise(
    subnets: List[dict], domains: List[dict], *,
    inline_max: Optional[int] = DEFAULT_INLINE_MAX, domains_inline_max: Optional[int] = DEFAULT_INLINE_MAX,
) -> dict:
    """The scope block's counts and the inline/external decision.  IPv4
    addresses are summed; IPv6 networks are counted, never their addresses
    (a single /64 is 18 quintillion).  A cutoff of None is a template that
    does not print that list at all: it is never "external", so no file is
    named and nothing asks for one."""
    ipv4_addresses = 0
    ipv6_networks = 0
    per_site: Dict[str, Dict[str, int]] = defaultdict(lambda: {"networks": 0, "addresses": 0})
    for row in subnets:
        net = _addresses(row["cidr"])
        site = row.get("site") or NO_SITE
        per_site[site]["networks"] += 1
        if net is None:
            continue
        if net.version == 4:
            ipv4_addresses += net.num_addresses
            per_site[site]["addresses"] += net.num_addresses
        else:
            ipv6_networks += 1
    sites = sorted(per_site.items(), key=lambda kv: (-kv[1]["networks"], kv[0]))
    by_site = [{"site": s, **counts} for s, counts in sites[:SITE_ROWS]]
    rest = sites[SITE_ROWS:]
    if rest:
        by_site.append({
            "site": f"{OTHER_SITES} ({len(rest)})",
            "networks": sum(c["networks"] for _, c in rest),
            "addresses": sum(c["addresses"] for _, c in rest),
        })
    subnets_inline = inline_max is None or len(subnets) <= inline_max
    domains_inline = domains_inline_max is None or len(domains) <= domains_inline_max
    return {
        "totals": {
            "networks": len(subnets), "ipv4_addresses": ipv4_addresses,
            "ipv6_networks": ipv6_networks, "domains": len(domains),
            "sites": len([s for s in per_site if s != NO_SITE]),
        },
        "by_site": by_site,
        "inline_max": inline_max,
        "domains_inline_max": domains_inline_max,
        "subnets_inline": subnets_inline,
        "domains_inline": domains_inline,
        "external": not (subnets_inline and domains_inline),
    }


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def scope_csv(scope: dict) -> bytes:
    """The complete scope as CSV — every declared network, then every domain,
    in the dataset's order.  Deterministic: the same scope always gives the
    same bytes (so the same SHA-256).  UTF-8 with a byte-order mark, which is
    what makes Excel read non-ASCII site names correctly."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["kind", "value", "site", "description", "subdomains"])
    for row in scope.get("subnets") or []:
        writer.writerow(["network", _cell(row.get("cidr")), _cell(row.get("site")),
                         _cell(row.get("description")), ""])
    for row in scope.get("domains") or []:
        writer.writerow(["domain", _cell(row.get("domain")), "", "",
                         "included" if row.get("include_subdomains") else "not included"])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def file_name(project_slug: Optional[str], number: Optional[int], report_id: Optional[int]) -> str:
    """``scope-<project>-report-<n>.csv`` for an issued report,
    ``scope-<project>-draft-<id>.csv`` before issue."""
    slug = _SLUG.sub("-", (project_slug or "project").lower()).strip("-")[:60] or "project"
    which = f"report-{number}" if number is not None else f"draft-{report_id or 0}"
    return f"scope-{slug}-{which}.csv"


def attach_file(scope: dict, *, project_slug: Optional[str], number: Optional[int],
                report_id: Optional[int]) -> dict:
    """Add ``file`` {name, sha256, bytes} to an external scope block (None
    when the report lists the scope itself)."""
    if not scope.get("external"):
        return {**scope, "file": None}
    data = scope_csv(scope)
    return {**scope, "file": {
        "name": file_name(project_slug, number, report_id),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }}
