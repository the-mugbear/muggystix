/**
 * Insights API — derived, cross-host analytics over project data.
 *
 * Per-subnet insights: the attention model (exposure + neglect) re-grouped
 * by subnet, plus a hygiene lens (EOL OS / TLS cert issues / weak auth /
 * risky services) that surfaces "lack of IT management".  Worst-first.
 */
import { api, p } from './client';
import { buildHostsUrl } from '../../utils/drilldownLinks';

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
  // Pagination: `subnets` is the requested page (worst-first); `total` is the
  // full count; `totals` is project-wide, not page-scoped.
  total: number;
  limit: number;
  offset: number;
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

export const getSubnetInsights = async (
  limit = 50,
  offset = 0,
): Promise<SubnetInsightsResponse> => {
  const response = await api.get<SubnetInsightsResponse>(`${p()}/insights/subnets`, {
    params: { limit, offset },
  });
  return response.data;
};

// --- Systemic insights -----------------------------------------------------
// Cross-sectional, single-snapshot: which weaknesses recur across the estate
// and how widely they spread. A condition spanning most sites is an estate
// "blind spot" (the spread is the diagnosis).

export interface SystemicCondition {
  key: string;
  label: string;
  vector: string;
  severity_weight: number;
  recommended_action: string;
  affected_hosts: number;
  host_fraction: number;
  subnet_spread: number;
  site_spread: number;
  systemic_score: number;
  example_ips: (string | null)[];
  is_blind_spot: boolean;
  // Phase 1: spread classification (single source of is_blind_spot) + the
  // program-level pattern family this weakness rolls up to.
  classification: 'isolated' | 'recurring' | 'estate_wide';
  family: string | null;
  family_label: string | null;
  severity?: string; // present on shared-vulnerability blind spots
}

export interface SegmentOutlier {
  subnet_id: number;
  cidr: string;
  site: string | null;
  host_count: number;
  issue_density: number;
  estate_median_density: number;
  // null when the estate median density is 0 — the outlier was flagged by an
  // absolute-density floor, so there's no meaningful "× median". Show the
  // density instead.
  times_median: number | null;
  conditions: string[];
}

export interface DiagnosticProfile {
  subnet_id: number;
  cidr: string;
  site: string | null;
  host_count: number;
  conditions: string[];
  root_cause: { kind: string; text: string };
}

/** Per pattern-family rollup — the Patterns page's primary rows. */
export interface SystemicFamily {
  family: string;
  family_label: string;
  root_cause_hypothesis: string;
  recommended_control: string;
  conditions: string[];
  affected_hosts: number;
  host_fraction: number;
  subnet_spread: number;
  site_spread: number;
  classification: 'isolated' | 'recurring' | 'estate_wide';
}

export interface SystemicInsightsResponse {
  adopted: boolean;
  estate?: {
    hosts_in_scope: number;
    subnets: number;
    sites: number;
    blind_spot_count: number;
  };
  family_summary?: SystemicFamily[];
  blind_spots?: SystemicCondition[];
  segment_outliers?: SegmentOutlier[];
  conditions?: SystemicCondition[];
  diagnostic_profiles?: DiagnosticProfile[];
}

export const getSystemicInsights = async (): Promise<SystemicInsightsResponse> => {
  const response = await api.get<SystemicInsightsResponse>(`${p()}/insights/systemic`);
  return response.data;
};

// --- Drill-down deep-links -------------------------------------------------
// Turn an insight row into a /hosts query so an analyst can jump from a
// finding straight to the hosts behind it.  The systemic condition keys map to
// the matching `has:` DSL predicate (see host_query_dsl), which resolves the
// SAME hosts the insight counts.  Shared-vulnerability blind spots (key
// `vuln:<plugin_id>`) have no host-filter predicate, so they return null and
// the caller renders no drill-down link.
const CONDITION_DSL: Record<string, string> = {
  eol_os: 'has:eol',
  smb_signing: 'has:smb_unsigned',
  weak_auth: 'has:weak_auth',
  tls_hygiene: 'has:cert_issue',
  weak_tls: 'has:weak_tls',
  cleartext_services: 'has:cleartext',
};

/**
 * /hosts link for a systemic condition, optionally narrowed to one subnet.
 * Returns null when the condition has no host-filter predicate.
 */
export const conditionHostsHref = (key: string, cidr?: string | null): string | null => {
  const q = CONDITION_DSL[key];
  if (!q) return null;
  return buildHostsUrl({ q, subnets: cidr ?? undefined });
};

/** /hosts link filtered to a single subnet/CIDR. */
export const subnetHostsHref = (cidr: string): string => buildHostsUrl({ subnets: cidr });

/**
 * /hosts link for a posture heatmap cell — a pattern family's condition(s)
 * optionally narrowed to one site. Combines the condition DSL predicate(s) with
 * the site filter so the cell's affected count reconciles with the list it opens.
 * Returns null when none of the family's conditions have a host-filter predicate.
 */
export const familyCellHostsHref = (
  conditions: string[],
  site?: string | null,
): string | null => {
  const preds = conditions.map((k) => CONDITION_DSL[k]).filter(Boolean);
  if (preds.length === 0) return null;
  // Multiple conditions in one family → OR them (the /hosts DSL supports `or`).
  const q = preds.length === 1 ? preds[0] : preds.join(' or ');
  return buildHostsUrl({ q, sites: site ?? undefined });
};

/**
 * Download the lightweight executive systemic report (standalone HTML) — a
 * self-contained file for sharing at a high-level meeting.  Fetched via the
 * authed client (the endpoint needs the JWT) and saved as a blob, mirroring the
 * host-report download.
 */
export const downloadSystemicReport = async (): Promise<void> => {
  const response = await api.get(`${p()}/reports/systemic.html`, { responseType: 'blob' });
  const url = window.URL.createObjectURL(new Blob([response.data], { type: 'text/html' }));
  const a = document.createElement('a');
  a.href = url;
  const cd = response.headers['content-disposition'] as string | undefined;
  const match = cd?.match(/filename="?([^"]+)"?/i);
  a.download = match?.[1] || `systemic_insights_${new Date().toISOString().split('T')[0]}.html`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
};
