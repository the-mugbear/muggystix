/**
 * Segments — "which network ranges / sites are neglected or in bad shape?"
 *
 * Merges the subnet and site comparison behind a Site | Subnet toggle (was two
 * pages: /insights + the sites table on /posture). Subnet mode is the attention
 * model (exposure + neglect) re-grouped by subnet plus a hygiene lens (EOL OS /
 * TLS cert issues / weak auth / risky services); Site mode is the same signals
 * rolled up per configured site (server-authoritative, from the posture
 * snapshot). Worst-first. Every number stays decomposed and explainable — no
 * opaque score; site criticality tier is shown as its own column and never
 * folded into the raw counts.
 *
 * UI-style-guide compliance: tables are `table-fixed` with explicit column
 * widths; CIDR / site / owner / action cells truncate; every state
 * (loading / error / not-adopted / empty) renders a safe fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { SEVERITY_BADGE_VARIANT } from '../utils/severity';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, Copy, Download, Info, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';

import {
  getSubnetInsights,
  getPosture,
  conditionHostsHref,
  subnetHostsHref,
  type SubnetInsight,
  type SubnetInsightsResponse,
  type PostureSite,
} from '../services/api';
import { buildHostsUrl } from '../utils/drilldownLinks';
import SeverityBar from '../components/ui/SeverityBar';
import { tierHsl, TIER_LABEL } from '../components/posture/postureTheme';
import { formatApiError } from '../utils/apiErrors';
import { copyToClipboard, downloadTextFile } from '../utils/clipboard';
import { useToast } from '../contexts/ToastContext';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';

type BadgeVariant =
  | 'default' | 'secondary' | 'destructive' | 'success'
  | 'warning' | 'info' | 'outline' | 'muted';

// Recommended-action kind → badge tone.  Mirrors the attention surface's
// vocabulary, extended with the hygiene actions (modernize / harden /
// renew-cert / rescan).
const ACTION_TONE: Record<string, BadgeVariant> = {
  scan: 'warning',
  triage: 'warning',
  remediate: 'destructive',
  modernize: 'warning',
  harden: 'warning',
  'renew-cert': 'info',
  rescan: 'warning',
  review: 'muted',
  ok: 'success',
};

const SEVERITY_VARIANT = SEVERITY_BADGE_VARIANT;

function tierTone(tier: number): BadgeVariant {
  if (tier <= 1) return 'destructive';
  if (tier === 2) return 'warning';
  return 'muted';
}

function medianAgeLabel(days: number | null): string {
  if (days === null) return 'no scans';
  if (days === 0) return 'today';
  return `${days}d`;
}

// Plain-English "what is this metric and how is it derived?" help — every
// ranking input is justified on an explicit (i), not left for the operator to
// infer (this surface drives where they spend time).
const InfoTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button type="button" aria-label="How is this derived?"
        className="inline-flex shrink-0 rounded text-muted-foreground/70 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Info className="size-3.5" aria-hidden />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left text-caption leading-snug">{text}</TooltipContent>
  </Tooltip>
);

// Render the current page of subnet insights as a shareable Markdown table.
function subnetsToMarkdown(data: SubnetInsightsResponse, projectName?: string): string {
  const lines: string[] = [`# Subnet Insights${projectName ? ` — ${projectName}` : ''}`, ''];
  if (!data.adopted) return [...lines, '_No scoped subnets yet._'].join('\n');
  const t = data.totals;
  if (t) {
    lines.push(
      `Subnets: ${t.subnet_count} · Hosts in scope: ${t.hosts_in_scope} · Active findings: ${t.active_findings} · ` +
      `EOL OS: ${t.eol_os_hosts} · Cert issues: ${t.cert_issue_hosts} · Weak auth: ${t.weak_auth_hosts}`,
      '',
    );
  }
  lines.push(
    `_Worst-first; showing ${data.subnets.length} of ${data.total} subnets._`, '',
    '| Subnet | Site | Hosts | Exposure | EOL | Cert | Weak | Risky | Next action |',
    '|---|---|---:|---:|---:|---:|---:|---:|---|',
  );
  for (const s of data.subnets) {
    const h = s.hygiene;
    const action = (s.recommended_action?.text || '').replace(/\|/g, '/');
    lines.push(
      `| ${s.cidr} | ${s.site ?? '—'} | ${s.host_count} | ${s.exposure.weighted_score} | ` +
      `${h.eol_os_hosts} | ${h.cert_issue_hosts} | ${h.weak_auth_hosts} | ${h.risky_service_hosts} | ${action} |`,
    );
  }
  return lines.join('\n');
}

const Segments: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [mode, setMode] = useState<'subnet' | 'site'>('subnet');
  const [data, setData] = useState<SubnetInsightsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Pagination — the page can have thousands of subnets; render one page.
  const PAGE_SIZE = 50;
  const [offset, setOffset] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    getSubnetInsights(PAGE_SIZE, offset)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Could not load subnet insights.')))
      .finally(() => setLoading(false));
  }, [offset]);


  // Reset to the first page when the project changes.
  useEffect(() => { setOffset(0); }, [currentProject?.id]);

  // Reload when the active project or page changes (the API client reads the
  // current project id, so a stale fetch would otherwise show the wrong
  // project's subnets after a switch).
  useEffect(() => { load(); }, [load, currentProject?.id]);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const totals = data?.totals;
  const subnets = useMemo(() => data?.subnets ?? [], [data]);

  const handleCopyMarkdown = useCallback(async () => {
    if (!data) return;
    const ok = await copyToClipboard(subnetsToMarkdown(data, currentProject?.name));
    toast[ok ? 'success' : 'error'](ok ? 'Subnet table copied as Markdown' : 'Could not copy to clipboard');
  }, [data, currentProject?.name, toast]);

  const handleDownloadJson = useCallback(() => {
    if (!data) return;
    downloadTextFile(`subnet_insights_${new Date().toISOString().split('T')[0]}.json`,
      JSON.stringify(data, null, 2), 'application/json');
  }, [data]);

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Segments</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Which network segments need attention — ranked worst-first. Toggle between{' '}
            <strong className="text-foreground">Site</strong> (configured groupings, with criticality
            tier) and <strong className="text-foreground">Subnet</strong> (network ranges).{' '}
            <InfoTip text="Ranking: exposure first, then neglect + hygiene magnitude, then host count. Deliberately no single opaque score — each component is shown so the order is explainable. Site criticality tier is shown as its own column and never folded into the raw counts." />{' '}
            <strong className="text-foreground">Exposure</strong> is active findings by severity;{' '}
            <strong className="text-foreground">neglect</strong> is unowned/unreviewed/stale signals;{' '}
            <strong className="text-foreground">hygiene</strong> surfaces end-of-life OS, certificate
            issues, weak authentication, and risky exposed services.
          </p>
        </div>
        {mode === 'subnet' && (
          <div className="flex flex-wrap items-center gap-xs">
            <Button size="sm" variant="outline" onClick={handleCopyMarkdown} disabled={loading || !data?.adopted}>
              <Copy className="size-3.5" aria-hidden /> Copy table
            </Button>
            <Button size="sm" variant="outline" onClick={handleDownloadJson} disabled={loading || !data?.adopted}>
              <Download className="size-3.5" aria-hidden /> JSON
            </Button>
            <Button size="sm" variant="outline" onClick={load} disabled={loading}>
              <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
            </Button>
          </div>
        )}
      </div>

      {/* Site | Subnet lens toggle. */}
      <div className="inline-flex rounded-md border border-border p-0.5" role="tablist" aria-label="Segment lens">
        {(['site', 'subnet'] as const).map((m) => (
          <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
            className={`rounded px-md py-1 text-caption font-medium capitalize transition-colors ${mode === m ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
            {m}
          </button>
        ))}
      </div>

      {mode === 'site' ? (
        <SitePanel />
      ) : loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Assessing subnets…</p>
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load insights</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : !data?.adopted ? (
        <Card>
          <CardContent className="p-lg text-center">
            <ShieldAlert className="mx-auto mb-sm size-8 text-muted-foreground" aria-hidden />
            <p className="text-subheading font-semibold text-foreground">No scoped subnets yet</p>
            <p className="mx-auto mt-xs max-w-md text-metadata text-muted-foreground">
              Subnet insights group your hosts by network range. Define a scope with subnets,
              then re-run this view to see which ranges are neglected or in poor shape.
            </p>
            <Button asChild size="sm" className="mt-md">
              <Link to="/scopes">Manage scopes</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Totals strip — the project-wide hygiene roll-up. */}
          {totals && (
            <div className="flex flex-wrap gap-x-lg gap-y-xs text-caption text-muted-foreground">
              <span>Subnets: <span className="font-medium text-foreground">{totals.subnet_count}</span></span>
              <span>Hosts in scope: <span className="font-medium text-foreground">{totals.hosts_in_scope}</span></span>
              <span>Active findings: <span className="font-medium text-foreground">{totals.active_findings}</span></span>
              <span>EOL OS hosts: <span className="font-medium text-foreground">{totals.eol_os_hosts}</span></span>
              <span>Cert issues: <span className="font-medium text-foreground">{totals.cert_issue_hosts}</span></span>
              <span>Weak auth: <span className="font-medium text-foreground">{totals.weak_auth_hosts}</span></span>
            </div>
          )}

          {subnets.length === 0 ? (
            <Alert variant="info">
              <AlertDescription>
                No hosts are mapped to any scoped subnet yet. Upload a scan or run recon, then
                re-check — coverage gaps will surface here.
              </AlertDescription>
            </Alert>
          ) : (
            <Card>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[36px]" />
                        <TableHead className="w-[20%]">Subnet</TableHead>
                        <TableHead className="w-[14%]">Site</TableHead>
                        <TableHead className="w-[8%] text-right">Hosts</TableHead>
                        <TableHead className="w-[16%]">
                          <span className="inline-flex items-center gap-xxs">Exposure
                            <InfoTip text="Severity-weighted active findings (critical=10, high=5, medium=2, low=1, info=0) summed across the subnet's hosts, then scaled by the site's criticality tier (×2.0 tier-1 … ×0.5 tier-4). The primary ranking signal." />
                          </span>
                        </TableHead>
                        <TableHead className="w-[18%]">
                          <span className="inline-flex items-center gap-xxs">Hygiene
                            <InfoTip text="Latent weaknesses on the subnet's hosts, independent of findings: end-of-life OS, expired/self-signed TLS certificates, weak or guest authentication (NetExec), and risky exposed services. A subnet with zero findings can still score badly here — absence of findings is not health." />
                          </span>
                        </TableHead>
                        <TableHead className="w-[24%]">Next action</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {subnets.map((s) => (
                        <SubnetRow
                          key={s.subnet_id}
                          s={s}
                          open={expanded.has(s.subnet_id)}
                          onToggle={() => toggle(s.subnet_id)}
                        />
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Pager — the worst subnets are first, so page 1 is the most
              actionable; further pages exist only on large projects. */}
          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-sm text-caption text-muted-foreground">
              <span>
                Showing {data.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total} subnets (worst-first)
              </span>
              <div className="flex items-center gap-xs">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={loading || offset === 0}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={loading || offset + PAGE_SIZE >= data.total}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

const SubnetRow: React.FC<{ s: SubnetInsight; open: boolean; onToggle: () => void }> = ({ s, open, onToggle }) => {
  const h = s.hygiene;
  // Each hygiene badge drills into that subnet's hosts filtered to the
  // condition — the analyst's "show me those N hosts" path.  Risky services
  // have no `has:` predicate, so that badge lands on the subnet's hosts.
  const hygieneBadge = (
    key: string | null, variant: BadgeVariant, label: string, count: number, title: string,
  ) => {
    const href = key ? conditionHostsHref(key, s.cidr) : subnetHostsHref(s.cidr);
    const badge = <Badge variant={variant} title={title}>{label} {count}</Badge>;
    return href
      ? <Link key={label} to={href} title={`${title} — view hosts`}
          className="hover:opacity-80">{badge}</Link>
      : badge;
  };
  const hygieneBadges = (
    <div className="flex flex-wrap gap-xxs">
      {h.eol_os_hosts > 0 && hygieneBadge('eol_os', 'destructive', 'EOL', h.eol_os_hosts, 'Hosts on end-of-life OS')}
      {h.weak_auth_hosts > 0 && hygieneBadge('weak_auth', 'destructive', 'Weak', h.weak_auth_hosts, 'Hosts with weak/guest authentication')}
      {h.cert_issue_hosts > 0 && hygieneBadge('tls_hygiene', 'warning', 'Cert', h.cert_issue_hosts, 'Hosts with expired/self-signed certs')}
      {h.risky_service_hosts > 0 && hygieneBadge(null, 'muted', 'Risky', h.risky_service_hosts, 'Hosts exposing risky services')}
      {h.eol_os_hosts + h.weak_auth_hosts + h.cert_issue_hosts + h.risky_service_hosts === 0 && (
        <span className="text-caption text-muted-foreground">—</span>
      )}
    </div>
  );

  return (
    <>
      <TableRow className={s.no_coverage ? 'bg-warning/5' : undefined}>
        <TableCell className="align-top">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-label={open ? 'Collapse details' : 'Expand details'}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
          </button>
        </TableCell>
        <TableCell className="align-top">
          <div className="min-w-0">
            <Link to={subnetHostsHref(s.cidr)} title={`View hosts in ${s.cidr}`}
              className="block truncate font-mono text-metadata font-medium text-info hover:underline">
              {s.cidr}
            </Link>
            <span className="block truncate text-caption text-muted-foreground" title={s.scope_name}>
              {safeFallback(s.scope_name, 'no scope')}
            </span>
          </div>
        </TableCell>
        <TableCell className="align-top">
          <div className="flex min-w-0 items-center gap-xxs">
            <Badge variant={tierTone(s.criticality_tier)} title={`Criticality tier ${s.criticality_tier} (1 = most critical)`}>
              T{s.criticality_tier}
            </Badge>
            <span className="min-w-0 truncate text-caption text-foreground" title={s.site ?? undefined}>
              {s.site ? s.site : <span className="italic text-muted-foreground">unassigned</span>}
            </span>
          </div>
        </TableCell>
        <TableCell className="align-top text-right">
          <span className={s.no_coverage ? 'font-medium text-warning' : 'text-foreground'} title={`${s.usable_addresses} usable addresses`}>
            {s.host_count}
          </span>
        </TableCell>
        <TableCell className="align-top">
          {s.exposure.active_findings === 0 ? (
            <span className="text-caption text-muted-foreground">none</span>
          ) : (
            <div className="flex flex-wrap items-center gap-xxs">
              {(['critical', 'high', 'medium'] as const)
                .filter((sev) => s.exposure.by_severity[sev] > 0)
                .map((sev) => (
                  <Badge key={sev} variant={SEVERITY_VARIANT[sev] as never}>
                    {s.exposure.by_severity[sev]} {sev}
                  </Badge>
                ))}
              <span className="text-caption text-muted-foreground" title="Tier-weighted exposure score">
                ·{s.exposure.weighted_score}
              </span>
            </div>
          )}
        </TableCell>
        <TableCell className="align-top">{hygieneBadges}</TableCell>
        <TableCell className="align-top">
          <div className="flex min-w-0 items-start gap-xxs">
            <Badge variant={ACTION_TONE[s.recommended_action.kind] ?? 'muted'}>
              {s.recommended_action.kind === 'ok' ? 'OK' : 'Do'}
            </Badge>
            <span className="min-w-0 text-caption text-foreground" title={s.recommended_action.text}>
              {s.recommended_action.text}
            </span>
          </div>
        </TableCell>
      </TableRow>

      {open && (
        <TableRow className="bg-muted/30">
          <TableCell />
          <TableCell colSpan={6} className="align-top">
            <div className="grid gap-md py-xs md:grid-cols-3">
              {/* Exposure detail */}
              <div>
                <p className="mb-xxs flex items-center gap-xxs text-caption font-semibold text-muted-foreground">
                  Exposure
                  <InfoTip text="Active findings on this subnet's hosts, counted by severity and weighted (critical=10 … low=1) × the site tier. This is the primary worst-first sort key." />
                </p>
                {s.exposure.active_findings === 0 ? (
                  <p className="text-caption text-muted-foreground">No active findings.</p>
                ) : (() => {
                  // Informational excluded — it distorts the picture and isn't
                  // actionable; show a quiet note if a subnet has only info.
                  const sevs = (['critical', 'high', 'medium', 'low'] as const)
                    .filter((sev) => s.exposure.by_severity[sev] > 0);
                  return sevs.length === 0 ? (
                    <p className="text-caption text-muted-foreground">Informational only.</p>
                  ) : (
                    <div className="flex flex-wrap gap-xxs">
                      {sevs.map((sev) => (
                        <Badge key={sev} variant={SEVERITY_VARIANT[sev] as never}>
                          {s.exposure.by_severity[sev]} {sev}
                        </Badge>
                      ))}
                    </div>
                  );
                })()}
              </div>

              {/* Neglect detail */}
              <div>
                <p className="mb-xxs flex items-center gap-xxs text-caption font-semibold text-muted-foreground">
                  Neglect
                  <InfoTip text="Under-management signals: active findings with no owner, hosts not yet marked Reviewed, and how stale the last scan is. The first tiebreaker after exposure in the ranking." />
                </p>
                <ul className="space-y-0.5 text-caption text-foreground">
                  <li>Unowned findings: <span className="font-medium">{s.neglect.unowned_active_findings}</span></li>
                  <li>Unreviewed hosts: <span className="font-medium">{s.neglect.unreviewed_hosts}</span></li>
                  <li>Median host age: <span className="font-medium">{medianAgeLabel(s.neglect.median_host_age_days)}</span></li>
                  <li>Stale hosts: <span className="font-medium">{s.neglect.stale_host_count}{s.neglect.stale_host_pct !== null ? ` (${s.neglect.stale_host_pct}%)` : ''}</span></li>
                </ul>
              </div>

              {/* Hygiene detail */}
              <div>
                <p className="mb-xxs flex items-center gap-xxs text-caption font-semibold text-muted-foreground">
                  Hygiene
                  <InfoTip text="Latent weaknesses independent of findings — end-of-life OS, TLS certificate problems, weak/guest auth, and risky exposed services. Surfaced so a 'clean' (no-findings) subnet that's quietly rotten still ranks up." />
                </p>
                {s.hygiene.eol_os_detail.length > 0 && (
                  <div className="mb-xs">
                    <p className="text-caption text-muted-foreground">End-of-life OS</p>
                    <ul className="space-y-0.5 text-caption text-foreground">
                      {s.hygiene.eol_os_detail.map((e) => (
                        <li key={e.host_id} className="truncate" title={`${e.ip_address ?? ''} — ${e.os_name ?? ''}`}>
                          <Link to={`/hosts/${e.host_id}`} title="Open host"
                            className="font-mono text-info hover:underline">{safeFallback(e.ip_address, '?')}</Link>{' '}
                          <span className="text-muted-foreground">{e.eol_label} (EOL {e.eol_date})</span>
                        </li>
                      ))}
                      {s.hygiene.eol_os_hosts > s.hygiene.eol_os_detail.length && (
                        <li className="text-muted-foreground">
                          +{s.hygiene.eol_os_hosts - s.hygiene.eol_os_detail.length} more…
                        </li>
                      )}
                    </ul>
                  </div>
                )}
                {s.hygiene.risky_services.length > 0 && (
                  <div className="mb-xs">
                    <p className="text-caption text-muted-foreground">Risky services</p>
                    <div className="flex flex-wrap gap-xxs">
                      {s.hygiene.risky_services.map((r) => (
                        <Badge key={r.port} variant="muted" title={`${r.category} — ${r.host_count} host(s)`}>
                          {r.label} ({r.host_count})
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                <ul className="space-y-0.5 text-caption text-foreground">
                  <li>Cert issues: <span className="font-medium">{s.hygiene.cert_issue_hosts}</span> host(s)</li>
                  <li>Weak/guest auth: <span className="font-medium">{s.hygiene.weak_auth_hosts}</span> host(s)</li>
                </ul>
                {s.hygiene.eol_os_hosts + s.hygiene.cert_issue_hosts + s.hygiene.weak_auth_hosts + s.hygiene.risky_service_hosts === 0 && (
                  <p className="text-caption text-muted-foreground">No hygiene issues detected.</p>
                )}
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

// ---------------------------------------------------------------------------
// Site mode — the configured-site rollup, from the posture snapshot's
// server-authoritative per-site attention (exposure + neglect + tier).
// ---------------------------------------------------------------------------
const SitePanel: React.FC = () => {
  const { currentProject } = useProject();
  const [sites, setSites] = useState<PostureSite[] | null>(null);
  const [adopted, setAdopted] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getPosture()
      .then((d) => {
        setSites(d.sites.items);
        setAdopted(d.sites.adopted);
        setError(null);
      })
      .catch((e) => setError(formatApiError(e, 'Could not load site rollup.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load, currentProject?.id]);

  if (loading && !sites) {
    return (
      <div className="flex items-center gap-xs" role="status" aria-live="polite">
        <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
        <p className="text-metadata text-muted-foreground">Rolling up sites…</p>
      </div>
    );
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Couldn't load site rollup</AlertTitle>
        <AlertDescription>
          <p className="break-words">{error}</p>
          <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
            <RefreshCw className="size-3.5" aria-hidden /> Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (!adopted || !sites || sites.length === 0) {
    return (
      <Card>
        <CardContent className="p-lg text-center">
          <ShieldAlert className="mx-auto mb-sm size-8 text-muted-foreground" aria-hidden />
          <p className="text-subheading font-semibold text-foreground">No sites configured yet</p>
          <p className="mx-auto mt-xs max-w-md text-metadata text-muted-foreground">
            Group subnets into sites to compare exposure, coverage, and criticality by location.
            Until then, switch to the <strong className="text-foreground">Subnet</strong> lens above.
          </p>
          <Button asChild size="sm" className="mt-md"><Link to="/scopes">Manage scopes</Link></Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[22%]">Site</TableHead>
                <TableHead className="w-[14%]">Tier</TableHead>
                <TableHead className="w-[9%] text-right">Hosts</TableHead>
                <TableHead className="w-[22%]">Exposure</TableHead>
                <TableHead className="w-[9%] text-right">Unowned</TableHead>
                <TableHead className="w-[10%] text-right">Unreviewed</TableHead>
                <TableHead className="w-[14%]">Next action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sites.map((s) => {
                const tier = s.criticality_tier;
                const hostsHref = s.site ? buildHostsUrl({ sites: s.site }) : undefined;
                return (
                  <TableRow key={s.site_id ?? (s.unassigned ? 'unassigned' : s.site ?? 'na')}>
                    <TableCell className="align-top">
                      <span className="block truncate font-medium text-foreground" title={s.site ?? 'Unassigned'}>
                        {s.unassigned ? <span className="italic text-muted-foreground">Unassigned</span>
                          : safeFallback(s.site, '—')}
                      </span>
                      {s.owner_name && (
                        <span className="block truncate text-caption text-muted-foreground" title={s.owner_name}>
                          {s.owner_name}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      {tier ? (
                        <span className="inline-flex items-center gap-xxs text-caption text-foreground"
                          title={TIER_LABEL[tier]}>
                          <span className="size-2.5 rounded-full" style={{ background: tierHsl(tier) }} aria-hidden />
                          {TIER_LABEL[tier]?.split(' — ')[0] ?? `Tier ${tier}`}
                        </span>
                      ) : <span className="text-caption text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="align-top text-right tabular-nums text-foreground">
                      {hostsHref ? <Link to={hostsHref} className="text-info hover:underline">{s.host_count}</Link> : s.host_count}
                      {s.coverage_gap != null && s.coverage_gap > 0 && (
                        <span className="block text-caption text-warning" title="Expected hosts not yet discovered">
                          −{s.coverage_gap} gap
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="flex items-center gap-xs">
                        <span className="shrink-0 text-caption tabular-nums text-muted-foreground">
                          {s.exposure.active_findings}
                        </span>
                        <SeverityBar counts={s.exposure.by_severity} variant="compact" />
                      </div>
                    </TableCell>
                    <TableCell className="align-top text-right tabular-nums">
                      {s.neglect.unowned_active_findings > 0 ? (
                        <span className="text-warning">{s.neglect.unowned_active_findings}</span>
                      ) : <span className="text-muted-foreground">0</span>}
                    </TableCell>
                    <TableCell className="align-top text-right tabular-nums">
                      {s.neglect.unreviewed_hosts > 0 ? (
                        <span className="text-warning">{s.neglect.unreviewed_hosts}</span>
                      ) : <span className="text-muted-foreground">0</span>}
                    </TableCell>
                    <TableCell className="align-top">
                      <span className="block truncate text-caption text-foreground" title={s.recommended_action?.text}>
                        {safeFallback(s.recommended_action?.text, '—')}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

export default Segments;
