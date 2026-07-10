import React, { useEffect, useState } from 'react';
import { Search, ChevronDown, ExternalLink, Copy, Loader2, RefreshCw } from 'lucide-react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { useToast } from '../contexts/ToastContext';
import {
  getToolReadiness,
  ToolReadinessResponse,
  ToolReadinessStatus,
} from '../services/api/references';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Tool catalogue — single source of truth
// ---------------------------------------------------------------------------

interface ToolEntry {
  name: string;
  description: string;
  category: string;
  ports: string;
  install: string;
  url: string;
  /** Included in Kali Linux by default */
  kali: boolean;
}

/**
 * BlueStick-ingestible run commands, keyed by tool name.  Only tools with a
 * parser get an entry — the exact invocation (with the machine-readable output
 * flag) that produces a file BlueStick can upload.  `note` explains what to
 * upload / any gotcha.  Kept as a sibling map (not inlined on every TOOLS row)
 * so the ~200-char catalogue lines stay readable; grounded in AGENTS.md's
 * "Supported upload formats" table and documentation/UPLOAD_FORMATS.md — keep
 * the two aligned.  `<target>` / list files are placeholders the operator fills.
 */
interface RunCommand {
  run: string;
  note?: string;
}

const RUN_COMMANDS: Record<string, RunCommand> = {
  // Port scanning
  nmap: { run: 'nmap -sV -sC -O -oX scan.xml <target>', note: 'Upload scan.xml (-oX). Use -oG for a .gnmap instead.' },
  masscan: { run: 'sudo masscan -p1-65535 --rate=1000 -oX masscan.xml <target>', note: 'Upload the XML (-oX), JSON (-oJ), or list (-oL).' },
  rustscan: { run: 'rustscan -a <target> -- -sV -oX scan.xml', note: 'Pipes into nmap — upload the resulting nmap scan.xml.' },
  naabu: { run: 'naabu -host <target> -json -o naabu.json', note: 'Upload naabu.json (-json). Include "naabu" in the filename.' },
  // Web analysis
  httpx: { run: 'httpx -l targets.txt -sc -title -tech-detect -favicon -json -o httpx.jsonl', note: 'Upload httpx.jsonl. Call ProjectDiscovery\'s binary by path if the Python httpx CLI shadows it.' },
  whatweb: { run: 'whatweb -a 3 --input-file=targets.txt --log-json=whatweb.json --no-errors', note: 'Upload whatweb.json (--log-json).' },
  eyewitness: { run: 'eyewitness --web -f urls.txt -d eyewitness_report --no-prompt', note: 'Upload the JSON/CSV report (filename must contain "eyewitness" or "report").' },
  nikto: { run: 'nikto -h <target> -Format json -o nikto.json', note: 'Upload nikto.json (-Format json).' },
  nuclei: { run: 'nuclei -l targets.txt -je nuclei.json', note: 'Upload nuclei.json (-je writes the JSON export).' },
  // SMB / AD
  smbmap: { run: 'smbmap -H <target> | tee smbmap.txt', note: 'Upload smbmap.txt — keep the "[+] <ip>" host lines.' },
  netexec: { run: "netexec smb <target> -u '' -p '' --shares", note: 'Upload the --json output or the standard text report.' },
  'bloodhound-python': { run: 'bloodhound-python -d <domain> -u <user> -p <pass> -c All -ns <dc-ip>', note: 'Upload the extracted JSON files, not the ZIP bundle.' },
  // DNS / subdomains
  amass: { run: 'amass enum -d <domain> -json amass.json', note: 'Upload amass.json — best results include resolved IPs.' },
  subfinder: { run: 'subfinder -d <domain> -oJ -o subfinder.json', note: 'Upload subfinder.json (-oJ) with resolved IPs.' },
  dnsx: { run: 'dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa -cname -mx -ns -txt -o dnsx-output.json', note: 'Upload dnsx-output.json (-j). PTR answers feed hostnames.' },
  // Content discovery (unified dirbuster parser — put the tool name in the filename)
  gobuster: { run: 'gobuster dir -u http://<target> -w wordlist.txt -o gobuster.txt', note: 'Upload gobuster.txt (.json/.csv/.txt all parse).' },
  feroxbuster: { run: 'feroxbuster -u http://<target> --json -o feroxbuster.json', note: 'Upload feroxbuster.json (--json).' },
  ffuf: { run: 'ffuf -u http://<target>/FUZZ -w wordlist.txt -of json -o ffuf.json', note: 'Upload ffuf.json (-of json).' },
  dirsearch: { run: 'dirsearch -u http://<target> --format json -o dirsearch.json', note: 'Upload dirsearch.json (--format json).' },
  dirb: { run: 'dirb http://<target> wordlist.txt -o dirb.txt', note: 'Upload dirb.txt.' },
  wfuzz: { run: 'wfuzz -w wordlist.txt -f wfuzz.json,json http://<target>/FUZZ', note: 'Upload wfuzz.json (-f … ,json).' },
};

const CATEGORIES = [
  'Web Content Discovery',
  'Web Analysis',
  'Port Scanning',
  'SMB / NetBIOS',
  'Remote Access',
  'Network Services',
  'Databases',
  'Active Directory',
  'General Purpose',
] as const;

type Category = (typeof CATEGORIES)[number];

type CategoryTone = 'default' | 'destructive' | 'warning' | 'success' | 'secondary' | 'info' | 'muted' | 'outline';

