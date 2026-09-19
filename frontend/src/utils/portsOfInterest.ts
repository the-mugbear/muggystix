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

/**
 * nmap service names that identify a high-value service regardless of the
 * port it was found on.  Keys are lower-cased `Port.service_name` values as
 * nmap's probe engine reports them; values are the PORTS_OF_INTEREST entry
 * whose label and weight apply.  So RDP probed on 3390 still reads "RDP",
 * and a port-number guess is never needed when the probe already answered.
 */
const SERVICE_NAME_TO_POI: Record<string, number> = {
  telnet: 23,
  'microsoft-ds': 445,
  'netbios-ssn': 445,
  smb: 445,
  'ms-wbt-server': 3389,
  rdp: 3389,
  wsman: 5985,
  winrm: 5985,
  'ms-sql-s': 1433,
  mssql: 1433,
  mongodb: 27017,
  mongod: 27017,
  redis: 6379,
  ssh: 22,
  ldap: 389,
  ldaps: 389,
  mysql: 3306,
  postgresql: 5432,
  postgres: 5432,
  elasticsearch: 9200,
  vnc: 5900,
  'vnc-http': 5900,
  globalcatldap: 3268,
  globalcatldapssl: 3268,
};

export interface ExposureChip {
  /** Stable key for React. */
  key: string;
  /** What the chip says: the high-value label, or the probed service name. */
  label: string;
  port: number;
  /** True when a service probe identified the service; false when the label
   *  is a guess from the port number alone (no probe, or a table lookup). */
  detected: boolean;
  weight: number;
}

/** The subset of Port a chip needs; keeps this util free of the API types. */
export interface ExposurePortLike {
  port_number: number;
  state: string | null;
  service_name?: string | null;
  service_method?: string | null;
}

/**
 * The Exposure column's chips, ranked.
 *
 * v5.220.0 — the column used to be built from port numbers alone and printed
 * "no high-value services" whenever none of the well-known ports were open,
 * which hid an admin application probed on an unusual port.  Now, per open
 * port: a probed service name wins and is shown as detected; otherwise a
 * well-known port number yields a guess, marked as such.  High-value
 * services rank first (by weight, detected ahead of guessed); other probed
 * services follow (weight 0) so a host with only http/https still says so.
 * A port whose only service name came from nmap's port table (method
 * "table") counts as a guess: nmap assigned it from the number, not from a
 * response.
 */
export const exposureChips = (ports?: ExposurePortLike[] | null): ExposureChip[] => {
  const byLabel = new Map<string, ExposureChip>();
  for (const p of ports ?? []) {
    if (p.state !== 'open') continue;
    const probed = p.service_method === 'probed';
    const name = (p.service_name ?? '').trim().toLowerCase();
    const poiFromName = name ? PORTS_OF_INTEREST_BY_PORT.get(SERVICE_NAME_TO_POI[name] ?? -1) : undefined;
    const poiFromPort = PORTS_OF_INTEREST_BY_PORT.get(p.port_number);
    let chip: ExposureChip | null = null;
    if (probed && poiFromName) {
      chip = { key: poiFromName.label, label: poiFromName.label, port: p.port_number, detected: true, weight: poiFromName.weight };
    } else if (probed && name) {
      chip = { key: name, label: name, port: p.port_number, detected: true, weight: 0 };
    } else if (poiFromPort) {
      chip = { key: poiFromPort.label, label: poiFromPort.label, port: p.port_number, detected: false, weight: poiFromPort.weight };
    }
    if (!chip) continue;
    const existing = byLabel.get(chip.key);
    // Same service on two ports: keep one chip, and let a detection beat a guess.
    if (!existing || (chip.detected && !existing.detected)) byLabel.set(chip.key, chip);
  }
  return [...byLabel.values()].sort(
    (a, b) => b.weight - a.weight || Number(b.detected) - Number(a.detected) || a.label.localeCompare(b.label),
  );
};
