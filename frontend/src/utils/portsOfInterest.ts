export interface PortOfInterestDefinition {
  port: number;
  label: string;
  // Risk weight (mirrors backend app/services/ports_of_interest.py) so the
  // Hosts-list Exposure column can rank a host's high-value open ports by
  // risk instead of arbitrary scan order.
  weight: number;
}

export const PORTS_OF_INTEREST: PortOfInterestDefinition[] = [
  { port: 23, label: 'Telnet', weight: 6 },
  { port: 445, label: 'SMB', weight: 7 },
  { port: 3389, label: 'RDP', weight: 7 },
  { port: 5985, label: 'WinRM', weight: 6 },
  { port: 1433, label: 'MSSQL', weight: 6 },
  { port: 27017, label: 'MongoDB', weight: 6 },
  { port: 6379, label: 'Redis', weight: 6 },
  { port: 22, label: 'SSH', weight: 5 },
  { port: 389, label: 'LDAP', weight: 5 },
  { port: 3306, label: 'MySQL', weight: 5 },
  { port: 5432, label: 'PostgreSQL', weight: 5 },
  { port: 9200, label: 'Elasticsearch', weight: 5 },
  { port: 5900, label: 'VNC', weight: 5 },
  { port: 3268, label: 'Global Catalog', weight: 4 },
];

export const PORTS_OF_INTEREST_SET = new Set(PORTS_OF_INTEREST.map((entry) => entry.port));

export const PORTS_OF_INTEREST_BY_PORT: Map<number, PortOfInterestDefinition> = new Map(
  PORTS_OF_INTEREST.map((entry) => [entry.port, entry]),
);
