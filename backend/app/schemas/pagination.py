"""Generic pagination envelope (v2.86.13).

Standardizes the response shape for paginated list endpoints on::

    {
      "items": [...],
      "total": <int>,
      "skip": <int>,
      "limit": <int>,
      "has_more": <bool>
    }

Pre-fix, list endpoints returned either a bare ``List[T]`` or a
hand-rolled envelope, and several recently-paginated endpoints
(``/recon-sessions/``, ``/execution-sessions/``, etc.) surfaced the
total via an ``X-Total-Count`` response header.  That worked but
left the client guessing whether each endpoint used the header or a
body field, and made the "Showing N of T" affordance bespoke per
page.

Adoption is gradual — see the v2.86.13 CHANGELOG entry for the
endpoints migrated in this commit.  Older endpoints continue to
return ``List[T]`` until a feature touches them; mixing shapes is
fine because the envelope and the bare list are distinct response
models.

Pydantic v2 ``Generic[T]`` requires no special config — the type
variable is resolved at endpoint declaration time when the caller
writes ``response_model=Paginated[ReconSessionRow]``.
"""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    """Standardised pagination envelope.

    The ``has_more`` field is derived server-side from
    ``skip + len(items) < total`` so the client doesn't have to
    repeat that math (and can't get it wrong on the boundary case
    where the slice fits exactly).
    """

    items: List[T] = Field(default_factory=list)
    # Total rows matching the active filter set, regardless of the
    # slice the caller got back.  Drives "Showing N of T" affordances.
    total: int = 0
    skip: int = 0
    limit: int = 100
    has_more: bool = False

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def build(cls, items: List[T], total: int, skip: int, limit: int) -> "Paginated[T]":
        """Convenience constructor that fills in ``has_more`` from the
        offset + slice length so endpoint handlers don't have to."""
        return cls(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
            has_more=skip + len(items) < total,
        )
