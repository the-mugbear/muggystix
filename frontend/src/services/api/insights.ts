/**
 * Insights API — derived, cross-host analytics over project data.
 *
 * Per-subnet insights: the attention model (exposure + neglect) re-grouped
 * by subnet, plus a hygiene lens (EOL OS / TLS cert issues / weak auth /
 * risky services) that surfaces "lack of IT management".  Worst-first.
 */
import { api, p } from './client';

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface EolOsHost {
  host_id: number;
  ip_address: string | null;
  os_name: string | null;
  eol_label: string;
  eol_date: string;
}

export interface RiskyServiceBreakdown {
  port: number;
  label: string;
  category: string;
  host_count: number;
}

export interface SubnetInsight {
  subnet_id: number;
  cidr: string;
  scope_name: string;
  site: string | null;
  site_id: number | null;
  criticality_tier: number;
  owner_id: number | null;
  owner_name: string | null;
  host_count: number;
  usable_addresses: number;
  no_coverage: boolean;
  exposure: {
    raw_score: number;
    weighted_score: number;
    active_findings: number;
    by_severity: SeverityCounts;
  };
  neglect: {
    unowned_active_findings: number;
    unreviewed_hosts: number;
    // Median age (days) of the subnet's hosts, plus how many / what share are
    // past the stale threshold.  Replaces the old "freshest host" staleness,
    // which let one recently-seen host mask a stale subnet.
    median_host_age_days: number | null;
    stale_host_count: number;
    stale_host_pct: number | null;
  };
  hygiene: {
    eol_os_hosts: number;
    eol_os_detail: EolOsHost[];
    cert_issue_hosts: number;
    weak_auth_hosts: number;
    risky_service_hosts: number;
    risky_services: RiskyServiceBreakdown[];
  };
  recommended_action: { kind: string; text: string };
}

export interface SubnetInsightsResponse {
  adopted: boolean;
  subnets: SubnetInsight[];
  totals: {
    subnet_count: number;
    hosts_in_scope: number;
    eol_os_hosts: number;
    cert_issue_hosts: number;
    weak_auth_hosts: number;
    active_findings: number;
    by_severity: SeverityCounts;
  };
}

export const getSubnetInsights = async (): Promise<SubnetInsightsResponse> => {
  const response = await api.get<SubnetInsightsResponse>(`${p()}/insights/subnets`);
  return response.data;
};
