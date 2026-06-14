"""Pydantic schemas for the Finding spine (foundation phase 5).

In its own module rather than the already-large schemas.py.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FindingHostInfo(BaseModel):
    host_id: int
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    host_status: str
    model_config = ConfigDict(from_attributes=True)


class FindingResponse(BaseModel):
    id: int
    project_id: int
    title: str
    severity: str
    status: str
    source: str
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    evidence_annotation_id: Optional[int] = None
    vuln_id: Optional[int] = None
    exec_result_id: Optional[int] = None
    host_count: int = 0
    hosts: List[FindingHostInfo] = []
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class FindingListResponse(BaseModel):
    items: List[FindingResponse]
    total: int
    # Per-severity counts for the rollup header (respects all filters except
    # severity; independent of pagination). Keys: critical/high/medium/low/info.
    severity_counts: dict = {}


class FindingStatusHistoryEntry(BaseModel):
    id: int
    from_status: Optional[str] = None
    to_status: str
    changed_by_id: Optional[int] = None
    changed_by_name: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime


class PromoteAnnotationRequest(BaseModel):
    # Severity is required — promotion is the deliberate structuring step.
    severity: str
    title: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = None  # defaults to 'confirmed' server-side
    owner_id: Optional[int] = None
    # Additional hosts this single finding also affects (cross-host dedup).
    extra_host_ids: List[int] = []


class PromoteVulnerabilityRequest(BaseModel):
    vuln_id: int
    # Severity defaults to the vulnerability's own severity server-side.
    severity: Optional[str] = None
    # Defaults to 'confirmed'; pass a terminal status (false_positive /
    # accepted_risk) to dismiss the vuln as a finding instead.
    status: Optional[str] = None
    owner_id: Optional[int] = None
    # Optional triage rationale — recorded on the finding's disposition
    # history (esp. for a false-positive/accepted-risk dismissal).
    summary: Optional[str] = None


class PromoteVulnerabilityPreview(BaseModel):
    """Blast radius of a vuln promotion (read-only; §11)."""
    plugin_id: Optional[str] = None
    affected_host_count: int
    affected_host_sample: List[str] = []
    already_promoted: bool = False
    finding_id: Optional[int] = None


class FindingCreateRequest(BaseModel):
    title: str = Field(..., max_length=500)
    severity: str
    status: Optional[str] = None
    owner_id: Optional[int] = None
    host_ids: List[int] = []


class FindingUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    severity: Optional[str] = None
    owner_id: Optional[int] = None


class FindingStatusUpdateRequest(BaseModel):
    status: str
    summary: Optional[str] = None


class FindingHostsRequest(BaseModel):
    host_ids: List[int]
