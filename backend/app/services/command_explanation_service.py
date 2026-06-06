"""
Command Explanation Service

This service provides detailed explanations of scan commands and their arguments
for various security scanning tools like nmap and masscan.
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class ArgumentExplanation:
    """Represents an explanation for a command-line argument"""
    arg: str
    description: str
    category: str
    risk_level: str = "low"  # low, medium, high
    examples: List[str] = None

    def __post_init__(self):
        if self.examples is None:
            self.examples = []

@dataclass
class CommandAnalysis:
    """Complete analysis of a command line"""
    tool: str
    command: str
    target: str
    scan_type: str
    arguments: List[ArgumentExplanation]
    summary: str
    risk_assessment: str

class CommandExplanationService:
    """Service for analyzing and explaining security scan commands"""
    
    def __init__(self):
        self.nmap_args = self._init_nmap_arguments()
        self.masscan_args = self._init_masscan_arguments()
    
    def analyze_command(self, command_line: str, tool_name: str = None) -> Optional[CommandAnalysis]:
        """
        Analyze a command line and provide detailed explanations
        
        Args:
            command_line: The full command line string
            tool_name: Optional tool name hint (nmap, masscan)
            
        Returns:
            CommandAnalysis object or None if command cannot be parsed
        """
        if not command_line or not command_line.strip():
            return None
            
        # Auto-detect tool if not provided
        if not tool_name:
            tool_name = self._detect_tool(command_line)
            
        if not tool_name:
            return None
            
        if tool_name.lower() == 'nmap':
            return self._analyze_nmap_command(command_line)
        elif tool_name.lower() == 'masscan':
            return self._analyze_masscan_command(command_line)
        else:
            return None
    
    def _detect_tool(self, command_line: str) -> Optional[str]:
        """Detect the scanning tool from command line"""
        cmd_lower = command_line.lower()
        if 'nmap' in cmd_lower:
            return 'nmap'
        elif 'masscan' in cmd_lower:
            return 'masscan'
        return None
    
    def _analyze_nmap_command(self, command_line: str) -> CommandAnalysis:
        """Analyze nmap command line"""
        # Parse command into tokens
        tokens = self._parse_command_tokens(command_line)
        
        # Extract target(s)
        targets = self._extract_nmap_targets(tokens)
        
        # Extract and explain arguments
        arguments = []
        scan_techniques = []
        
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.startswith('-'):
                arg_info = self._explain_nmap_argument(token, tokens, i)
                if arg_info:
                    arguments.append(arg_info[0])
                    if arg_info[0].category == "Scan Techniques":
                        scan_techniques.append(arg_info[0].arg)
                    i += arg_info[1]  # Skip additional tokens consumed
                else:
                    i += 1
            else:
                i += 1
        
        # Determine primary scan type
        scan_type = self._determine_nmap_scan_type(scan_techniques, tokens)
        
        # Generate summary
        summary = self._generate_nmap_summary(scan_type, targets, arguments)
        
        # Risk assessment
        risk_assessment = self._assess_nmap_risk(arguments)
        
        return CommandAnalysis(
            tool='nmap',
            command=command_line,
            target=', '.join(targets) if targets else 'Unknown',
            scan_type=scan_type,
            arguments=arguments,
            summary=summary,
            risk_assessment=risk_assessment
        )
    
    def _analyze_masscan_command(self, command_line: str) -> CommandAnalysis:
        """Analyze masscan command line"""
        tokens = self._parse_command_tokens(command_line)
        
        # Extract targets and ports
        targets = []
        ports = []
        rate = None
        
        arguments = []
        
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.startswith('-'):
                arg_info = self._explain_masscan_argument(token, tokens, i)
                if arg_info:
                    arguments.append(arg_info[0])
                    i += arg_info[1]
                    
                    # Extract specific values for analysis
                    if token in ['-p', '--ports']:
                        ports.append(arg_info[0].description)
                    elif token in ['--rate']:
                        rate = tokens[i-1] if i > 0 else None
                else:
                    i += 1
            else:
                # Likely a target
                if not token.startswith('/') and '.' in token:  # Basic IP/range detection
                    targets.append(token)
                i += 1
        
        scan_type = "TCP Port Scan"
        summary = self._generate_masscan_summary(targets, ports, rate)
        risk_assessment = self._assess_masscan_risk(arguments, rate)
        
        return CommandAnalysis(
            tool='masscan',
            command=command_line,
            target=', '.join(targets) if targets else 'Unknown',
            scan_type=scan_type,
            arguments=arguments,
            summary=summary,
            risk_assessment=risk_assessment
        )
    
    def _parse_command_tokens(self, command_line: str) -> List[str]:
        """Parse command line into tokens, handling quoted strings"""
        if not command_line or not command_line.strip():
            return []
        # Simple tokenization - could be enhanced for complex quoting
        return command_line.strip().split()
    
    def _extract_nmap_targets(self, tokens: List[str]) -> List[str]:
        """Extract target specifications from nmap tokens"""
        if not tokens:
            return []

        targets = []
        skip_next = False

        for i, token in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
                
            # Skip the nmap command itself and known flags
            if token == 'nmap' or token.startswith('-'):
                # Check if this flag takes a parameter
                if token in ['-p', '-sV', '-O', '-A', '--script', '-oX', '-oN', '-oG']:
                    skip_next = True
                continue
                
            # Likely a target if it looks like IP, hostname, or CIDR
            if (re.match(r'^\d+\.\d+\.\d+\.\d+', token) or 
                re.match(r'^[\w\.-]+$', token) or
                '/' in token):
                targets.append(token)
        
        return targets
    
    def _explain_nmap_argument(self, token: str, tokens: List[str], index: int) -> Optional[Tuple[ArgumentExplanation, int]]:
        """Explain a single nmap argument"""
        consumed_tokens = 1
        
        if token in self.nmap_args:
            arg_info = self.nmap_args[token]
            explanation = ArgumentExplanation(
                arg=token,
                description=arg_info['description'],
                category=arg_info['category'],
                risk_level=arg_info.get('risk_level', 'low'),
                examples=arg_info.get('examples', [])
            )
            return explanation, consumed_tokens
        
        # Handle compound arguments like -sS, -sT, etc.
        if len(token) > 2 and token.startswith('-s'):
            scan_type = token[2]
            if f"-s{scan_type}" in self.nmap_args:
                arg_info = self.nmap_args[f"-s{scan_type}"]
                explanation = ArgumentExplanation(
                    arg=token,
                    description=arg_info['description'],
                    category=arg_info['category'],
                    risk_level=arg_info.get('risk_level', 'low')
                )
                return explanation, consumed_tokens
        
        # Handle arguments with values
        if token == '-p' and index + 1 < len(tokens):
            ports = tokens[index + 1]
            explanation = ArgumentExplanation(
                arg=f"{token} {ports}",
                description=f"Scan specific ports: {ports}",
                category="Port Specification",
                risk_level="low"
            )
            return explanation, 2
            
        return None
    
    def _explain_masscan_argument(self, token: str, tokens: List[str], index: int) -> Optional[Tuple[ArgumentExplanation, int]]:
        """Explain a single masscan argument"""
        consumed_tokens = 1
        
        if token in self.masscan_args:
            arg_info = self.masscan_args[token]
            
            # Handle arguments that take values
            value = ""
            if arg_info.get('takes_value') and index + 1 < len(tokens):
                value = tokens[index + 1]
                consumed_tokens = 2
                
            description = arg_info['description']
            if value:
                description = f"{description}: {value}"
                
            explanation = ArgumentExplanation(
                arg=f"{token} {value}".strip(),
                description=description,
                category=arg_info['category'],
                risk_level=arg_info.get('risk_level', 'low')
            )
            return explanation, consumed_tokens
            
        return None
    
    def _determine_nmap_scan_type(self, scan_techniques: List[str], tokens: List[str]) -> str:
        """Determine the primary scan type from arguments"""
        if '-sS' in scan_techniques:
            return "TCP SYN Scan"
        elif '-sT' in scan_techniques:
            return "TCP Connect Scan"
        elif '-sU' in scan_techniques:
            return "UDP Scan"
        elif '-sN' in scan_techniques:
            return "TCP NULL Scan"
        elif '-sF' in scan_techniques:
            return "TCP FIN Scan"
        elif '-sX' in scan_techniques:
            return "TCP Xmas Scan"
        elif '-sA' in scan_techniques:
            return "TCP ACK Scan"
        elif '-sW' in scan_techniques:
            return "TCP Window Scan"
        elif '-sM' in scan_techniques:
            return "TCP Maimon Scan"
        elif any('-sV' in token for token in tokens):
            return "Version Detection Scan"
        elif any('-O' in token for token in tokens):
            return "OS Detection Scan"
        else:
            return "TCP SYN Scan (default)"
    
    def _generate_nmap_summary(self, scan_type: str, targets: List[str], arguments: List[ArgumentExplanation]) -> str:
        """Generate human-readable summary of nmap scan"""
        target_str = f"{len(targets)} target(s)" if len(targets) > 1 else targets[0] if targets else "unspecified targets"
        
        features = []
        for arg in arguments:
            if arg.category in ["Service Detection", "OS Detection", "Script Scanning"]:
                features.append(arg.category.lower())
                
        feature_str = f" with {', '.join(features)}" if features else ""
        
        return f"{scan_type} against {target_str}{feature_str}"
    
    def _generate_masscan_summary(self, targets: List[str], ports: List[str], rate: str) -> str:
        """Generate human-readable summary of masscan scan"""
        target_str = f"{len(targets)} target(s)" if len(targets) > 1 else targets[0] if targets else "unspecified targets"
        port_str = f" on {len(ports)} port range(s)" if ports else ""
        rate_str = f" at {rate} packets/second" if rate else ""
        
        return f"High-speed TCP port scan against {target_str}{port_str}{rate_str}"
    
    def _assess_nmap_risk(self, arguments: List[ArgumentExplanation]) -> str:
        """Assess the detectability/risk level of nmap scan"""
        high_risk_args = [arg for arg in arguments if arg.risk_level == 'high']
        medium_risk_args = [arg for arg in arguments if arg.risk_level == 'medium']
        
        if high_risk_args:
            return f"High detectability: Using {len(high_risk_args)} aggressive techniques"
        elif medium_risk_args:
            return f"Medium detectability: Using {len(medium_risk_args)} moderately detectable techniques"
        else:
            return "Low detectability: Using stealthy scanning techniques"
    
    def _assess_masscan_risk(self, arguments: List[ArgumentExplanation], rate: str) -> str:
        """Assess the detectability/risk level of masscan scan"""
        if rate:
            try:
                rate_val = int(rate.replace(',', ''))
                if rate_val > 10000:
                    return f"Very high detectability: Extremely high packet rate ({rate} pps)"
                elif rate_val > 1000:
                    return f"High detectability: High packet rate ({rate} pps)"
                else:
                    return f"Medium detectability: Moderate packet rate ({rate} pps)"
            except (ValueError, TypeError, AttributeError):
                pass
                
        return "High detectability: Fast scanning tool, likely to trigger IDS/IPS"
    
    def _init_nmap_arguments(self) -> Dict[str, Dict]:
        """Initialize nmap argument explanations"""
        return {
            # Scan Techniques
            '-sS': {
                'description': 'TCP SYN scan (half-open scan) - stealthy, doesn\'t complete connections',
                'category': 'Scan Techniques',
                'risk_level': 'low',
                'examples': ['-sS']
            },
            '-sT': {
                'description': 'TCP connect scan - completes full TCP connections',
                'category': 'Scan Techniques', 
                'risk_level': 'medium',
                'examples': ['-sT']
            },
            '-sU': {
                'description': 'UDP scan - scans UDP ports (slower than TCP)',
                'category': 'Scan Techniques',
                'risk_level': 'low',
                'examples': ['-sU']
            },
            '-sN': {
                'description': 'TCP NULL scan - sends packets with no flags set',
                'category': 'Scan Techniques',
                'risk_level': 'medium',
                'examples': ['-sN']
            },
            '-sF': {
                'description': 'TCP FIN scan - sends packets with FIN flag set',
                'category': 'Scan Techniques',
                'risk_level': 'medium',
                'examples': ['-sF']  
            },
            '-sX': {
                'description': 'TCP Xmas scan - sends packets with FIN, PSH, and URG flags',
                'category': 'Scan Techniques',
                'risk_level': 'medium',
                'examples': ['-sX']
            },
            
            # Port Specification
            '-p': {
                'description': 'Specify ports to scan',
                'category': 'Port Specification',
                'risk_level': 'low',
                'examples': ['-p 80', '-p 1-65535', '-p 22,80,443']
            },
            '-F': {
                'description': 'Fast scan - scan fewer ports than default',
                'category': 'Port Specification', 
                'risk_level': 'low',
                'examples': ['-F']
            },
            '--top-ports': {
                'description': 'Scan the most common ports',
                'category': 'Port Specification',
                'risk_level': 'low',
                'examples': ['--top-ports 100']
            },
            
            # Service Detection  
            '-sV': {
                'description': 'Version detection - determine service versions',
                'category': 'Service Detection',
                'risk_level': 'medium',
                'examples': ['-sV']
            },
            '--version-intensity': {
                'description': 'Set version scan intensity (0-9)',
                'category': 'Service Detection',
                'risk_level': 'medium',
                'examples': ['--version-intensity 5']
            },
            
            # OS Detection
            '-O': {
                'description': 'OS detection - identify operating system',
                'category': 'OS Detection', 
                'risk_level': 'medium',
                'examples': ['-O']
            },
            '--osscan-guess': {
                'description': 'Guess OS more aggressively',
                'category': 'OS Detection',
                'risk_level': 'high',
                'examples': ['--osscan-guess']
            },
            
            # Script Scanning
            '-sC': {
                'description': 'Default script scan - run default NSE scripts',
                'category': 'Script Scanning',
                'risk_level': 'medium', 
                'examples': ['-sC']
            },
            '--script': {
                'description': 'Run specific NSE scripts',
                'category': 'Script Scanning',
                'risk_level': 'high',
                'examples': ['--script vuln', '--script smb-enum-shares']
            },
            
            # Timing and Performance
            '-T0': {'description': 'Paranoid timing (very slow)', 'category': 'Timing', 'risk_level': 'low'},
            '-T1': {'description': 'Sneaky timing (slow)', 'category': 'Timing', 'risk_level': 'low'},
            '-T2': {'description': 'Polite timing (slower)', 'category': 'Timing', 'risk_level': 'low'},
            '-T3': {'description': 'Normal timing (default)', 'category': 'Timing', 'risk_level': 'medium'},
            '-T4': {'description': 'Aggressive timing (faster)', 'category': 'Timing', 'risk_level': 'high'},
            '-T5': {'description': 'Insane timing (very fast)', 'category': 'Timing', 'risk_level': 'high'},
            
            # Output Options
            '-oX': {
                'description': 'XML output format',
                'category': 'Output',
                'risk_level': 'low',
                'examples': ['-oX scan_results.xml']
            },
            '-oN': {
                'description': 'Normal output format', 
                'category': 'Output',
                'risk_level': 'low',
                'examples': ['-oN scan_results.txt']
            },
            '-v': {
                'description': 'Increase verbosity',
                'category': 'Output',
                'risk_level': 'low',
                'examples': ['-v', '-vv']
            },
            
            # Aggressive Options
            '-A': {
                'description': 'Aggressive scan (OS detection, version detection, scripts, traceroute)',
                'category': 'Aggressive',
                'risk_level': 'high',
                'examples': ['-A']
            }
        }
    
    def _init_masscan_arguments(self) -> Dict[str, Dict]:
        """Initialize masscan argument explanations"""
        return {
            '-p': {
                'description': 'Specify ports to scan',
                'category': 'Port Specification',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['-p 80', '-p 1-65535', '-p 22,80,443']
            },
            '--ports': {
                'description': 'Specify ports to scan (alternative syntax)',
                'category': 'Port Specification', 
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['--ports 80', '--ports 1-65535']
            },
            '--rate': {
                'description': 'Specify packet transmission rate',
                'category': 'Performance',
                'risk_level': 'high',
                'takes_value': True,
                'examples': ['--rate 1000', '--rate 10000']
            },
            '--max-rate': {
                'description': 'Maximum packet transmission rate',
                'category': 'Performance',
                'risk_level': 'high', 
                'takes_value': True,
                'examples': ['--max-rate 10000']
            },
            '--connection-timeout': {
                'description': 'TCP connection timeout in seconds',
                'category': 'Timing',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['--connection-timeout 10']
            },
            '--randomize-hosts': {
                'description': 'Randomize host scan order',
                'category': 'Stealth',
                'risk_level': 'low',
                'takes_value': False,
                'examples': ['--randomize-hosts']
            },
            '--source-ip': {
                'description': 'Specify source IP address for spoofing',
                'category': 'Stealth',
                'risk_level': 'high',
                'takes_value': True,
                'examples': ['--source-ip 192.168.1.100']
            },
            '--source-port': {
                'description': 'Specify source port',
                'category': 'Stealth',
                'risk_level': 'medium',
                'takes_value': True,
                'examples': ['--source-port 53']
            },
            '-oX': {
                'description': 'XML output format',
                'category': 'Output',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['-oX results.xml']
            },
            '-oJ': {
                'description': 'JSON output format',
                'category': 'Output',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['-oJ results.json']
            },
            '-oL': {
                'description': 'List output format (simple text)',
                'category': 'Output',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['-oL results.txt']
            },
            '--exclude': {
                'description': 'Exclude IP addresses/ranges from scan',
                'category': 'Target Specification',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['--exclude 192.168.1.1', '--exclude 10.0.0.0/8']
            },
            '--excludefile': {
                'description': 'Exclude IP addresses from file',
                'category': 'Target Specification',
                'risk_level': 'low',
                'takes_value': True,
                'examples': ['--excludefile exclude.txt']
            }
        }