"""
Extended models for confidence tracking and conflict resolution.

These models extend the base v2 schema to track confidence scores
and historical conflicts for better visibility into data quality.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Float, JSON, func, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.session import Base


class HostConfidence(Base):
    """Track confidence metadata for host fields"""
    __tablename__ = "host_confidence"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String, nullable=False)  # hostname, os_name, state, etc.

    # Current winning value confidence
    confidence_score = Column(Integer, nullable=False)  # 0-100
    scan_type = Column(String, nullable=False)  # nmap, netexec, masscan, etc.
    data_source = Column(String, nullable=False)  # service_banner, os_fingerprint, etc.
    method = Column(String, nullable=False)  # nmap -sV, netexec smb, etc.
    scan_id = Column(Integer, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)

    # Timestamps
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Additional metadata
    additional_factors = Column(JSON)  # service_conf, os_accuracy, etc.

    # Relationships
    host = relationship("Host", foreign_keys=[host_id])
    scan = relationship("Scan", foreign_keys=[scan_id])

    __table_args__ = (
        # One winning-confidence row per (host, field). _track_field_confidence
        # check-then-updates on this exact key; the UNIQUE both enforces that
        # (no silent accretion across scans) and serves the (host_id, field_name)
        # lookup, so the old non-unique index is redundant and dropped.
        UniqueConstraint("host_id", "field_name", name="uq_host_confidence_host_field"),
        Index("idx_host_confidence_scan", "scan_id"),
    )


class PortConfidence(Base):
    """Track confidence metadata for port/service fields"""
    __tablename__ = "port_confidence"

    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String, nullable=False)  # service_name, service_version, state, etc.

    # Current winning value confidence
    confidence_score = Column(Integer, nullable=False)  # 0-100
    scan_type = Column(String, nullable=False)  # nmap, netexec, masscan, etc.
    data_source = Column(String, nullable=False)  # service_banner, version_probe, etc.
    method = Column(String, nullable=False)  # nmap -sV, netexec smb, etc.
    scan_id = Column(Integer, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)

    # Timestamps
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Additional metadata
    additional_factors = Column(JSON)  # service_conf, banner_length, etc.

    # Relationships
    port = relationship("Port", foreign_keys=[port_id])
    scan = relationship("Scan", foreign_keys=[scan_id])

    __table_args__ = (
        # One winning-confidence row per (port, field) — see HostConfidence.
        UniqueConstraint("port_id", "field_name", name="uq_port_confidence_port_field"),
        Index("idx_port_confidence_scan", "scan_id"),
    )


class ConflictHistory(Base):
    """Track historical conflicts and resolutions"""
    __tablename__ = "conflict_history"

    id = Column(Integer, primary_key=True, index=True)

    # What was in conflict — real FKs (de-polymorphized from the old
    # object_type/object_id pair, which had no referential integrity and left
    # orphan rows when a host/port was deleted).  Exactly ONE is set: a host
    # field conflict sets host_id (port_id null); a port field conflict sets
    # port_id (host_id null, the host derivable via the port).  ON DELETE
    # CASCADE means a deleted host/port takes its conflict history with it.
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id", ondelete="CASCADE"), nullable=True)
    field_name = Column(String, nullable=False)

    # Previous value that lost the conflict
    previous_value = Column(Text)
    previous_confidence = Column(Integer)
    previous_scan_id = Column(Integer, ForeignKey("scans.id", ondelete="SET NULL"))
    previous_method = Column(String)

    # New value that won the conflict
    new_value = Column(Text)
    new_confidence = Column(Integer)
    new_scan_id = Column(Integer, ForeignKey("scans.id", ondelete="SET NULL"))
    new_method = Column(String)

    # When the conflict was resolved
    resolved_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    previous_scan = relationship("Scan", foreign_keys=[previous_scan_id])
    new_scan = relationship("Scan", foreign_keys=[new_scan_id])

    __table_args__ = (
        Index("idx_conflict_history_host", "host_id"),
        Index("idx_conflict_history_port", "port_id"),
        Index("idx_conflict_history_prev_scan", "previous_scan_id"),
        Index("idx_conflict_history_new_scan", "new_scan_id"),
        Index("idx_conflict_history_resolved_at", "resolved_at"),
    )


class NetexecResult(Base):
    """Store netexec-specific enumeration results"""
    __tablename__ = "netexec_results"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False)

    # Protocol and service info
    protocol = Column(String, nullable=False)  # smb, ldap, winrm, rdp
    port = Column(Integer)

    # Authentication and access
    auth_success = Column(Boolean)
    username = Column(String)
    domain = Column(String)

    # Enumeration results
    shares = Column(JSON)  # SMB shares
    users = Column(JSON)   # Domain users
    groups = Column(JSON)  # Domain groups
    policies = Column(JSON) # Password policies, etc.

    # Host information gathered
    hostname = Column(String)
    domain_name = Column(String)
    os_version = Column(String)
    arch = Column(String)

    # Service banners and versions
    service_banner = Column(Text)
    service_version = Column(String)

    # Raw output for debugging
    raw_output = Column(Text)

    # Confidence indicators
    response_time_ms = Column(Float)
    connection_stable = Column(Boolean, default=True)
    multiple_confirmations = Column(Boolean, default=False)

    # Timestamps
    discovered_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan", foreign_keys=[scan_id])
    host = relationship("Host", foreign_keys=[host_id])

    __table_args__ = (
        Index("idx_netexec_results_scan", "scan_id"),
        Index("idx_netexec_results_host", "host_id"),
    )