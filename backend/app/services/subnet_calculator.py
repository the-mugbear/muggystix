import ipaddress
from typing import Dict, List, Tuple, Optional
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
    
    @staticmethod
    def calculate_scope_aggregates(subnet_metrics: List[Dict]) -> Dict[str, any]:
        """
        Calculate aggregate metrics across all subnets in a scope
        
        Args:
            subnet_metrics: List of subnet metric dictionaries
            
        Returns:
            Aggregated metrics for the entire scope
        """
        if not subnet_metrics:
            return {
                'total_subnets': 0,
                'total_addresses': 0,
                'total_usable_addresses': 0,
                'total_discovered_hosts': 0,
                'overall_utilization': 0.0,
                'risk_distribution': {'unknown': 0, 'low': 0, 'medium': 0, 'high': 0, 'critical': 0},
                'largest_subnet': None,
                'most_utilized_subnet': None
            }
        
        total_addresses = sum(subnet['total_addresses'] for subnet in subnet_metrics)
        total_usable = sum(subnet['usable_addresses'] for subnet in subnet_metrics)
        total_discovered = sum(subnet['discovered_hosts'] for subnet in subnet_metrics)
        
        overall_utilization = (total_discovered / total_usable * 100) if total_usable > 0 else 0
        
        # Risk distribution
        risk_counts = {'unknown': 0, 'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
        for subnet in subnet_metrics:
            risk_level = subnet.get('risk_level', 'unknown')
            risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1
        
        # Find largest and most utilized subnets
        largest_subnet = max(subnet_metrics, key=lambda x: x['total_addresses']) if subnet_metrics else None
        most_utilized = max(subnet_metrics, key=lambda x: x['utilization_percentage']) if subnet_metrics else None
        
        return {
            'total_subnets': len(subnet_metrics),
            'total_addresses': total_addresses,
            'total_usable_addresses': total_usable,
            'total_discovered_hosts': total_discovered,
            'overall_utilization': round(overall_utilization, 2),
            'risk_distribution': risk_counts,
            'largest_subnet': largest_subnet,
            'most_utilized_subnet': most_utilized
        }
    
    @staticmethod
    def suggest_capacity_expansion(subnet_metrics: List[Dict]) -> List[Dict[str, str]]:
        """
        Analyze subnet utilization and suggest capacity expansion recommendations
        
        Returns list of recommendation dictionaries
        """
        recommendations = []
        
        for subnet in subnet_metrics:
            utilization = subnet.get('utilization_percentage', 0)
            cidr = subnet.get('cidr', '')
            discovered = subnet.get('discovered_hosts', 0)
            
            if utilization > 80:
                recommendations.append({
                    'type': 'capacity_warning',
                    'subnet': cidr,
                    'message': f'Subnet {cidr} is {utilization:.1f}% utilized ({discovered} hosts). Consider expanding or segmenting.',
                    'priority': 'high'
                })
            elif utilization > 60:
                recommendations.append({
                    'type': 'capacity_notice',
                    'subnet': cidr,
                    'message': f'Subnet {cidr} is {utilization:.1f}% utilized ({discovered} hosts). Monitor for growth.',
                    'priority': 'medium'
                })
            elif utilization == 0 and subnet.get('usable_addresses', 0) > 0:
                recommendations.append({
                    'type': 'unused_subnet',
                    'subnet': cidr,
                    'message': f'Subnet {cidr} has no discovered hosts. Verify it\'s still needed or investigate scanning coverage.',
                    'priority': 'low'
                })
        
        return recommendations