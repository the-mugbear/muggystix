/**
 * SystemicInsights — "what does this environment systematically get wrong?"
 *
 * Cross-sectional companion to SubnetInsights for a single engagement's
 * snapshot.  Per-subnet insights rank *locations* by risk; this asks which
 * weaknesses recur across the estate and how widely they spread.  A weakness
 * on one host is incidental; the same weakness spanning many subnets and sites
 * is a process failure, and one spanning essentially the whole estate is an
 * organisational blind spot about a threat/vector — the spread IS the
 * diagnosis.
 *
 * Three nested tiers: estate blind spots → segment outliers (density vs the
 * estate's own median) → per-subnet diagnostic profiles (co-occurrence → root
 * cause).
 *
 * UI-style-guide compliance: tables are `table-fixed` with explicit widths;
 * CIDR / site / vector cells truncate or wrap; every state (loading / error /
 * not-adopted / empty) renders a safe fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Loader2, RefreshCw, ShieldAlert, AlertTriangle, ShieldCheck, Info, ArrowRight,
  Copy, Download, FileText,
} from 'lucide-react';

import {
  getSystemicInsights,
  conditionHostsHref,
  subnetHostsHref,
  downloadSystemicReport,
  type SystemicInsightsResponse,
  type SystemicCondition,
} from '../services/api';
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

// Plain-English "how is this derived?" help — systemic analysis is the least
// self-evident view, so each tier explains its method on an explicit (i).
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

type BadgeVariant =
  | 'default' | 'secondary' | 'destructive' | 'success'
  | 'warning' | 'info' | 'outline' | 'muted';

// Diagnostic root-cause kind → badge tone.
const ROOT_CAUSE_TONE: Record<string, BadgeVariant> = {
  abandoned: 'destructive',
  'patch-gap': 'warning',
  'no-pki': 'info',
  'cred-hygiene': 'warning',
  'flat-network': 'warning',
  mixed: 'muted',
};

// Human label for a condition key (used in the per-segment condition chips).
const CONDITION_LABEL: Record<string, string> = {
  eol_os: 'EOL OS',
  cleartext_services: 'Cleartext',
  tls_hygiene: 'TLS',
  weak_auth: 'Weak auth',
  smb_signing: 'SMB signing',
};

function conditionChip(key: string): string {
  if (key.startsWith('vuln:')) return 'Shared vuln';
  return CONDITION_LABEL[key] ?? key;
}

// Condition chips for a subnet — each links to that subnet's hosts filtered to
// the condition (the analyst's drill-down) when the condition has a /hosts
// predicate; chips without one (shared-vuln) render plain.
const ConditionChips: React.FC<{ keys: string[]; cidr: string }> = ({ keys, cidr }) => (
  <div className="flex flex-wrap gap-xxs">
    {keys.map((k) => {
      const href = conditionHostsHref(k, cidr);
      const label = conditionChip(k);
      return href ? (
        <Link key={k} to={href} title={`View ${cidr} hosts with ${label}`}>
          <Badge variant="muted" className="cursor-pointer hover:bg-muted-foreground/20">{label}</Badge>
        </Link>
      ) : (
        <Badge key={k} variant="muted">{label}</Badge>
      );
    })}
  </div>
);

// Render the systemic response as a shareable Markdown summary (for pasting
// into a ticket / Slack / email).  Mirrors the on-page sections.
function systemicToMarkdown(data: SystemicInsightsResponse, projectName?: string): string {
  const lines: string[] = [`# Systemic Insights${projectName ? ` — ${projectName}` : ''}`, ''];
  if (!data.adopted) return [...lines, '_No scoped subnets yet._'].join('\n');
  const e = data.estate;
  if (e) {
    lines.push(
      `Hosts in scope: ${e.hosts_in_scope} · Subnets: ${e.subnets} · Sites: ${e.sites} · Estate blind spots: ${e.blind_spot_count}`,
      '',
    );
  }
  const blind = data.blind_spots ?? [];
  if (blind.length) {
    lines.push('## Estate blind spots', '');
    for (const b of blind) {
      lines.push(
        `- **${b.label}** — ${b.affected_hosts} hosts (${Math.round(b.host_fraction * 100)}%), ` +
        `${b.subnet_spread} subnets, ${b.site_spread} sites. ${b.recommended_action}`,
      );
    }
    lines.push('');
  }
  const conditions = data.conditions ?? [];
  if (conditions.length) {
    lines.push('## Systemic conditions', '', '| Condition | Hosts | Subnets | Sites | Score | Scope |', '|---|---:|---:|---:|---:|---|');
    for (const c of conditions) {
      lines.push(
        `| ${c.label.replace(/\|/g, '/')} | ${c.affected_hosts} (${Math.round(c.host_fraction * 100)}%) | ` +
        `${c.subnet_spread} | ${c.site_spread} | ${c.systemic_score} | ${c.is_blind_spot ? 'estate-wide' : 'localised'} |`,
      );
    }
    lines.push('');
  }
  const outliers = data.segment_outliers ?? [];
  if (outliers.length) {
    lines.push('## Segment outliers', '', '| Subnet | Site | Hosts | Density | Conditions |', '|---|---|---:|---:|---|');
    for (const o of outliers) {
      lines.push(`| ${o.cidr} | ${o.site ?? '—'} | ${o.host_count} | ${o.times_median}× median | ${(o.conditions || []).join(', ') || '—'} |`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

const SystemicInsights: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [data, setData] = useState<SystemicInsightsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const handleCopyMarkdown = useCallback(async () => {
    if (!data) return;
    const ok = await copyToClipboard(systemicToMarkdown(data, currentProject?.name));
    toast[ok ? 'success' : 'error'](ok ? 'Summary copied as Markdown' : 'Could not copy to clipboard');
  }, [data, currentProject?.name, toast]);

  const handleDownloadJson = useCallback(() => {
    if (!data) return;
    downloadTextFile(`systemic_insights_${new Date().toISOString().split('T')[0]}.json`,
      JSON.stringify(data, null, 2), 'application/json');
  }, [data]);

  const handleExportReport = useCallback(async () => {
    setExporting(true);
    try {
      await downloadSystemicReport();
    } catch (e) {
      toast.error(formatApiError(e, 'Could not export the report.'));
    } finally {
      setExporting(false);
    }
  }, [toast]);

  const load = useCallback(() => {
    setLoading(true);
    getSystemicInsights()
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Could not load systemic insights.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load, currentProject?.id]);

  const estate = data?.estate;
  const blindSpots = useMemo(() => data?.blind_spots ?? [], [data]);
  const conditions = useMemo(() => data?.conditions ?? [], [data]);
  const outliers = useMemo(() => data?.segment_outliers ?? [], [data]);
  const profiles = useMemo(() => data?.diagnostic_profiles ?? [], [data]);
  const nothingFound =
    !!data?.adopted && blindSpots.length === 0 && conditions.length === 0;

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Systemic Insights</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Which weaknesses recur across the{' '}
            <strong className="text-foreground">estate</strong> — every in-scope host across all
            sites and subnets in this project — and how widely they spread. A weakness on one
            host is incidental;{' '}
            <strong className="text-foreground">the same weakness across many subnets and sites</strong>{' '}
            is a process failure, and one spanning essentially the whole estate points at an
            organisational blind spot about that threat. The spread is the diagnosis. These are
            evidence-backed hypotheses, not verdicts — review the breakdown.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button size="sm" variant="outline" onClick={handleCopyMarkdown} disabled={loading || !data?.adopted}>
            <Copy className="size-3.5" aria-hidden /> Copy summary
          </Button>
          <Button size="sm" variant="outline" onClick={handleDownloadJson} disabled={loading || !data?.adopted}>
            <Download className="size-3.5" aria-hidden /> JSON
          </Button>
          <Button size="sm" variant="outline" onClick={handleExportReport} disabled={loading || exporting}>
            <FileText className={`size-3.5 ${exporting ? 'animate-pulse' : ''}`} aria-hidden /> Export report
          </Button>
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
          </Button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Analysing the estate…</p>
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
              Systemic analysis groups hosts by subnet and site to measure how widely a weakness
              spreads. Define a scope with subnets, then re-run this view.
            </p>
            <Button asChild size="sm" className="mt-md">
              <Link to="/scopes">Manage scopes</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {estate && (
            <div className="flex flex-wrap items-center gap-x-lg gap-y-xs text-caption text-muted-foreground">
              <span className="inline-flex items-center gap-xxs">
                Hosts in scope: <span className="font-medium text-foreground">{estate.hosts_in_scope}</span>
                <InfoTip text="Only hosts that resolve to a scoped subnet are analysed — systemic spread is measured against this denominator, not every host in the project. Define scopes/subnets to bring more hosts in scope." />
              </span>
              <span>Subnets: <span className="font-medium text-foreground">{estate.subnets}</span></span>
              <span>Sites: <span className="font-medium text-foreground">{estate.sites}</span></span>
              <span>Estate blind spots: <span className="font-medium text-foreground">{estate.blind_spot_count}</span></span>
            </div>
          )}

          {nothingFound ? (
            <Alert variant="success">
              <ShieldCheck className="size-4" aria-hidden />
              <AlertTitle>No systemic patterns detected</AlertTitle>
              <AlertDescription>
                No weakness recurs widely enough across the in-scope estate to suggest a systemic
                process failure. Per-host issues still surface in{' '}
                <Link to="/insights" className="underline">Subnet Insights</Link>.
              </AlertDescription>
            </Alert>
          ) : (
            <>
              {/* Tier 1 — estate blind spots */}
              <section className="space-y-sm">
                <div className="flex items-center gap-xs">
                  <AlertTriangle className="size-4 text-warning" aria-hidden />
                  <h2 className="text-subheading font-semibold text-foreground">Estate blind spots</h2>
                  <InfoTip text="A weakness becomes a blind spot when it affects a meaningful share of in-scope hosts AND spans most of the estate's sites (or the whole estate in a single-site project). Derived from the systemic analysis — the spread is the diagnosis, not the raw count." />
                  <span className="text-caption text-muted-foreground">
                    weaknesses spanning most of the estate — likely an org-level gap
                  </span>
                </div>
                {blindSpots.length === 0 ? (
                  <p className="text-caption text-muted-foreground">
                    No weakness spans the estate widely enough to be a blind spot. See systemic
                    conditions below.
                  </p>
                ) : (
                  <div className="grid gap-sm md:grid-cols-2">
                    {blindSpots.map((b) => <BlindSpotCard key={b.key} c={b} />)}
                  </div>
                )}
              </section>

              {/* Systemic conditions (full list, incl. those not estate-wide) */}
              {conditions.length > 0 && (
                <section className="space-y-sm">
                  <div className="flex items-center gap-xs">
                    <h2 className="text-subheading font-semibold text-foreground">Systemic conditions</h2>
                    <InfoTip text="Every recurring weakness and how far it spreads. Hosts = affected in-scope hosts (and their % of the estate); Subnets / Sites = distinct segments touched; Score = severity weight × affected hosts × (1 + subnets + sites). 'Estate-wide' marks the blind spots above." />
                  </div>
                  <Card>
                    <CardContent className="p-0">
                      <div className="overflow-x-auto">
                        <Table className="table-fixed">
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-[26%]">Condition</TableHead>
                              <TableHead className="w-[10%] text-right">Hosts</TableHead>
                              <TableHead className="w-[9%] text-right">Subnets</TableHead>
                              <TableHead className="w-[9%] text-right">Sites</TableHead>
                              <TableHead className="w-[10%] text-right">Score</TableHead>
                              <TableHead className="w-[12%]">Scope</TableHead>
                              <TableHead className="w-[24%]">Recommended action</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {conditions.map((c) => (
                              <TableRow key={c.key}>
                                <TableCell className="align-top">
                                  <span className="block truncate font-medium text-foreground" title={c.label}>
                                    {c.label}
                                  </span>
                                  <span className="block truncate text-caption text-muted-foreground" title={c.vector}>
                                    {c.vector}
                                  </span>
                                </TableCell>
                                <TableCell className="align-top text-right text-foreground">
                                  {(() => {
                                    const href = conditionHostsHref(c.key);
                                    return href ? (
                                      <Link to={href} title="View these hosts"
                                        className="font-medium text-info hover:underline">
                                        {c.affected_hosts}
                                      </Link>
                                    ) : c.affected_hosts;
                                  })()}
                                  <span className="ml-xxs text-caption text-muted-foreground">
                                    ({Math.round(c.host_fraction * 100)}%)
                                  </span>
                                </TableCell>
                                <TableCell className="align-top text-right text-foreground">{c.subnet_spread}</TableCell>
                                <TableCell className="align-top text-right text-foreground">{c.site_spread}</TableCell>
                                <TableCell className="align-top text-right text-foreground">{c.systemic_score}</TableCell>
                                <TableCell className="align-top">
                                  {c.is_blind_spot
                                    ? <Badge variant="destructive">estate-wide</Badge>
                                    : <Badge variant="muted">localised</Badge>}
                                </TableCell>
                                <TableCell className="align-top text-caption text-foreground"
                                  title={c.recommended_action ?? undefined}>
                                  {safeFallback(c.recommended_action, '—')}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </section>
              )}

              {/* Tier 2 — segment outliers */}
              {outliers.length > 0 && (
                <section className="space-y-sm">
                  <div className="flex items-center gap-xs">
                    <h2 className="text-subheading font-semibold text-foreground">Segment outliers</h2>
                    <InfoTip text="Subnets whose issue density (condition-incidences ÷ hosts) is at least 2× the estate-wide median density. Derived per subnet, then compared to the median — surfaces anomalously-bad ranges, not just the largest ones." />
                  </div>
                  <p className="text-caption text-muted-foreground">
                    Subnets whose issue density (issues per host) is well above the estate's own
                    median — anomalies, not just the biggest ranges.
                  </p>
                  <Card>
                    <CardContent className="p-0">
                      <div className="overflow-x-auto">
                        <Table className="table-fixed">
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-[24%]">Subnet</TableHead>
                              <TableHead className="w-[16%]">Site</TableHead>
                              <TableHead className="w-[10%] text-right">Hosts</TableHead>
                              <TableHead className="w-[16%] text-right">Density</TableHead>
                              <TableHead className="w-[34%]">Conditions</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {outliers.map((o) => (
                              <TableRow key={o.subnet_id}>
                                <TableCell className="align-top">
                                  <Link to={subnetHostsHref(o.cidr)} title={`View hosts in ${o.cidr}`}
                                    className="block truncate font-mono text-metadata text-info hover:underline" >
                                    {o.cidr}
                                  </Link>
                                </TableCell>
                                <TableCell className="align-top">
                                  <span className="block truncate text-caption text-foreground" title={o.site ?? undefined}>
                                    {o.site ?? <span className="italic text-muted-foreground">unassigned</span>}
                                  </span>
                                </TableCell>
                                <TableCell className="align-top text-right text-foreground">{o.host_count}</TableCell>
                                <TableCell className="align-top text-right">
                                  <span className="font-medium text-warning" title={`Estate median ${o.estate_median_density}`}>
                                    {o.times_median}× median
                                  </span>
                                </TableCell>
                                <TableCell className="align-top">
                                  <ConditionChips keys={o.conditions} cidr={o.cidr} />
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </section>
              )}

              {/* Tier 3 — diagnostic profiles */}
              {profiles.length > 0 && (
                <section className="space-y-sm">
                  <div className="flex items-center gap-xs">
                    <h2 className="text-subheading font-semibold text-foreground">Diagnostic profiles</h2>
                    <InfoTip text="Which conditions co-occur within each subnet, mapped to a likely management root cause — e.g. EOL OS + missing patches → patch-gap; expired/self-signed TLS → no-PKI; weak/guest auth → cred-hygiene. A hypothesis from the co-occurrence pattern, not a verdict." />
                  </div>
                  <p className="text-caption text-muted-foreground">
                    Per-subnet co-occurrence signature → a likely management root cause.
                  </p>
                  <Card>
                    <CardContent className="p-0">
                      <div className="overflow-x-auto">
                        <Table className="table-fixed">
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-[22%]">Subnet</TableHead>
                              <TableHead className="w-[14%]">Site</TableHead>
                              <TableHead className="w-[28%]">Conditions</TableHead>
                              <TableHead className="w-[36%]">Likely root cause</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {profiles.map((d) => (
                              <TableRow key={d.subnet_id}>
                                <TableCell className="align-top">
                                  <Link to={subnetHostsHref(d.cidr)} title={`View hosts in ${d.cidr}`}
                                    className="block truncate font-mono text-metadata text-info hover:underline">
                                    {d.cidr}
                                  </Link>
                                </TableCell>
                                <TableCell className="align-top">
                                  <span className="block truncate text-caption text-foreground" title={d.site ?? undefined}>
                                    {safeFallback(d.site, 'unassigned')}
                                  </span>
                                </TableCell>
                                <TableCell className="align-top">
                                  <ConditionChips keys={d.conditions} cidr={d.cidr} />
                                </TableCell>
                                <TableCell className="align-top">
                                  <div className="flex min-w-0 items-start gap-xxs">
                                    <Badge variant={ROOT_CAUSE_TONE[d.root_cause.kind] ?? 'muted'}>
                                      {d.root_cause.kind}
                                    </Badge>
                                    <span className="min-w-0 text-caption text-foreground" title={d.root_cause.text}>
                                      {d.root_cause.text}
                                    </span>
                                  </div>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
};

const BlindSpotCard: React.FC<{ c: SystemicCondition }> = ({ c }) => {
  const ips = c.example_ips.filter(Boolean) as string[];
  const href = conditionHostsHref(c.key);
  return (
    <Card className="border-l-4 border-l-destructive">
      <CardContent className="space-y-xs p-md">
        <div className="flex items-start justify-between gap-xs">
          <p className="min-w-0 font-semibold text-foreground" title={c.label}>{c.label}</p>
          {c.severity && (
            <Badge variant="destructive" className="shrink-0">{c.severity}</Badge>
          )}
        </div>
        <p className="text-caption text-muted-foreground">{c.vector}</p>
        <div className="flex flex-wrap gap-xxs">
          <Badge variant="destructive" title="Hosts affected">
            {c.affected_hosts} hosts ({Math.round(c.host_fraction * 100)}%)
          </Badge>
          <Badge variant="warning" title="Distinct subnets spanned">{c.subnet_spread} subnets</Badge>
          <Badge variant="warning" title="Distinct sites spanned">{c.site_spread} sites</Badge>
        </div>
        {ips.length > 0 && (
          <p className="truncate font-mono text-caption text-muted-foreground" title={ips.join(', ')}>
            e.g. {ips.slice(0, 4).join(', ')}{c.affected_hosts > 4 ? '…' : ''}
          </p>
        )}
        <p className="text-caption text-foreground">{c.recommended_action}</p>
        {href && (
          <Link to={href}
            className="inline-flex items-center gap-xxs text-caption font-medium text-info hover:underline">
            View affected hosts <ArrowRight className="size-3" aria-hidden />
          </Link>
        )}
      </CardContent>
    </Card>
  );
};

export default SystemicInsights;
