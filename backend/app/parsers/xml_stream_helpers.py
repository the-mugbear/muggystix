"""Shared XML iterparse helpers with XXE/billion-laughs hardening
pre-applied.

Before this module existed, ``nmap_parser`` and ``masscan_parser``
each carried their own copy of the same iterparse setup:

    etree.iterparse(
        file_path,
        events=("start", "end"),
        resolve_entities=False,   # defeats billion-laughs + SYSTEM file disclosure
        no_network=True,          # blocks external DTD / entity fetches
        huge_tree=False,          # caps memory
    )

plus their own near-identical ``_strip_namespace`` / ``_clear_element``
helpers.  The problem with duplication is exactly that — if a future
edit drops one of the hardening flags from one copy (say a developer
unsure about ``huge_tree`` switches it to True for a "large file"
fix), the same flag remains true in the other parser and the
weakening goes unnoticed until exploitation.  Having one source of
truth makes the hardening tamper-evident in code review.

``nessus_parser`` historically used ``defusedxml.ElementTree.iterparse``,
which is also safe but doesn't share the lxml-specific element-clear
pattern (defusedxml wraps stdlib ElementTree).  This module's
``strip_namespace`` is dialect-agnostic and works for both.
"""
from __future__ import annotations

from typing import Iterable, Tuple

from lxml import etree


def iterparse_safe(
    source,
    events: Tuple[str, ...] = ("start", "end"),
):
    """Return an lxml iterparse context with hardening flags applied.

    ``source`` may be a path or a file-like object — same surface as
    ``lxml.etree.iterparse``.  Caller is responsible for iterating
    the result and calling :func:`clear_element` on each fully-
    consumed element.

    The hardening:

    * ``resolve_entities=False`` — neutralises billion-laughs (entity
      expansion bombs) and SYSTEM-entity file disclosure (where a
      malicious DTD declares ``<!ENTITY x SYSTEM "file:///etc/passwd">``
      then refers to ``&x;`` in the body).
    * ``no_network=True`` — prevents external DTD / entity fetches
      that would let a malicious upload trigger outbound HTTP from
      the worker.
    * ``huge_tree=False`` — caps internal allocations so a deeply
      nested or attribute-heavy document can't be used to OOM the
      worker.

    These three together are the canonical "parse untrusted XML
    safely with lxml" baseline.  Don't change them in callers; if a
    case ever needs different flags, add a clearly-named alternate
    entry point here so the divergence is visible in one place.
    """
    return etree.iterparse(
        source,
        events=events,
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
    )


def strip_namespace(tag: str) -> str:
    """Drop the ``{namespace-uri}`` prefix from an XML tag name.

    Works whether the caller uses lxml or stdlib ElementTree — both
    serialise namespaced tags the same way (``"{uri}localname"``).
    """
    if not tag:
        return tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def clear_element(elem) -> None:
    """Release memory held by a fully-consumed iterparse element.

    Calls ``elem.clear()`` then walks back from the element's parent
    deleting earlier siblings — necessary because lxml's iterparse
    doesn't automatically prune predecessors.  Without this, parsing
    a 500 MB nmap XML file holds the whole document in memory by the
    time the root closes.

    Safe to call on the root element too (parent is None → only the
    clear() runs).
    """
    parent = elem.getparent() if hasattr(elem, "getparent") else None
    elem.clear()
    if parent is not None:
        while elem.getprevious() is not None:
            del parent[0]
