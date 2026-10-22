"""The page envelope — the ONE place its arithmetic is written.

``{items, total, limit, offset, returned, has_more}``: ``total`` is every row
the filters match (the caller's own count of the list it paged, never a
second count made here), ``returned`` the length of ``items``, ``has_more``
whether rows remain past this page.

Review 2026-10-10, R1f: ``has_more`` was written in three services (host
tests, the remediation list, a host's remediation timeline) and then written
again by ``agent_schemas.as_agent_page`` for the agents' door.  The services
now answer this envelope, the agents' helper (``agent_schemas.agent_page``)
is this function, and the router factories two doors share answer the
service's dict on both.  ``tests/test_paging_one_envelope.py`` refuses a
``has_more`` computed anywhere else under ``app/``.
"""
from __future__ import annotations

from typing import Any, Dict, List

PAGE_KEYS = ("items", "total", "limit", "offset", "returned", "has_more")


def rows_remain(offset: int, returned: int, total: int) -> bool:
    """Whether rows remain past a page that skipped ``offset`` rows and
    returned ``returned`` of ``total`` — every ``has_more`` under ``app/``."""
    return offset + returned < int(total or 0)


def page_envelope(items: List[Any], *, total: int, limit: int, offset: int) -> Dict[str, Any]:
    """The envelope of one page (keys in ``PAGE_KEYS`` order)."""
    total = int(total or 0)
    returned = len(items)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": returned,
        "has_more": rows_remain(offset, returned, total),
    }
