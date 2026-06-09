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

    def parse_subnet_csv(self, file_content: str) -> List[Tuple[str, List[str], str, str]]:
        """Parse a subnet CSV where each row is one entry:

            <subnet>[, <label1> <label2> ...][, <description>][, <site>]

        Column 1 is the subnet (CIDR or single IP, normalized via
        ``ip_network(strict=False)`` so ``10.0.0.5`` → ``10.0.0.5/32``);
        column 2 (optional) is one or more whitespace-delimited label names;
        column 3 (optional) is a free-text description; column 4 (optional)
        is the site/location.  Returns ``[(cidr, [labels], description,
        site), ...]`` with per-row labels deduped and empty strings for a
        missing description/site.

        Blank rows and ``#`` comments are skipped.  A first-row header whose
        first cell isn't a valid subnet (e.g. ``subnet,labels,description``)
        is skipped; an invalid subnet on any later row raises so typos aren't
        silently dropped.
        """
        out: List[Tuple[str, List[str], str, str]] = []
        reader = csv.reader(io.StringIO(file_content))
        for row_num, row in enumerate(reader, 1):
            if not row:
                continue
            cidr_raw = row[0].strip()
            if not cidr_raw or cidr_raw.startswith('#'):
                continue
            try:
                network = ipaddress.ip_network(cidr_raw, strict=False)
            except ValueError as e:
                if row_num == 1:
                    continue  # tolerate a header row
                raise ValueError(f"Invalid subnet on row {row_num}: '{cidr_raw}' - {e}")
            labels: List[str] = []
            if len(row) > 1 and row[1].strip():
                # whitespace-delimited; dedup preserving order; cap to the
                # SubnetLabel.name length (60).
                seen = set()
                for raw in row[1].split():
                    name = raw.strip()[:60]
                    if name and name not in seen:
                        seen.add(name)
                        labels.append(name)
            description = row[2].strip() if len(row) > 2 else ""
            site = (row[3].strip()[:255] if len(row) > 3 else "")
            out.append((str(network), labels, description, site))
        if not out:
            raise ValueError("No valid subnets found in file")
        return out

    def parse_cidr_list(self, file_content: str) -> List[str]:
        """Parse the file content into a validated list of CIDR strings.

        Used by the v2.9.4 upload path which appends to an existing
        default scope instead of creating a new one per file.  Each
        line is validated with ``ipaddress.ip_network(strict=False)``
        so single-address entries like ``10.0.0.5`` are accepted and
        normalized to ``10.0.0.5/32``.  Lines starting with ``#`` and
        blank lines are skipped so user comment files work too.
        """
        lines = file_content.strip().split('\n')
        valid_cidrs: List[str] = []
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                network = ipaddress.ip_network(line, strict=False)
                valid_cidrs.append(str(network))
            except ValueError as e:
                raise ValueError(f"Invalid subnet on line {line_num}: '{line}' - {str(e)}")
        if not valid_cidrs:
            raise ValueError("No valid subnets found in file")
        return valid_cidrs

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