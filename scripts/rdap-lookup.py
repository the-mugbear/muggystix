#!/usr/bin/env python3
"""Bulk RDAP lookup — produces a file BlueStick ingests.

Establishes hosting provenance for a set of addresses: who each netblock is
registered to, which ASN and organisation, which registry. Upload the output
like any scanner result and the RDAP parser turns it into per-host attribution.

**Why this runs here and not inside BlueStick.** The application's model is
that operators run tools and upload output; its only outbound HTTP is a narrow
SSRF-filtered allowlist. Keeping RDAP on this side means no new egress surface
in the backend, no registry rate-limiting inside a web request, and air-gapped
deployments keep working — run this wherever you have connectivity, carry the
file in. It also makes the lookup replayable: the artifact is retained like any
other scan.

Only stdlib is used, so it runs on a bare Python 3.9+ with no pip install.

Usage
-----
    # From a list of addresses (one per line; CIDRs are fine)
    ./scripts/rdap-lookup.py --input targets.txt --output rdap.ndjson

    # Straight from arguments
    ./scripts/rdap-lookup.py 203.0.113.10 198.51.100.0/24 -o rdap.ndjson

    # From a BlueStick host export: Reports -> Host Inventory (CSV).
    # The "IP Address" column is read on its own — a whole-row scan would
    # pull CIDRs out of the Subnet column and stray addresses out of service
    # banners, querying blocks nobody asked about.
    ./scripts/rdap-lookup.py --input hosts.csv --output rdap.ndjson

Then upload ``rdap.ndjson`` on the Scans page.

Notes
-----
* Queries are deduplicated **per netblock**, not per host: RDAP answers about a
  CIDR, so a /24 with 200 hosts costs one lookup. Once a block is known, any
  later address inside it is skipped.
* Private, loopback, link-local and reserved addresses are skipped — they have
  no public registration, and sending them to a registry leaks your internal
  addressing for no return.
* Registries rate-limit. The default pacing is deliberately unhurried; raise
  ``--delay`` if you see 429s rather than lowering it.
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

BOOTSTRAP_V4 = "https://data.iana.org/rdap/ipv4.json"
BOOTSTRAP_V6 = "https://data.iana.org/rdap/ipv6.json"
USER_AGENT = "BlueStick-rdap-lookup/1.0 (+network attribution)"
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")


def _get_json(url: str, timeout: float) -> Optional[dict]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rdap+json, application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # 404 is a normal answer: the address has no registration at this
        # registry. Anything else is worth surfacing.
        if exc.code != 404:
            print(f"  ! HTTP {exc.code} for {url}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"  ! {type(exc).__name__} for {url}: {exc}", file=sys.stderr)
        return None


class Bootstrap:
    """IANA bootstrap: which RDAP server is authoritative for a prefix."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self._v4: List[Tuple[ipaddress.IPv4Network, str]] = []
        self._v6: List[Tuple[ipaddress.IPv6Network, str]] = []

    def load(self) -> bool:
        ok = False
        for url, store in ((BOOTSTRAP_V4, self._v4), (BOOTSTRAP_V6, self._v6)):
            data = _get_json(url, self.timeout)
            if not data:
                continue
            for entry in data.get("services", []):
                if len(entry) < 2:
                    continue
                prefixes, servers = entry[0], entry[1]
                base = next((s for s in servers if s.startswith("https://")), None)
                if not base:
                    continue
                for prefix in prefixes:
                    try:
                        store.append((ipaddress.ip_network(prefix, strict=False), base))
                    except ValueError:
                        continue
            ok = True
        # Most specific first, so a delegated sub-block wins over its parent.
        self._v4.sort(key=lambda t: t[0].prefixlen, reverse=True)
        self._v6.sort(key=lambda t: t[0].prefixlen, reverse=True)
        return ok

    def server_for(self, addr) -> Optional[str]:
        table = self._v4 if addr.version == 4 else self._v6
        for net, base in table:
            if addr in net:
                return base.rstrip("/")
        return None


def _iter_targets(values: Iterable[str]) -> Iterable[str]:
    """Yield address-ish tokens from raw lines, a CSV, or argv.

    Tolerant on purpose: an operator should be able to point this at whatever
    they already have — a host export, a scope list, pasted output — without
    reformatting it first.
    """
    for raw in values:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for match in IP_RE.findall(line):
            yield match


# Header spellings that mean "the host address". Compared after normalising
# case, spaces and underscores, so BlueStick's own "IP Address" export header
# matches alongside the snake_case variants other tools emit.
_ADDR_COLUMNS = {"ipaddress", "ip", "address", "host", "hostip"}


