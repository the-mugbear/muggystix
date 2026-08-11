"""Network attribution — who a netblock belongs to and where it's hosted.

Answers the scope question a pentest actually turns on: *is this host really
the client's?* Today ``out_of_scope_only`` resolves to "no HostSubnetMapping
row", i.e. it validates hosts against the CIDRs someone typed into the scope.
That is entirely self-referential — if a client hands over a range they don't
own, or a prefix is fat-fingered, nothing in the system can tell. Attribution
is the first signal here that checks a declared scope against the outside
world, which makes it an authorization control and not just metadata.

Keyed on the NETBLOCK, not the host: RDAP answers about a CIDR, and a /24 with
200 hosts is one registration, not 200. Hosts attach through
``HostNetworkAttribution``, mirroring how ``HostSubnetMapping`` attaches hosts
to scope subnets — same ``IPTrie`` correlation pass, same shape.

Columns, not a blob: per the column-vs-blob policy, ASN / org / cloud provider
are exactly the fields a filter, a DSL predicate and a report need to query
across hosts ("every host outside the client's ASN", "everything in AWS"). The
untouched provider response is kept in ``raw`` for reprocessing only.
"""
from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class AttributionSource:
    """Where an attribution row came from. Not an enum column — the set will
    grow (prefix lists, offline ASN tables) and the vocabulary should evolve
    without an ALTER TYPE, same reasoning as the Finding spine's status."""
    RDAP = "rdap"
    PREFIX_LIST = "prefix_list"
    MANUAL = "manual"


class NetworkAttribution(Base):
    __tablename__ = "network_attributions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The registered netblock this record describes, as CIDR text. Stored as
    # text rather than Postgres INET so the SQLite test path keeps working;
    # containment matching happens in the IPTrie correlation pass, not in SQL.
    cidr = Column(String(64), nullable=False, index=True)

    # --- Registry attribution (RDAP) ---
    asn = Column(Integer, nullable=True, index=True)
    as_name = Column(String(255), nullable=True)
    org_name = Column(String(255), nullable=True, index=True)
    country = Column(String(8), nullable=True)
    registry = Column(String(32), nullable=True)  # ARIN / RIPE / APNIC / …
    # Registration handle (e.g. NET-192-0-2-0-1) — the stable id an operator
    # can quote back to the registry as evidence.
    handle = Column(String(64), nullable=True)

    # --- Hosting attribution (cloud prefix lists) ---
    cloud_provider = Column(String(32), nullable=True, index=True)
    cloud_region = Column(String(64), nullable=True)

    source = Column(String(20), nullable=False, default=AttributionSource.RDAP)
    # Untouched provider response, for reprocessing when parsing improves.
    # Never queried by a column predicate — if a field here starts appearing
    # in a WHERE, promote it.
    raw = Column(JSON, nullable=True)
    # Registration data goes stale (ASNs are reassigned, orgs are acquired).
    # Without this an operator can hand a client evidence that was true six
    # months ago, which is worse than showing nothing.
    looked_up_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    hosts = relationship(
        "HostNetworkAttribution", back_populates="attribution",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # One row per (project, block, source): RDAP and a cloud prefix list
        # legitimately describe the same block from different angles, and both
        # are worth keeping — agreement between independent signals is the
        # point (see the confidence service's treatment of conflicting scans).
        UniqueConstraint("project_id", "cidr", "source", name="uq_attribution_block"),
        Index("idx_attribution_project_asn", "project_id", "asn"),
    )


class HostNetworkAttribution(Base):
    """Host ↔ attribution, populated by the IPTrie correlation pass.

    Mirrors ``HostSubnetMapping``. A host can carry several: its registry
    attribution and its cloud attribution are different facts about the same
    address, and disagreement between them ("cert says Acme, ASN says a
    reseller") is itself the interesting finding.
    """
    __tablename__ = "host_network_attributions"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(
        Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    attribution_id = Column(
        Integer, ForeignKey("network_attributions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    attribution = relationship("NetworkAttribution", back_populates="hosts")
    host = relationship("Host")

    __table_args__ = (
        UniqueConstraint("host_id", "attribution_id", name="uq_host_attribution"),
    )
