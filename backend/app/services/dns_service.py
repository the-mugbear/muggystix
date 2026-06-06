import dns.resolver
import dns.reversename
import dns.zone
import dns.query
import dns.exception
import ipaddress
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session
from app.db import models
import logging

logger = logging.getLogger(__name__)

# Per-operation timeouts in seconds.  ``timeout`` is the per-nameserver
# attempt cap; ``lifetime`` is the overall query budget across retries
# and fallback nameservers.  Without these, an unresponsive NS blocks
# the worker indefinitely — these calls run synchronously from FastAPI
# request handlers and from the per-host enrichment loop.
_DNS_RESOLVE_TIMEOUT = 3.0
_DNS_RESOLVE_LIFETIME = 5.0
_ZONE_TRANSFER_TIMEOUT = 5.0


def _is_axfr_target_allowed(ip: str) -> bool:
    """Return False for addresses we refuse to send AXFR to.

    The caller of ``attempt_zone_transfer`` controls the domain we
    resolve NS records for, and therefore (indirectly) the IPs we'd
    issue AXFR queries against.  We reject private, loopback,
    link-local, multicast, reserved, and unspecified addresses so the
    feature can't be used to fingerprint internal DNS hosts from the
    server.  Public IPs that happen to host an internal authoritative
    NS by accident are out of scope — the operator who controls those
    can ban the egress at the firewall.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return True


class DNSService:
    def __init__(self, db: Session, custom_dns_server: Optional[str] = None, project_id: int = None):
        self.db = db
        self.project_id = project_id
        # Per-instance cache of zone-transfer attempts keyed by root domain.
        # Bulk enrichment reuses one DNSService across every host, and many
        # hosts share a domain (200 hosts in example.com would otherwise
        # issue 200 identical AXFR attempts to the same nameservers).
        self._zone_attempt_cache: Dict[str, Dict[str, Any]] = {}
        self.resolver = dns.resolver.Resolver()
        # Bound every resolver call so a stalled NS can't pin a worker.
        self.resolver.timeout = _DNS_RESOLVE_TIMEOUT
        self.resolver.lifetime = _DNS_RESOLVE_LIFETIME
        self.custom_dns_server = custom_dns_server

        # Configure custom DNS server if provided.  The value comes from
        # user-controlled upload options, so validate it parses as an IP
        # address before handing it to the resolver — a hostname (or typo)
        # would otherwise raise inside dns.resolver, get swallowed, and
        # silently fall back to system DNS with no signal to the operator.
        if custom_dns_server:
            try:
                ipaddress.ip_address(custom_dns_server)
            except (ValueError, TypeError):
                logger.warning(
                    "Ignoring custom DNS server %r — not a valid IP address; "
                    "using system DNS.", custom_dns_server,
                )
                self.custom_dns_server = None
            else:
                self.resolver.nameservers = [custom_dns_server]
                logger.info("Using custom DNS server: %s", custom_dns_server)

    def lookup_hostname(self, ip_address: str) -> Optional[str]:
        """Perform reverse DNS lookup for an IP address.

        Always uses ``dns.resolver`` (never ``socket.gethostbyaddr``) so
        the per-call timeout configured on ``self.resolver`` actually
        applies — the legacy socket path is unbounded and could pin a
        worker on a stalled system resolver.
        """
        try:
            reversed_ip = dns.reversename.from_address(ip_address)
            answers = self.resolver.resolve(reversed_ip, 'PTR')
            if answers:
                hostname = str(answers[0]).rstrip('.')
                self._store_dns_record(hostname, 'PTR', ip_address)
                return hostname
            return None
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return None
        except dns.exception.Timeout:
            logger.debug(f"Reverse DNS lookup timed out for {ip_address}")
            return None
        except Exception as e:
            logger.debug(f"Reverse DNS lookup failed for {ip_address}: {str(e)}")
            return None
    
    def resolve_hostname(self, hostname: str) -> List[str]:
        """Resolve hostname to IP addresses"""
        ip_addresses = []
        
        try:
            # A records (IPv4)
            try:
                answers = self.resolver.resolve(hostname, 'A')
                for answer in answers:
                    ip = str(answer)
                    ip_addresses.append(ip)
                    self._store_dns_record(hostname, 'A', ip, answer.ttl)
            except dns.resolver.NXDOMAIN:
                pass
            except dns.resolver.NoAnswer:
                pass
            
            # AAAA records (IPv6)
            try:
                answers = self.resolver.resolve(hostname, 'AAAA')
                for answer in answers:
                    ip = str(answer)
                    ip_addresses.append(ip)
                    self._store_dns_record(hostname, 'AAAA', ip, answer.ttl)
            except dns.resolver.NXDOMAIN:
                pass
            except dns.resolver.NoAnswer:
                pass
                
        except Exception as e:
            logger.warning(f"DNS resolution failed for {hostname}: {str(e)}")
        
        return ip_addresses
    
    def get_dns_records(self, hostname: str, record_types: List[str] = None) -> Dict[str, List[str]]:
        """Get various DNS records for a hostname"""
        if record_types is None:
            record_types = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SOA']
        
        records = {}
        
        for record_type in record_types:
            try:
                answers = self.resolver.resolve(hostname, record_type)
                record_values = []
                
                for answer in answers:
                    value = str(answer)
                    record_values.append(value)
                    self._store_dns_record(hostname, record_type, value, answer.ttl)
                
                if record_values:
                    records[record_type] = record_values
                    
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                pass
            except Exception as e:
                logger.warning(f"Failed to get {record_type} records for {hostname}: {str(e)}")
        
        return records
    
    def attempt_zone_transfer(self, hostname: str, nameservers: List[str] = None) -> Dict[str, Any]:
        """Attempt DNS zone transfer for a domain.

        Refuses AXFR against private / loopback / link-local nameserver
        IPs.  Without this guard an authenticated analyst could submit
        an internal-only domain whose authoritative NS resolves to an
        RFC1918 host, and we'd issue AXFR to it from inside the server
        — turning the endpoint into a soft-SSRF probe of internal DNS
        infrastructure.  The same guard applies whether the operator
        supplies ``nameservers`` directly or lets us resolve them from
        the zone's NS records.
        """
        # Reuse a prior attempt for the same domain within this run (only
        # for the enrichment path, which doesn't pass explicit nameservers).
        # Avoids re-issuing the same AXFR once per host sharing the domain.
        cache_key = hostname if not nameservers else None
        if cache_key is not None and cache_key in self._zone_attempt_cache:
            return self._zone_attempt_cache[cache_key]

        zone_data = {
            'success': False,
            'records': [],
            'error': None,
            'nameserver_used': None
        }

        # Get nameservers if not provided
        if not nameservers:
            try:
                ns_records = self.resolver.resolve(hostname, 'NS')
                nameservers = [str(ns).rstrip('.') for ns in ns_records]
            except Exception as e:
                zone_data['error'] = f"Failed to get nameservers: {str(e)}"
                if cache_key is not None:
                    self._zone_attempt_cache[cache_key] = zone_data
                return zone_data

        tried_with_reasons: List[str] = []
        # Try zone transfer with each nameserver
        for ns in nameservers:
            try:
                # Try to get the IP of the nameserver
                ns_ips = self.resolve_hostname(ns)
                if not ns_ips:
                    tried_with_reasons.append(f"{ns}: could not resolve NS to IP")
                    continue

                target_ip = ns_ips[0]
                if not _is_axfr_target_allowed(target_ip):
                    tried_with_reasons.append(
                        f"{ns} ({target_ip}): private/loopback/link-local — refused"
                    )
                    logger.warning(
                        "Refusing AXFR to %s (%s): non-public address",
                        ns, target_ip,
                    )
                    continue

                # Attempt zone transfer (bounded — without timeout the
                # call blocks until the kernel closes the TCP socket).
                zone = dns.zone.from_xfr(
                    dns.query.xfr(target_ip, hostname, timeout=_ZONE_TRANSFER_TIMEOUT)
                )
                
                # Parse zone records
                records = []
                for name, node in zone.nodes.items():
                    for rdataset in node.rdatasets:
                        for rdata in rdataset:
                            record = {
                                'name': str(name),
                                'type': dns.rdatatype.to_text(rdataset.rdtype),
                                'value': str(rdata),
                                'ttl': rdataset.ttl
                            }
                            records.append(record)
                            
                            # Store in database
                            full_name = f"{name}.{hostname}" if name != '@' else hostname
                            self._store_dns_record(
                                full_name, 
                                dns.rdatatype.to_text(rdataset.rdtype), 
                                str(rdata), 
                                rdataset.ttl
                            )
                
                zone_data.update({
                    'success': True,
                    'records': records,
                    'nameserver_used': ns
                })
                
                logger.info(f"Zone transfer successful for {hostname} using {ns}")
                break
                
            except dns.exception.Timeout:
                tried_with_reasons.append(f"{ns}: timeout after {_ZONE_TRANSFER_TIMEOUT}s")
                logger.debug(f"Zone transfer timeout for {hostname} using {ns}")
                continue
            except Exception as e:
                tried_with_reasons.append(f"{ns}: {type(e).__name__}: {e}")
                logger.debug(f"Zone transfer failed for {hostname} using {ns}: {str(e)}")
                continue

        if not zone_data['success']:
            if tried_with_reasons:
                zone_data['error'] = (
                    "Zone transfer failed with all nameservers. Attempts: "
                    + "; ".join(tried_with_reasons)
                )
            else:
                zone_data['error'] = "Zone transfer failed with all nameservers"

        if cache_key is not None:
            self._zone_attempt_cache[cache_key] = zone_data
        return zone_data

    def enrich_host_data(self, host: models.Host) -> Dict[str, Any]:
        """Enrich host data with DNS information"""
        enrichment_data = {
            'reverse_dns': None,
            'dns_records': {},
            'zone_transfer': None
        }
        
        # Perform reverse DNS lookup
        if host.ip_address:
            hostname = self.lookup_hostname(host.ip_address)
            if hostname:
                host.hostname = hostname
                enrichment_data['reverse_dns'] = hostname
                
                # Get additional DNS records for the hostname
                dns_records = self.get_dns_records(hostname)
                enrichment_data['dns_records'] = dns_records
                
                # Extract domain for zone transfer attempt
                domain_parts = hostname.split('.')
                if len(domain_parts) >= 2:
                    domain = '.'.join(domain_parts[-2:])  # Get root domain
                    zone_transfer_result = self.attempt_zone_transfer(domain)
                    if zone_transfer_result['success']:
                        enrichment_data['zone_transfer'] = zone_transfer_result
        
        # If hostname was already known, get DNS records
        elif host.hostname:
            dns_records = self.get_dns_records(host.hostname)
            enrichment_data['dns_records'] = dns_records
            
            # Attempt zone transfer
            domain_parts = host.hostname.split('.')
            if len(domain_parts) >= 2:
                domain = '.'.join(domain_parts[-2:])
                zone_transfer_result = self.attempt_zone_transfer(domain)
                if zone_transfer_result['success']:
                    enrichment_data['zone_transfer'] = zone_transfer_result

        # NB: no per-host commit — the bulk caller (_enrich_dns) commits in
        # batches.  Committing once per host during enrichment held a DB
        # connection open across each host's multi-second DNS round-trips.
        return enrichment_data
    
    def _store_dns_record(self, domain: str, record_type: str, value: str, ttl: int = None):
        """Store DNS record in database"""
        try:
            # Check if record already exists
            query = self.db.query(models.DNSRecord).filter(
                models.DNSRecord.domain == domain,
                models.DNSRecord.record_type == record_type,
                models.DNSRecord.value == value
            )
            if self.project_id is not None:
                query = query.filter(models.DNSRecord.project_id == self.project_id)
            existing = query.first()
            
            if existing:
                # Update TTL if provided
                if ttl is not None:
                    existing.ttl = ttl
            else:
                # Create new record
                dns_record = models.DNSRecord(
                    domain=domain,
                    record_type=record_type,
                    value=value,
                    ttl=ttl,
                    project_id=self.project_id,
                )
                self.db.add(dns_record)
                
        except Exception as e:
            logger.warning(f"Failed to store DNS record for {domain}: {str(e)}")
    
    def get_stored_dns_records(self, domain: str) -> List[models.DNSRecord]:
        """Get stored DNS records for a domain, scoped to the current project."""
        query = self.db.query(models.DNSRecord).filter(
            models.DNSRecord.domain == domain
        )
        if self.project_id is not None:
            query = query.filter(models.DNSRecord.project_id == self.project_id)
        return query.all()