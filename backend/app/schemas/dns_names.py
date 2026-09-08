"""Response / request shapes for the named-asset inventory (v2.322.0).

``GET /projects/{id}/names`` and friends.  A name row carries DERIVED
address state — "currently resolves to" is the latest A/AAAA observation
batch, "previously" is every other distinct address — computed per request,
never stored (see dns_name_service / endpoints/dns_names.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NameAddress(BaseModel):
    """One address a name has been observed at."""
    ip_address: str
    # The Host row for that address in this project, if one exists.  None is
    # normal for a name whose address was never scanned — nothing is invented.
    host_id: Optional[int] = None
    record_type: str            # 'A' / 'AAAA'
    first_observed: Optional[datetime] = None
    last_observed: Optional[datetime] = None
    observations: int = 0
    # Other names in the project observed resolving to the same address —
    # the load-balancer / vhost signal.  Excludes this name.
    shared_with: int = 0


class NameRow(BaseModel):
    id: int
    fqdn: str
    kind: str                   # 'fqdn' | 'wildcard'
    in_scope: bool
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    # Derived address state.
    current_addresses: List[NameAddress] = Field(default_factory=list)
    previous_address_count: int = 0
    # Observation counts by kind (A, AAAA, CNAME, PTR, IMPORT, HTTP, CERT, ...).
    evidence: Dict[str, int] = Field(default_factory=dict)
    # Coverage lens: imported / resolved / tested are distinct facts.
    imported: bool = False
    resolved: bool = False

    model_config = ConfigDict(from_attributes=True)


class NameObservation(BaseModel):
    id: int
    record_type: str
    value: str
    domain: str                 # raw string as the tool wrote it
    ttl: Optional[int] = None
    resolver_name: Optional[str] = None
    scan_id: Optional[int] = None
    scan_tool: Optional[str] = None
    scan_filename: Optional[str] = None
    observed_at: Optional[datetime] = None
    # For address-valued kinds: the Host row at that address, if any.
    host_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class NameDetail(NameRow):
    previous_addresses: List[NameAddress] = Field(default_factory=list)
    observations: List[NameObservation] = Field(default_factory=list)
    observations_total: int = 0
    # Names sharing any of this name's current addresses.
    sibling_names: List["SiblingName"] = Field(default_factory=list)


class SiblingName(BaseModel):
    id: int
    fqdn: str
    ip_address: str


class NameImportRequest(BaseModel):
    names: List[str] = Field(..., min_length=1, max_length=50_000,
                             description="One FQDN per entry; URLs and host:port are tolerated; '#' lines ignored.")
    # Import and scope declaration are separate decisions.  Default off.
    declare_scope: bool = False
    # When declaring scope, also cover every descendant of each concrete name.
    include_subdomains: bool = False


class NameImportResponse(BaseModel):
    names_created: int
    names_existing: int
    wildcards: int
    observations_recorded: int
    invalid_count: int
    invalid: List[str]
    scope_domains_added: int
    scope_domains_updated: int
    scope_invalid: List[str]


class NamesSummary(BaseModel):
    total: int
    unresolved: int
    resolved: int
    in_scope: int
    wildcards: int
    shared_addresses: int       # addresses with more than one name currently bound


class HostNamesResponse(BaseModel):
    host_id: int
    # Names with an A/AAAA observation whose value is this host's address.
    current: List["HostNameBinding"] = Field(default_factory=list)
    # Names observed at this address only by non-resolving kinds (HTTP, CERT,
    # SCANNER, PTR) — evidence the name is served here, not that it resolves here.
    other: List["HostNameBinding"] = Field(default_factory=list)
    in_scope_via_names: bool = False


class HostNameBinding(BaseModel):
    name_id: int
    fqdn: str
    kind: str
    in_scope: bool
    record_types: List[str]
    last_observed: Optional[datetime] = None


# --- scope domains ---------------------------------------------------------
class ScopeDomainCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=260)
    include_subdomains: bool = False
    description: Optional[str] = Field(None, max_length=1024)


class ScopeDomainBatchCreate(BaseModel):
    domains: List[ScopeDomainCreate] = Field(..., min_length=1, max_length=10_000)


class ScopeDomainRow(BaseModel):
    id: int
    scope_id: int
    domain: str
    include_subdomains: bool
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    # Names in the project this entry currently covers.
    name_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ScopeDomainBatchResponse(BaseModel):
    added: int
    updated: int
    invalid: List[str]
    domains: List[ScopeDomainRow]


NameDetail.model_rebuild()
HostNamesResponse.model_rebuild()