const CATEGORY_TONE: Record<Category, CategoryTone> = {
  'Web Content Discovery': 'default',
  'Web Analysis': 'info',
  'Port Scanning': 'destructive',
  'SMB / NetBIOS': 'warning',
  'Remote Access': 'secondary',
  'Network Services': 'success',
  Databases: 'muted',
  'Active Directory': 'warning',
  'General Purpose': 'muted',
};

const TOOLS: ToolEntry[] = [
  // Web Content Discovery
  { name: 'gobuster', description: 'Directory and DNS brute-force tool written in Go. Fast and lightweight, supports dir, dns, vhost, fuzz, and s3 modes. Ideal for discovering hidden paths and virtual hosts on web servers.', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install gobuster  # or  go install github.com/OJ/gobuster/v3@latest', url: 'https://github.com/OJ/gobuster', kali: true },
  { name: 'feroxbuster', description: 'Recursive content discovery tool written in Rust. Automatically discovers and recurses into directories, handles rate limiting gracefully, and supports response filtering by status code, word count, and line count.', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install feroxbuster  # or  cargo install feroxbuster', url: 'https://github.com/epi052/feroxbuster', kali: true },
  { name: 'ffuf', description: 'Fast web fuzzer written in Go. Extremely flexible — can fuzz any part of an HTTP request including URL paths, headers, POST data, and cookies. Output can be filtered by status code, response size, word count, or regex.', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install ffuf  # or  go install github.com/ffuf/ffuf/v2@latest', url: 'https://github.com/ffuf/ffuf', kali: true },
  { name: 'dirsearch', description: 'Web path brute-forcer written in Python. Ships with curated wordlists and supports recursive scanning, automatic calibration to ignore false positives, and multiple output formats (JSON, CSV, plain text).', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install dirsearch  # or  pip install dirsearch', url: 'https://github.com/maurosoria/dirsearch', kali: true },
  { name: 'dirb', description: 'Classic URL brute-forcer that tests for the existence of web objects by dictionary attack. Simple and reliable, though slower than modern alternatives like gobuster or ffuf.', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install dirb', url: 'https://dirb.sourceforge.net/', kali: true },
  { name: 'wfuzz', description: 'Flexible web fuzzer for brute-forcing directories, files, parameters, headers, cookies, and virtual hosts. Useful when a scan suggests hidden application surface beyond simple directory enumeration.', category: 'Web Content Discovery', ports: '80, 443, 8080, 8443', install: 'apt install wfuzz  # or  pip install wfuzz', url: 'https://github.com/xmendez/wfuzz', kali: true },
  // Web Analysis
  { name: 'httpx', description: 'Fast multi-purpose HTTP toolkit by ProjectDiscovery — the canonical first pass over web services. Probes status code, page title, server header, technology stack (Wappalyzer), favicon hash, TLS/CDN signals, and redirect chains, one HTTP request per target. Run it before eyewitness: httpx culls dead targets in seconds where eyewitness spins a headless browser per host. JSONL output ingests directly into BlueStick. Note: in Python-heavy environments the `httpx` name often resolves to the Python httpx CLI (incompatible flags) — install ProjectDiscovery’s binary and call it by explicit path.', category: 'Web Analysis', ports: '80, 443, 8080, 8443, 8000, 3000', install: 'go install github.com/projectdiscovery/httpx/cmd/httpx@latest  # or prebuilt binary from the releases page', url: 'https://github.com/projectdiscovery/httpx', kali: false },
  { name: 'eyewitness', description: 'Captures screenshots of web services and builds a browsable report, recording page titles, server headers, and default-credential hints. BlueStick ingests its CSV/JSON output (and the screenshots) into the per-host web-interface view. Run httpx first to cull dead targets, then eyewitness for visual triage.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install eyewitness', url: 'https://github.com/RedSiege/EyeWitness', kali: true },
  { name: 'nikto', description: 'Open-source web server scanner that checks for dangerous files, outdated server software, version-specific problems, and server configuration issues. Good for a quick initial assessment of web services. BlueStick ingests its JSON output.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install nikto', url: 'https://github.com/sullo/nikto', kali: true },
  { name: 'whatweb', description: 'Web technology fingerprinter that identifies content management systems, blogging platforms, JavaScript libraries, web servers, and embedded devices. Can detect version numbers, email addresses, and error messages.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install whatweb', url: 'https://github.com/urbanadventurer/WhatWeb', kali: true },
  { name: 'curl', description: 'Command-line tool for transferring data with URLs. Essential for manual HTTP requests, inspecting headers, testing APIs, and scripting web interactions. Supports virtually every protocol.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install curl  # usually pre-installed', url: 'https://curl.se/', kali: true },
  { name: 'testssl.sh', description: 'Specialized TLS and SSL auditing script that enumerates supported protocols, ciphers, certificates, and common HTTPS misconfigurations. A strong follow-up when scans report weak TLS settings.', category: 'Web Analysis', ports: '443, 8443, 9443', install: 'apt install testssl.sh', url: 'https://testssl.sh/', kali: true },
  { name: 'nuclei', description: 'Template-driven scanner used to validate known web exposures and common misconfigurations at scale. Helpful after service detection identifies a technology stack or likely vulnerability class.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install nuclei  # or  go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest', url: 'https://github.com/projectdiscovery/nuclei', kali: true },
  { name: 'wafw00f', description: 'Web application firewall fingerprinting tool. Useful when HTTP services behave inconsistently or appear to be fronted by a filtering or inspection layer.', category: 'Web Analysis', ports: '80, 443, 8080, 8443', install: 'apt install wafw00f', url: 'https://github.com/EnableSecurity/wafw00f', kali: true },
  // Port Scanning
  { name: 'nmap', description: 'The standard network exploration and security auditing tool. Discovers hosts and services, detects OS and service versions, and has a powerful scripting engine (NSE) with hundreds of scripts for vulnerability detection and enumeration.', category: 'Port Scanning', ports: 'All', install: 'apt install nmap', url: 'https://nmap.org/', kali: true },
  { name: 'masscan', description: 'Internet-scale port scanner. Can scan the entire Internet in under 6 minutes. Best for quickly finding open ports across large IP ranges before doing detailed service enumeration with nmap.', category: 'Port Scanning', ports: 'All', install: 'apt install masscan', url: 'https://github.com/robertdavidgraham/masscan', kali: true },
  { name: 'rustscan', description: 'Fast port scanner written in Rust that pipes results into nmap for service detection. Scans all 65,535 ports in seconds, then hands off to nmap for version/script scanning on discovered ports.', category: 'Port Scanning', ports: 'All', install: 'cargo install rustscan  # or download from GitHub releases', url: 'https://github.com/RustScan/RustScan', kali: false },
  { name: 'naabu', description: 'Fast port scanner written in Go by ProjectDiscovery. Designed for reliability and speed in bug bounty and penetration testing workflows, with native integration into other ProjectDiscovery tools.', category: 'Port Scanning', ports: 'All', install: 'go install github.com/projectdiscovery/naabu/v2/cmd/naabu@latest', url: 'https://github.com/projectdiscovery/naabu', kali: false },
  { name: 'unicornscan', description: 'Asynchronous TCP and UDP scanner useful for broad service discovery and validation, especially when a second opinion on open ports or banner behavior is needed.', category: 'Port Scanning', ports: 'All', install: 'apt install unicornscan', url: 'https://www.kali.org/tools/unicornscan/', kali: true },
  { name: 'amap', description: 'Application mapper that identifies services by protocol fingerprinting rather than relying only on port numbers. Useful for ambiguous or mislabeled services uncovered during scanning.', category: 'Port Scanning', ports: 'All', install: 'apt install amap', url: 'https://www.kali.org/tools/amap/', kali: true },
  // SMB / NetBIOS
  { name: 'smbclient', description: 'FTP-like client for accessing SMB/CIFS shares. Can list available shares, upload/download files, and interact with Windows file servers. Essential for testing null session access and share enumeration.', category: 'SMB / NetBIOS', ports: '139, 445', install: 'apt install smbclient  # part of samba-client', url: 'https://www.samba.org/', kali: true },
  { name: 'smbmap', description: 'SMB share enumerator that lists share permissions, finds files matching patterns, and tests access levels. Shows read/write/no-access for each share, making it easy to spot misconfigurations.', category: 'SMB / NetBIOS', ports: '139, 445', install: 'apt install smbmap  # or  pip install smbmap', url: 'https://github.com/ShawnDEvans/smbmap', kali: true },
  { name: 'enum4linux', description: 'Tool for enumerating information from Windows and Samba systems. Extracts user lists, share lists, group memberships, password policies, and OS information via SMB, RPC, and LDAP.', category: 'SMB / NetBIOS', ports: '139, 445', install: 'apt install enum4linux', url: 'https://github.com/CiscoCXSecurity/enum4linux', kali: true },
  { name: 'netexec', description: 'Swiss army knife for network protocol enumeration (successor to CrackMapExec). Supports SMB, WinRM, LDAP, MSSQL, RDP, SSH, and more. Tests credentials, enumerates shares, and executes commands across multiple hosts.', category: 'SMB / NetBIOS', ports: '139, 445, 389, 636, 5985, 5986, 1433, 3389, 22', install: 'pip install netexec  # or  apt install netexec', url: 'https://github.com/Pennyw0rth/NetExec', kali: true },
  { name: 'crackmapexec', description: 'Legacy predecessor to NetExec that many test plans and playbooks still reference for SMB, WinRM, MSSQL, and LDAP enumeration. Useful to include because analysts and agents often still suggest its older command syntax.', category: 'SMB / NetBIOS', ports: '139, 445, 389, 636, 5985, 5986, 1433, 3389', install: 'apt install crackmapexec', url: 'https://github.com/byt3bl33d3r/CrackMapExec', kali: true },
  { name: 'rpcclient', description: 'Samba RPC client for querying users, groups, shares, policies, and domain information from Windows hosts over SMB and MSRPC. Particularly useful during null-session or low-privilege enumeration.', category: 'SMB / NetBIOS', ports: '139, 445', install: 'apt install samba-common-bin', url: 'https://www.samba.org/samba/docs/current/man-html/rpcclient.1.html', kali: true },
  { name: 'smbget', description: 'Non-interactive SMB downloader that works like wget for SMB shares. Handy when share enumeration reveals anonymously accessible files that need quick retrieval for review.', category: 'SMB / NetBIOS', ports: '139, 445', install: 'apt install smbclient', url: 'https://www.samba.org/samba/docs/current/man-html/smbget.1.html', kali: true },
  // Remote Access
  { name: 'ssh', description: 'Secure Shell client for encrypted remote login. The primary tool for administering Linux/Unix systems remotely. Supports key-based authentication, port forwarding, SOCKS proxying, and tunneling.', category: 'Remote Access', ports: '22', install: 'apt install openssh-client  # usually pre-installed', url: 'https://www.openssh.com/', kali: true },
  { name: 'ssh-audit', description: 'SSH server and client configuration auditor. Checks algorithms, key exchange methods, ciphers, and MACs against known vulnerabilities. Reports weak or deprecated configurations.', category: 'Remote Access', ports: '22', install: 'pip install ssh-audit  # or  apt install ssh-audit', url: 'https://github.com/jtesta/ssh-audit', kali: false },
  { name: 'xfreerdp', description: 'Free RDP client for connecting to Windows Remote Desktop. Supports NLA, TLS, clipboard redirection, drive mapping, and multi-monitor. Part of the FreeRDP project.', category: 'Remote Access', ports: '3389', install: 'apt install freerdp2-x11', url: 'https://www.freerdp.com/', kali: true },
  { name: 'telnet', description: 'Classic network protocol client for unencrypted interactive communication. Useful for banner grabbing and manual protocol interaction on plain-text services like SMTP, POP3, and HTTP.', category: 'Remote Access', ports: '23', install: 'apt install telnet  # usually pre-installed', url: 'https://en.wikipedia.org/wiki/Telnet', kali: true },
  { name: 'vncviewer', description: 'VNC client for connecting to remote desktops via the VNC/RFB protocol. Used to interact with graphical sessions on Linux and Windows hosts that expose VNC.', category: 'Remote Access', ports: '5900, 5901', install: 'apt install tigervnc-viewer', url: 'https://tigervnc.org/', kali: true },
  { name: 'hydra', description: 'Parallelized network login cracker frequently used for cautious default-credential checks against SSH, RDP, VNC, FTP, HTTP auth, and other exposed remote access services.', category: 'Remote Access', ports: '21, 22, 23, 80, 443, 3389, 5900', install: 'apt install hydra', url: 'https://github.com/vanhauser-thc/thc-hydra', kali: true },
  { name: 'evil-winrm', description: 'WinRM shell client tailored for Windows administration and post-authentication validation. Commonly suggested when scans reveal port 5985 or 5986 and usable credentials are available.', category: 'Remote Access', ports: '5985, 5986', install: 'apt install evil-winrm', url: 'https://github.com/Hackplayers/evil-winrm', kali: true },
  // Network Services
  { name: 'ftp', description: 'Standard FTP client for file transfers. Useful for testing anonymous login, directory traversal, and file access on FTP servers.', category: 'Network Services', ports: '21', install: 'apt install ftp  # usually pre-installed', url: 'https://en.wikipedia.org/wiki/File_Transfer_Protocol', kali: true },
  { name: 'dig', description: 'DNS lookup utility for querying DNS servers. Can retrieve A, AAAA, MX, NS, TXT, and other record types. Essential for DNS enumeration, zone transfer testing, and verifying DNS configurations.', category: 'Network Services', ports: '53', install: 'apt install dnsutils', url: 'https://www.isc.org/bind/', kali: true },
  { name: 'snmpwalk', description: 'SNMP client that retrieves a tree of values from a network device using SNMP GET-NEXT requests. Can expose system information, interfaces, routing tables, and running processes if community strings are known.', category: 'Network Services', ports: '161 (UDP)', install: 'apt install snmp', url: 'http://www.net-snmp.org/', kali: true },
  { name: 'onesixtyone', description: 'Fast SNMP community string brute-forcer. Sends SNMP requests asynchronously to quickly test large lists of community strings against one or many hosts.', category: 'Network Services', ports: '161 (UDP)', install: 'apt install onesixtyone', url: 'https://github.com/trailofbits/onesixtyone', kali: true },
  { name: 'nc', description: 'Netcat — the network swiss army knife. Reads and writes data across TCP/UDP connections. Essential for banner grabbing, port testing, file transfers, and creating reverse/bind shells.', category: 'Network Services', ports: 'Any', install: 'apt install netcat-openbsd  # usually pre-installed', url: 'https://en.wikipedia.org/wiki/Netcat', kali: true },
  { name: 'ldapsearch', description: 'LDAP query tool for searching directory services. Can enumerate users, groups, OUs, and computer objects in Active Directory or OpenLDAP. Critical for AD reconnaissance.', category: 'Network Services', ports: '389, 636', install: 'apt install ldap-utils', url: 'https://www.openldap.org/', kali: true },
  { name: 'dnsrecon', description: 'DNS enumeration framework that performs standard record lookups, reverse lookups, brute force, SRV discovery, and zone transfer checks when scans indicate exposed DNS infrastructure.', category: 'Network Services', ports: '53', install: 'apt install dnsrecon', url: 'https://github.com/darkoperator/dnsrecon', kali: true },
  { name: 'dnsenum', description: 'Perl-based DNS enumeration utility for gathering hostnames, name servers, mail servers, subdomains, and attempted zone transfers. Useful as a lightweight DNS validation companion to dig.', category: 'Network Services', ports: '53', install: 'apt install dnsenum', url: 'https://github.com/fwaeytens/dnsenum', kali: true },
  { name: 'snmp-check', description: 'SNMP enumeration script that summarizes system information, processes, interfaces, shares, and routing details once a valid community string is identified.', category: 'Network Services', ports: '161 (UDP)', install: 'apt install snmp-check', url: 'https://www.kali.org/tools/snmpcheck/', kali: true },
  { name: 'amass', description: 'In-depth DNS / subdomain enumeration and attack-surface mapping by OWASP, combining passive sources, brute force, and name permutations. BlueStick ingests its JSON output as DNS records / hosts.', category: 'Network Services', ports: '53', install: 'apt install amass  # or  go install github.com/owasp-amass/amass/v4/...@master', url: 'https://github.com/owasp-amass/amass', kali: true },
  { name: 'subfinder', description: 'Fast passive subdomain enumeration tool by ProjectDiscovery that aggregates results from many DNS sources. BlueStick ingests its JSON (-oJ) output as DNS records / hosts.', category: 'Network Services', ports: '53', install: 'go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest', url: 'https://github.com/projectdiscovery/subfinder', kali: true },
  { name: 'dnsx', description: 'Fast multi-purpose DNS toolkit by ProjectDiscovery for resolving and probing records (A / AAAA / CNAME / MX / NS / TXT) at scale. BlueStick ingests its JSON (-json) output as DNS records.', category: 'Network Services', ports: '53', install: 'go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest', url: 'https://github.com/projectdiscovery/dnsx', kali: false },
  // Databases
  { name: 'mysql', description: 'MySQL command-line client for connecting to MySQL and MariaDB databases. Allows executing SQL queries, dumping data, and testing credentials.', category: 'Databases', ports: '3306', install: 'apt install default-mysql-client', url: 'https://www.mysql.com/', kali: true },
  { name: 'impacket-mssqlclient', description: 'Python-based MSSQL client from the Impacket toolkit. Supports Windows authentication, SQL query execution, and command execution via xp_cmdshell. Part of the essential Impacket suite.', category: 'Databases', ports: '1433', install: 'apt install python3-impacket  # or  pip install impacket', url: 'https://github.com/fortra/impacket', kali: true },
  { name: 'psql', description: 'PostgreSQL interactive terminal. Full-featured client for connecting to PostgreSQL databases, executing queries, managing schemas, and scripting database operations.', category: 'Databases', ports: '5432', install: 'apt install postgresql-client', url: 'https://www.postgresql.org/', kali: true },
  { name: 'redis-cli', description: 'Redis command-line interface for interacting with Redis key-value stores. Can read/write keys, check server configuration, and test for unauthenticated access.', category: 'Databases', ports: '6379', install: 'apt install redis-tools', url: 'https://redis.io/', kali: false },
  { name: 'sqlcmd', description: 'Microsoft SQL Server command-line client for running queries, checking connectivity, and validating credentials against exposed MSSQL services.', category: 'Databases', ports: '1433', install: 'apt install mssql-tools18', url: 'https://learn.microsoft.com/sql/tools/sqlcmd/sqlcmd-utility', kali: false },
  { name: 'mongosh', description: 'MongoDB shell for interacting with exposed MongoDB instances, validating authentication settings, and inspecting accessible databases and collections.', category: 'Databases', ports: '27017', install: 'apt install mongodb-mongosh', url: 'https://www.mongodb.com/docs/mongodb-shell/', kali: false },
  { name: 'sqsh', description: 'Interactive SQL shell for Microsoft SQL Server and Sybase. A practical alternative when lightweight manual MSSQL interaction is preferred over heavier frameworks.', category: 'Databases', ports: '1433', install: 'apt install sqsh', url: 'https://github.com/vonloxley/sqsh', kali: true },
  // Active Directory
  { name: 'impacket', description: 'Collection of Python classes for working with network protocols. Includes tools for SMB relay, Kerberos attacks, NTLM authentication, MSSQL interaction, and more. A foundational toolkit for Active Directory pentesting.', category: 'Active Directory', ports: '88, 135, 139, 389, 445, 636, 1433, 3389, 5985', install: 'apt install python3-impacket  # or  pip install impacket', url: 'https://github.com/fortra/impacket', kali: true },
  { name: 'kerbrute', description: 'Kerberos pre-authentication enumeration and password-spraying tool used to validate usernames and cautiously test authentication exposure in Active Directory environments.', category: 'Active Directory', ports: '88', install: 'apt install kerbrute  # or  go install github.com/ropnop/kerbrute@latest', url: 'https://github.com/ropnop/kerbrute', kali: true },
  { name: 'bloodhound-python', description: 'Python ingestor for BloodHound that collects Active Directory relationship data over LDAP, SMB, and Kerberos. Useful when scan results indicate a domain-connected Windows environment.', category: 'Active Directory', ports: '53, 88, 135, 389, 445, 636, 3268, 3269', install: 'apt install bloodhound.py  # or  pip install bloodhound', url: 'https://github.com/dirkjanm/BloodHound.py', kali: true },
  { name: 'certipy-ad', description: 'Active Directory Certificate Services assessment tool for enumerating templates, certificate authorities, and ESC-style abuse paths in AD CS deployments.', category: 'Active Directory', ports: '80, 135, 389, 445', install: 'pip install certipy-ad', url: 'https://github.com/ly4k/Certipy', kali: false },
  { name: 'ldapdomaindump', description: 'LDAP-based Active Directory information dumper that exports users, groups, computers, policy data, and trust information into browsable reports.', category: 'Active Directory', ports: '389, 636', install: 'apt install ldapdomaindump  # or  pip install ldapdomaindump', url: 'https://github.com/dirkjanm/ldapdomaindump', kali: true },
  // General Purpose
  { name: 'openssl', description: 'General-purpose TLS and cryptography toolkit. Frequently used for certificate inspection, STARTTLS probing, and manual testing of HTTPS and other SSL/TLS-enabled services.', category: 'General Purpose', ports: '443, 465, 587, 636, 8443', install: 'apt install openssl  # usually pre-installed', url: 'https://www.openssl.org/', kali: true },
  { name: 'wget', description: 'Command-line retriever for HTTP, HTTPS, and FTP content. Useful for quickly downloading exposed files, backups, or web resources discovered during enumeration.', category: 'General Purpose', ports: '21, 80, 443, 8080, 8443', install: 'apt install wget  # usually pre-installed', url: 'https://www.gnu.org/software/wget/', kali: true },
  { name: 'socat', description: 'Bidirectional data relay tool useful for raw protocol testing, banner collection, ad hoc tunneling, and pivot-friendly TCP/UDP interactions when simple netcat behavior is not enough.', category: 'General Purpose', ports: 'Any', install: 'apt install socat', url: 'https://www.dest-unreach.org/socat/', kali: true },
];

/** Names present in the static TOOLS catalogue above — used by the
 *  Host Readiness panel to decide whether "no install hint here →
 *  scroll to the catalogue entry" is a worthwhile affordance for a
 *  given tool. */
const STATIC_TOOL_NAMES = new Set(TOOLS.map((t) => t.name));

const scrollToCatalogueEntry = (toolName: string): void => {
  const el = document.getElementById(`tool-row-${toolName}`);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Brief highlight so the operator's eye lands on the right row —
    // CSS classes flicker via an inline style + setTimeout because we
    // don't want to thread a stateful "highlighted row" through the
    // catalogue render.
    el.classList.add('bg-accent');
    setTimeout(() => el.classList.remove('bg-accent'), 1200);
  }
};

// ---------------------------------------------------------------------------
// Host readiness — probe-driven view
// ---------------------------------------------------------------------------

const READINESS_BADGE: Record<ToolReadinessStatus, CategoryTone> = {
  installed: 'success',
  missing: 'destructive',
  warn: 'warning',
  unknown: 'outline',
};

const READINESS_LABEL: Record<ToolReadinessStatus, string> = {
  installed: 'Installed',
  missing: 'Missing',
  warn: 'Warning',
  unknown: 'Unknown',
};

/** Pick the most appropriate install command for a tool, preferring the
 *  probe's OS-derived provider, then a sensible fallback order. */
const pickInstallHint = (
  hints: Record<string, string>,
  preferred?: string | null,
): string | null => {
  if (preferred && hints[preferred]) return hints[preferred];
  return (
    hints.apt ||
    hints.brew ||
    hints.pipx ||
    hints.go ||
    hints.cargo ||
    hints.binary ||
    hints.docker ||
    null
  );
};

/**
 * Probe-driven host readiness — the agent tool catalog cross-referenced
 * against the operator's most recent environment probe.  Sits above the
 * static catalogue below; this panel reflects *this* host specifically.
 */
const HostReadinessPanel: React.FC = () => {
  const toast = useToast();
  const [data, setData] = useState<ToolReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toolFilter, setToolFilter] = useState('');

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    getToolReadiness()
      .then(setData)
      .catch(() => setError('Could not load host readiness.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const copyInstallScript = () => {
    if (!data) return;
    const missing = data.tools.filter((t) => t.status === 'missing');
    const header =
      `# Install commands for ${missing.length} missing tool` +
      `${missing.length === 1 ? '' : 's'}` +
      `${data.os_family ? ` — ${data.os_family} host` : ''}\n` +
      `# Generated by BlueStick from your environment probe` +
      `${data.probed_at ? ` (${new Date(data.probed_at).toLocaleString()})` : ''}.\n` +
      `# Review each line before running — package names and providers vary by host.\n`;
    const blocks = missing.map((t) => {
      const cmd = pickInstallHint(t.install_hints || {}, data.preferred_provider);
      return cmd
        ? `# ${t.tool}\n${cmd}`
        : `# ${t.tool} — no catalog install hint; see the catalogue below`;
    });
    navigator.clipboard.writeText([header, ...blocks].join('\n')).then(
      () => toast.success('Install commands copied to clipboard'),
      () => toast.error('Could not copy to clipboard'),
    );
  };

  // Readiness-table filter — scoped to this panel only (the search box
  // below the panel filters the static catalogue, not this table).
  const query = toolFilter.trim().toLowerCase();
  const visibleTools =
    data && data.has_probe
      ? query
        ? data.tools.filter((t) => t.tool.toLowerCase().includes(query))
        : data.tools
      : [];

  return (
    <Card className="mb-md" aria-busy={loading}>
      <CardContent className="p-md">
        <div className="mb-sm flex flex-wrap items-start justify-between gap-sm">
          <div className="min-w-0">
            <h2 className="text-subheading font-semibold">Host Readiness</h2>
            <p className="text-metadata text-muted-foreground">
              The agent tool catalog checked against your most recent environment probe —
              what this host already has and what it still needs for agentic workflows.
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            {loading ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="size-4" aria-hidden />
            )}
            Refresh
          </Button>
        </div>

        {/* First load only — a refresh keeps the prior data on screen
            (aria-busy on the Card + the spinning Refresh button signal
            the in-flight fetch) to avoid blanking the panel. */}
        {loading && !data && (
          <p className="text-metadata text-muted-foreground">Loading host readiness…</p>
        )}

        {error && (
          <Alert variant="destructive" className={data ? 'mb-sm' : undefined}>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {data && !data.has_probe && (
          <Alert variant="info">
            <AlertDescription>
              No environment probe recorded yet. Start an agentic <strong>recon</strong> or{' '}
              <strong>execution</strong> workflow — the agent probes your host at startup, and
              this panel will then show which catalog tools are installed and which are missing.
            </AlertDescription>
          </Alert>
        )}

        {data && data.has_probe && (
          <div className="flex flex-col gap-sm">
            <p className="text-caption text-muted-foreground">
              {data.os_family && (
                <>
                  Host: <strong>{data.os_release || data.os_family}</strong>
                </>
              )}
              {data.shell && <> · shell {data.shell}</>}
              {data.probed_at && <> · probed {new Date(data.probed_at).toLocaleString()}</>}
            </p>
            <div className="flex flex-wrap gap-xs">
              <Badge variant="success">{data.summary.installed} installed</Badge>
              <Badge variant="destructive">{data.summary.missing} missing</Badge>
              <Badge variant="warning">{data.summary.warn} warning</Badge>
              <Badge variant="outline">{data.summary.unknown} unknown</Badge>
            </div>
            {data.summary.missing > 0 && (
              <div>
                <Button size="sm" onClick={copyInstallScript}>
                  <Copy className="size-4" aria-hidden /> Copy install commands (
                  {data.summary.missing} missing)
                </Button>
              </div>
            )}
            <div className="relative max-w-xs">
              <Search
                className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                placeholder="Filter readiness by tool…"
                value={toolFilter}
                onChange={(e) => setToolFilter(e.target.value)}
                className="pl-xl"
                aria-label="Filter host readiness by tool name"
              />
            </div>
            {visibleTools.length === 0 ? (
              <p className="text-metadata text-muted-foreground">
                No tools match "{toolFilter}".
              </p>
            ) : (
              <div className="overflow-x-auto rounded-panel border border-border">
                <Table className="min-w-[720px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[20%]">Tool</TableHead>
                      <TableHead className="w-[14%]">Status</TableHead>
                      <TableHead className="w-[32%]">Details</TableHead>
                      <TableHead className="w-[34%]">Install</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleTools.map((t) => {
                      const hint = pickInstallHint(
                        t.install_hints || {},
                        data.preferred_provider,
                      );
                      const detail =
                        t.issue ||
                        t.path ||
                        (t.status === 'unknown'
                          ? 'Not reported by the probe'
                          : t.status === 'installed'
                            ? 'On PATH'
                            : '—');
                      return (
                        <TableRow key={t.tool}>
                          <TableCell>
                            <span className="font-semibold text-foreground break-words">
                              {t.tool}
                            </span>
                            {t.intrusive && (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Badge
                                    variant="warning"
                                    className="ml-xs cursor-help"
                                  >
                                    intrusive
                                  </Badge>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-sm">
                                  Generates active scanning traffic or runs
                                  potentially-impactful checks (vulnerability
                                  scans, exploit templates, brute force). The
                                  agent requests per-command approval before
                                  running these — they do not batch under
                                  plan-level approval.
                                </TooltipContent>
                              </Tooltip>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant={READINESS_BADGE[t.status]}>
                              {READINESS_LABEL[t.status]}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="line-clamp-2 cursor-help text-caption text-muted-foreground break-words">
                                  {detail}
                                </span>
                              </TooltipTrigger>
                              <TooltipContent className="max-w-sm">{detail}</TooltipContent>
                            </Tooltip>
                          </TableCell>
                          <TableCell>
                            {t.status === 'installed' ? (
                              <span className="text-caption text-muted-foreground">—</span>
                            ) : hint ? (
                              <code className="block break-words font-mono text-caption text-foreground">
                                {hint}
                              </code>
                            ) : STATIC_TOOL_NAMES.has(t.tool) ? (
                              // No install hint in the dynamic catalog,
                              // but the static catalogue below has an
                              // install command for this tool — link
                              // there directly instead of leaving the
                              // operator to scroll-and-search.
                              <button
                                type="button"
                                onClick={() => scrollToCatalogueEntry(t.tool)}
                                className="text-caption text-primary underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
                              >
                                View install command in catalogue ↓
                              </button>
                            ) : (
                              <span className="text-caption text-muted-foreground">
                                No install hint available
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const ToolReference: React.FC = () => {
  const toast = useToast();
  const [filter, setFilter] = useState('');

  const lowerFilter = filter.toLowerCase();
  const filtered = TOOLS.filter(
    (t) =>
      t.name.toLowerCase().includes(lowerFilter) ||
      t.description.toLowerCase().includes(lowerFilter) ||
      t.category.toLowerCase().includes(lowerFilter) ||
      t.ports.toLowerCase().includes(lowerFilter),
  );

  const grouped = CATEGORIES.reduce<Record<Category, ToolEntry[]>>(
    (acc, cat) => {
      const items = filtered.filter((t) => t.category === cat);
      if (items.length) acc[cat] = items;
      return acc;
    },
    {} as Record<Category, ToolEntry[]>,
  );

  const copyInstall = (cmd: string, toolName: string) => {
    const trimmed = cmd.split('#')[0].trim();
    navigator.clipboard.writeText(trimmed).then(
      () => toast.success(`Copied install command for ${toolName}`, { id: `copy-${toolName}` }),
      () => toast.error('Could not copy to clipboard'),
    );
  };

  // Run commands are copied VERBATIM — unlike install strings they carry no
  // "# or" alternative, and the output flags (-oX / -json / …) are exactly
  // what makes the result ingestible, so we must not strip anything.
  const copyRun = (cmd: string, toolName: string) => {
    navigator.clipboard.writeText(cmd).then(
      () => toast.success(`Copied run command for ${toolName}`, { id: `copyrun-${toolName}` }),
      () => toast.error('Could not copy to clipboard'),
    );
  };

  const groupedEntries = Object.entries(grouped) as Array<[Category, ToolEntry[]]>;

  return (
    <div className="p-md md:p-lg">
      <h1 className="text-page-title">Tool Reference</h1>
      <p className="mt-xxs mb-md text-metadata text-muted-foreground">
        Tools available as connection helpers on the host detail page. Each tool is suggested when
        a matching port or service is detected. Use the install commands below to set up any tools
        you are missing — and, where shown, the <span className="font-medium text-foreground">Run for
        BlueStick</span> command to produce output BlueStick can ingest.
      </p>

      <HostReadinessPanel />

      <div className="relative mb-md max-w-md">
        <Search
          className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          type="search"
          placeholder="Filter by name, category, description, or port..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="pl-xl"
          aria-label="Filter tools"
        />
      </div>

      {groupedEntries.length === 0 ? (
        <Card>
          <CardContent className="p-lg text-center text-metadata text-muted-foreground">
            No tools match "{filter}"
          </CardContent>
        </Card>
      ) : (
        <Accordion
          type="multiple"
          defaultValue={groupedEntries.map(([cat]) => cat)}
          className="flex flex-col gap-sm"
        >
          {groupedEntries.map(([category, tools]) => (
            <AccordionItem
              key={category}
              value={category}
              className="rounded-panel border border-border bg-card px-md"
            >
              <AccordionTrigger>
                <div className="flex items-center gap-sm">
                  <Badge variant={CATEGORY_TONE[category]}>{category}</Badge>
                  <span className="text-metadata font-medium text-muted-foreground">
                    {tools.length} tool{tools.length === 1 ? '' : 's'}
                  </span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="pb-md">
                <div className="overflow-x-auto rounded-panel border border-border">
                  <Table className="min-w-[860px]">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[14%]">Tool</TableHead>
                        <TableHead className="w-[34%]">Description</TableHead>
                        <TableHead className="w-[10%]">Ports</TableHead>
                        <TableHead className="w-[34%]">Install / Run</TableHead>
                        <TableHead className="w-[8%] text-center">Kali</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tools.map((tool) => (
                        // id lets the Host Readiness panel above scroll
                        // to a specific catalogue row when the operator
                        // clicks "View in catalogue" from a row whose
                        // dynamic catalog lacks an install_hints entry.
                        <TableRow key={tool.name} id={`tool-row-${tool.name}`}>
                          <TableCell>
                            <a
                              href={tool.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-xxs font-semibold text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                            >
                              <span className="truncate">{tool.name}</span>
                              <ExternalLink className="size-3 shrink-0" aria-hidden />
                            </a>
                          </TableCell>
                          <TableCell>
                            <span className="line-clamp-2 text-metadata text-foreground">
                              {tool.description}
                            </span>
                          </TableCell>
                          <TableCell>
                            <code className="font-mono text-caption text-foreground break-words">
                              {tool.ports}
                            </code>
                          </TableCell>
                          <TableCell>
                            <div className="space-y-xs">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <button
                                    type="button"
                                    onClick={() => copyInstall(tool.install, tool.name)}
                                    className={cn(
                                      'block w-full rounded-control bg-muted px-xs py-xxs text-left font-mono text-caption text-foreground break-words',
                                      'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                    )}
                                  >
                                    {tool.install}
                                  </button>
                                </TooltipTrigger>
                                <TooltipContent>Click to copy install command</TooltipContent>
                              </Tooltip>
                              {RUN_COMMANDS[tool.name] && (
                                <div className="space-y-xxs">
                                  <span className="block text-caption font-medium uppercase tracking-wider text-muted-foreground">
                                    Run for BlueStick
                                  </span>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <button
                                        type="button"
                                        onClick={() => copyRun(RUN_COMMANDS[tool.name].run, tool.name)}
                                        className={cn(
                                          'block w-full rounded-control border border-info/40 bg-info/10 px-xs py-xxs text-left font-mono text-caption text-foreground break-words',
                                          'transition-colors hover:bg-info/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                        )}
                                      >
                                        {RUN_COMMANDS[tool.name].run}
                                      </button>
                                    </TooltipTrigger>
                                    <TooltipContent>Click to copy — produces BlueStick-ingestible output</TooltipContent>
                                  </Tooltip>
                                  {RUN_COMMANDS[tool.name].note && (
                                    <span className="block text-caption text-muted-foreground break-words">
                                      {RUN_COMMANDS[tool.name].note}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-center">
                            {tool.kali ? (
                              <Badge variant="success">Yes</Badge>
                            ) : (
                              <Badge variant="outline">No</Badge>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      )}
    </div>
  );
};

export default ToolReference;
