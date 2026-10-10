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

    @staticmethod
    def calculate_utilization_percentage(discovered_count: int, cidr: str) -> float:
        """Calculate discovery utilization percentage for a subnet"""
        metrics = SubnetCalculator.calculate_subnet_metrics(cidr)
        usable_addresses = metrics['usable_addresses']

        if usable_addresses == 0:
            return 0.0

        return min(100.0, (discovered_count / usable_addresses) * 100)

    @staticmethod
    def get_subnet_risk_level(utilization_percentage: float, discovered_count: int) -> Dict[str, str]:
        """
        Determine risk level based on subnet utilization and discovery count

        Returns dict with risk_level and risk_description
        """
        if utilization_percentage == 0:
            return {
                'risk_level': 'unknown',
                'risk_description': 'No hosts discovered - requires further investigation'
            }
        elif utilization_percentage < 5:
            return {
                'risk_level': 'low',
                'risk_description': 'Minimal host discovery - likely limited or secured subnet'
            }
        elif utilization_percentage < 25:
            return {
                'risk_level': 'medium',
                'risk_description': 'Moderate host discovery - standard subnet usage'
            }
        elif utilization_percentage < 50:
            return {
                'risk_level': 'high',
                'risk_description': 'High host density - significant attack surface'
            }
        else:
            return {
                'risk_level': 'critical',
                'risk_description': 'Very high utilization - dense network with extensive exposure'
            }
