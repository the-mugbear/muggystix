"""
Database models v2 - Optimized schema with host deduplication

Key changes:
- Hosts are unique by IP address (no scan_id foreign key)
- Added tracking tables for scan history and data provenance
- Added timestamps for first/last seen tracking
- Ports are unique per host by port_number + protocol
- Added conflict resolution fields for host metadata
"""

import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Index, func, JSON, BigInteger, Enum as SQLEnum
from sqlalchemy.orm import relationship, backref
from app.db.session import Base


class Host(Base):
    __tablename__ = "hosts_v2"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    ip_address = Column(String, nullable=False, index=True)
    hostname = Column(String)
    state = Column(String)
    state_reason = Column(String)
    os_name = Column(String)
    os_family = Column(String)
    os_generation = Column(String)
    os_type = Column(String)
    os_vendor = Column(String)
    os_accuracy = Column(Integer)

    # Audit fields
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_updated_scan_id = Column(Integer, ForeignKey("scans.id"))  # Track which scan last updated this host

    # Relationships.  Hot read paths (host list, serializers, reports) touch
    # ports + vulnerabilities + attributes on every host, so they default to
    # selectin to make N+1 the explicit choice (opt-out via noload()) instead
    # of the silent default.  Audit-trail relationships (scan_history,
    # host_scripts) stay lazy='select' — drill-down only, not worth the join.
    project = relationship("Project", foreign_keys=[project_id])
    ports = relationship("Port", back_populates="host", cascade="all, delete-orphan", lazy="selectin")
    host_scripts = relationship("HostScript", back_populates="host", cascade="all, delete-orphan")
    scan_history = relationship("HostScanHistory", back_populates="host", cascade="all, delete-orphan")
    last_updated_scan = relationship("Scan", foreign_keys=[last_updated_scan_id])

    # New vulnerability and attribute relationships
    vulnerabilities = relationship("Vulnerability", back_populates="host", cascade="all, delete-orphan", lazy="selectin")
    attributes = relationship("HostAttribute", back_populates="host", cascade="all, delete-orphan", lazy="selectin")
    follows = relationship("HostFollow", back_populates="host", cascade="all, delete-orphan")
    notes = relationship("HostNote", back_populates="host", cascade="all, delete-orphan", lazy="selectin")
    tag_assignments = relationship("HostTagAssignment", back_populates="host", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        Index('idx_host_ip_address', 'ip_address'),
        UniqueConstraint('project_id', 'ip_address', name='uq_project_ip'),
        # Hot path: /hosts list filtered by (project_id, state='up'). Single
        # project_id index forces a state filter in the table; composite lets
        # the planner satisfy both from the index.
        Index('idx_host_project_state', 'project_id', 'state'),
        # v2.85.0 — /staleness + dashboard "recent activity" tile filter
        # by project_id and order by last_seen.  Composite avoids a sort
        # step on every dashboard hit.
        Index('idx_host_project_last_seen', 'project_id', 'last_seen'),
    )


class Port(Base):
    __tablename__ = "ports_v2"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    port_number = Column(Integer, nullable=False, index=True)
    protocol = Column(String, nullable=False)
    state = Column(String)
    reason = Column(String)
    service_name = Column(String)
    service_product = Column(String)
    service_version = Column(String)
    service_extrainfo = Column(Text)
    service_method = Column(String)
    service_conf = Column(Integer)
    
    # Audit fields
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_updated_scan_id = Column(Integer, ForeignKey("scans.id"))
    is_active = Column(Boolean, default=True)  # Track if port is currently active
    
    # Relationships
    host = relationship("Host", back_populates="ports")
    scripts = relationship("Script", back_populates="port", cascade="all, delete-orphan")
    last_updated_scan = relationship("Scan", foreign_keys=[last_updated_scan_id])

    # New vulnerability and attribute relationships
    vulnerabilities = relationship("Vulnerability", back_populates="port", cascade="all, delete-orphan")
    attributes = relationship("PortAttribute", back_populates="port", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('host_id', 'port_number', 'protocol', name='uq_host_port_protocol'),
        Index('idx_port_number_protocol', 'port_number', 'protocol'),
        Index('idx_port_state', 'state'),
        # Hot path: "open ports for host X" — composite avoids the
        # extra in-table filter on state after the host_id lookup.
        Index('idx_port_host_state', 'host_id', 'state'),
    )


