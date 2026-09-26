"""
Host Deduplication Service

Handles finding, creating, and updating host records to eliminate duplicates.
Implements conflict resolution and audit tracking for data changes.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy.orm import Session, noload
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models import Host, Port, Script, HostScript, HostScanHistory, PortScanHistory
from app.services.os_family import os_family_from_name

logger = logging.getLogger(__name__)


def port_state_is_active(state: Optional[str]) -> bool:
    """Port.is_active from its state — the one rule for the create and update
    paths (v2.419.0, review H8; they disagreed on a new closed port).  An
    uncertain ``open|filtered`` (UDP) counts as active, like ``filtered``: the
    port may answer.  No state (a tool that reports none) is active, as a
    created port always was."""
    return state is None or state in ('open', 'filtered', 'open|filtered')


def should_refresh_service_details(
    existing_name: Optional[str],
    existing_conf: Optional[int],
    new_name: Optional[str],
    new_conf: Optional[int],
) -> bool:
    """Should a new observation of the SAME service refresh its product,
    version and extra info? (v2.419.0, review 2026-09-25 H7.)

    ``should_replace_service`` decides WHICH identification wins; among equal
    confidence it wanted a longer name, so a rescan saying "http, nginx
    1.26.0" at nmap confidence 10 lost to the stored "http, nginx 1.18.0" at
    10 — the upgrade was invisible while "last seen" moved.  The same name at
    equal or higher confidence is the newer word on that service.  A name with
    no confidence behind it (NetExec, SMBMap, dirbuster) refreshes nothing, and
    the caller only copies values the observation actually carries.
    """
    return (
        bool(existing_name) and bool(new_name)
        and existing_name.strip().lower() == new_name.strip().lower()
        and (new_conf or 0) > 0
        and (new_conf or 0) >= (existing_conf or 0)
    )


def should_replace_service(
    existing_name: Optional[str],
    existing_conf: Optional[int],
    new_name: Optional[str],
    new_conf: Optional[int],
) -> bool:
    """Canonical port service-info merge rule: should the NEW scan's service
    info replace what's already stored?

    True when we have nothing yet, the new scan is more confident, or — among
    observations of EQUAL confidence — the new name is non-empty and more
    specific (longer).  This is the single source of truth for service-name
    conflict resolution.

    "Longer wins" used to apply regardless of confidence, so an enumeration
    tool that sends a name without one (NetExec ``winrm``, SMBMap, Nikto,
    dirbuster) replaced an nmap ``-sV`` identification and wiped its product,
    version and TLS tunnel (review 2026-09-23 C6c).  A name with no confidence
    behind it now only fills a blank or improves another unconfident name.

    The masscan bulk-SQL path (``masscan_parser._upsert_ports_chunk``) carries
    only a service name (no confidence), so it mirrors the reduced form of this
    rule — empty, or longer than an unconfident name — in a CASE expression.
    Keep the two in lockstep; ``test_masscan_service_merge`` pins masscan's SQL
    to this rule.
    """
    existing_c, new_c = existing_conf or 0, new_conf or 0
    return (
        not existing_name
        or new_c > existing_c
        or (new_c == existing_c and bool(new_name) and len(new_name) > len(existing_name or ""))
    )


class HostDeduplicationService:
    """Service to manage host deduplication and merging across scans"""
    
    def __init__(self, db: Session):
        self.db = db
        # Intra-scan history dedup (review A-3).  A history row for a given
        # (host_id/port_id, scan_id) may already have been added earlier in
        # THIS parse but not yet flushed — and with autoflush=False a DB query
        # won't see it.  We used to find it by linear-scanning ``db.new`` on
        # every host AND every port, which is O(rows) per call → O(n²) over a
        # re-scan where the create-path flush never fires and ``db.new`` grows
        # unbounded.  These dicts make that lookup O(1).  Keyed by
        # (id, scan_id); the value is the pending/added ORM row so a repeat
        # within the scan updates it in place instead of inserting a duplicate
        # (which would violate uq_host_scan / uq_port_scan).  Safe across the
        # savepoint retries in find_or_create_*: history is only recorded AFTER
        # the host/port INSERT savepoint has committed, so a cached row is
        # never one that later gets rolled back.
        self._pending_host_history: Dict[Tuple[int, int], HostScanHistory] = {}
        self._pending_port_history: Dict[Tuple[int, int], PortScanHistory] = {}
        # v2.322.0 — per-parse memo for name rows + observation identity so
        # the name→address bookkeeping costs one lookup per distinct name.
        from app.services.dns_name_service import ObservationCache
        self._name_cache = ObservationCache()
        # Per-host working set (see _load_host_working_set).
        self._ws_host_id: Optional[int] = None
        self._ws_scan_id: Optional[int] = None
        self._ws_ports: Dict[Tuple[int, str], Port] = {}
        self._ws_scripts: Dict[Tuple[int, str], Script] = {}
        self._ws_fresh_port_ids: set = set()
        self._ws_history_checked: set = set()
        # Hosts created in this parse: they have no ports, scripts or history.
        self._fresh_host_ids: set = set()

    def discard_rolled_back_state(self) -> None:
        """Forget what a caller's SAVEPOINT rollback just discarded (v2.419.0,
        review 2026-09-25 H1).  Call it after rolling back a savepoint that
        wrapped calls into this service.

        The history caches hold ORM rows added in THIS parse; a row added
        inside a rolled-back savepoint is gone from the database but stayed in
        the cache, so a later observation of the same host or port found it,
        updated the discarded object, and never inserted history — the host
        silently lost its membership in the scan.  Rows that were already
        persistent before the savepoint survive (expired, reloaded on access)
        and are kept.  The per-host working set and the name memo can hold
        rows from the savepoint too; they are rebuilt on next use."""
        from sqlalchemy import inspect as sa_inspect
        from app.services.dns_name_service import ObservationCache

        for cache in (self._pending_host_history, self._pending_port_history):
            for key in [k for k, row in cache.items() if not sa_inspect(row).persistent]:
                del cache[key]
        self._ws_host_id = None
        self._ws_scan_id = None
        self._ws_ports = {}
        self._ws_scripts = {}
        self._ws_fresh_port_ids = set()
        self._ws_history_checked = set()
        self._name_cache = ObservationCache()

    def find_or_create_host(self, ip_address: str, scan_id: int, host_data: Dict[str, Any], project_id: int = None) -> Host:
        """
        Find existing host by IP (within the same project) or create new one.
        Updates existing host with new information using conflict resolution.

        Handles concurrent inserts: if another session creates the same IP
        between our SELECT and INSERT, we catch the UniqueViolation, rollback
        the failed flush, and merge with the now-existing row.

        v2.322.0 — every name the scan reported for this address is recorded
        as a name→address observation (``dns_records``) on the way out, so a
        load balancer's forty vhosts all survive as relationships instead of
        one display name plus thirty-nine "conflicts".
        """
        host = self._find_or_create_host_inner(ip_address, scan_id, host_data, project_id)
        self._record_name_observations(host, ip_address, scan_id, host_data)
        return host

    def _record_name_observations(self, host: Host, ip_address: str, scan_id: int, host_data: Dict[str, Any]) -> None:
        """Persist ``host_data['hostnames']`` (a list of ``(name, kind)``) and
        the plain ``host_data['hostname']`` as observations about names.

        ``kind`` is the nmap hostname type: ``'PTR'`` (reverse DNS → PTR
        observation), ``'user'`` (the operator gave the scanner this name and
        it resolved to the address → A/AAAA observation), anything else →
        SCANNER ("a scanner reported this name for this address").
        """
        project_id = host.project_id
        if project_id is None:
            return
        # A parser that has ALREADY written this host's name observations
        # (dnsx / amass / DNS CSV write them with resolver + TTL detail the
        # dedup service doesn't have) says so, or we'd add a second, poorer
        # row for the same fact.
        if host_data.get('names_recorded'):
            return
        pairs: List[Tuple[str, str]] = []
        for entry in host_data.get('hostnames') or []:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                pairs.append((str(entry[0]), str(entry[1] or '')))
            elif isinstance(entry, str):
                pairs.append((entry, ''))
        plain = host_data.get('hostname')
        if plain and not any(n == plain for n, _ in pairs):
            pairs.append((str(plain), host_data.get('hostname_kind') or ''))
        if not pairs:
            return
        from app.db.models import DNS_OBS_SCANNER
        from app.services.dns_name_service import record_observation
        is_v6 = ':' in ip_address
        for name, kind in pairs:
            k = (kind or '').lower()
            if k == 'ptr':
                record_type = 'PTR'
            elif k == 'user':
                record_type = 'AAAA' if is_v6 else 'A'
            else:
                record_type = DNS_OBS_SCANNER
            # SAVEPOINT so a bad name can't poison the host's transaction; the
            # journal lets the cache forget what the rollback discarded.
            journal: List[Tuple[str, object]] = []
            sp = self.db.begin_nested()
            try:
                record_observation(
                    self.db, project_id=project_id, name=name, record_type=record_type,
                    value=ip_address, scan_id=scan_id, cache=self._name_cache, journal=journal,
                )
                sp.commit()
            except Exception as exc:  # noqa: BLE001 — never let a name break host ingest
                sp.rollback()
                self._name_cache.forget(journal)
                logger.warning("name observation %r for %s skipped: %s", name, ip_address, exc)

    def _find_or_create_host_inner(self, ip_address: str, scan_id: int, host_data: Dict[str, Any], project_id: int = None) -> Host:
        # v2.90.3 (code review NEW C) — suppress the eager loads
        # inherited from Host.* lazy="selectin" relationships.  The
        # dedup lookup only needs Host.id + scalar fields to decide
        # update-vs-create; pre-fix loading every Host fired
        # selectin queries for ports / vulnerabilities / attributes
        # / notes / tag_assignments on EVERY host, even when the
        # parser only intended to compare a scalar.  On a 40k-host
        # re-scan that meant ~200k extra round-trips just to look up
        # who we'd seen before.
        host_query = (
            self.db.query(Host)
            .options(
                noload(Host.ports),
                noload(Host.vulnerabilities),
                noload(Host.attributes),
                noload(Host.notes),
                noload(Host.tag_assignments),
            )
            .filter(Host.ip_address == ip_address)
        )
        if project_id is not None:
            host_query = host_query.filter(Host.project_id == project_id)
        existing_host = host_query.first()

        if existing_host:
            # Update existing host
            updated_host = self._update_existing_host(existing_host, scan_id, host_data)
            # Record this scan discovered the host
            self._record_host_scan_history(updated_host.id, scan_id, host_data)
            return updated_host
        else:
            # Create new host
            new_host = self._create_new_host(ip_address, scan_id, host_data)
            if project_id is not None:
                new_host.project_id = project_id
            # The savepoint opens BEFORE the add: begin_nested() flushes what
            # is pending, and with the host already added that flush — the
            # INSERT that loses a concurrent race — ran outside the try below,
            # so the IntegrityError escaped and poisoned the parent
            # transaction (2.374.4 review H2).
            #
            # Use a savepoint so that a UniqueViolation only rolls back the
            # INSERT, not the entire transaction (which would destroy the
            # scan record and poison all subsequent operations).
            #
            # We catch *any* exception inside the savepoint (not just
            # IntegrityError) and rollback before re-raising, because a
            # DataError / OperationalError / StaleDataError leaving the
            # savepoint open will poison the parent transaction the
            # exact same way an unhandled IntegrityError would.  The
            # original "only catch IntegrityError" version produced
            # PendingRollbackError chains downstream when a parser line
            # contained malformed UTF-8 or an over-long string slipped
            # past upstream validation.
            nested = self.db.begin_nested()
            self.db.add(new_host)
            try:
                self.db.flush()  # Get the ID
                nested.commit()
            except IntegrityError:
                # Another session inserted this IP between our SELECT and INSERT.
                # Roll back the failed INSERT, then re-resolve inside a SECOND
                # savepoint so a third-writer race (TTL reaper, project-archive
                # delete, manual cleanup) on the row between our retry SELECT and
                # the update can't poison the parent scan transaction either.
                nested.rollback()
                logger.debug(
                    "Concurrent insert for %s — falling back to update", ip_address
                )
                fallback_nested = self.db.begin_nested()
                try:
                    # v2.90.3 — same noload suppression as the primary
                    # lookup above; the concurrent-insert fallback
                    # otherwise re-triggered every selectin relationship.
                    fallback_query = (
                        self.db.query(Host)
                        .options(
                            noload(Host.ports),
                            noload(Host.vulnerabilities),
                            noload(Host.attributes),
                            noload(Host.notes),
                            noload(Host.tag_assignments),
                        )
                        .filter(Host.ip_address == ip_address)
                    )
                    if project_id is not None:
                        fallback_query = fallback_query.filter(Host.project_id == project_id)
                    existing_host = fallback_query.first()
                    if existing_host:
                        updated_host = self._update_existing_host(
                            existing_host, scan_id, host_data
                        )
                        self._record_host_scan_history(
                            updated_host.id, scan_id, host_data
                        )
                        fallback_nested.commit()
                        return updated_host
                    # Row was inserted then deleted between our two queries —
                    # rare but possible under aggressive TTL reaping.  Surface
                    # the actual cause rather than re-raising the stale
                    # IntegrityError, which would be misleading.
                    fallback_nested.rollback()
                    raise RuntimeError(
                        f"Host {ip_address!r} (project_id={project_id}) inserted by a "
                        "concurrent writer but no longer present at fallback-SELECT time; "
                        "likely deleted by a TTL reaper or project archive during this "
                        "parse run.  Re-run the upload to recreate."
                    )
                except Exception:
                    # Roll back the fallback savepoint on any exit other
                    # than the successful return above so the parent
                    # transaction stays clean.
                    try:
                        fallback_nested.rollback()
                    except Exception as rollback_exc:  # noqa: BLE001
                        # v2.65.0 — was silent `pass`.  If the savepoint
                        # rollback itself fails, the session is most
                        # likely toast and the *original* exception is
                        # about to re-raise (next line), but the operator
                        # needs to see this in collect-logs.sh so a
                        # transient DB outage doesn't read as "ingest
                        # silently produced fewer hosts."
                        logger.warning(
                            "host dedup fallback-rollback failed for scan_id=%s: %s",
                            scan_id, rollback_exc,
                        )
                    raise
            except Exception:
                # Any non-IntegrityError flush failure (DataError,
                # OperationalError, StaleDataError, ...) must roll back
                # the savepoint before the exception escapes — otherwise
                # the caller's broad except will continue with a session
                # that can no longer commit.
                try:
                    nested.rollback()
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(
                        "host dedup nested-rollback failed for scan_id=%s: %s",
                        scan_id, rollback_exc,
                    )
                raise

            self._fresh_host_ids.add(new_host.id)
            # Record initial scan history
            self._record_host_scan_history(new_host.id, scan_id, host_data, is_new=True)
            return new_host
    
    def find_or_create_port(self, host_id: int, scan_id: int, port_data: Dict[str, Any]) -> Port:
        """
        Find existing port by host_id + port_number + protocol or create new one.
        Updates existing port with new information.
        """
        port_number = port_data.get('port_number')
        protocol = port_data.get('protocol', 'tcp')

        # The host's ports come from the per-host working set (one query for
        # all of them), not one SELECT per port.
        self._load_host_working_set(host_id, scan_id)
        existing_port = self._ws_ports.get((port_number, protocol))

        if existing_port:
            # Update existing port
            updated_port = self._update_existing_port(existing_port, scan_id, port_data)
            # Record port scan history
            self._record_port_scan_history(updated_port.id, scan_id, port_data)
            return updated_port
        else:
            # Create new port
            new_port = self._create_new_port(host_id, scan_id, port_data)
            # Savepoint first, then add — see find_or_create_host (H2).
            nested = self.db.begin_nested()
            self.db.add(new_port)
            try:
                self.db.flush()  # Get the ID
                nested.commit()
            except IntegrityError:
                # Same two-savepoint pattern as find_or_create_host — protect
                # the parent transaction from a third-writer race on the row.
                nested.rollback()
                logger.debug(
                    "Concurrent insert for port %s/%s on host %s — falling back to update",
                    port_number, protocol, host_id,
                )
                fallback_nested = self.db.begin_nested()
                try:
                    existing_port = self.db.query(Port).filter(
                        Port.host_id == host_id,
                        Port.port_number == port_number,
                        Port.protocol == protocol,
                    ).first()
                    if existing_port:
                        updated_port = self._update_existing_port(existing_port, scan_id, port_data)
                        self._record_port_scan_history(updated_port.id, scan_id, port_data)
                        fallback_nested.commit()
                        # The working set now vouches for this port, so it
                        # must hold the port's scripts too.
                        for script in self.db.query(Script).filter(Script.port_id == updated_port.id):
                            self._ws_scripts.setdefault((updated_port.id, script.script_id), script)
                        self._ws_ports[(port_number, protocol)] = updated_port
                        return updated_port
                    fallback_nested.rollback()
                    raise RuntimeError(
                        f"Port {port_number}/{protocol} on host {host_id} inserted by a "
                        "concurrent writer but no longer present at fallback-SELECT time; "
                        "likely deleted between our two queries."
                    )
                except Exception:
                    try:
                        fallback_nested.rollback()
                    except Exception as rollback_exc:  # noqa: BLE001
                        logger.warning(
                            "port dedup fallback-rollback failed for host_id=%s scan_id=%s: %s",
                            host_id, scan_id, rollback_exc,
                        )
                    raise
            except Exception:
                # Match find_or_create_host: catch *any* non-IntegrityError
                # flush failure and roll back the savepoint before the
                # exception escapes, so the parent transaction stays
                # commitable.
                try:
                    nested.rollback()
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(
                        "port dedup nested-rollback failed for host_id=%s scan_id=%s: %s",
                        host_id, scan_id, rollback_exc,
                    )
                raise

            # A port created in this parse has no history or scripts in the DB.
            self._ws_ports[(port_number, protocol)] = new_port
            self._ws_fresh_port_ids.add(new_port.id)
            # Record initial port scan history
            self._record_port_scan_history(new_port.id, scan_id, port_data, is_new=True)
            return new_port

    # ------------------------------------------------------------------
    # Per-host working set (v2.393.0; review 2026-09-23 R5)
    # ------------------------------------------------------------------
    def _load_host_working_set(self, host_id: int, scan_id: int) -> None:
        """Load the host's ports, their scripts and their history rows for
        ``scan_id`` in three queries, the first time a port of this host is
        touched.  An nmap import spent a SELECT per port on each (94
        statements per host; ~21 minutes for an 80k-host file).

        Held for ONE host at a time: parsers walk a host's ports together, so
        this bounds memory, and it is dropped when the objects left the
        session (a parser that commits + expunges between hosts) — a
        detached object's changes would be lost silently."""
        if (
            self._ws_host_id == host_id and self._ws_scan_id == scan_id
            and all(obj in self.db for obj in list(self._ws_ports.values())[:1])
        ):
            return
        self._ws_host_id, self._ws_scan_id = host_id, scan_id
        self._ws_scripts = {}
        self._ws_fresh_port_ids = set()
        if host_id in self._fresh_host_ids:
            # Created in this parse: nothing to load — but only the FIRST
            # time.  A later element for the same host (merged XML, host A →
            # B → A) must reload what the first one wrote, or its scripts
            # look new and collide on uq_port_script (remediation review
            # 2026-09-23 finding 2).
            self._fresh_host_ids.discard(host_id)
            self._ws_ports = {}
            return
        ports = self.db.query(Port).filter(Port.host_id == host_id).all()
        self._ws_ports = {(p.port_number, p.protocol): p for p in ports}
        port_ids = [p.id for p in ports]
        if not port_ids:
            return
        for script in self.db.query(Script).filter(Script.port_id.in_(port_ids)).all():
            self._ws_scripts[(script.port_id, script.script_id)] = script
        for history in (
            self.db.query(PortScanHistory)
            .filter(PortScanHistory.port_id.in_(port_ids), PortScanHistory.scan_id == scan_id)
            .all()
        ):
            self._pending_port_history.setdefault((history.port_id, scan_id), history)
        # Every other (port, scan) of this host is known to have no history row.
        self._ws_history_checked.update((pid, scan_id) for pid in port_ids)

    def _ws_knows_port(self, port_id: int) -> bool:
        """Whether the working set holds everything the DB has for this port."""
        return port_id in self._ws_fresh_port_ids or any(
            p.id == port_id for p in self._ws_ports.values()
        )

    def add_or_update_script(self, port_id: int, scan_id: int, script_data: Dict[str, Any]) -> Script:
        """Add or update a script for a port"""
        script_id = script_data.get('script_id')
        output = script_data.get('output', '')

        # From the per-host working set when it covers this port; a query
        # otherwise (a caller that did not go through find_or_create_port).
        if self._ws_knows_port(port_id):
            existing_script = self._ws_scripts.get((port_id, script_id))
        else:
            existing_script = self.db.query(Script).filter(
                Script.port_id == port_id,
                Script.script_id == script_id
            ).first()

        if existing_script:
            # The output is the latest observation's; ``scan_id`` stays the
            # scan that FIRST recorded it (v2.419.0, review 2026-09-25 H2 —
            # the vulnerability rule since v2.332.0).  It used to move to the
            # newest scan, and ``scan_id`` cascades on delete: an import that
            # failed part-way had its scan deleted by the cleanup, taking the
            # rows an EARLIER scan created with it.
            existing_script.output = output
            existing_script.last_seen = func.now()
            return existing_script
        else:
            # Create new script
            new_script = Script(
                port_id=port_id,
                script_id=script_id,
                output=output,
                scan_id=scan_id
            )
            self.db.add(new_script)
            # autoflush is off — flush so a repeat of the same (port_id, script_id)
            # later in THIS scan (merged/cat'd XML with duplicate host entries)
            # finds the row above instead of inserting a second one that would
            # detonate uq_port_script at the next commit.
            self.db.flush()
            if self._ws_knows_port(port_id):
                self._ws_scripts[(port_id, script_id)] = new_script
            return new_script
    
    def add_or_update_host_script(self, host_id: int, scan_id: int, script_data: Dict[str, Any]) -> HostScript:
        """Add or update a host script"""
        script_id = script_data.get('script_id')
        output = script_data.get('output', '')
        
        # Try to find existing host script
        existing_script = self.db.query(HostScript).filter(
            HostScript.host_id == host_id,
            HostScript.script_id == script_id
        ).first()
        
        if existing_script:
            # scan_id stays the first recorder (see add_or_update_script, H2).
            existing_script.output = output
            existing_script.last_seen = func.now()
            return existing_script
        else:
            # Create new host script
            new_script = HostScript(
                host_id=host_id,
                script_id=script_id,
                output=output,
                scan_id=scan_id
            )
            self.db.add(new_script)
            # autoflush is off — flush so an in-scan repeat finds this row instead
            # of inserting a duplicate that breaks uq_host_script at commit.
            self.db.flush()
            return new_script
    
    def _create_new_host(self, ip_address: str, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """Create a new host record"""
        from app.services.dns_name_service import display_name_candidate

        hostname = (host_data.get('hostname') or '').strip() or None
        # An address is not a name, nor is a URL or ip:port (same rule as
        # apply_hostname_candidate; v2.402.0).  An operator-sourced name is
        # kept as typed, as there.
        if hostname and host_data.get('hostname_source') != 'operator':
            hostname = display_name_candidate(hostname)
        host = Host(
            ip_address=ip_address,
            hostname=hostname,
            # Provenance of the display name (see dns_name_service).  Parsers
            # that know better (dnsx PTR, operator edits) pass hostname_source;
            # everything else is a scanner-reported name.
            hostname_source=(host_data.get('hostname_source') or 'scanner') if hostname else None,
            state=host_data.get('state'),
            state_reason=host_data.get('state_reason'),
            os_name=host_data.get('os_name'),
            # v2.421.0 — nmap's family when it gave one, else the name's.
            os_family=host_data.get('os_family') or os_family_from_name(host_data.get('os_name')),
            os_generation=host_data.get('os_generation'),
            os_type=host_data.get('os_type'),
            os_vendor=host_data.get('os_vendor'),
            os_accuracy=host_data.get('os_accuracy'),
            last_updated_scan_id=scan_id
        )
        return host
    
    def _record_conflict(self, object_type, object_id, field_name, previous_value, new_value, previous_scan_id, new_scan_id):
        """Record that a scan reported a DIFFERENT value for an attribute than
        the one we hold — so "scan A said Linux, scan B said Windows" leaves an
        audit trail (surfaced via GET /hosts/{id}/conflicts) instead of the
        loser being silently dropped.  Recorded whether or not we adopt the new
        value; confidence/method stay null (the dedup has no confidence model).

        Idempotent on the distinct disagreement ``(object, field, prev, new)``:
        the same held-vs-reported pair adds nothing after the first record, so
        re-scans (or a host processed more than once in one scan) must not pile
        up identical rows — that inflated the conflict count and made the
        host-detail "Resolution history" show the same line many times.  A
        repeat just refreshes the last-seen scan + timestamp."""
        from app.db.models_confidence import ConflictHistory
        host_id = object_id if object_type == 'host' else None
        port_id = object_id if object_type == 'port' else None
        prev_s = str(previous_value) if previous_value is not None else None
        new_s = str(new_value) if new_value is not None else None
        existing = self.db.query(ConflictHistory).filter(
            ConflictHistory.host_id == host_id,
            ConflictHistory.port_id == port_id,
            ConflictHistory.field_name == field_name,
            ConflictHistory.previous_value == prev_s,
            ConflictHistory.new_value == new_s,
        ).first()
        if existing is not None:
            existing.new_scan_id = new_scan_id
            existing.resolved_at = func.now()
            return
        self.db.add(ConflictHistory(
            host_id=host_id,
            port_id=port_id,
            field_name=field_name,
            previous_value=prev_s,
            new_value=new_s,
            previous_scan_id=previous_scan_id, new_scan_id=new_scan_id,
            resolved_at=func.now(),
        ))
        # autoflush is off — flush so an identical conflict later in THIS same
        # scan finds the row above instead of inserting a second copy.
        self.db.flush()

    def _update_existing_host(self, host: Host, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """
        Update existing host with new data using conflict resolution strategy.
        Strategy: "Most recent wins" with some intelligence for better data.
        """
        updated = False
        prior_scan = host.last_updated_scan_id  # captured before we overwrite it

        # Display name: decided by ONE rule (dns_name_service
        # .apply_hostname_candidate) — a candidate replaces the current name
        # only when its provenance outranks it (operator > PTR > scanner >
        # forward).  A differing name is NOT a conflict any more: many names
        # legitimately share one address, and every reported name is kept as
        # a name→address observation by find_or_create_host.  Pre-v2.322.0
        # hostname conflict rows stay as history; none are added.
        from app.services.dns_name_service import apply_hostname_candidate
        new_hostname = host_data.get('hostname')
        if new_hostname and apply_hostname_candidate(
            host, new_hostname, host_data.get('hostname_source') or 'scanner',
        ):
            updated = True

        # Update state (most recent wins) — but 'unknown' carries no
        # information and must never clobber a known state.  gnmap, for
        # example, emits a host's Status: and Ports: on separate lines;
        # the Ports: line parses with state='unknown', and without this
        # guard it would overwrite the 'up' from the Status: line.
        new_state = host_data.get('state')
        if new_state and new_state != 'unknown' and new_state != host.state:
            # A held 'unknown' is a blank being filled in, not a disagreement
            # between two scans (v2.367.0) — it used to be recorded, and was the
            # single most common "conflict" in the inventory.
            if host.state and host.state != 'unknown':
                self._record_conflict('host', host.id, 'state', host.state, new_state, prior_scan, scan_id)
            host.state = new_state
            # Only overwrite the reason when the new scan actually supplies one
            # — gnmap emits an empty reason, which would otherwise erase a
            # meaningful nmap reason (e.g. "syn-ack") on a re-scan.
            new_reason = host_data.get('state_reason')
            if new_reason:
                host.state_reason = new_reason
            updated = True

        # Update OS information if new scan has higher accuracy or we don't have OS info
        new_accuracy = host_data.get('os_accuracy', 0)
        new_os = host_data.get('os_name')
        if new_os and host.os_name and new_os != host.os_name:
            self._record_conflict('host', host.id, 'os_name', host.os_name, new_os, prior_scan, scan_id)
        if (not host.os_name or new_accuracy > (host.os_accuracy or 0)):
            if host_data.get('os_name'):
                host.os_name = host_data.get('os_name')
                host.os_family = host_data.get('os_family') or os_family_from_name(host_data.get('os_name'))
                host.os_generation = host_data.get('os_generation')
                host.os_type = host_data.get('os_type')
                host.os_vendor = host_data.get('os_vendor')
                host.os_accuracy = new_accuracy
                updated = True
        
        # Always update last seen and scan reference
        host.last_seen = func.now()
        host.last_updated_scan_id = scan_id
        
        return host
    
    def _create_new_port(self, host_id: int, scan_id: int, port_data: Dict[str, Any]) -> Port:
        """Create a new port record"""
        port = Port(
            host_id=host_id,
            port_number=port_data.get('port_number'),
            protocol=port_data.get('protocol', 'tcp'),
            state=port_data.get('state'),
            reason=port_data.get('reason'),
            service_name=port_data.get('service_name'),
            service_product=port_data.get('service_product'),
            service_version=port_data.get('service_version'),
            service_extrainfo=port_data.get('service_extrainfo'),
            service_method=port_data.get('service_method'),
            service_conf=port_data.get('service_conf'),
            service_tunnel=port_data.get('service_tunnel'),
            last_updated_scan_id=scan_id,
            # H8 — the update path's rule: a new closed port was active
            # until its first repeat observation turned it inactive.
            is_active=port_state_is_active(port_data.get('state')),
        )
        return port
    
    def _update_existing_port(self, port: Port, scan_id: int, port_data: Dict[str, Any]) -> Port:
        """
        Update existing port with new data using conflict resolution.
        Strategy: Keep most detailed/accurate service information.
        """
        # Update state (most recent wins)
        new_state = port_data.get('state')
        if new_state:
            # The reason belongs to the observation that set the state.  A tool
            # that reports no reason (Nikto, NetExec, SMBMap…) keeps nmap's
            # ``syn-ack`` while the state is unchanged; a CHANGED state drops
            # a reason that described the old one.
            new_reason = port_data.get('reason')
            if new_reason or new_state != port.state:
                port.reason = new_reason
            port.state = new_state
            port.is_active = port_state_is_active(new_state)
        
        # Update service info if new scan has better information — the single
        # canonical rule (mirrored by masscan's bulk SQL); see should_replace_service.
        new_service_name = port_data.get('service_name')
        new_service_conf = port_data.get('service_conf', 0)

        if should_replace_service(
            port.service_name, port.service_conf, new_service_name, new_service_conf
        ):
            port.service_name = new_service_name
            port.service_product = port_data.get('service_product')
            port.service_version = port_data.get('service_version')
            port.service_extrainfo = port_data.get('service_extrainfo')
            port.service_method = port_data.get('service_method')
            port.service_conf = new_service_conf
            # Moves with the rest of the service block: tunnel belongs to the
            # observation that won, and carrying a stale "ssl" onto a service
            # a better scan says is plaintext would be worse than NULL.
            port.service_tunnel = port_data.get('service_tunnel')
        elif should_refresh_service_details(
            port.service_name, port.service_conf, new_service_name, new_service_conf
        ):
            # H7 — the same service re-identified: the newer details win,
            # but a value the observation does not carry is not erased.
            for column in ('service_product', 'service_version', 'service_extrainfo', 'service_method'):
                value = port_data.get(column)
                if value:
                    setattr(port, column, value)
            port.service_conf = new_service_conf

        # Always update timestamps and scan reference
        port.last_seen = func.now()
        port.last_updated_scan_id = scan_id
        
        return port
    
    def _record_host_scan_history(self, host_id: int, scan_id: int, host_data: Dict[str, Any], is_new: bool = False):
        """Record that this scan discovered/updated this host"""
        key = (host_id, scan_id)
        # Pending row added earlier in THIS parse (O(1)); else a row from a
        # prior flush, found by the uq_host_scan index.
        existing_history = self._pending_host_history.get(key)
        if existing_history is None:
            existing_history = self.db.query(HostScanHistory).filter(
                HostScanHistory.host_id == host_id,
                HostScanHistory.scan_id == scan_id
            ).first()

        if existing_history:
            # Update existing history entry.  v2.332.3 — a later observation
            # of the same host in the SAME scan must not erase a definite one:
            # a .gnmap file emits a "Status: Up" line and a separate "Ports:"
            # line per host, and the Ports line (no Status) arrived here as
            # 'unknown' and overwrote 'up', so every gnmap scan reported 0 up
            # hosts.  'unknown'/None carry no information; a definite state
            # or a name is only replaced by another definite value.
            new_state = host_data.get('state')
            if new_state and new_state != 'unknown':
                existing_history.state_at_scan = new_state
            elif not existing_history.state_at_scan:
                existing_history.state_at_scan = new_state
            new_hostname = host_data.get('hostname')
            if new_hostname:
                existing_history.hostname_at_scan = new_hostname
            existing_history.os_info_updated = (
                existing_history.os_info_updated or bool(host_data.get('os_name'))
            )
            # Never downgrade created→updated: if any record of this (host,
            # scan) marked the scan as the creator, keep it.
            if is_new:
                existing_history.host_created = True
        else:
            # Create new history entry
            history = HostScanHistory(
                host_id=host_id,
                scan_id=scan_id,
                state_at_scan=host_data.get('state'),
                hostname_at_scan=host_data.get('hostname'),
                os_info_updated=bool(host_data.get('os_name')),  # True if this scan provided OS info
                host_created=is_new,  # dedup create/update decision — ground truth
            )
            self.db.add(history)
            self._pending_host_history[key] = history

    def _record_port_scan_history(self, port_id: int, scan_id: int, port_data: Dict[str, Any], is_new: bool = False):
        """Record port state at time of this scan"""
        key = (port_id, scan_id)
        # Pending row added earlier in THIS parse (O(1)); else a row from a
        # prior flush, found by the uq_port_scan index — unless the working
        # set already knows there is none (a port created in this parse, or
        # a host whose history for this scan was prefetched).
        existing_history = self._pending_port_history.get(key)
        known_absent = port_id in self._ws_fresh_port_ids or key in self._ws_history_checked
        if existing_history is None and not known_absent:
            existing_history = self.db.query(PortScanHistory).filter(
                PortScanHistory.port_id == port_id,
                PortScanHistory.scan_id == scan_id
            ).first()

        service_info = {
            'service_name': port_data.get('service_name'),
            'service_product': port_data.get('service_product'),
            'service_version': port_data.get('service_version'),
            'service_extrainfo': port_data.get('service_extrainfo'),
            'service_method': port_data.get('service_method'),
            'service_conf': port_data.get('service_conf')
        }

        # v2.333.0 — typed copy of the name inside service_info (what /scans
        # counts as "service identified"), kept in lockstep with the JSON.
        service_name = port_data.get('service_name') or None

        if existing_history:
            # Update existing history entry
            existing_history.state_at_scan = port_data.get('state')
            existing_history.service_info = json.dumps(service_info) if any(service_info.values()) else None
            existing_history.service_name = service_name
            # Never downgrade created→re-observed (same rule as host_created).
            if is_new:
                existing_history.port_created = True
        else:
            # Create new history entry
            history = PortScanHistory(
                port_id=port_id,
                scan_id=scan_id,
                state_at_scan=port_data.get('state'),
                service_info=json.dumps(service_info) if any(service_info.values()) else None,
                service_name=service_name,
                port_created=is_new,  # dedup create/update decision — ground truth
            )
            self.db.add(history)
            self._pending_port_history[key] = history

    # NOTE (code review): the former ``update_scan_statistics`` was removed
    # here — it had no callers anywhere in the app, so Scan.new_hosts /
    # updated_hosts / ports_discovered were never populated by the dedup
    # path and the method's two exists()-subquery counts were dead weight.
    # If those per-scan counters need surfacing again, recompute them where
    # the scan is finalized and verify the subqueries scale on large scans.

