"""Presentation mode (2.530.0) — request bodies.  Answers are the service's
``state`` and ``slides_for`` dicts (``presentation_service``), described in
documentation/API_GUIDE.md."""
from typing import Literal, Optional

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


class MoveIn(ClientIn):
    revision: int = Field(..., ge=1, description="The revision this tab last accepted.")
    slide_key: str = Field(..., min_length=1, max_length=200)
    host_filter: Optional[HostFilterIn] = None


class EndIn(BaseModel):
    client_id: Optional[str] = Field(None, max_length=64)


class LayoutReportIn(BaseModel):
    slide_key: str = Field(..., min_length=1, max_length=200)
    overflow_px: int = Field(..., ge=0, le=100_000)
    font: str = Field(..., min_length=1, max_length=100)
