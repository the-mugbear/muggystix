"""
Host Deduplication Service

Handles finding, creating, and updating host records to eliminate duplicates.
Implements conflict resolution and audit tracking for data changes.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Type, TypeVar
from sqlalchemy.orm import Session, noload
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models import Host, Port, Script, HostScript, HostScanHistory, PortScanHistory

logger = logging.getLogger(__name__)


TModel = TypeVar("TModel")


class HostDeduplicationService:
    """Service to manage host deduplication and merging across scans"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def find_or_create_host(self, ip_address: str, scan_id: int, host_data: Dict[str, Any], project_id: int = None) -> Host:
        """
        Find existing host by IP (within the same project) or create new one.
        Updates existing host with new information using conflict resolution.

        Handles concurrent inserts: if another session creates the same IP
        between our SELECT and INSERT, we catch the UniqueViolation, rollback
        the failed flush, and merge with the now-existing row.
        """
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
            self.db.add(new_host)
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
        
        # Try to find existing port
        existing_port = self.db.query(Port).filter(
            Port.host_id == host_id,
            Port.port_number == port_number,
            Port.protocol == protocol
        ).first()
        
        if existing_port:
            # Update existing port
            updated_port = self._update_existing_port(existing_port, scan_id, port_data)
            # Record port scan history
            self._record_port_scan_history(updated_port.id, scan_id, port_data)
            return updated_port
        else:
            # Create new port
            new_port = self._create_new_port(host_id, scan_id, port_data)
            self.db.add(new_port)
            nested = self.db.begin_nested()
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

            # Record initial port scan history
            self._record_port_scan_history(new_port.id, scan_id, port_data, is_new=True)
            return new_port

    def add_or_update_script(self, port_id: int, scan_id: int, script_data: Dict[str, Any]) -> Script:
        """Add or update a script for a port"""
        script_id = script_data.get('script_id')
        output = script_data.get('output', '')
        
        # Try to find existing script
        existing_script = self.db.query(Script).filter(
            Script.port_id == port_id,
            Script.script_id == script_id
        ).first()
        
        if existing_script:
            # Update existing script
            existing_script.output = output
            existing_script.last_seen = func.now()
            existing_script.scan_id = scan_id  # Update to latest scan
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
            # Update existing script
            existing_script.output = output
            existing_script.last_seen = func.now()
            existing_script.scan_id = scan_id  # Update to latest scan
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
            return new_script
    
    def _create_new_host(self, ip_address: str, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """Create a new host record"""
        host = Host(
            ip_address=ip_address,
            hostname=host_data.get('hostname'),
            state=host_data.get('state'),
            state_reason=host_data.get('state_reason'),
            os_name=host_data.get('os_name'),
            os_family=host_data.get('os_family'),
            os_generation=host_data.get('os_generation'),
            os_type=host_data.get('os_type'),
            os_vendor=host_data.get('os_vendor'),
            os_accuracy=host_data.get('os_accuracy'),
            last_updated_scan_id=scan_id
        )
        return host
    
    def _update_existing_host(self, host: Host, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """
        Update existing host with new data using conflict resolution strategy.
        Strategy: "Most recent wins" with some intelligence for better data.
        """
        updated = False
        
        # Update hostname if new one is provided and not null
        new_hostname = host_data.get('hostname')
        if new_hostname and (not host.hostname or len(new_hostname) > len(host.hostname or '')):
            host.hostname = new_hostname
            updated = True
        
        # Update state (most recent wins) — but 'unknown' carries no
        # information and must never clobber a known state.  gnmap, for
        # example, emits a host's Status: and Ports: on separate lines;
        # the Ports: line parses with state='unknown', and without this
        # guard it would overwrite the 'up' from the Status: line.
        new_state = host_data.get('state')
        if new_state and new_state != 'unknown' and new_state != host.state:
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
        if (not host.os_name or new_accuracy > (host.os_accuracy or 0)):
            if host_data.get('os_name'):
                host.os_name = host_data.get('os_name')
                host.os_family = host_data.get('os_family')
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
            last_updated_scan_id=scan_id,
            is_active=True
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
            port.state = new_state
            port.reason = port_data.get('reason')
            port.is_active = (new_state in ['open', 'filtered'])
        
        # Update service info if new scan has better information
        new_service_name = port_data.get('service_name')
        new_service_conf = port_data.get('service_conf', 0)
        
        # Use service info with higher confidence or if we don't have any
        if (not port.service_name or 
            new_service_conf > (port.service_conf or 0) or
            (new_service_name and len(new_service_name) > len(port.service_name or ''))):
            
            port.service_name = new_service_name
            port.service_product = port_data.get('service_product')
            port.service_version = port_data.get('service_version')
            port.service_extrainfo = port_data.get('service_extrainfo')
            port.service_method = port_data.get('service_method')
            port.service_conf = new_service_conf
        
        # Always update timestamps and scan reference
        port.last_seen = func.now()
        port.last_updated_scan_id = scan_id
        
        return port
    
    def _record_host_scan_history(self, host_id: int, scan_id: int, host_data: Dict[str, Any], is_new: bool = False):
        """Record that this scan discovered/updated this host"""
        existing_history = self._find_pending_history(HostScanHistory, host_id=host_id, scan_id=scan_id)
        if existing_history is None:
            existing_history = self.db.query(HostScanHistory).filter(
                HostScanHistory.host_id == host_id,
                HostScanHistory.scan_id == scan_id
            ).first()
        
        if existing_history:
            # Update existing history entry
            existing_history.state_at_scan = host_data.get('state')
            existing_history.hostname_at_scan = host_data.get('hostname')
            existing_history.os_info_updated = bool(host_data.get('os_name'))
        else:
            # Create new history entry
            history = HostScanHistory(
                host_id=host_id,
                scan_id=scan_id,
                state_at_scan=host_data.get('state'),
                hostname_at_scan=host_data.get('hostname'),
                os_info_updated=bool(host_data.get('os_name'))  # True if this scan provided OS info
            )
            self.db.add(history)
    
    def _record_port_scan_history(self, port_id: int, scan_id: int, port_data: Dict[str, Any], is_new: bool = False):
        """Record port state at time of this scan"""
        # Check if history entry already exists for this port+scan combination
        existing_history = self._find_pending_history(PortScanHistory, port_id=port_id, scan_id=scan_id)
        if existing_history is None:
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
        
        if existing_history:
            # Update existing history entry
            existing_history.state_at_scan = port_data.get('state')
            existing_history.service_info = json.dumps(service_info) if any(service_info.values()) else None
        else:
            # Create new history entry
            history = PortScanHistory(
                port_id=port_id,
                scan_id=scan_id,
                state_at_scan=port_data.get('state'),
                service_info=json.dumps(service_info) if any(service_info.values()) else None
            )
            self.db.add(history)

    def _find_pending_history(self, model: Type[TModel], **attrs: Any) -> Optional[TModel]:
        """Return a matching history row that hasn't been flushed yet."""
        for pending in self.db.new:
            if isinstance(pending, model) and all(getattr(pending, key, None) == value for key, value in attrs.items()):
                return pending
        return None
    
    # NOTE (code review): the former ``update_scan_statistics`` was removed
    # here — it had no callers anywhere in the app, so Scan.new_hosts /
    # updated_hosts / ports_discovered were never populated by the dedup
    # path and the method's two exists()-subquery counts were dead weight.
    # If those per-scan counters need surfacing again, recompute them where
    # the scan is finalized and verify the subqueries scale on large scans.

    def get_host_statistics(self) -> Dict[str, int]:
        """Get overall host statistics"""
        total_hosts = self.db.query(Host).count()
        active_hosts = self.db.query(Host).filter(Host.state == 'up').count()
        total_ports = self.db.query(Port).count()
        open_ports = self.db.query(Port).filter(Port.state == 'open').count()
        
        return {
            'total_hosts': total_hosts,
            'active_hosts': active_hosts,
            'total_ports': total_ports,
            'open_ports': open_ports
        }