def _normalise_header(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _read_input(path: str) -> List[str]:
    if path == "-":
        return list(_iter_targets(sys.stdin))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        head = fh.read(4096)
        fh.seek(0)
        # A CSV with a recognisable address column: read ONLY that column.
        # Scanning the whole row would pull CIDRs out of a Subnet column and
        # stray addresses out of free-text service banners, sending lookups
        # for blocks the operator never asked about.
        if "," in head:
            reader = csv.DictReader(fh)
            col = next(
                (c for c in (reader.fieldnames or [])
                 if c and _normalise_header(c) in _ADDR_COLUMNS),
                None,
            )
            if col:
                return list(_iter_targets(row.get(col, "") for row in reader))
            fh.seek(0)
        return list(_iter_targets(fh))


def _usable(addr) -> bool:
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bulk RDAP lookup producing NDJSON for BlueStick ingest.",
    )
    ap.add_argument("targets", nargs="*", help="IPs or CIDRs")
    ap.add_argument("-i", "--input", help="File of IPs/CIDRs, a CSV host export, or - for stdin")
    ap.add_argument("-o", "--output", default="rdap.ndjson", help="Output NDJSON (default: rdap.ndjson)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between queries (default: 1.0; registries rate-limit)")
    ap.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout (default: 15)")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N lookups (0 = no limit)")
    args = ap.parse_args()

    raw: List[str] = list(_iter_targets(args.targets))
    if args.input:
        raw += _read_input(args.input)
    if not raw:
        ap.error("no targets given (pass addresses, --input FILE, or --input -)")

    # Deduplicate to representative addresses, skipping anything unroutable.
    seen_addr = set()
    queue = []
    skipped_private = 0
    for token in raw:
        try:
            addr = (
                ipaddress.ip_network(token, strict=False).network_address
                if "/" in token else ipaddress.ip_address(token)
            )
        except ValueError:
            continue
        if not _usable(addr):
            skipped_private += 1
            continue
        if addr in seen_addr:
            continue
        seen_addr.add(addr)
        queue.append(addr)

    if not queue:
        print("Nothing to look up — all targets were private/reserved or unparseable.",
              file=sys.stderr)
        return 1

    print(f"Loading IANA bootstrap…", file=sys.stderr)
    bootstrap = Bootstrap(args.timeout)
    if not bootstrap.load():
        print("Could not load the IANA bootstrap registry — check connectivity.",
              file=sys.stderr)
        return 1

    print(f"{len(queue)} candidate address(es); {skipped_private} private/reserved skipped.",
          file=sys.stderr)

    covered: List[ipaddress._BaseNetwork] = []
    written = 0
    queried = 0

    with open(args.output, "w", encoding="utf-8") as out:
        for addr in queue:
            # One lookup per BLOCK: if a previous answer already covers this
            # address, the registration is the same and re-querying only burns
            # rate limit.
            if any(addr in net for net in covered):
                continue
            if args.limit and queried >= args.limit:
                print(f"Reached --limit {args.limit}; stopping.", file=sys.stderr)
                break

            server = bootstrap.server_for(addr)
            if not server:
                print(f"  ? no RDAP server for {addr}", file=sys.stderr)
                continue

            queried += 1
            body = _get_json(f"{server}/ip/{addr}", args.timeout)
            if body is None:
                time.sleep(args.delay)
                continue

            record = {
                "query": str(addr),
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "rdap": body,
            }
            out.write(json.dumps(record) + "\n")
            written += 1

            for net in _networks_from(body):
                covered.append(net)
            print(f"  + {addr} → {_summary(body)}", file=sys.stderr)
            time.sleep(args.delay)

    print(
        f"\nWrote {written} record(s) from {queried} lookup(s) to {args.output}.\n"
        f"Upload it on the Scans page to attribute your hosts.",
        file=sys.stderr,
    )
    return 0


def _networks_from(body: dict) -> List[ipaddress._BaseNetwork]:
    """The block(s) an RDAP answer covers, so later addresses inside them are
    not re-queried."""
    nets: List[ipaddress._BaseNetwork] = []
    for entry in body.get("cidr0_cidrs") or []:
        prefix = entry.get("v4prefix") or entry.get("v6prefix")
        length = entry.get("length")
        if prefix and length is not None:
            try:
                nets.append(ipaddress.ip_network(f"{prefix}/{length}", strict=False))
            except ValueError:
                pass
    if nets:
        return nets
    start, end = body.get("startAddress"), body.get("endAddress")
    if start and end:
        try:
            nets = list(ipaddress.summarize_address_range(
                ipaddress.ip_address(start), ipaddress.ip_address(end),
            ))
        except (ValueError, TypeError):
            pass
    return nets


def _summary(body: dict) -> str:
    name = body.get("name") or "?"
    country = body.get("country") or "?"
    handle = body.get("handle") or "?"
    return f"{handle} {name} ({country})"


if __name__ == "__main__":
    sys.exit(main())
