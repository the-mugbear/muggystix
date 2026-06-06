"""
Confidence-based conflict resolution and metadata service.

Provides confidence scoring for different scan types and data sources,
and tracks conflicting information for display purposes.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
import json


class ScanType(Enum):
    NMAP = "nmap"
    MASSCAN = "masscan"
    NETEXEC = "netexec"
    GNMAP = "gnmap"
    EYEWITNESS = "eyewitness"


class DataSource(Enum):
    SERVICE_BANNER = "service_banner"
    OS_FINGERPRINT = "os_fingerprint"
    VERSION_PROBE = "version_probe"
    SCRIPT_OUTPUT = "script_output"
    CONNECTION_TEST = "connection_test"
    SMB_ENUM = "smb_enum"
    LDAP_ENUM = "ldap_enum"
    DNS_LOOKUP = "dns_lookup"


@dataclass
class ConfidenceScore:
    """Represents confidence in a piece of data"""
    score: int  # 0-100
    source: DataSource
    scan_type: ScanType
    method: str  # Specific method used (e.g., "nmap -sV", "netexec smb")
    timestamp: datetime
    additional_info: Optional[Dict[str, Any]] = None


@dataclass
class ConflictingData:
    """Represents conflicting values for a field"""
    field_name: str
    current_value: Any
    current_confidence: ConfidenceScore
    conflicting_values: List[Tuple[Any, ConfidenceScore]]


class ConfidenceService:
    """Service for managing confidence scores and conflict resolution"""

    # Base confidence scores by scan type and data source
    CONFIDENCE_MATRIX = {
        ScanType.NMAP: {
            DataSource.SERVICE_BANNER: 90,
            DataSource.OS_FINGERPRINT: 85,
            DataSource.VERSION_PROBE: 95,
            DataSource.SCRIPT_OUTPUT: 80,
            DataSource.CONNECTION_TEST: 70,
        },
        ScanType.MASSCAN: {
            DataSource.CONNECTION_TEST: 60,
            DataSource.SERVICE_BANNER: 50,  # Limited banner detection
        },
        ScanType.NETEXEC: {
            DataSource.SMB_ENUM: 95,
            DataSource.LDAP_ENUM: 90,
            DataSource.SERVICE_BANNER: 85,
            DataSource.CONNECTION_TEST: 80,
            DataSource.OS_FINGERPRINT: 75,
        },
        ScanType.GNMAP: {
            DataSource.SERVICE_BANNER: 90,
            DataSource.OS_FINGERPRINT: 85,
            DataSource.VERSION_PROBE: 95,
            DataSource.CONNECTION_TEST: 70,
        },
        ScanType.EYEWITNESS: {
            DataSource.SERVICE_BANNER: 70,
            DataSource.VERSION_PROBE: 60,
        }
    }

    # Method-specific confidence modifiers
    METHOD_MODIFIERS = {
        "nmap -sV": 10,          # Version detection adds confidence
        "nmap -O": 5,            # OS detection
        "nmap -A": 15,           # Aggressive scan
        "nmap -sC": 5,           # Script scan
        "netexec smb": 10,       # SMB enumeration is very reliable
        "netexec ldap": 8,       # LDAP enumeration
        "masscan": -20,          # Fast but less accurate
        "banner_grab": 15,       # Direct banner grab is reliable
    }

    def calculate_confidence(
        self,
        scan_type: ScanType,
        data_source: DataSource,
        method: str,
        additional_factors: Optional[Dict[str, Any]] = None
    ) -> int:
        """Calculate confidence score for a piece of data"""

        # Base confidence from matrix
        base_confidence = self.CONFIDENCE_MATRIX.get(scan_type, {}).get(data_source, 50)

        # Method modifier
        method_modifier = 0
        for method_key, modifier in self.METHOD_MODIFIERS.items():
            if method_key.lower() in method.lower():
                method_modifier += modifier
                break

        # Additional factors
        additional_modifier = 0
        if additional_factors:
            # Service confidence from nmap
            if "service_conf" in additional_factors:
                conf = additional_factors["service_conf"]
                if conf >= 8:
                    additional_modifier += 10
                elif conf >= 5:
                    additional_modifier += 5
                elif conf <= 2:
                    additional_modifier -= 10

            # OS accuracy from nmap
            if "os_accuracy" in additional_factors:
                accuracy = additional_factors["os_accuracy"]
                if accuracy >= 95:
                    additional_modifier += 15
                elif accuracy >= 85:
                    additional_modifier += 10
                elif accuracy >= 70:
                    additional_modifier += 5
                elif accuracy < 50:
                    additional_modifier -= 10

            # Multiple confirmation sources
            if "multiple_sources" in additional_factors:
                additional_modifier += 20

            # Recent vs old data
            if "scan_age_days" in additional_factors:
                age = additional_factors["scan_age_days"]
                if age > 30:
                    additional_modifier -= 10
                elif age > 7:
                    additional_modifier -= 5

        # Calculate final score
        final_score = base_confidence + method_modifier + additional_modifier

        # Clamp to 0-100 range
        return max(0, min(100, final_score))

    def create_confidence_score(
        self,
        scan_type: ScanType,
        data_source: DataSource,
        method: str,
        timestamp: datetime,
        additional_factors: Optional[Dict[str, Any]] = None
    ) -> ConfidenceScore:
        """Create a confidence score object"""

        score = self.calculate_confidence(scan_type, data_source, method, additional_factors)

        return ConfidenceScore(
            score=score,
            source=data_source,
            scan_type=scan_type,
            method=method,
            timestamp=timestamp,
            additional_info=additional_factors
        )

    def resolve_conflict(
        self,
        current_value: Any,
        current_confidence: ConfidenceScore,
        new_value: Any,
        new_confidence: ConfidenceScore
    ) -> Tuple[Any, ConfidenceScore, bool]:
        """
        Resolve conflict between current and new value.
        Returns: (winning_value, winning_confidence, value_changed)
        """

        # If values are the same, use higher confidence
        if current_value == new_value:
            if new_confidence.score > current_confidence.score:
                return new_value, new_confidence, False
            else:
                return current_value, current_confidence, False

        # Different values - use confidence to decide
        if new_confidence.score > current_confidence.score:
            return new_value, new_confidence, True
        elif new_confidence.score == current_confidence.score:
            # Tie-breaker: use more recent data
            if new_confidence.timestamp > current_confidence.timestamp:
                return new_value, new_confidence, True
            else:
                return current_value, current_confidence, False
        else:
            return current_value, current_confidence, False

    def detect_netexec_scan_type(self, output_line: str) -> Tuple[ScanType, DataSource, str]:
        """Detect specific netexec scan type from output"""

        output_lower = output_line.lower()

        if "smb" in output_lower:
            if "enum" in output_lower or "shares" in output_lower:
                return ScanType.NETEXEC, DataSource.SMB_ENUM, "netexec smb --shares"
            elif "version" in output_lower or "banner" in output_lower:
                return ScanType.NETEXEC, DataSource.SERVICE_BANNER, "netexec smb"
            else:
                return ScanType.NETEXEC, DataSource.CONNECTION_TEST, "netexec smb"

        elif "ldap" in output_lower:
            if "enum" in output_lower:
                return ScanType.NETEXEC, DataSource.LDAP_ENUM, "netexec ldap --enum"
            else:
                return ScanType.NETEXEC, DataSource.CONNECTION_TEST, "netexec ldap"

        elif "winrm" in output_lower:
            return ScanType.NETEXEC, DataSource.CONNECTION_TEST, "netexec winrm"

        elif "rdp" in output_lower:
            return ScanType.NETEXEC, DataSource.CONNECTION_TEST, "netexec rdp"

        else:
            return ScanType.NETEXEC, DataSource.CONNECTION_TEST, "netexec"

    def get_scan_method_from_command(self, command_line: str) -> str:
        """Extract specific scan method from command line"""

        if not command_line:
            return "unknown"

        cmd_lower = command_line.lower()

        # Nmap detection
        if "nmap" in cmd_lower:
            if "-sv" in cmd_lower or "--version-intensity" in cmd_lower:
                return "nmap -sV"
            elif "-a" in cmd_lower:
                return "nmap -A"
            elif "-o" in cmd_lower:
                return "nmap -O"
            elif "-sc" in cmd_lower or "--script" in cmd_lower:
                return "nmap -sC"
            else:
                return "nmap"

        # Masscan detection
        elif "masscan" in cmd_lower:
            return "masscan"

        # Netexec detection
        elif "netexec" in cmd_lower or "nxc" in cmd_lower:
            if "smb" in cmd_lower:
                return "netexec smb"
            elif "ldap" in cmd_lower:
                return "netexec ldap"
            elif "winrm" in cmd_lower:
                return "netexec winrm"
            else:
                return "netexec"

        return command_line[:50]  # First 50 chars if unknown