class Script(Base):
    __tablename__ = "scripts_v2"

    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id"), nullable=False)
    script_id = Column(String, nullable=False)
    output = Column(Text)
    
    # Audit fields
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    
    # Relationships
    port = relationship("Port", back_populates="scripts")
    scan = relationship("Scan")

    __table_args__ = (
        UniqueConstraint('port_id', 'script_id', name='uq_port_script'),
        Index('idx_script_id', 'script_id'),
    )


class HostScript(Base):
    __tablename__ = "host_scripts_v2"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    script_id = Column(String, nullable=False)
    output = Column(Text)
    
    # Audit fields
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()) 
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    
    # Relationships
    host = relationship("Host", back_populates="host_scripts")
    scan = relationship("Scan")

    __table_args__ = (
        UniqueConstraint('host_id', 'script_id', name='uq_host_script'),
        Index('idx_host_script_id', 'script_id'),
    )


class HostScanHistory(Base):
    """Track which scans have seen each host for audit purposes"""
    __tablename__ = "host_scan_history"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    discovered_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Host state at time of this scan
    state_at_scan = Column(String)
    hostname_at_scan = Column(String)
    os_info_updated = Column(Boolean, default=False)  # Whether this scan updated OS info
    
    # Relationships
    host = relationship("Host", back_populates="scan_history")
    scan = relationship("Scan")

    __table_args__ = (
        UniqueConstraint('host_id', 'scan_id', name='uq_host_scan'),
        Index('idx_host_scan_discovered', 'discovered_at'),
        # Hot path: "scan timeline" queries filter by scan_id and order by
        # discovered_at. Composite serves both without sorting.
        Index('idx_host_scan_history_scan_discovered', 'scan_id', 'discovered_at'),
    )


class PortScanHistory(Base):
    """Track port state changes over time"""
    __tablename__ = "port_scan_history"

    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id"), nullable=False)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    discovered_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Port state at time of this scan
    state_at_scan = Column(String)
    service_info = Column(Text)  # JSON of service details at this scan
    
    # Relationships
    port = relationship("Port")
    scan = relationship("Scan")

    __table_args__ = (
        UniqueConstraint('port_id', 'scan_id', name='uq_port_scan'),
        Index('idx_port_scan_discovered', 'discovered_at'),
        # uq_port_scan covers port_id (leading) but not scan_id.
        # Hot path: "what ports did this scan observe?".
        Index('idx_port_scan_history_scan', 'scan_id'),
    )


# Additional models needed for the application

class Scan(Base):
    __tablename__ = "scans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    filename = Column(String, nullable=False)
    scan_type = Column(String)
    tool_name = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    command_line = Column(Text)
    version = Column(String)
    xml_output_version = Column(String)
    uploaded_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    scan_info = relationship("ScanInfo", back_populates="scan", cascade="all, delete-orphan")
    # v2.12.0: EyewitnessResult → WebInterface (unified table for web-fingerprint
    # tools: httpx, eyewitness, nikto, etc.) linked per-host + per-port, not only
    # per-scan.  eyewitness_results table is dropped in init.py since no data
    # existed to migrate.
    web_interfaces = relationship("WebInterface", back_populates="scan", cascade="all, delete-orphan")
    vulnerabilities = relationship("Vulnerability", back_populates="scan", cascade="all, delete-orphan")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])

class Scope(Base):
    __tablename__ = "scopes"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    uploaded_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    
    # Relationships
    subnets = relationship("Subnet", back_populates="scope", cascade="all, delete-orphan")

