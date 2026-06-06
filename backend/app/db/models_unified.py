"""
Unified Models Module
Provides a single import point for all database models across the application.
This standardizes imports and makes model access more consistent.
"""

# Import all models from various model files
from .models import (
    Host, Port, Script, HostScript, HostScanHistory, PortScanHistory,
    Scan, Scope, Subnet, HostSubnetMapping, ScanInfo, EyewitnessResult,
    DNSRecord, OutOfScopeHost, ParseError, IngestionJob,
    HostFollow, FollowStatus, HostNote, NoteStatus,
)

from .models_auth import (
    User, UserSession, AuditLog, APIKey, SecurityPolicy, UserRole
)

from .models_risk import (
    HostRiskAssessment, HostVulnerability, SecurityFinding,
    VulnerabilityDatabase, RiskRecommendation
)

from .models_confidence import (
    HostConfidence, PortConfidence, ConflictHistory,
    DataSourceMetadata, NetexecResult
)

from .models_project import (
    Project, ProjectMembership, Notification, NoteMention
)

# Re-export all models for convenient access
__all__ = [
    # Core models
    'Host', 'Port', 'Script', 'HostScript', 'HostScanHistory', 'PortScanHistory',
    'Scan', 'Scope', 'Subnet', 'HostSubnetMapping', 'ScanInfo', 'EyewitnessResult',
    'DNSRecord', 'OutOfScopeHost', 'ParseError', 'IngestionJob',
    'HostFollow', 'FollowStatus', 'HostNote', 'NoteStatus',

    # Auth models
    'User', 'UserSession', 'AuditLog', 'APIKey', 'SecurityPolicy', 'UserRole',

    # Risk assessment models
    'HostRiskAssessment', 'HostVulnerability', 'SecurityFinding',
    'VulnerabilityDatabase', 'RiskRecommendation',

    # Confidence models
    'HostConfidence', 'PortConfidence', 'ConflictHistory',
    'DataSourceMetadata', 'NetexecResult',

    # Project models
    'Project', 'ProjectMembership', 'Notification', 'NoteMention',
]
