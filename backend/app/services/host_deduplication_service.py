"""
Host Deduplication Service

Handles finding, creating, and updating host records to eliminate duplicates.
Implements conflict resolution and audit tracking for data changes.
"""

import json
import logging
from typing import Dict, Iterable, List, Optional, Tuple, Any
from sqlalchemy.orm import Session, noload
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Host, Port, Script, HostScript, HostScanHistory, PortScanHistory
from app.services import scan_sightings
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

    The set-based form (``HostDeduplicationService.upsert_ports_bulk``) takes
    only a service name (no confidence), so it states this rule for a new
    confidence of 0 — empty, or longer than an unconfident name — in SQL
    (``_REPLACES_SERVICE``); ``tests/test_review_2026_10_23b_ingestion.py``
    holds it to this function over every case.
    """
    existing_c, new_c = existing_conf or 0, new_conf or 0
    return (
        not existing_name
        or new_c > existing_c
        or (new_c == existing_c and bool(new_name) and len(new_name) > len(existing_name or ""))
    )


def port_history_service_info(port_data: Dict[str, Any]) -> Optional[str]:
    """``port_scan_history.service_info``: what the observation said of the
    service, as JSON, or NULL when it said nothing."""
    service_info = {
        'service_name': port_data.get('service_name'),
        'service_product': port_data.get('service_product'),
        'service_version': port_data.get('service_version'),
        'service_extrainfo': port_data.get('service_extrainfo'),
        'service_method': port_data.get('service_method'),
        'service_conf': port_data.get('service_conf'),
    }
    return json.dumps(service_info) if any(service_info.values()) else None


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
        # The ids of the ports in ``_ws_ports`` that this parse did not
        # create (those are ``_ws_fresh_port_ids``): "does the working set
        # hold this port" is a set lookup, not a walk over the host's ports.
        self._ws_port_ids: set = set()
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
        self._reset_working_set()
        self._name_cache = ObservationCache()

    def _reset_working_set(self) -> None:
        self._ws_host_id = None
        self._ws_scan_id = None
        self._ws_ports = {}
        self._ws_port_ids = set()
        self._ws_scripts = {}
        self._ws_fresh_port_ids = set()
        self._ws_history_checked = set()

    def forget_session_rows(self) -> None:
        """Forget every ORM row held here.  Call it after the caller detached
        its session's objects (``commit()`` + ``expunge_all()``, the Nessus
        batch boundary) — review 2026-10-01 R3.

        The caches outlive the session's objects: a detached, expired row
        raises ``DetachedInstanceError`` on its first attribute read.  For the
        name memo that error was caught and logged as a skipped name, so a
        name first seen in one batch lost every later name→address
        observation; a detached history row took its update with it.
        Everything is re-read from the database on next use — the rows are
        committed, so the indexed lookups find them."""
        from app.services.dns_name_service import ObservationCache

        self._pending_host_history = {}
        self._pending_port_history = {}
        self._reset_working_set()
        self._name_cache = ObservationCache()

    def release_committed_history(self) -> None:
        """Let go of the history rows that are already in the database (review
        2026-10-01 R7).  Call it between hosts, after a flush or commit.

        The pending-history dicts exist to find a row added in THIS parse
        before it was flushed.  Kept for the whole file they pinned one ORM
        object per host and per port in the identity map — 800k ports is 800k
        objects, every one expired and reloaded by each heartbeat commit.  A
        flushed row is found by the uq_host_scan / uq_port_scan lookup on a
        repeat, so only rows not yet flushed stay.  The working set goes too:
        it vouches "this port has no history row yet", which stops being true
        once the row it would have found here is dropped."""
        from sqlalchemy import inspect as sa_inspect

        for cache in (self._pending_host_history, self._pending_port_history):
            for key in [k for k, row in cache.items() if sa_inspect(row).persistent]:
                del cache[key]
        self._reset_working_set()

    def find_or_create_host(self, ip_address: str, scan_id: int, host_data: Dict[str, Any], project_id: int) -> Host:
        """
        Find existing host by IP within ``project_id`` or create new one.
        Updates existing host with new information using conflict resolution.
        A host always belongs to a project (``hosts_v2.project_id`` is NOT
        NULL since e3b7a1c9d5f0); there is no project-less lookup.

        Handles concurrent inserts: if another session creates the same IP
        between our SELECT and INSERT, we catch the UniqueViolation, rollback
        the failed flush, and merge with the now-existing row.

        v2.322.0 — every name the scan reported for this address is recorded
        as a name→address observation (``dns_records``) on the way out, so a
        load balancer's forty vhosts all survive as relationships instead of
        one display name plus thirty-nine "conflicts".
        """
        if project_id is None:
            raise ValueError("find_or_create_host needs the project the host belongs to")
        address, written = self.host_address(ip_address)
        host = self._find_or_create_host_inner(address, scan_id, host_data, project_id, written=written)
        self._record_name_observations(host, address, scan_id, host_data)
        return host

    @staticmethod
    def host_address(value) -> Tuple[str, Optional[str]]:
        """``(canonical address, the spelling as written when it differs)``
        for a host's address, or ValueError for anything that is not one
        (review 2026-10-10, S5).

        A host is one row per address, so the address is stored the one way
        ``parser_utils.normalize_ip`` writes it — an IPv6 address written two
        ways was two hosts — and a value that is no address at all (a
        ``.nessus`` ReportHost named by a hostname) never becomes a host.  A
        row stored before this in another spelling is still found by it
        (the second value), so nothing already imported is split."""
        from app.parsers.parser_utils import normalize_ip

        address = normalize_ip(str(value) if value is not None else None)
        if address is None:
            raise ValueError(f"Not an IP address: {value!r}")
        written = str(value).strip()
        return address, (written if written != address else None)

    @staticmethod
    def address_is(address: str, written: Optional[str]):
        """The lookup of a host by address: the canonical spelling, and the
        one it was written in when that differs (a row stored that way
        before addresses were normalised) — the canonical row first.  Also
        ``parser_utils.resolve_host_cached``'s (the web parsers)."""
        if not written:
            return Host.ip_address == address, Host.id
        from sqlalchemy import case

        return Host.ip_address.in_([address, written]), case((Host.ip_address == address, 0), else_=1)

    def _record_name_observations(self, host: Host, ip_address: str, scan_id: int, host_data: Dict[str, Any]) -> None:
        """Persist ``host_data['hostnames']`` (a list of ``(name, kind)``) and
        the plain ``host_data['hostname']`` as observations about names.

        ``kind`` is the nmap hostname type: ``'PTR'`` (reverse DNS → PTR
        observation), ``'user'`` (the operator gave the scanner this name and
        it resolved to the address → A/AAAA observation), anything else →
        SCANNER ("a scanner reported this name for this address").
        """
        project_id = host.project_id
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
        from app.parsers.parser_utils import write_record
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
            # One record (``write_record``) so a bad name can't poison the
            # host's transaction; the journal lets the cache forget what the
            # rollback discarded.
            journal: List[Tuple[str, object]] = []
            outcome = write_record(
                self.db,
                lambda name=name, record_type=record_type, journal=journal: record_observation(
                    self.db, project_id=project_id, name=name, record_type=record_type,
                    value=ip_address, scan_id=scan_id, cache=self._name_cache, journal=journal,
                ),
                on_rollback=lambda journal=journal: self._name_cache.forget(journal),
            )
            if outcome.error is not None:
                logger.warning("name observation %r for %s skipped: %s", name, ip_address, outcome.error)

    def _find_or_create_host_inner(
        self, ip_address: str, scan_id: int, host_data: Dict[str, Any], project_id: int,
        *, written: Optional[str] = None,
    ) -> Host:
        same_address, canonical_first = self.address_is(ip_address, written)
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
            .filter(same_address, Host.project_id == project_id)
        )
        existing_host = host_query.order_by(canonical_first).first()

        if existing_host:
            return self._update_and_record_existing_host(existing_host, scan_id, host_data)
        else:
            # Create new host
            new_host = self._create_new_host(ip_address, scan_id, host_data)
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
                        .filter(same_address, Host.project_id == project_id)
                    )
                    existing_host = fallback_query.order_by(canonical_first).first()
                    if existing_host:
                        updated_host = self._update_and_record_existing_host(
                            existing_host, scan_id, host_data
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
    
    def find_or_create_port(
        self, host_id: int, scan_id: int, port_data: Dict[str, Any], *, isolated: bool = False,
    ) -> Port:
        """
        Find existing port by host_id + port_number + protocol or create new one.
        Updates existing port with new information.

        ``isolated=True`` (review 2026-10-01 R7) is the caller's statement that
        it wraps this host in its OWN savepoint and, on an ``IntegrityError``,
        rolls that back, calls ``discard_rolled_back_state()`` and processes
        the host again.  The new port is then inserted without a savepoint of
        its own: SAVEPOINT + RELEASE around every new port was two extra
        statements per port (1.6M on an 800k-port file) to cover a concurrent
        insert the caller's retry covers as well.  The default keeps the
        per-port savepoint for callers that have no such retry.
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
            if isolated:
                # The caller's savepoint is the isolation; a lost race
                # surfaces as IntegrityError for its retry.
                self.db.add(new_port)
                self.db.flush()  # Get the ID
                self._ws_ports[(port_number, protocol)] = new_port
                self._ws_fresh_port_ids.add(new_port.id)
                self._record_port_scan_history(new_port.id, scan_id, port_data, is_new=True)
                return new_port
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
                        self._ws_port_ids.add(updated_port.id)
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
        # One port stands for all of them (a commit + expunge detaches every
        # one) — taken without copying the host's ports, which made a re-scan
        # of a 65,535-port host quadratic (review 2026-10-22, nitpick).
        first = next(iter(self._ws_ports.values()), None)
        if (
            self._ws_host_id == host_id and self._ws_scan_id == scan_id
            and (first is None or first in self.db)
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
            self._ws_port_ids = set()
            return
        ports = self.db.query(Port).filter(Port.host_id == host_id).all()
        self._ws_ports = {(p.port_number, p.protocol): p for p in ports}
        port_ids = [p.id for p in ports]
        self._ws_port_ids = set(port_ids)
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
        return port_id in self._ws_fresh_port_ids or port_id in self._ws_port_ids

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
            # scan that FIRST recorded it, and this scan's report of it is a
            # sighting — written here, in the caller's record savepoint, so
            # a record that is rolled back leaves none.
            existing_script.output = output
            existing_script.last_seen = func.now()
            scan_sightings.see(self.db, scan_sightings.SCRIPT, existing_script.id, scan_id)
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
            scan_sightings.see(self.db, scan_sightings.SCRIPT, new_script.id, scan_id)
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
            # scan_id stays the first recorder; this scan's report is a
            # sighting (see add_or_update_script).
            existing_script.output = output
            existing_script.last_seen = func.now()
            scan_sightings.see(self.db, scan_sightings.HOST_SCRIPT, existing_script.id, scan_id)
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
            scan_sightings.see(self.db, scan_sightings.HOST_SCRIPT, new_script.id, scan_id)
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

    def _update_and_record_existing_host(self, host: Host, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """Update a host that already exists and record that this scan saw it
        — and whether the update changed its DISPLAY name
        (``host_scan_history.hostname_changed``).  The name is compared
        across the update, so whatever in ``_update_existing_host`` writes it
        (today only ``apply_hostname_candidate``) is counted, for every tool
        that reaches the inventory through ``find_or_create_host``."""
        name_before = host.hostname or None
        updated_host = self._update_existing_host(host, scan_id, host_data)
        self._record_host_scan_history(
            updated_host.id, scan_id, host_data,
            renamed=(updated_host.hostname or None) != name_before,
        )
        return updated_host

    def note_hostname_changed(self, host_id: int, scan_id: int, hostname: Optional[str] = None) -> None:
        """A parser changed the display name of a host it already brought
        through ``find_or_create_host`` in THIS scan, outside that call
        (NetExec fills an empty name from a later line of the same host).
        Marks the scan's history row through its one writer here — and,
        given the new ``hostname``, records it as the name at this scan (third
        review 2026-10-15, N14: the row kept the earlier one).  Marks nothing
        when the scan created the host."""
        self._record_host_scan_history(host_id, scan_id, {"hostname": hostname} if hostname else {}, renamed=True)

    def _update_existing_host(self, host: Host, scan_id: int, host_data: Dict[str, Any]) -> Host:
        """
        Update existing host with new data using conflict resolution strategy.
        Strategy: "Most recent wins" with some intelligence for better data.
        """
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
        if new_hostname:
            # Called for its effect on ``host``; whether it replaced the name
            # is not needed here.
            apply_hostname_candidate(
                host, new_hostname, host_data.get('hostname_source') or 'scanner',
            )

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

        # Update OS information if new scan has higher accuracy or we don't have OS info
        new_accuracy = host_data.get('os_accuracy', 0)
        new_os = host_data.get('os_name')
        if new_os and host.os_name and new_os != host.os_name:
            self._record_conflict('host', host.id, 'os_name', host.os_name, new_os, prior_scan, scan_id)
        # An operator's correction (``os_source == 'operator'``, set by
        # ``PATCH /agent/hosts/{id}``) is never replaced by a scan, however
        # accurate its guess: the disagreement is the conflict row above.
        # The same rule as the display name's operator rank.
        if host.os_source == 'operator':
            pass
        elif (not host.os_name or new_accuracy > (host.os_accuracy or 0)):
            if host_data.get('os_name'):
                host.os_name = host_data.get('os_name')
                host.os_family = host_data.get('os_family') or os_family_from_name(host_data.get('os_name'))
                host.os_generation = host_data.get('os_generation')
                host.os_type = host_data.get('os_type')
                host.os_vendor = host_data.get('os_vendor')
                host.os_accuracy = new_accuracy
        
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
            # Which import inserted the row: how a failed import's cleanup
            # finds the ports it created.
            created_scan_id=scan_id,
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
        # canonical rule (``upsert_ports_bulk`` states it in SQL); see should_replace_service.
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
    
    def _record_host_scan_history(
        self, host_id: int, scan_id: int, host_data: Dict[str, Any], is_new: bool = False,
        renamed: bool = False,
    ):
        """Record that this scan discovered/updated this host.  ``renamed``:
        this record changed the display name of a host that already existed
        (``hostname_changed``; never on the row of the scan that created the
        host, never turned back to false)."""
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
            if renamed and not existing_history.host_created:
                existing_history.hostname_changed = True
        else:
            # Create new history entry
            history = HostScanHistory(
                host_id=host_id,
                scan_id=scan_id,
                state_at_scan=host_data.get('state'),
                hostname_at_scan=host_data.get('hostname'),
                os_info_updated=bool(host_data.get('os_name')),  # True if this scan provided OS info
                host_created=is_new,  # dedup create/update decision — ground truth
                hostname_changed=renamed and not is_new,
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

        service_info = port_history_service_info(port_data)

        # v2.333.0 — typed copy of the name inside service_info (what /scans
        # counts as "service identified"), kept in lockstep with the JSON.
        service_name = port_data.get('service_name') or None

        if existing_history:
            # Update existing history entry
            existing_history.state_at_scan = port_data.get('state')
            existing_history.service_info = service_info
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
                service_info=service_info,
                service_name=service_name,
                port_created=is_new,  # dedup create/update decision — ground truth
            )
            self.db.add(history)
            self._pending_port_history[key] = history

    # ------------------------------------------------------------------
    # The set-based form of the same rules (review 2026-10-23b, ING-7)
    #
    # masscan writes tens of thousands of hosts per file, a few statements
    # per 500 hosts instead of several per host.  These are the rules of
    # ``find_or_create_host`` / ``find_or_create_port`` for an observation
    # that says a host is up and a port is in a state, with at most a service
    # NAME (no confidence, product or version): the address in its one
    # spelling, the state change recorded as a conflict, the reason and
    # service merges of ``_update_existing_port``, the created/updated
    # decision in the history rows.  They lived as a second copy in
    # masscan_parser and had drifted (no conflict row, no address check).
    # ``tests/test_review_2026_10_23b_ingestion.py`` holds the bulk form to
    # the per-row one.  PostgreSQL only (``ON CONFLICT``, ``xmax``).
    # ------------------------------------------------------------------

    def upsert_hosts_bulk(
        self, project_id: int, scan_id: int, addresses: Dict[str, Iterable[str]], *, state: str = 'up',
    ) -> Dict[str, int]:
        """Find or create one host per address and record that ``scan_id``
        saw it in ``state``; returns ``{address: host_id}``.

        ``addresses`` maps each address, already in ``host_address``'s
        canonical spelling, to the other spellings the file wrote it in — so
        a row stored in one of those before addresses were normalised is
        found, as ``address_is`` finds it."""
        if project_id is None:
            raise ValueError("upsert_hosts_bulk needs the project the hosts belong to")
        if not addresses:
            return {}
        from app.parsers.parser_utils import normalize_ip

        wanted = set(addresses)
        spellings = wanted.union(*(set(v) for v in addresses.values()))
        found: Dict[str, Host] = {}
        for row in (
            self.db.query(Host.id, Host.ip_address, Host.state, Host.last_updated_scan_id)
            .filter(Host.project_id == project_id, Host.ip_address.in_(spellings))
        ):
            address = normalize_ip(row.ip_address)
            # The canonical row first, as ``address_is`` orders it.
            if address in wanted and (address not in found or row.ip_address == address):
                found[address] = row

        # ``_update_existing_host``'s state rule: a held 'unknown' is a blank
        # being filled in; any other different state is a disagreement.
        self._record_state_conflicts_bulk([
            (row.id, row.state, row.last_updated_scan_id) for row in found.values()
            if row.state and row.state != 'unknown' and row.state != state
        ], state, scan_id)
        host_ids = {address: row.id for address, row in found.items()}
        if host_ids:
            self.db.execute(
                text(
                    "UPDATE hosts_v2 SET last_seen = NOW(), last_updated_scan_id = :scan_id, state = :state "
                    "WHERE id = ANY(:ids)"
                ),
                {"scan_id": scan_id, "state": state, "ids": list(host_ids.values())},
            )

        created: set = set()
        new = [address for address in addresses if address not in host_ids]
        if new:
            params: Dict[str, Any] = {"scan_id": scan_id, "project_id": project_id, "state": state}
            values = []
            for idx, address in enumerate(new):
                params[f"ip_{idx}"] = address
                values.append(f"(:ip_{idx}, :state, :scan_id, :project_id)")
            # (xmax = 0) is true only for a row this INSERT created; a row the
            # DO UPDATE branch touched (a concurrent writer inserted it after
            # the SELECT) carries the updating transaction's xid.  That is the
            # created-vs-updated decision ``host_created`` records.  DO UPDATE
            # returns every row, so each new address comes back with its id.
            result = self.db.execute(text(
                "INSERT INTO hosts_v2 (ip_address, state, last_updated_scan_id, project_id) "
                "VALUES " + ", ".join(values) + " "
                "ON CONFLICT (project_id, ip_address) DO UPDATE SET "
                "last_seen = NOW(), last_updated_scan_id = :scan_id, state = :state "
                "RETURNING id, ip_address, (xmax = 0) AS inserted"
            ), params)
            for row in result:
                host_ids[row.ip_address] = row.id
                if row.inserted:
                    created.add(row.ip_address)

        params = {"scan_id": scan_id, "state": state}
        values = []
        for idx, (address, host_id) in enumerate(host_ids.items()):
            params[f"hid_{idx}"] = host_id
            values.append(f"(:hid_{idx}, :scan_id, :state, {'true' if address in created else 'false'})")
        if values:
            self.db.execute(text(
                "INSERT INTO host_scan_history (host_id, scan_id, state_at_scan, host_created) "
                "VALUES " + ", ".join(values) + " "
                "ON CONFLICT (host_id, scan_id) DO NOTHING"
            ), params)
        self.db.flush()
        return host_ids

    def _record_state_conflicts_bulk(
        self, conflicts: List[Tuple[int, str, Optional[int]]], new_state: str, scan_id: int,
    ) -> None:
        """``_record_conflict`` for many hosts' ``state`` at once:
        ``(host_id, held state, the scan that set it)``.  Idempotent on the
        same disagreement, as there: a repeat refreshes its scan and time."""
        if not conflicts:
            return
        from sqlalchemy import insert, update
        from app.db.models_confidence import ConflictHistory

        by_previous: Dict[str, List[Tuple[int, Optional[int]]]] = {}
        for host_id, previous, prior_scan in conflicts:
            by_previous.setdefault(previous, []).append((host_id, prior_scan))
        for previous, hosts in by_previous.items():
            same = (
                ConflictHistory.host_id.in_([host_id for host_id, _ in hosts]),
                ConflictHistory.port_id.is_(None),
                ConflictHistory.field_name == 'state',
                ConflictHistory.previous_value == previous,
                ConflictHistory.new_value == new_state,
            )
            known = {
                host_id for (host_id,) in self.db.query(ConflictHistory.host_id).filter(*same).distinct()
            }
            if known:
                self.db.execute(
                    update(ConflictHistory).where(*same, ConflictHistory.host_id.in_(known))
                    .values(new_scan_id=scan_id, resolved_at=func.now())
                )
            fresh = [
                {"host_id": host_id, "port_id": None, "field_name": 'state', "previous_value": previous,
                 "new_value": new_state, "previous_scan_id": prior_scan, "new_scan_id": scan_id}
                for host_id, prior_scan in hosts if host_id not in known
            ]
            if fresh:
                self.db.execute(insert(ConflictHistory), fresh)

    # Each port row binds 7 parameters; PostgreSQL caps a statement at
    # 65 535, so a 500-host batch of a wide-port scan (-p0-65535) overflows
    # one INSERT.  5 000 rows × 7 = 35 000, comfortably bounded.
    PORT_UPSERT_CHUNK = 5000

    # ``should_replace_service`` for a new name with no confidence: take it
    # when nothing is held, or when it is longer than a name that is itself
    # unconfident — never over an identification with confidence.  The whole
    # service block moves with the name, as in ``_update_existing_port``.
    _REPLACES_SERVICE = (
        "(COALESCE(ports_v2.service_name, '') = '' "
        "OR (NULLIF(EXCLUDED.service_name, '') IS NOT NULL "
        "AND COALESCE(ports_v2.service_conf, 0) = 0 "
        "AND length(EXCLUDED.service_name) > length(ports_v2.service_name)))"
    )

    def upsert_ports_bulk(self, scan_id: int, rows: List[Tuple[int, Dict[str, Any]]]) -> None:
        """Find or create each ``(host_id, port_data)`` port and record that
        ``scan_id`` saw it; one row per port (the caller merges a file's
        repeats).  ``port_data``: ``port_number``, ``protocol``, ``state``,
        and optionally ``reason`` and ``service_name`` — no confidence,
        product or version (``find_or_create_port`` takes those)."""
        for row in rows:
            extra = set(row[1]) - {'port_number', 'protocol', 'state', 'reason', 'service_name'}
            if extra:
                raise ValueError(f"upsert_ports_bulk takes no {sorted(extra)}; use find_or_create_port")
        for start in range(0, len(rows), self.PORT_UPSERT_CHUNK):
            self._upsert_ports_chunk(scan_id, rows[start:start + self.PORT_UPSERT_CHUNK])

    def _upsert_ports_chunk(self, scan_id: int, rows: List[Tuple[int, Dict[str, Any]]]) -> None:
        if not rows:
            return
        params: Dict[str, Any] = {"scan_id": scan_id}
        values = []
        for idx, (host_id, port_data) in enumerate(rows):
            state = port_data.get('state')
            params.update({
                f"hid_{idx}": host_id,
                f"pn_{idx}": port_data['port_number'],
                f"proto_{idx}": port_data.get('protocol', 'tcp'),
                f"st_{idx}": state,
                f"rsn_{idx}": port_data.get('reason') or None,
                f"svc_{idx}": port_data.get('service_name'),
                f"act_{idx}": port_state_is_active(state),
            })
            values.append(
                f"(:hid_{idx}, :pn_{idx}, :proto_{idx}, :st_{idx}, :rsn_{idx}, :svc_{idx}, "
                f":scan_id, :scan_id, :act_{idx})"
            )
        replaces = self._REPLACES_SERVICE

        def service(column: str, replaced_by: str) -> str:
            return f"{column} = CASE WHEN {replaces} THEN {replaced_by} ELSE ports_v2.{column} END"

        result = self.db.execute(text(
            "INSERT INTO ports_v2 "
            # created_scan_id is written by the INSERT only (the DO UPDATE
            # leaves it): which import created the row, for the cleanup of a
            # failed one.
            "(host_id, port_number, protocol, state, reason, service_name, "
            "last_updated_scan_id, created_scan_id, is_active) "
            "VALUES " + ", ".join(values) + " "
            "ON CONFLICT (host_id, port_number, protocol) DO UPDATE SET "
            "state = EXCLUDED.state, is_active = EXCLUDED.is_active, last_seen = NOW(), "
            # The reason belongs to the observation that set the state: none
            # given keeps the stored one while the state is unchanged; a
            # changed state drops a reason that described the old one.
            "reason = CASE "
            "WHEN EXCLUDED.reason IS NOT NULL THEN EXCLUDED.reason "
            "WHEN ports_v2.state IS DISTINCT FROM EXCLUDED.state THEN NULL "
            "ELSE ports_v2.reason END, "
            "last_updated_scan_id = EXCLUDED.last_updated_scan_id, "
            + ", ".join([
                service("service_name", "EXCLUDED.service_name"),
                service("service_product", "NULL"),
                service("service_version", "NULL"),
                service("service_extrainfo", "NULL"),
                service("service_method", "NULL"),
                service("service_tunnel", "NULL"),
                service("service_conf", "0"),
            ]) + " "
            # (xmax = 0): this INSERT created the row — port_created.
            "RETURNING id, host_id, port_number, protocol, (xmax = 0) AS inserted"
        ), params)
        stored = list(result)
        reported = {
            (host_id, pd['port_number'], pd.get('protocol', 'tcp')): pd for host_id, pd in rows
        }
        if not stored:
            return
        params = {"scan_id": scan_id}
        values = []
        for idx, row in enumerate(stored):
            pd = reported[(row.host_id, row.port_number, row.protocol)]
            # What THIS scan reported (the merge above may have kept an older,
            # longer name).
            params.update({
                f"pid_{idx}": row.id,
                f"st_{idx}": pd.get('state'),
                f"svc_{idx}": pd.get('service_name') or None,
                f"info_{idx}": port_history_service_info(pd),
            })
            values.append(
                f"(:pid_{idx}, :scan_id, :st_{idx}, :svc_{idx}, :info_{idx}, "
                f"{'true' if row.inserted else 'false'})"
            )
        self.db.execute(text(
            "INSERT INTO port_scan_history "
            "(port_id, scan_id, state_at_scan, service_name, service_info, port_created) "
            "VALUES " + ", ".join(values) + " "
            "ON CONFLICT (port_id, scan_id) DO NOTHING"
        ), params)
        self.db.flush()

    # NOTE (code review): the former ``update_scan_statistics`` was removed
    # here — it had no callers anywhere in the app, so Scan.new_hosts /
    # updated_hosts / ports_discovered were never populated by the dedup
    # path and the method's two exists()-subquery counts were dead weight.
    # If those per-scan counters need surfacing again, recompute them where
    # the scan is finalized and verify the subqueries scale on large scans.