class Subnet(Base):
    __tablename__ = "subnets"

    id = Column(Integer, primary_key=True, index=True)
    scope_id = Column(Integer, ForeignKey("scopes.id"), nullable=False)
    cidr = Column(String, nullable=False, index=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scope = relationship("Scope", back_populates="subnets")
    host_mappings = relationship("HostSubnetMapping", back_populates="subnet", cascade="all, delete-orphan")
    # v2.86.0 — project-scoped subnet labels (parallel to HostTag).  Eager-
    # loaded so the labels list rides along on Subnet responses without a
    # second round-trip, matching how HostTag rides on Host responses.
    label_assignments = relationship(
        "SubnetLabelAssignment",
        back_populates="subnet",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def labels(self):
        """List of SubnetLabel rows attached to this subnet.

        Walked through ``label_assignments`` (selectin) and each
        assignment's ``.label`` (joined-load — see SubnetLabelAssignment).
        Pydantic's ``from_attributes`` picks this up so the Subnet
        response schema's ``labels`` field is automatically populated.
        Sorted by name so the UI ordering is stable across responses.
        """
        out = []
        for a in self.label_assignments or []:
            if a.label is not None:
                out.append(a.label)
        return sorted(out, key=lambda lbl: (lbl.name or "").lower())


class HostSubnetMapping(Base):
    __tablename__ = "host_subnet_mappings"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False, index=True)
    subnet_id = Column(Integer, ForeignKey("subnets.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("host_id", "subnet_id", name="uq_host_subnet"),
    )

    # Relationships
    host = relationship("Host")
    subnet = relationship("Subnet", back_populates="host_mappings")


class ScanInfo(Base):
    __tablename__ = "scan_info"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    type = Column(String)
    protocol = Column(String)
    numservices = Column(Integer)
    services = Column(Text)

    # Relationships
    scan = relationship("Scan", back_populates="scan_info")

    __table_args__ = (
        Index("idx_scan_info_scan", "scan_id"),
    )


class WebInterface(Base):
    """Unified per-host web interface discovered by any fingerprint tool.

    v2.12.0 — replaces EyewitnessResult.  httpx, eyewitness, nikto all
    describe the same thing ("this URL responded, here's what we saw")
    so the storage is unified with a ``source`` discriminator.  The
    important structural change is that rows bind to a ``host_id`` (and
    optionally a ``port_id``), not only to ``scan_id`` — that's what
    unlocks per-host rollup on the HostDetail page and
    has_web_interface filtering on the Hosts list.

    Parser responsibilities:
    - Resolve ``host_id`` from the tool's IP/hostname field at write time.
    - Best-effort resolve ``port_id`` from (host_id, port, protocol).
    - Store the tool's full record in ``raw`` for debugging + later
      schema extensions without requiring re-ingest.

    Screenshot handling: tools that produce PNGs (eyewitness) extract
    them into ``uploads/web_screenshots/{scan_id}/{filename}`` and
    store the relative path in ``screenshot_path``.  Served via
    ``GET /projects/{pid}/web-interfaces/{id}/screenshot``.
    """
    __tablename__ = "web_interfaces"

    id = Column(Integer, primary_key=True, index=True)
    # Scope — per-host + per-port for rollup; scan kept for audit trail.
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=True, index=True)
    port_id = Column(Integer, ForeignKey("ports_v2.id", ondelete="SET NULL"), nullable=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)

    # Tool that produced this row.
    source = Column(String(32), nullable=False, default="unknown")  # httpx | eyewitness | nikto | ...

    # Identity of the interface.
    url = Column(String, nullable=False, index=True)
    protocol = Column(String(10))  # http | https
    port = Column(Integer)
    ip_address = Column(String(45), index=True)

    # Fingerprint fields — union across tools.
    status_code = Column(Integer)
    title = Column(String)
    server_header = Column(String)
    content_length = Column(Integer)
    # Flattened technology list for UI chip rendering (e.g.
    # ["Nginx 1.18.0", "React", "Bootstrap"]).  Wappalyzer categories
    # preserved in raw.  Null when the tool didn't report any.
    technologies = Column(JSON)
    favicon_hash = Column(String(64), index=True)  # mmh3 hash; indexed for cross-host clustering
    tls_info = Column(JSON)  # {issuer, subject, not_after, sni, ...}

    # EyeWitness extras (null for httpx).
    screenshot_path = Column(String)  # relative path under uploads/web_screenshots/{scan_id}/
    page_text = Column(Text)

    # Full source record for debugging + future schema additions.
    raw = Column(JSON)

    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Idempotency: re-ingesting the same tool against the same URL
        # on the same scan updates the row in place instead of
        # inserting duplicates.
        UniqueConstraint("scan_id", "url", "source", name="uq_web_interface_scan_url_source"),
        Index("idx_web_interface_host", "host_id"),
        Index("idx_web_interface_favicon", "favicon_hash"),
    )

    # Relationships
    scan = relationship("Scan", back_populates="web_interfaces")
    host = relationship("Host")
    port_row = relationship("Port", foreign_keys=[port_id])


class DNSRecord(Base):
    __tablename__ = "dns_records"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    domain = Column(String, nullable=False, index=True)
    record_type = Column(String, nullable=False)  # A, AAAA, CNAME, MX, TXT, etc.
    value = Column(String, nullable=False)
    ttl = Column(Integer)
    # v2.89.0 (#44.1) — which DNS server produced this row.  Populated
    # by the dnsx parser (each dnsx output line carries the resolver
    # that answered); left NULL by the CSV / amass paths whose
    # sources don't carry resolver attribution.  Indexed because the
    # analytical query this unlocks ("show me records resolver A
    # returned that resolver B didn't") filters by this column first.
    resolver_name = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class OutOfScopeHost(Base):
    __tablename__ = "out_of_scope_hosts"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False)
    ip_address = Column(String, nullable=False, index=True)
    hostname = Column(String)
    ports = Column(JSON)  # Store port information as JSON
    tool_source = Column(String)  # Which tool found this host
    reason = Column(String)  # Why it's out of scope
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan")

    __table_args__ = (
        Index("idx_out_of_scope_hosts_scan", "scan_id"),
    )


