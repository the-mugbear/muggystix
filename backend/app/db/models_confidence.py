"""
Extended models for confidence tracking and conflict resolution.

These models extend the base v2 schema to track confidence scores
and historical conflicts for better visibility into data quality.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Float, JSON, func, Index
from sqlalchemy.orm import relationship
from app.db.session import Base


class HostConfidence(Base):
    """Track confidence metadata for host fields"""
    __tablename__ = "host_confidence"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    field_name = Column(String, nullable=False)  # hostname, os_name, state, etc.

    # Current winning value confidence
    confidence_score = Column(Integer, nullable=False)  # 0-100
    scan_type = Column(String, nullable=False)  # nmap, netexec, masscan, etc.
    data_source = Column(String, nullable=False)  # service_banner, os_fingerprint, etc.
    method = Column(String, nullable=False)  # nmap -sV, netexec smb, etc.
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)

    # Timestamps
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Additional metadata
    additional_factors = Column(JSON)  # service_conf, os_accuracy, etc.

    # Relationships
    host = relationship("Host", foreign_keys=[host_id])
    scan = relationship("Scan", foreign_keys=[scan_id])

    __table_args__ = (
        Index("idx_host_confidence_host_field", "host_id", "field_name"),
        Index("idx_host_confidence_scan", "scan_id"),
    )


class PortConfidence(Base):
    """Track confidence metadata for port/service fields"""
    __tablename__ = "port_confidence"

    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id"), nullable=False)
    field_name = Column(String, nullable=False)  # service_name, service_version, state, etc.

    # Current winning value confidence
    confidence_score = Column(Integer, nullable=False)  # 0-100
    scan_type = Column(String, nullable=False)  # nmap, netexec, masscan, etc.
    data_source = Column(String, nullable=False)  # service_banner, version_probe, etc.
    method = Column(String, nullable=False)  # nmap -sV, netexec smb, etc.
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)

    # Timestamps
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Additional metadata
    additional_factors = Column(JSON)  # service_conf, banner_length, etc.

    # Relationships
    port = relationship("Port", foreign_keys=[port_id])
    scan = relationship("Scan", foreign_keys=[scan_id])

    __table_args__ = (
        Index("idx_port_confidence_port_field", "port_id", "field_name"),
        Index("idx_port_confidence_scan", "scan_id"),
    )


class ConflictHistory(Base):
    """Track historical conflicts and resolutions"""
    __tablename__ = "conflict_history"

    id = Column(Integer, primary_key=True, index=True)

    # What was in conflict
    object_type = Column(String, nullable=False)  # host, port
    object_id = Column(Integer, nullable=False)  # host_id or port_id
    field_name = Column(String, nullable=False)

    # Previous value that lost the conflict
    previous_value = Column(Text)
    previous_confidence = Column(Integer)
    previous_scan_id = Column(Integer, ForeignKey("scans.id"))
    previous_method = Column(String)

    # New value that won the conflict
    new_value = Column(Text)
    new_confidence = Column(Integer)
    new_scan_id = Column(Integer, ForeignKey("scans.id"))
    new_method = Column(String)

    # When the conflict was resolved
    resolved_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    previous_scan = relationship("Scan", foreign_keys=[previous_scan_id])
    new_scan = relationship("Scan", foreign_keys=[new_scan_id])

    __table_args__ = (
        Index("idx_conflict_history_object", "object_type", "object_id"),
        Index("idx_conflict_history_prev_scan", "previous_scan_id"),
        Index("idx_conflict_history_new_scan", "new_scan_id"),
        Index("idx_conflict_history_resolved_at", "resolved_at"),
    )


class DataSourceMetadata(Base):
    """Track metadata about different data sources for analysis"""
    __tablename__ = "data_source_metadata"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)

    # Source identification
    scan_type = Column(String, nullable=False)  # nmap, netexec, masscan
    tool_version = Column(String)
    command_line = Column(Text)

    # Quality metrics
    total_hosts_scanned = Column(Integer)
    successful_responses = Column(Integer)
    failed_responses = Column(Integer)
    timeout_count = Column(Integer)

    # Timing information
    scan_duration_seconds = Column(Float)
    scan_started_at = Column(DateTime(timezone=True))
    scan_completed_at = Column(DateTime(timezone=True))

    # Source-specific metadata
    source_metadata = Column(JSON)  # Different for each scan type

    # Quality score for this data source
    overall_quality_score = Column(Float)  # 0.0-1.0

    # Relationships
    scan = relationship("Scan", foreign_keys=[scan_id])

    __table_args__ = (
        Index("idx_data_source_metadata_scan", "scan_id"),
    )


class NetexecResult(Base):
    """Store netexec-specific enumeration results"""
    __tablename__ = "netexec_results"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)

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