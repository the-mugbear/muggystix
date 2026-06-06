"""
IP Address Trie (Radix Tree) for efficient subnet lookups.

This implementation provides O(1) average case lookup time for subnet matching,
dramatically improving performance over linear searches through subnet lists.
"""

import ipaddress
import logging
from typing import List, Optional, Set, Union
from app.db.models import Subnet

logger = logging.getLogger(__name__)


class TrieNode:
    """A node in the IP trie representing a network prefix."""
    
    def __init__(self):
        self.subnets: Set[Subnet] = set()  # Subnets that match this exact prefix
        self.children = {}  # 0 or 1 for binary trie
        self.is_terminal = False  # True if this node represents a complete subnet
    
    def add_subnet(self, subnet: Subnet):
        """Add a subnet to this node."""
        self.subnets.add(subnet)
        self.is_terminal = True
    
    def get_all_subnets(self) -> Set[Subnet]:
        """Get all subnets at this node and all parent nodes."""
        return self.subnets.copy()


class IPTrie:
    """
    IP Address Trie for efficient subnet lookups.
    
    Supports both IPv4 and IPv6 addresses.
    Time complexity: O(32) for IPv4, O(128) for IPv6 lookups.
    """
    
    def __init__(self):
        self.ipv4_root = TrieNode()
        self.ipv6_root = TrieNode()
    
    def clear(self):
        """Clear all subnets from the trie."""
        self.ipv4_root = TrieNode()
        self.ipv6_root = TrieNode()
    
    def add_subnet(self, subnet: Subnet):
        """Add a subnet to the trie."""
        try:
            network = ipaddress.ip_network(subnet.cidr, strict=False)
            
            if isinstance(network, ipaddress.IPv4Network):
                self._add_ipv4_subnet(network, subnet)
            elif isinstance(network, ipaddress.IPv6Network):
                self._add_ipv6_subnet(network, subnet)
                
        except ValueError as e:
            # Skip invalid CIDR blocks
            logger.warning(f"Invalid CIDR block {subnet.cidr}: {e}")
    
    def _add_ipv4_subnet(self, network: ipaddress.IPv4Network, subnet: Subnet):
        """Add an IPv4 subnet to the trie."""
        # Convert network address to 32-bit integer
        network_int = int(network.network_address)
        prefix_length = network.prefixlen
        
        current = self.ipv4_root
        
        # Traverse the trie based on network bits
        for i in range(prefix_length):
            # Extract bit at position (31-i) from left
            bit = (network_int >> (31 - i)) & 1
            
            if bit not in current.children:
                current.children[bit] = TrieNode()
            
            current = current.children[bit]
        
        # Add subnet at the final node
        current.add_subnet(subnet)
    
    def _add_ipv6_subnet(self, network: ipaddress.IPv6Network, subnet: Subnet):
        """Add an IPv6 subnet to the trie."""
        # Convert network address to 128-bit integer
        network_int = int(network.network_address)
        prefix_length = network.prefixlen
        
        current = self.ipv6_root
        
        # Traverse the trie based on network bits
        for i in range(prefix_length):
            # Extract bit at position (127-i) from left
            bit = (network_int >> (127 - i)) & 1
            
            if bit not in current.children:
                current.children[bit] = TrieNode()
            
            current = current.children[bit]
        
        # Add subnet at the final node
        current.add_subnet(subnet)
    
    def find_matching_subnets(self, ip_address: str) -> List[Subnet]:
        """
        Find all subnets that contain the given IP address.
        
        Args:
            ip_address: IP address to look up
            
        Returns:
            List of Subnet objects that contain the IP address
        """
        try:
            ip = ipaddress.ip_address(ip_address)
            
            if isinstance(ip, ipaddress.IPv4Address):
                return self._find_ipv4_matches(ip)
            elif isinstance(ip, ipaddress.IPv6Address):
                return self._find_ipv6_matches(ip)
            else:
                return []
                
        except ValueError:
            # Invalid IP address
            return []
    
    def _find_ipv4_matches(self, ip: ipaddress.IPv4Address) -> List[Subnet]:
        """Find all IPv4 subnets that contain the given IP."""
        ip_int = int(ip)
        matching_subnets = set()
        current = self.ipv4_root
        
        # Add any subnets at the root (0.0.0.0/0)
        matching_subnets.update(current.subnets)
        
        # Traverse the trie following the IP's bits
        for i in range(32):  # 32 bits for IPv4
            bit = (ip_int >> (31 - i)) & 1
            
            if bit not in current.children:
                break
                
            current = current.children[bit]
            # Add any subnets at this level (longer prefix matches)
            matching_subnets.update(current.subnets)
        
        return list(matching_subnets)
    
    def _find_ipv6_matches(self, ip: ipaddress.IPv6Address) -> List[Subnet]:
        """Find all IPv6 subnets that contain the given IP."""
        ip_int = int(ip)
        matching_subnets = set()
        current = self.ipv6_root
        
        # Add any subnets at the root (::0/0)
        matching_subnets.update(current.subnets)
        
        # Traverse the trie following the IP's bits
        for i in range(128):  # 128 bits for IPv6
            bit = (ip_int >> (127 - i)) & 1
            
            if bit not in current.children:
                break
                
            current = current.children[bit]
            # Add any subnets at this level (longer prefix matches)
            matching_subnets.update(current.subnets)
        
        return list(matching_subnets)
    
    def get_stats(self) -> dict:
        """Get statistics about the trie."""
        return {
            'ipv4_nodes': self._count_nodes(self.ipv4_root),
            'ipv6_nodes': self._count_nodes(self.ipv6_root),
            'total_subnets': self._count_subnets(self.ipv4_root) + self._count_subnets(self.ipv6_root)
        }
    
    def _count_nodes(self, root: TrieNode) -> int:
        """Iteratively count nodes in the trie."""
        count = 0
        stack = [root]
        while stack:
            node = stack.pop()
            count += 1
            stack.extend(node.children.values())
        return count

    def _count_subnets(self, root: TrieNode) -> int:
        """Iteratively count subnets in the trie."""
        count = 0
        stack = [root]
        while stack:
            node = stack.pop()
            count += len(node.subnets)
            stack.extend(node.children.values())
        return count