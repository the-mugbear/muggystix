"""Query-parameter bundles shared by more than one router.

A class here is consumed with ``Depends()``, so it needs FastAPI's ``Query``
objects — which is why it lives under ``app/api`` and not in ``app/services``
(a service never imports FastAPI's request machinery from ``app.api``; see
``tests/test_service_router_boundary.py``).  It is not an endpoint module:
routers import from here, never from one another's files (review 2026-10-01
B4 — ``reports.py`` used to import ``HostFilterParams`` from ``hosts.py``).

No ``from __future__ import annotations`` here: FastAPI resolves a dependency
CLASS's ``__init__`` annotations without the module's globals, so stringified
annotations would not evaluate.
"""
from typing import Any, Dict, List, Optional

from fastapi import Query


class HostFilterParams:
    """The shared /hosts filter query params, declared once.

    Consumed via ``Depends()`` by every endpoint that filters hosts
    (listing, matching-ids, filter-data, tool-ready export).  Declaring
    the params here — instead of repeating ~25 ``Query(...)`` defaults
    across four signatures — means adding a filter dimension is a one-line
    change in one place, and the four endpoints can never drift out of
    sync.  Attribute names match ``build_filtered_host_query``'s kwargs,
    so ``as_builder_kwargs()`` splats straight in.
    """

    def __init__(
        self,
        state: Optional[str] = Query(None, description="Host state filter", examples=["up"]),
        search: Optional[str] = Query(None, description="Search by IP address, hostname, OS name, port number, or service name", examples=["10.0.0"]),
        ports: Optional[str] = Query(None, description="Comma-separated port numbers to match", examples=["22,80,443,8080"]),
        services: Optional[str] = Query(None, description="Comma-separated service names to match (mapped to common ports automatically)", examples=["ssh,http,https,rdp"]),
        port_states: Optional[str] = Query(None, description="Comma-separated port states to match. With ports= or services= it is the state of THAT port, which is open unless named here; `any` matches every state", examples=["open,filtered", "any"]),
        has_open_ports: Optional[bool] = Query(None, description="If true, only hosts with at least one open port"),
        os_filter: Optional[str] = Query(None, description="Filter by OS name or family (partial match)", examples=["Linux"]),
        subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks; hosts must fall within at least one", examples=["192.168.1.0/24,10.0.0.0/8"]),
        has_critical_vulns: Optional[bool] = Query(None, description="If true, only hosts with critical-severity vulnerabilities"),
        has_high_vulns: Optional[bool] = Query(None, description="If true, only hosts with high-severity vulnerabilities"),
        has_medium_vulns: Optional[bool] = Query(None, description="If true, only hosts with medium-severity vulnerabilities"),
        has_low_vulns: Optional[bool] = Query(None, description="If true, only hosts with low-severity vulnerabilities"),
        has_exploit_available: Optional[bool] = Query(None, description="If true, only hosts with at least one vulnerability flagged as exploitable by Nessus (exploit_available / metasploit_name / canvas_package / core_impact_name / exploit_code_maturity in {functional, high, proof-of-concept})"),
        has_test_execution: Optional[bool] = Query(None, description="If true, only hosts that have been tested: at least one evidence record whose outcome is finding, no_finding or inconclusive (a failed attempt or an informational record is not a test). Drives the 'tested' badge on the Hosts list."),
        follow_status: Optional[str] = Query(None, description="Filter by team-shared review status: in_review, reviewed, or none (nobody reviewing)", examples=["none"]),
        out_of_scope_only: Optional[bool] = Query(None, description="If true, only hosts not mapped to any scope/subnet"),
        scan_ids: Optional[str] = Query(None, description="Comma-separated scan IDs; hosts must appear in at least one", examples=["1,2,5"]),
        first_seen_in_scan: Optional[bool] = Query(None, description="Used with scan_ids — if true, only hosts first discovered in those scans"),
        with_notes_only: Optional[bool] = Query(None, description="If true, only hosts that have at least one note"),
        has_web_interface: Optional[bool] = Query(None, description="If true, only hosts with at least one web interface recorded (httpx / eyewitness / nikto)"),
        tech: Optional[str] = Query(None, description="Comma-separated list of technology strings; OR semantics — host qualifies if any interface has any listed tech (substring match, case-insensitive)", examples=["nginx,jenkins"]),
        tags: Optional[str] = Query(None, description="Comma-separated tag IDs; OR semantics — host qualifies if it carries any listed tag", examples=["3,7"]),
        subnet_labels: Optional[str] = Query(None, description="Comma-separated subnet-label IDs; OR semantics — host qualifies if it sits in any subnet carrying any listed label", examples=["2,5"]),
        sites: Optional[str] = Query(None, description="Comma-separated site names; OR semantics — host qualifies if any of its subnets belongs to a listed site", examples=["London DC"]),
        assigned_to: Optional[str] = Query(None, description="Assignment filter: 'me', 'any', or a numeric user id", examples=["me"]),
        # RDAP network-attribution filters (org / ASN / country). Repeated
        # params (?orgs=A&orgs=B), NOT comma-joined, because org names routinely
        # contain commas ("Google, LLC"). OR semantics within each group.
        orgs: Optional[List[str]] = Query(None, description="Registered netblock owner(s) from RDAP; repeat the param per value. OR semantics; substring match.", examples=["Google, LLC"]),
        asns: Optional[List[str]] = Query(None, description="Autonomous system number(s) from RDAP; repeat the param per value. OR semantics.", examples=["15169"]),
        countries: Optional[List[str]] = Query(None, description="ISO country code(s) of the registered netblock; repeat the param per value. OR semantics; exact match.", examples=["US"]),
        weaknesses: Optional[str] = Query(None, description="Comma-separated weakness / access flags (the DSL's has: values smb_unsigned, eol, weak_tls, cert_issue, cleartext, weak_auth, local_admin, writable_share); OR semantics", examples=["smb_unsigned,weak_tls"]),
        checks: Optional[str] = Query(None, description="Comma-separated misconfiguration check ids (the DSL's check:); OR semantics", examples=["smb_signing_not_required"]),
        q: Optional[str] = Query(None, description="Boolean query DSL. Fields (port, os, service, subnet, tag, label, cve, vuln, header, note, has:, …) combined with AND/OR/NOT + parentheses. Comma = OR within a field; repeated field = AND. e.g. 'port:80 port:443 AND NOT tag:test', 'cve:CVE-2021-44228 OR vuln:\"log4j\"'. ANDs with the other filters."),
    ):
        self.state = state
        self.search = search
        self.ports = ports
        self.services = services
        self.port_states = port_states
        self.has_open_ports = has_open_ports
        self.os_filter = os_filter
        self.subnets = subnets
        self.has_critical_vulns = has_critical_vulns
        self.has_high_vulns = has_high_vulns
        self.has_medium_vulns = has_medium_vulns
        self.has_low_vulns = has_low_vulns
        self.has_exploit_available = has_exploit_available
        self.has_test_execution = has_test_execution
        self.follow_status = follow_status
        self.out_of_scope_only = out_of_scope_only
        self.scan_ids = scan_ids
        self.first_seen_in_scan = first_seen_in_scan
        self.with_notes_only = with_notes_only
        self.has_web_interface = has_web_interface
        self.tech = tech
        self.tags = tags
        self.subnet_labels = subnet_labels
        self.sites = sites
        self.assigned_to = assigned_to
        self.orgs = orgs
        self.asns = asns
        self.countries = countries
        self.weaknesses = weaknesses
        self.checks = checks
        self.q = q

    def as_builder_kwargs(self) -> Dict[str, Any]:
        """Kwargs for ``build_filtered_host_query`` (excludes ``project_id``,
        which the endpoint supplies from the resolved project)."""
        return dict(self.__dict__)

    def active(self) -> bool:
        """True if any filter (including ``q``) is set — used by the
        filter-data endpoint to decide whether to scope the cascade."""
        return any(v is not None for v in self.__dict__.values())