class ParseError(Base):
    __tablename__ = "parse_errors"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    filename = Column(String, nullable=False)
    file_type = Column(String)  # nmap_xml, eyewitness_json, masscan_xml, etc.
    file_size = Column(Integer)  # in bytes
    error_type = Column(String, nullable=False)  # parsing_error, validation_error, format_error
    error_message = Column(Text, nullable=False)
    error_details = Column(JSON)  # Additional error context (line numbers, stack trace, etc.)
    file_preview = Column(Text)  # First few lines/characters of the file for debugging
    user_message = Column(Text)  # User-friendly explanation of the error
    status = Column(String, default="unresolved")  # unresolved, reviewed, fixed, ignored
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    filename = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")  # queued, processing, completed, failed
    message = Column(Text)
    error_message = Column(Text)
    tool_name = Column(String)
    file_size = Column(BigInteger)
    options = Column(JSON, default=dict)

    # Audit finding H4: previously the worker would catch any
    # exception during processing, log it, and return — leaving the
    # job in ``processing`` state forever (or bouncing back to
    # ``queued`` on the next poll, creating an infinite retry loop
    # with no backoff).  These columns give the worker a bounded
    # retry budget and a terminal ``failed`` state so pathologically-
    # broken uploads stop consuming worker cycles and surface to the
    # user in the ingestion queue UI.  Default 0 / NULL means
    # existing rows behave as before until the worker touches them.
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)

    submitted_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=True)
    parse_error_id = Column(Integer, ForeignKey("parse_errors.id"), nullable=True)
    # v2.11.0: when the upload was submitted through the agent recon
    # workflow (POST /agent/recon/upload), this binds the job back to
    # the ReconSession so /agent/recon/summary can count results and
    # the UI can distinguish agent-ingested scans from human uploads.
    # Null for normal human uploads.
    recon_session_id = Column(
        Integer,
        ForeignKey("recon_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    last_heartbeat = Column(DateTime(timezone=True))
    progress = Column(String)  # e.g. "1200/5000 hosts"

    # v2.22.0: parser ingestion quality.  Parsers like httpx + EyeWitness
    # silently dropped malformed rows and reported success — users couldn't
    # tell a clean parse from a partially-degraded one.  These columns
    # surface the gap so a "completed" job that skipped 30/200 rows shows
    # up as such in the UI.
    skipped_count = Column(Integer, nullable=False, default=0, server_default="0")
    parser_warnings = Column(Text, nullable=True)
    # v2.86.2 — operator-set "I've seen this" marker for failed jobs.
    # Pre-fix, failed jobs sat in the Ingestion Queue forever with no
    # action affordance so they read as a permanent error banner.  Now
    # the failed-row Dismiss button writes this timestamp and the queue
    # list endpoint filters out non-null rows by default (admins can
    # surface them again via ?include_dismissed=true).  Successful jobs
    # never need this — they already vanish from the queue via the
    # status='completed' filter on the frontend.
    dismissed_at = Column(DateTime(timezone=True), nullable=True)

    scan = relationship("Scan")
    parse_error = relationship("ParseError")


class FollowStatus(str, enum.Enum):
    WATCHING = "watching"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"


class HostFollow(Base):
    __tablename__ = "host_follows"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(SQLEnum(FollowStatus), nullable=False, default=FollowStatus.WATCHING)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Ownership / assignment (v2.71.0).  A non-null ``assigned_at`` on the
    # (host, user) row means "this host is assigned to ``user_id`` by
    # ``assigned_by_id``".  ``user_id`` already IS the assignee, so there is
    # no separate ``assigned_to_id`` column — assignment is the act of
    # creating/owning the assignee's follow row, set by someone else.
    assigned_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)

    host = relationship("Host", back_populates="follows")
    user = relationship("User", foreign_keys=[user_id], back_populates="host_follows")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])

    __table_args__ = (
        UniqueConstraint('host_id', 'user_id', name='uq_host_follow_user'),
        Index('idx_host_follow_assigned', 'assigned_at'),
    )


