"""Presentation mode (2.530.0) — request bodies.  Answers are the service's
``state`` and ``slides_for`` dicts (``presentation_service``), described in
documentation/API_GUIDE.md."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class HostFilterIn(BaseModel):
    kind: Literal["segment", "site", "text"]
    value: str = Field(..., min_length=1, max_length=200)


class ClientIn(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=64)


class RenderedIn(BaseModel):
    revision: int = Field(..., ge=1)
    slide_key: str = Field(..., min_length=1, max_length=200)
    host_filter: Optional[HostFilterIn] = None
    fitted: bool = False


class HeartbeatIn(ClientIn):
    kind: Literal["display", "controller", "browse"]
    rendered: Optional[RenderedIn] = None


class AgendaPositionIn(BaseModel):
    """The agenda part of a position: the pinned version, following it or
    exploring the full report, and the ACTIVE item (0-based)."""
    agenda_id: int = Field(..., ge=1)
    version: int = Field(..., ge=1)
    mode: Literal["agenda", "explore"]
    item_index: int = Field(..., ge=0)


class MoveIn(ClientIn):
    revision: int = Field(..., ge=1, description="The revision this tab last accepted.")
    slide_key: str = Field(..., min_length=1, max_length=200)
    host_filter: Optional[HostFilterIn] = None
    agenda: Optional[AgendaPositionIn] = Field(
        None, description="The agenda state after the move; null: the full report, no agenda.")


class SpotlightIn(ClientIn):
    revision: int = Field(..., ge=1, description="The revision the point was clicked at (the current one).")
    # Stage coordinates; the service checks them against the stage's size.
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)


class AgendaIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    items: List[str] = Field(..., min_length=1, max_length=200,
                             description="Anchors in order: overview, summary, scope, finding:<ref>, "
                                         "section:<ref>:<section>.")


class AgendaSaveIn(AgendaIn):
    expected_version: int = Field(..., ge=1, description="The version the editor opened.")


class EndIn(BaseModel):
    client_id: Optional[str] = Field(None, max_length=64)


class LayoutReportIn(BaseModel):
    slide_key: str = Field(..., min_length=1, max_length=200)
    overflow_px: int = Field(..., ge=0, le=100_000)
    font: str = Field(..., min_length=1, max_length=100)
