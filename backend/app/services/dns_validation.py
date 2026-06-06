"""
DNS server validation to prevent SSRF via user-supplied resolver addresses.
"""

import os
import ipaddress
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Allowlist of DNS server IPs/CIDRs. Set DNS_SERVER_ALLOWLIST env var as a
# comma-separated list (e.g. "8.8.8.8,1.1.1.1,192.168.1.53/32").
# If the env var is empty or unset, only public IPs are permitted.
_raw_allowlist = os.getenv("DNS_SERVER_ALLOWLIST", "").strip()
DNS_SERVER_ALLOWLIST: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
if _raw_allowlist:
    for entry in _raw_allowlist.split(","):
        entry = entry.strip()
        if entry:
            try:
                DNS_SERVER_ALLOWLIST.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning("Ignoring invalid DNS_SERVER_ALLOWLIST entry: %s", entry)


def validate_dns_server(value: str) -> str:
    """Validate a user-supplied DNS server address.

    Rules:
    - Must be a valid IPv4 or IPv6 address (not a hostname).
    - If DNS_SERVER_ALLOWLIST is configured, the address must fall within one
      of the listed networks.
    - If no allowlist is configured, private/reserved/loopback addresses are
      rejected to prevent internal network probing.

    Returns the validated IP string, or raises HTTPException 400.
    """
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="dns_server must be a valid IPv4 or IPv6 address",
        )

    # If an explicit allowlist is configured, enforce it strictly
    if DNS_SERVER_ALLOWLIST:
        if not any(addr in net for net in DNS_SERVER_ALLOWLIST):
            raise HTTPException(
                status_code=400,
                detail="dns_server address is not in the configured allowlist",
            )
        return str(addr)

    # No allowlist — reject private, loopback, link-local, and reserved ranges
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        raise HTTPException(
            status_code=400,
            detail="dns_server must be a public IP address, or configure DNS_SERVER_ALLOWLIST to permit internal resolvers",
        )

    return str(addr)