class HostTag(Base):
    """A project-scoped label that can be attached to hosts.

    Tags carve a large host inventory into working sets ("prod", "DMZ",
    "owned", "ignore").  One definition row per (project, name); the
    actual host↔tag links live in ``HostTagAssignment``.
    """
    __tablename__ = "host_tags"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(60), nullable=False)
    # Palette key (e.g. "red", "blue") resolved to a colour by the
    # frontend — keeps the backend free of presentation concerns.
    color = Column(String(20), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assignments = relationship("HostTagAssignment", back_populates="tag", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('project_id', 'name', name='uq_host_tag_name'),
    )


class HostTagAssignment(Base):
    """Many-to-many link between a host and a project tag."""
    __tablename__ = "host_tag_assignments"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey("host_tags.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    host = relationship("Host", back_populates="tag_assignments")
    tag = relationship("HostTag", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint('host_id', 'tag_id', name='uq_host_tag_assignment'),
    )


class SubnetLabel(Base):
    """A project-scoped label that can be attached to subnets (v2.86.0).

    Parallel vocabulary to ``HostTag`` — kept separate so subnet groupings
    ("internet-facing", "PCI", "lab", "decommission") don't collide with
    host-level tags even when the operator wants both to exist.  The
    host-inventory page can filter on either dimension; the two filter
    groups intersect (AND), values within one group union (OR), matching
    how the existing host-tag filter behaves.
    """
    __tablename__ = "subnet_labels"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(60), nullable=False)
    # Palette key (e.g. "red", "blue") — same convention HostTag uses;
    # frontend resolves to a colour so the backend stays presentation-free.
    color = Column(String(20), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assignments = relationship("SubnetLabelAssignment", back_populates="label", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('project_id', 'name', name='uq_subnet_label_name'),
    )


class SubnetLabelAssignment(Base):
    """Many-to-many link between a subnet and a project subnet label."""
    __tablename__ = "subnet_label_assignments"

    id = Column(Integer, primary_key=True, index=True)
    subnet_id = Column(Integer, ForeignKey("subnets.id", ondelete="CASCADE"), nullable=False, index=True)
    label_id = Column(Integer, ForeignKey("subnet_labels.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    subnet = relationship("Subnet", back_populates="label_assignments")
    # joined-load: the assignment is always rendered with its label
    # (Subnet.labels property walks every assignment's .label), and one
    # JOIN row per assignment is cheaper than a separate per-assignment
    # SELECT.  Selectin on the parent side handles the subnet→assignments
    # hop; this handles the assignment→label hop in the same query.
    label = relationship("SubnetLabel", back_populates="assignments", lazy="joined")

    __table_args__ = (
        UniqueConstraint('subnet_id', 'label_id', name='uq_subnet_label_assignment'),
    )


class HostFilterView(Base):
    """A user's saved Hosts page filter preset, scoped to a project.

    Each row is one named view ("Critical web hosts", "AD servers",
    etc.) belonging to one user inside one project.  The actual filter
    state lives in `filter_json` as an opaque blob — the frontend owns
    its shape — so adding new filter dimensions doesn't require a
    schema migration.
    """
    __tablename__ = "host_filter_views"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    filter_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "name", name="uq_host_filter_view_name"),
    )


class HostQueryHistory(Base):
    """A recently-run Hosts-page boolean query (``q=``), per user/project.

    Backs the command bar's "recent queries" dropdown.  Rows are appended
    only when a query is *committed* (not on every keystroke), with
    consecutive duplicates collapsed and the list trimmed to the most
    recent N — so it stays a short, useful recency list rather than an
    unbounded audit log.  ``result_count`` is the match count at run time,
    shown alongside each entry.
    """
    __tablename__ = "host_query_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    q = Column(Text, nullable=False)
    result_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")

    __table_args__ = (
        # Serves the "newest N for this user+project" read directly.
        Index("ix_host_query_history_user_project_created", "user_id", "project_id", "created_at"),
    )


class NoteStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class HostNote(Base):
    __tablename__ = "host_notes"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False)
    # v2.86.2 — was nullable=False, no ondelete.  Flipped to nullable +
    # SET NULL so a deleted author leaves the note body behind as "by
    # deleted user" instead of either blocking the delete (NOT NULL
    # before the fix) or wiping shared annotations (CASCADE).
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    parent_id = Column(Integer, ForeignKey("host_notes.id"), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(SQLEnum(NoteStatus), nullable=False, default=NoteStatus.OPEN)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    host = relationship("Host", back_populates="notes")
    author = relationship("User", back_populates="host_notes")
    replies = relationship("HostNote", backref=backref("parent", remote_side="HostNote.id"), lazy="selectin")
