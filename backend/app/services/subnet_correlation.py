import ipaddress
import logging
import struct
import socket
import sys
from typing import Dict, List, Set, Tuple

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text

from app.db.models import Host, Scope, Subnet, HostSubnetMapping, HostScanHistory

logger = logging.getLogger(__name__)


class SubnetCorrelationService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def correlate_all_hosts_to_subnets(self, project_id: int = None) -> int:
        """Correlate every host in the database.  Uses the fast batch path."""
        return self._batch_correlate_hosts(scan_id=None, project_id=project_id)

    def correlate_scan_hosts_to_subnets(self, scan_id: int) -> int:
        """Correlate hosts from a specific scan (legacy entry point)."""
        return self._batch_correlate_hosts(scan_id=scan_id)

    def batch_correlate_scan_hosts_to_subnets(self, scan_id: int) -> int:
        """Correlate hosts from a specific scan (batch-optimised)."""
        return self._batch_correlate_hosts(scan_id=scan_id)

    def get_host_subnets(self, host_id: int) -> List[Subnet]:
        mappings = (
            self.db.query(HostSubnetMapping)
            .options(joinedload(HostSubnetMapping.subnet))
            .filter(HostSubnetMapping.host_id == host_id)
            .all()
        )
        return [m.subnet for m in mappings]

    def get_subnet_hosts(self, subnet_id: int) -> List[Host]:
        mappings = (
            self.db.query(HostSubnetMapping)
            .options(joinedload(HostSubnetMapping.host))
            .filter(HostSubnetMapping.subnet_id == subnet_id)
            .all()
        )
        return [m.host for m in mappings]

    def invalidate_subnet_cache(self):
        # No-op: the batch correlation path (_batch_correlate_hosts)
        # re-queries project-scoped subnets on every call, so there is no
        # cross-request cache to invalidate.  Retained as a stable no-op
        # because scopes.py calls it after subnet mutations — kept rather
        # than churning that caller.  (The former global, cross-project
        # SubnetParser trie + its single-host correlate path were removed:
        # the trie ignored project boundaries, and nothing called it.)
        return None

    # ------------------------------------------------------------------
    # Fast batch correlation
    # ------------------------------------------------------------------

    def _batch_correlate_hosts(self, scan_id: int | None, project_id: int | None = None) -> int:
        """
        Core batch-correlation implementation.

        Strategy:
        1. Load all subnets and pre-compute their integer ranges (network_int,
           broadcast_int, subnet_id) once.
        2. Fetch only (host_id, ip_address) tuples — not full ORM objects.
        3. Convert each IP to an integer once and test against ranges.
        4. Bulk-delete old mappings and bulk-insert new ones.

        For typical deployments with <1000 subnets this is O(hosts * subnets)
        with pure integer comparisons in Python — faster in practice than the
        trie approach because it avoids ipaddress object creation overhead and
        keeps the inner loop in simple integer math.
        """

        # 1. Load subnet ranges -----------------------------------------
        subnet_query = self.db.query(Subnet)
        if project_id is not None:
            subnet_query = subnet_query.join(Scope, Subnet.scope_id == Scope.id).filter(Scope.project_id == project_id)
        subnets = subnet_query.all()
        if not subnets:
            return 0

        # Pre-compute (network_int, broadcast_int, subnet_id) for each subnet
        ranges: List[Tuple[int, int, int]] = []
        for s in subnets:
            try:
                net = ipaddress.ip_network(s.cidr, strict=False)
                net_int = int(net.network_address)
                bcast_int = int(net.broadcast_address)
                ranges.append((net_int, bcast_int, s.id))
            except ValueError:
                continue

        if not ranges:
            return 0

        # 2. Fetch lightweight (host_id, ip_address) tuples -------------
        if scan_id is not None:
            host_query = (
                self.db.query(Host.id, Host.ip_address)
                .join(HostScanHistory, Host.id == HostScanHistory.host_id)
                .filter(HostScanHistory.scan_id == scan_id)
            )
        else:
            host_query = self.db.query(Host.id, Host.ip_address)
        if project_id is not None:
            host_query = host_query.filter(Host.project_id == project_id)
        rows = host_query.all()

        if not rows:
            return 0

        # 3. Match hosts to subnets using integer ranges ----------------
        mapping_set: Set[Tuple[int, int]] = set()

        for host_id, ip_str in rows:
            ip_int = _ip_to_int(ip_str)
            if ip_int is None:
                continue
            for net_int, bcast_int, subnet_id in ranges:
                if net_int <= ip_int <= bcast_int:
                    mapping_set.add((host_id, subnet_id))

        # 4. Bulk delete + insert in a single transaction ---------------
        host_ids = [hid for hid, _ in rows]
        # Delete in batches to avoid oversized IN clauses
        BATCH = 5000
        for i in range(0, len(host_ids), BATCH):
            batch = host_ids[i : i + BATCH]
            self.db.query(HostSubnetMapping).filter(
                HostSubnetMapping.host_id.in_(batch)
            ).delete(synchronize_session=False)

        if mapping_set:
            # Batch inserts to avoid deep SQLAlchemy expression trees that
            # can trigger "maximum recursion depth exceeded" on large scans.
            mapping_list = [{"host_id": hid, "subnet_id": sid} for hid, sid in mapping_set]
            INSERT_BATCH = 5000
            old_limit = sys.getrecursionlimit()
            try:
                if len(mapping_list) > INSERT_BATCH:
                    sys.setrecursionlimit(max(old_limit, 10000))
                for i in range(0, len(mapping_list), INSERT_BATCH):
                    self.db.bulk_insert_mappings(
                        HostSubnetMapping,
                        mapping_list[i : i + INSERT_BATCH],
                    )
            finally:
                sys.setrecursionlimit(old_limit)

        self.db.commit()

        logger.info(
            "Correlated %d hosts to %d subnets (%d mappings)",
            len(rows),
            len(subnets),
            len(mapping_set),
        )
        return len(mapping_set)


def _ip_to_int(ip_str: str) -> int | None:
    """Convert an IP address string to an integer.  ~10x faster than
    ipaddress.ip_address() for the common IPv4 case."""
    try:
        return struct.unpack("!I", socket.inet_aton(ip_str))[0]
    except OSError:
        pass
    # Fallback for IPv6
    try:
        return int(ipaddress.ip_address(ip_str))
    except ValueError:
        return None
