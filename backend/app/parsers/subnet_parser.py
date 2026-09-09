import csv
import io
import ipaddress
from typing import List, Tuple
from sqlalchemy.orm import Session
from app.db.models import Subnet
from app.services.ip_trie import IPTrie

class SubnetParser:
    def __init__(self, db: Session):
        self.db = db
        self._trie = None  # Lazy-loaded IP trie

    # ------------------------------------------------------------------
    # Scope-file parsing (v2.326.0).  A scope file is a mix of subnets and
    # domain names: any row that isn't a CIDR/IP is treated as a name and
    # validated with the same normaliser DNSName uses, so ``*.example.com``,
    # a trailing dot or an IDN label all behave exactly as the domains
    # card would.  Subnet rows and domain rows are returned separately —
    # they land in different tables and name scope never confers subnet
    # scope.  Anything that is neither raises so a typo isn't dropped.
    # ------------------------------------------------------------------
    @staticmethod
    def _classify(raw: str, where: str) -> Tuple[str, str, bool]:
        """``(kind, value, include_subdomains)`` for one scope entry.

        ``kind`` is ``"subnet"`` (value normalised via ``ip_network``) or
        ``"domain"`` (value as written; the upsert normalises it).  Raises
        ``ValueError`` naming ``where`` when the entry is neither.
        """
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as net_err:
            # Lazy import: dns_name_service pulls the ORM models, which the
            # parser package shouldn't import at module load.
            from app.services.dns_name_service import InvalidName, normalize_fqdn

            try:
                _fqdn, kind = normalize_fqdn(raw)
            except InvalidName as name_err:
                raise ValueError(
                    f"Invalid entry on {where}: '{raw}' - not a subnet ({net_err}) "
                    f"and not a domain name ({name_err})"
                )
            return "domain", raw, kind == "wildcard"
        return "subnet", str(network), False

    def parse_scope_csv(
        self, file_content: str,
    ) -> Tuple[List[Tuple[str, List[str], str, str]], List[Tuple[str, bool, str]], int]:
        """Parse a scope CSV where each row is one entry:

            <subnet or domain>[, <label1> <label2> ...][, <description>][, <site>]

        Column 1 is a subnet (CIDR or single IP, normalized via
        ``ip_network(strict=False)`` so ``10.0.0.5`` → ``10.0.0.5/32``) or a
        domain name (``*.example.com`` declares the domain with
        include-subdomains); column 2 (optional) is one or more
        whitespace-delimited label names; column 3 (optional) is a free-text
        description; column 4 (optional) is the site/location.

        Returns ``(subnets, domains, domain_rows_with_subnet_only_columns)``:
        ``subnets`` is ``[(cidr, [labels], description, site), ...]`` with
        per-row labels deduped and empty strings for a missing
        description/site; ``domains`` is ``[(raw_domain, include_subdomains,
        description), ...]``.  Labels and site are subnet concepts — a domain
        row carrying them keeps its description and the count of such rows is
        returned so the caller can say they were ignored.

        Blank rows and ``#`` comments are skipped.  A first-row header is
        skipped: its first cell is either unparseable or a dotless word such
        as ``subnet`` / ``entry`` (a valid single-label name, but never a
        scope declaration on row 1).  An invalid entry on any later row
        raises so typos aren't silently dropped.
        """
        subnets: List[Tuple[str, List[str], str, str]] = []
        domains: List[Tuple[str, bool, str]] = []
        ignored_cols = 0
        reader = csv.reader(io.StringIO(file_content))
        for row_num, row in enumerate(reader, 1):
            if not row:
                continue
            raw = row[0].strip()
            if not raw or raw.startswith('#'):
                continue
            try:
                kind, value, include_sub = self._classify(raw, f"row {row_num}")
            except ValueError:
                if row_num == 1:
                    continue  # tolerate a header row
                raise
            if row_num == 1 and kind == "domain" and "." not in value:
                # ``subnet``, ``entry``, ``domain`` … are all valid
                # single-label names; on row 1 a dotless cell is a header.
                continue
            labels: List[str] = []
            if len(row) > 1 and row[1].strip():
                # whitespace-delimited; dedup preserving order; cap to the
                # SubnetLabel.name length (60).
                seen = set()
                for raw_label in row[1].split():
                    name = raw_label.strip()[:60]
                    if name and name not in seen:
                        seen.add(name)
                        labels.append(name)
            description = row[2].strip() if len(row) > 2 else ""
            site = (row[3].strip()[:255] if len(row) > 3 else "")
            if kind == "domain":
                if labels or site:
                    ignored_cols += 1
                domains.append((value, include_sub, description))
            else:
                subnets.append((value, labels, description, site))
        if not subnets and not domains:
            raise ValueError("No valid subnets or domains found in file")
        return subnets, domains, ignored_cols

    def parse_scope_list(
        self, file_content: str,
    ) -> Tuple[List[str], List[Tuple[str, bool, str]]]:
        """Parse a flat scope list: one subnet or domain per line.

        Each line is a CIDR/IP (validated with ``ip_network(strict=False)``
        so ``10.0.0.5`` is accepted and normalized to ``10.0.0.5/32``) or a
        domain name (``*.example.com`` declares include-subdomains).  Lines
        starting with ``#`` and blank lines are skipped so user comment
        files work too.  Returns ``(cidrs, domains)`` with ``domains`` as
        ``[(raw_domain, include_subdomains, ""), ...]``.
        """
        lines = file_content.strip().split('\n')
        cidrs: List[str] = []
        domains: List[Tuple[str, bool, str]] = []
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            kind, value, include_sub = self._classify(line, f"line {line_num}")
            if kind == "domain":
                domains.append((value, include_sub, ""))
            else:
                cidrs.append(value)
        if not cidrs and not domains:
            raise ValueError("No valid subnets or domains found in file")
        return cidrs, domains

    def parse_subnet_csv(self, file_content: str) -> List[Tuple[str, List[str], str, str]]:
        """Subnet-only CSV (see ``parse_scope_csv``); a domain row raises."""
        subnets, domains, _ = self.parse_scope_csv(file_content)
        if domains:
            raise ValueError(f"Invalid subnet: '{domains[0][0]}' is a domain name")
        if not subnets:
            raise ValueError("No valid subnets found in file")
        return subnets

    def parse_cidr_list(self, file_content: str) -> List[str]:
        """Subnet-only flat list (see ``parse_scope_list``); a domain line raises."""
        cidrs, domains = self.parse_scope_list(file_content)
        if domains:
            raise ValueError(f"Invalid subnet: '{domains[0][0]}' is a domain name")
        if not cidrs:
            raise ValueError("No valid subnets found in file")
        return cidrs

    def validate_subnet(self, cidr: str) -> bool:
        """Validate a single subnet CIDR notation."""
        try:
            ipaddress.ip_network(cidr, strict=False)
            return True
        except ValueError:
            return False
    
    def ip_in_subnet(self, ip_address: str, cidr: str) -> bool:
        """Check if an IP address belongs to a subnet."""
        try:
            ip = ipaddress.ip_address(ip_address)
            network = ipaddress.ip_network(cidr, strict=False)
            return ip in network
        except ValueError:
            return False
    
    def _get_trie(self) -> IPTrie:
        """Get or build the IP trie for efficient subnet lookups."""
        if self._trie is None:
            self._trie = IPTrie()
            subnets = self.db.query(Subnet).all()
            
            for subnet in subnets:
                self._trie.add_subnet(subnet)
        
        return self._trie
    
    def find_matching_subnets(self, ip_address: str) -> List[Subnet]:
        """Find all subnets that contain the given IP address using efficient trie lookup."""
        trie = self._get_trie()
        return trie.find_matching_subnets(ip_address)

    def get_all_subnets(self) -> List[Subnet]:
        """Get all subnets from the database."""
        return self.db.query(Subnet).all()

    def find_matching_subnets_from_list(self, ip_address: str, subnets: List[Subnet]) -> List[Subnet]:
        """
        Find matching subnets from a pre-loaded list.
        
        Note: This method now builds a temporary trie for efficiency when dealing with
        large subnet lists. For single lookups, use find_matching_subnets() instead.
        """
        # For small subnet lists, use the old linear method to avoid trie overhead
        if len(subnets) < 50:
            matching_subnets = []
            for subnet in subnets:
                if self.ip_in_subnet(ip_address, subnet.cidr):
                    matching_subnets.append(subnet)
            return matching_subnets
        
        # For larger lists, build a temporary trie
        temp_trie = IPTrie()
        for subnet in subnets:
            temp_trie.add_subnet(subnet)
        
        return temp_trie.find_matching_subnets(ip_address)
    
    def invalidate_trie_cache(self):
        """Invalidate the cached trie (call after subnet changes)."""
        self._trie = None
    
    def get_trie_stats(self) -> dict:
        """Get statistics about the current trie."""
        trie = self._get_trie()
        return trie.get_stats()