import ipaddress
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class SubnetCalculator:
    """Service for calculating subnet metrics and IP address statistics"""

    @staticmethod
    def calculate_subnet_metrics(cidr: str) -> Dict[str, int]:
        """
        Calculate comprehensive metrics for a subnet CIDR

        Returns:
            Dict containing total_addresses, usable_addresses, network_address,
            broadcast_address, subnet_mask, prefix_length
        """
        try:
            network = ipaddress.ip_network(cidr, strict=False)

            return {
                'total_addresses': network.num_addresses,
                'usable_addresses': max(0, network.num_addresses - 2) if network.num_addresses > 2 else network.num_addresses,
                'prefix_length': network.prefixlen,
                'network_address': str(network.network_address),
                'broadcast_address': str(network.broadcast_address) if network.num_addresses > 1 else str(network.network_address),
                'subnet_mask': str(network.netmask),
                'is_private': network.is_private,
                'is_multicast': network.is_multicast,
                'is_reserved': network.is_reserved
            }
        except (ipaddress.AddressValueError, ValueError) as e:
            logger.error(f"Invalid CIDR format '{cidr}': {str(e)}")
            return {
                'total_addresses': 0,
                'usable_addresses': 0,
                'prefix_length': 0,
                'network_address': '',
                'broadcast_address': '',
                'subnet_mask': '',
                'is_private': False,
                'is_multicast': False,
                'is_reserved': False
            }
