import React, { useMemo } from 'react';
import {
  Shield,
  AlertTriangle,
  AlertOctagon,
  Info,
  Calculator,
  Gauge,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';

type SeverityVariant = 'destructive' | 'warning' | 'info' | 'success' | 'muted';

const SEVERITY_VARIANT: Record<string, SeverityVariant> = {
  Critical: 'destructive',
  High: 'warning',
  'Medium-High': 'warning',
  Medium: 'info',
  Low: 'success',
  Info: 'muted',
};

const portRiskCategories = [
  { category: 'Administrative Access', ports: ['22 (SSH)', '23 (Telnet)', '3389 (RDP)', '5900 (VNC)', '5985 (WinRM)'], riskLevel: 'Critical', reasoning: 'Direct system access — prime targets for credential attacks and lateral movement.' },
  { category: 'Database Services', ports: ['3306 (MySQL)', '5432 (PostgreSQL)', '1433 (MSSQL)', '1521 (Oracle)', '27017 (MongoDB)', '6379 (Redis)', '9200 (Elasticsearch)'], riskLevel: 'Critical', reasoning: 'Database exposure can lead to data exfiltration, injection, and complete system compromise.' },
  { category: 'Directory Services', ports: ['389 (LDAP)', '636 (LDAPS)', '88 (Kerberos)', '3268 (Global Catalog)'], riskLevel: 'High', reasoning: 'Active Directory and LDAP services reveal domain structure and enable credential attacks.' },
  { category: 'File Transfer & Sharing', ports: ['21 (FTP)', '445 (SMB)', '139 (NetBIOS)', '2049 (NFS)', '69 (TFTP)'], riskLevel: 'High', reasoning: 'File services can expose sensitive data, enable lateral movement, and allow anonymous access.' },
  { category: 'Web Services', ports: ['80 (HTTP)', '443 (HTTPS)', '8080 (HTTP Alt)', '8443 (HTTPS Alt)'], riskLevel: 'Medium-High', reasoning: 'Web services are common attack vectors but may be legitimately exposed. Check for misconfigurations.' },
  { category: 'Email & Communication', ports: ['25 (SMTP)', '110 (POP3)', '143 (IMAP)', '993 (IMAPS)', '995 (POP3S)'], riskLevel: 'Medium', reasoning: 'Email services can leak information and enable relay attacks if misconfigured.' },
  { category: 'Management & Monitoring', ports: ['161 (SNMP)', '623 (IPMI)', '514 (Syslog/RSH)', '135 (MS-RPC)'], riskLevel: 'High', reasoning: 'Management interfaces often have weak auth and can provide deep system access.' },
];

const serviceWeights = [
  { service: 'Telnet', weight: 10, rationale: 'Cleartext protocol with no encryption — highest risk' },
  { service: 'SMB', weight: 9, rationale: 'Lateral movement, ransomware propagation (WannaCry, EternalBlue)' },
  { service: 'RDP', weight: 9, rationale: 'Remote desktop — direct GUI access, BlueKeep vulnerable' },
  { service: 'VNC', weight: 9, rationale: 'Remote access often with weak or no authentication' },
  { service: 'FTP', weight: 8, rationale: 'Cleartext credentials, anonymous access risk' },
  { service: 'Redis', weight: 8, rationale: 'Often unauthenticated, allows arbitrary command execution' },
  { service: 'LDAP', weight: 7, rationale: 'Domain enumeration, credential harvesting' },
  { service: 'SNMP', weight: 7, rationale: 'Default community strings reveal system configuration' },
  { service: 'MySQL', weight: 7, rationale: 'Database access, potential empty root password' },
  { service: 'MSSQL', weight: 7, rationale: 'Database access, xp_cmdshell for command execution' },
  { service: 'NFS', weight: 7, rationale: 'Network shares often world-readable' },
  { service: 'PostgreSQL', weight: 6, rationale: 'Database access with trust auth misconfiguration' },
  { service: 'MongoDB', weight: 6, rationale: 'Often deployed without authentication' },
  { service: 'SMTP', weight: 6, rationale: 'Open relay testing, user enumeration' },
  { service: 'WinRM', weight: 6, rationale: 'Remote PowerShell execution' },
  { service: 'Elasticsearch', weight: 5, rationale: 'Typically unauthenticated, data exposure' },
  { service: 'HTTP', weight: 5, rationale: 'Unencrypted web traffic, application vulnerabilities' },
  { service: 'SSH', weight: 4, rationale: 'Encrypted but subject to brute force and key compromise' },
  { service: 'DNS', weight: 4, rationale: 'Zone transfers, cache poisoning' },
  { service: 'HTTPS', weight: 3, rationale: 'Encrypted — lowest base risk among common services' },
];

const vulnerabilityServiceChecks = [
  { service: 'SSH', check: 'SSH protocol version < 2.0', points: 30, severity: 'Critical' },
  { service: 'SMB', check: 'SMBv1 enabled (EternalBlue / WannaCry)', points: 35, severity: 'Critical' },
  { service: 'MySQL', check: 'Empty root password', points: 30, severity: 'Critical' },
  { service: 'MSSQL', check: 'Empty SA password', points: 30, severity: 'Critical' },
  { service: 'RDP', check: 'BlueKeep vulnerability (CVE-2019-0708)', points: 35, severity: 'Critical' },
  { service: 'Telnet', check: 'Service running (inherently insecure)', points: 25, severity: 'High' },
  { service: 'SMTP', check: 'Open relay configured', points: 20, severity: 'High' },
  { service: 'FTP', check: 'Anonymous access allowed', points: 15, severity: 'Medium' },
  { service: 'RDP', check: 'Service exposed externally', points: 15, severity: 'Medium' },
  { service: 'SSH', check: 'Weak password authentication enabled', points: 10, severity: 'Medium' },
  { service: 'MySQL', check: 'Database service exposed', points: 10, severity: 'Medium' },
  { service: 'FTP', check: 'Cleartext authentication', points: 10, severity: 'Medium' },
  { service: 'HTTP', check: 'Server banner disclosure', points: 5, severity: 'Low' },
];

const weightVariant = (w: number): SeverityVariant =>
  w >= 9 ? 'destructive' : w >= 7 ? 'warning' : w >= 5 ? 'info' : 'success';

const RiskAssessment: React.FC = () => {
  const riskLevels = useMemo(
    () => [
      {
        level: 'Critical',
        variant: 'destructive' as const,
        range: '80 - 100',
        description: 'Immediate security threats requiring urgent attention',
        criteria: [
          'Open administrative ports (SSH, RDP, Telnet) exposed to public networks',
          'Unencrypted protocols handling sensitive data (HTTP, FTP, Telnet)',
          'Database services exposed without proper access controls',
          'Known vulnerable services with public exploits available',
        ],
      },
      {
        level: 'High',
        variant: 'warning' as const,
        range: '60 - 79',
        description: 'Significant security concerns that should be addressed promptly',
        criteria: [
          'Services running on non-standard ports that may indicate evasion',
          'Outdated protocols with known security limitations',
          'Services that commonly have misconfigurations',
          'High-value targets with elevated privilege access',
        ],
      },
      {
        level: 'Medium',
        variant: 'info' as const,
        range: '40 - 59',
        description: 'Security issues that require attention but are not immediately critical',
        criteria: [
          'Common services that should be hardened or monitored',
          'Protocols that can leak information if misconfigured',
          'Services running with default configurations',
          'Open ports that expand the attack surface',
        ],
      },
      {
        level: 'Low',
        variant: 'success' as const,
        range: '20 - 39',
        description: 'Services that are generally safe but should be documented',
        criteria: [
          'Standard services running on expected ports',
          'Properly configured encrypted services',
          'Services with appropriate access controls',
          'Development or testing services in isolated environments',
        ],
      },
      {
        level: 'Info',
        variant: 'muted' as const,
        range: '0 - 19',
        description: 'Minimal risk — standard services with no detected issues',
        criteria: [
          'Well-configured services with current versions',
          'Expected services in hardened configurations',
          'Internal services with proper network segmentation',
        ],
      },
    ],
    [],
  );

  return (
    <div className="p-md md:p-lg">
      <h1 className="mb-md text-page-title">Risk Assessment Documentation</h1>

      <Alert variant="info" className="mb-md">
        <AlertDescription>
          BlueStick calculates risk scores automatically based on discovered services, known
          vulnerabilities, and network exposure. Scores range from 0 to 100. This page explains
          exactly how scores are computed.
        </AlertDescription>
      </Alert>

      {/* Score Formula */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-xs">
            <Calculator className="size-5" aria-hidden /> Score Formula
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="warning" className="mb-md">
            <AlertDescription className="font-mono">
              Risk Score = (Vulnerability × 0.40) + (Exposure × 0.25) + (Configuration × 0.20) + (Attack Surface × 0.15)
            </AlertDescription>
          </Alert>
          <p className="mb-sm text-metadata text-foreground">
            Each component is scored independently on a 0-100 scale, then combined with the weights
            above. The final score is capped at 100.
          </p>
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/5">Component</TableHead>
                  <TableHead className="w-24 text-center">Weight</TableHead>
                  <TableHead>What It Measures</TableHead>
                  <TableHead className="w-1/3">How It's Computed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell className="font-semibold">Vulnerability</TableCell>
                  <TableCell className="text-center"><Badge variant="destructive">40%</Badge></TableCell>
                  <TableCell>Known vulnerabilities detected in services</TableCell>
                  <TableCell>Sum of per-service vulnerability checks + Nessus/OpenVAS findings weighted by CVSS</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-semibold">Exposure</TableCell>
                  <TableCell className="text-center"><Badge variant="warning">25%</Badge></TableCell>
                  <TableCell>Dangerous ports and total open port count</TableCell>
                  <TableCell>+8 points per dangerous port open. +10 bonus if &gt;10 open ports, +20 if &gt;20</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-semibold">Configuration</TableCell>
                  <TableCell className="text-center"><Badge variant="info">20%</Badge></TableCell>
                  <TableCell>Insecure services and weak configurations</TableCell>
                  <TableCell>+15 points per insecure service detected (Telnet, FTP, RSH, rlogin, TFTP)</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-semibold">Attack Surface</TableCell>
                  <TableCell className="text-center"><Badge variant="muted">15%</Badge></TableCell>
                  <TableCell>Service complexity weighted by inherent risk</TableCell>
                  <TableCell>Sum of service weights for all open ports (see Service Weights table)</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Risk Level Thresholds */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-xs">
            <Gauge className="size-5" aria-hidden /> Risk Level Thresholds
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-sm sm:grid-cols-2 md:grid-cols-5">
            {riskLevels.map((risk) => (
              <Card key={risk.level} className={cn('border-t-4', borderTone(risk.variant))}>
                <CardContent className="p-sm">
                  <div className="mb-xs flex items-center justify-between">
                    <Badge variant={risk.variant}>{risk.level}</Badge>
                    <span className="font-mono text-caption font-semibold text-foreground">{risk.range}</span>
                  </div>
                  <p className="text-caption text-muted-foreground">{risk.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Vulnerability Checks */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-xs">
            <AlertOctagon className="size-5" aria-hidden /> Vulnerability Checks (40% of Score)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-sm text-metadata text-foreground">
            The vulnerability component runs service-specific checks against detected services and
            adds points for each finding. Additionally, vulnerabilities imported from Nessus or
            OpenVAS scans contribute based on their CVSS severity: Critical = +25, High = +15,
            Medium = +8, Low = +3 points each.
          </p>
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/6">Service</TableHead>
                  <TableHead className="w-2/5">Check</TableHead>
                  <TableHead className="w-24 text-center">Points</TableHead>
                  <TableHead className="w-1/6">Severity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {vulnerabilityServiceChecks.map((c, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-semibold">{c.service}</TableCell>
                    <TableCell>{c.check}</TableCell>
                    <TableCell className="text-center font-semibold">+{c.points}</TableCell>
                    <TableCell>
                      <Badge variant={SEVERITY_VARIANT[c.severity]}>{c.severity}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Exposure */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-xs">
            <AlertTriangle className="size-5" aria-hidden /> Exposure Score (25% of Score)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-sm text-metadata text-foreground">
            The exposure component evaluates which dangerous ports are open and how many total ports
            are exposed. Each dangerous port adds <strong>+8 points</strong>. Having more than 10
            open ports adds a +10 bonus, and more than 20 adds +20.
          </p>
          <p className="mb-xs text-caption font-semibold text-foreground">Dangerous Ports (24 ports, +8 each):</p>
          <div className="flex flex-wrap gap-xxs">
            {['21 FTP', '23 Telnet', '25 SMTP', '53 DNS', '69 TFTP', '110 POP3', '135 RPC', '139 NetBIOS', '143 IMAP', '161 SNMP', '389 LDAP', '445 SMB', '514 RSH', '993 IMAPS', '995 POP3S', '1433 MSSQL', '1521 Oracle', '2049 NFS', '3306 MySQL', '3389 RDP', '5432 PostgreSQL', '5900 VNC', '6379 Redis', '27017 MongoDB'].map((p) => (
              <Badge key={p} variant="outline">{p}</Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Service Weights */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-xs">
            <Shield className="size-5" aria-hidden /> Attack Surface — Service Weights (15% of Score)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-sm text-metadata text-foreground">
            Each open service adds its weight to the attack surface score. Higher-weight services
            represent greater inherent risk. Services not listed default to weight 3.
          </p>
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/6">Service</TableHead>
                  <TableHead className="w-20 text-center">Weight</TableHead>
                  <TableHead>Rationale</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {serviceWeights.map((row) => (
                  <TableRow key={row.service}>
                    <TableCell className="font-semibold">{row.service}</TableCell>
                    <TableCell className="text-center">
                      <Badge variant={weightVariant(row.weight)}>{row.weight}</Badge>
                    </TableCell>
                    <TableCell>{row.rationale}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Port Risk Categories */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle>Port Risk Categories</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/6">Category</TableHead>
                  <TableHead className="w-1/3">Ports</TableHead>
                  <TableHead className="w-32">Risk</TableHead>
                  <TableHead>Reasoning</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {portRiskCategories.map((category, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium">{category.category}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-xxs">
                        {category.ports.map((p) => (
                          <Badge key={p} variant="outline">{p}</Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={SEVERITY_VARIANT[category.riskLevel]}>{category.riskLevel}</Badge>
                    </TableCell>
                    <TableCell>{category.reasoning}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Worked Example */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle>Worked Example</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="info" className="mb-sm">
            <AlertDescription>
              <strong>Host:</strong> 192.168.1.100 (Windows Server) with SSH 1.2, SMBv1, RDP, MySQL, DNS
            </AlertDescription>
          </Alert>
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Component</TableHead>
                  <TableHead>Calculation</TableHead>
                  <TableHead className="text-center">Raw</TableHead>
                  <TableHead className="text-center">Weighted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>Vulnerability</TableCell>
                  <TableCell>SSH 1.x (+30) + SMBv1 (+35) + MySQL exposed (+10) = 75</TableCell>
                  <TableCell className="text-center">75</TableCell>
                  <TableCell className="text-center"><strong>75 × 0.40 = 30.0</strong></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>Exposure</TableCell>
                  <TableCell>5 open ports, 3 dangerous (SMB, RDP, MySQL) × 8 = 24</TableCell>
                  <TableCell className="text-center">24</TableCell>
                  <TableCell className="text-center"><strong>24 × 0.25 = 6.0</strong></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>Configuration</TableCell>
                  <TableCell>No insecure cleartext services detected</TableCell>
                  <TableCell className="text-center">0</TableCell>
                  <TableCell className="text-center"><strong>0 × 0.20 = 0.0</strong></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>Attack Surface</TableCell>
                  <TableCell>SSH(4) + SMB(9) + RDP(9) + MySQL(7) + DNS(4) = 33</TableCell>
                  <TableCell className="text-center">33</TableCell>
                  <TableCell className="text-center"><strong>33 × 0.15 = 5.0</strong></TableCell>
                </TableRow>
                <TableRow className="bg-accent/40">
                  <TableCell colSpan={3} className="font-semibold">Total Risk Score</TableCell>
                  <TableCell className="text-center"><Badge variant="info">41.0 — Medium</Badge></TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Risk Level Details */}
      <Card className="mb-md">
        <CardHeader>
          <CardTitle>Risk Level Criteria</CardTitle>
        </CardHeader>
        <CardContent>
          <Accordion type="multiple" defaultValue={['Critical']}>
            {riskLevels.map((risk) => (
              <AccordionItem key={risk.level} value={risk.level}>
                <AccordionTrigger>
                  <div className="flex flex-wrap items-center gap-sm">
                    <Badge variant={risk.variant}>{risk.level}</Badge>
                    <span className="font-mono text-caption">Score {risk.range}</span>
                    <span className="text-caption text-muted-foreground">{risk.description}</span>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <ul className="space-y-xxs">
                    {risk.criteria.map((c, i) => (
                      <li key={i} className="flex items-start gap-xs text-metadata">
                        <span className={cn('mt-1.5 inline-block size-2 shrink-0 rounded-full', dotTone(risk.variant))} />
                        <span>{c}</span>
                      </li>
                    ))}
                  </ul>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </CardContent>
      </Card>

      <Alert variant="warning">
        <AlertDescription>
          <strong>Important:</strong> Risk scores are automated assessments based on detected
          services and known vulnerability patterns. They provide a prioritization framework, not a
          definitive security verdict. Always consider your specific network architecture, business
          context, and security policies when making remediation decisions.
        </AlertDescription>
      </Alert>

      {/* Defensive: keep Info icon imported for future use without ESLint warning */}
      <span className="hidden"><Info className="size-4" aria-hidden /></span>
    </div>
  );
};

function borderTone(v: SeverityVariant): string {
  switch (v) {
    case 'destructive':
      return 'border-t-destructive';
    case 'warning':
      return 'border-t-warning';
    case 'info':
      return 'border-t-info';
    case 'success':
      return 'border-t-success';
    case 'muted':
    default:
      return 'border-t-muted-foreground';
  }
}

function dotTone(v: SeverityVariant): string {
  switch (v) {
    case 'destructive':
      return 'bg-destructive';
    case 'warning':
      return 'bg-warning';
    case 'info':
      return 'bg-info';
    case 'success':
      return 'bg-success';
    case 'muted':
    default:
      return 'bg-muted-foreground';
  }
}

export default RiskAssessment;
