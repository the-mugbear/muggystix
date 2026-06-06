export interface PortOfInterestDefinition {
  port: number;
  label: string;
}

export const PORTS_OF_INTEREST: PortOfInterestDefinition[] = [
  { port: 22, label: 'SSH Administration' },
  { port: 23, label: 'Telnet' },
  { port: 445, label: 'SMB File Sharing' },
  { port: 389, label: 'LDAP' },
  { port: 3268, label: 'Global Catalog' },
  { port: 3389, label: 'RDP' },
  { port: 5985, label: 'WinRM' },
  { port: 1433, label: 'MSSQL' },
  { port: 3306, label: 'MySQL' },
  { port: 5432, label: 'PostgreSQL' },
  { port: 27017, label: 'MongoDB' },
  { port: 9200, label: 'Elasticsearch' },
  { port: 6379, label: 'Redis' },
  { port: 5900, label: 'VNC' },
];

export const PORTS_OF_INTEREST_SET = new Set(PORTS_OF_INTEREST.map((entry) => entry.port));
