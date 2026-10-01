import logging
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session, joinedload

from app.db.models import Host, Scan, Scope, Subnet, HostSubnetMapping, HostScanHistory
from app.services.ip_trie import IPTrie

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

    def correlate_subnet(self, subnet_id: int) -> int:
        """Correlate every project host against a SINGLE subnet, replacing only
        that subnet's mappings.

        Used when one subnet is added or its CIDR edited.  Matching all project
        hosts against just this subnet is O(hosts), and touching only this
        subnet's mapping rows avoids the whole-project delete+reinsert that
        ``correlate_all_hosts_to_subnets`` does — which, on a large project,
        rewrites the entire ``HostSubnetMapping`` table (and contends with every
        concurrent read of it) on every single-subnet edit.

        A host's mappings to OTHER subnets are unaffected: those are maintained
        when the host is ingested (full correlate over all subnets) or when
        those subnets are themselves added/edited, so the invariant holds.
        """
        return self.correlate_subnets([subnet_id])

    def correlate_subnets(self, subnet_ids: Sequence[int]) -> int:
        """Correlate the project's hosts against SEVERAL subnets in one pass,
        replacing only those subnets' mappings (review 2026-10-01 R24).

        ``POST /scopes/{id}/subnets`` used to call ``correlate_subnet`` once
        per new subnet — up to 500 — and each call read every host of the
        project and committed: a 100-subnet paste on an 80k-host project was
        100 × an 80k-row fetch.  Here the hosts are read ONCE per project,
        matched against one trie holding all the named subnets, and the
        mappings are written and committed once.  Same result as the calls one
        after another, since each subnet's mappings depend only on its own
        CIDR.

        Returns the number of mappings written.  Commits (including whatever
        the caller has flushed, so the new subnets and their mappings land
        together or not at all).
        """
        ids = list(dict.fromkeys(i for i in subnet_ids if i is not None))
        if not ids:
            return 0
        found = (
            self.db.query(Subnet, Scope.project_id)
            .outerjoin(Scope, Scope.id == Subnet.scope_id)
            .filter(Subnet.id.in_(ids))
            .all()
        )
        if not found:
            return 0

        # One trie per project (in practice one: the subnets of one scope).
        by_project: Dict[Optional[int], IPTrie] = {}
        for subnet, project_id in found:
            # invalid CIDR is skipped (logged) internally
            by_project.setdefault(project_id, IPTrie()).add_subnet(subnet)

        mapping_set: Set[Tuple[int, int]] = set()
        for project_id, trie in by_project.items():
            host_query = self.db.query(Host.id, Host.ip_address)
            if project_id is not None:
                host_query = host_query.filter(Host.project_id == project_id)
            for host_id, ip_str in host_query.all():
                for subnet in trie.find_matching_subnets(ip_str):
                    mapping_set.add((host_id, subnet.id))

        # Replace ONLY these subnets' mappings.  A CIDR edit can change which
        # hosts match, so the scoped delete (not a whole-project wipe) is both
        # correct and cheap.
        found_ids = [subnet.id for subnet, _ in found]
        self.db.query(HostSubnetMapping).filter(
            HostSubnetMapping.subnet_id.in_(found_ids)
        ).delete(synchronize_session=False)

        if mapping_set:
            mapping_list = [{"host_id": hid, "subnet_id": sid} for hid, sid in mapping_set]
            INSERT_BATCH = 5000
            for i in range(0, len(mapping_list), INSERT_BATCH):
                self.db.bulk_insert_mappings(
                    HostSubnetMapping, mapping_list[i : i + INSERT_BATCH]
                )

        self.db.commit()
        logger.info(
            "Correlated %d subnet(s) to project hosts (%d mappings)", len(found_ids), len(mapping_set),
        )
        return len(mapping_set)

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

    # ------------------------------------------------------------------
    # Fast batch correlation
    # ------------------------------------------------------------------

    def _scan_project_id(self, scan_id: int) -> int | None:
        """The project a scan's hosts belong to.

        ``Scan.project_id`` is authoritative; a legacy scan row without one
        falls back to the project of the hosts the scan observed (a scan never
        spans projects, so the first is the only one).
        """
        scan_project = (
            self.db.query(Scan.project_id).filter(Scan.id == scan_id).scalar()
        )
        if scan_project is not None:
            return scan_project
        return (
            self.db.query(Host.project_id)
            .join(HostScanHistory, Host.id == HostScanHistory.host_id)
            .filter(HostScanHistory.scan_id == scan_id, Host.project_id.isnot(None))
            .limit(1)
            .scalar()
        )

    def _batch_correlate_hosts(self, scan_id: int | None, project_id: int | None = None) -> int:
        """
        Core batch-correlation implementation.

        Strategy:
        1. Load the in-scope subnets and build a radix trie (IPTrie) once —
           O(subnets * prefix_len).
        2. Fetch only (host_id, ip_address) tuples — not full ORM objects.
        3. Look each host up in the trie — O(32) for IPv4 / O(128) for IPv6 —
           collecting EVERY containing subnet (overlapping /24 + /16 both match).
        4. Bulk-delete old mappings and bulk-insert new ones.

        This replaces the prior O(hosts * subnets) integer double-loop, which
        ran inline on every scope mutation and was a real hazard on large,
        many-subnet projects (30k hosts * 1k subnets = 30M comparisons per
        edit).  The trie makes step 3 independent of the subnet count.

        Correctness note: the trie matches by address family, so an IPv4 host
        can no longer spuriously match an IPv6 ``::/0`` (the old raw-integer
        comparison did — it compared a 32-bit int against 128-bit ranges).  For
        same-family data the result set is identical.

        Project boundary (v2.342.0): a scan's hosts are matched ONLY against
        the subnets of the scan's own project.  The scan-time path used to
        build the trie from every subnet in the database, so a host in
        project B was mapped to project A's ``10.0.0.0/8`` — which made it
        "in scope" in B (scope coverage is derived from the mapping rows) and
        listed it among A's subnet hosts.  A scan whose project cannot be
        determined correlates nothing rather than against the world.
        """
        if scan_id is not None and project_id is None:
            project_id = self._scan_project_id(scan_id)
            if project_id is None:
                logger.warning(
                    "Scan %s has no resolvable project; skipping subnet correlation",
                    scan_id,
                )
                return 0

        # 1. Load in-scope subnets and build the trie -------------------
        subnet_query = self.db.query(Subnet)
        if project_id is not None:
            subnet_query = subnet_query.join(Scope, Subnet.scope_id == Scope.id).filter(Scope.project_id == project_id)
        subnets = subnet_query.all()

        trie = IPTrie()
        for s in subnets:
            trie.add_subnet(s)  # invalid CIDRs are skipped (logged) internally

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

        # 3. Match each host via the trie (all containing subnets) ------
        # With no subnets on the project the set stays empty and step 4 only
        # clears whatever these hosts still carry — mappings can only be
        # stale (a subnet's own deletion cascades) or, before the project
        # boundary above, cross-project.
        mapping_set: Set[Tuple[int, int]] = set()
        for host_id, ip_str in rows:
            for subnet in trie.find_matching_subnets(ip_str):
                mapping_set.add((host_id, subnet.id))

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
