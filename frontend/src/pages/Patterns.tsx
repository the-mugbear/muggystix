/**
 * Patterns — "which weaknesses recur across the estate, and what might they
 * have in common?"
 *
 * v5.262.0 — the Posture layout (sections over thin rules, no cards), read top
 * to bottom: the answer in one sentence, four measures, then
 *   1. Pattern families — the program-level rollup (identity & auth,
 *      encryption & trust, lifecycle, …), each with its hypothesis and control;
 *   2. Weaknesses — every recurring condition, estate-wide ones first.  The
 *      estate blind spots used to be a card grid ABOVE this table while also
 *      being rows in it; they are now marked in place;
 *   3. Subnets that stand out — density well above the estate's own median;
 *   4. What co-occurs, by subnet — the signature and the question it raises.
 * A weakness on one host is incidental; the same weakness across subnets and
 * sites suggests a shared cause.  Spread is what was observed; every cause
 * named here is a hypothesis.  No opaque score is shown: the order is spread.
 *
 * UI-style-guide: tables are table-fixed with explicit widths; CIDR / site /
 * vector cells truncate; every state (loading / error / not-adopted / empty)
 * renders a safe fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Copy, Download, FileText, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';

import {
  getSystemicInsights,
  conditionHostsHref,
  familyCellHostsHref,
  subnetHostsHref,
  downloadSystemicReport,
  type SystemicInsightsResponse,
  type SystemicCondition,
  type SystemicFamily,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { copyToClipboard, downloadTextFile } from '../utils/clipboard';
import { useToast } from '../contexts/ToastContext';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import PostureEmpty from '../components/posture/PostureEmpty';

type Spread = SystemicCondition['classification'];

// Spread classification → the row's ONE status chip.
const SPREAD: Record<Spread, { variant: 'destructive' | 'warning' | 'muted'; label: string }> = {
  estate_wide: { variant: 'destructive', label: 'Estate-wide' },
  recurring: { variant: 'warning', label: 'Recurring' },
  isolated: { variant: 'muted', label: 'Isolated' },
};
const SPREAD_ORDER: Record<Spread, number> = { estate_wide: 0, recurring: 1, isolated: 2 };
const spreadOf = (c: { classification: Spread }) => SPREAD[c.classification] ?? SPREAD.isolated;

// Kinds name WHAT co-occurs (v5.252.0), not a verdict on who runs the segment.
const ROOT_CAUSE_LABEL: Record<string, string> = {
  compounding: 'Compounding',
  lifecycle: 'Lifecycle',
  certificates: 'Certificates',
  'access-control': 'Access control',
  'legacy-services': 'Legacy services',
  mixed: 'Mixed',
};

// Human label for a condition key.
const CONDITION_LABEL: Record<string, string> = {
  eol_os: 'End-of-life OS',
  cleartext_services: 'Cleartext services',
  tls_hygiene: 'TLS certificates',
  weak_tls: 'Weak TLS',
  weak_auth: 'Weak authentication',
  smb_signing: 'SMB signing',
};

function conditionName(key: string): string {
  if (key.startsWith('vuln:')) return 'Shared vulnerability';
  if (key.startsWith('tech:')) return 'Technology monoculture';
  return CONDITION_LABEL[key] ?? key;
}

const pct = (fraction: number) => `${Math.round(fraction * 100)}%`;
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** Condition names as dot-separated text links (not a row of chips). */
const ConditionLinks: React.FC<{ keys: string[]; cidr?: string | null }> = ({ keys, cidr }) => (
  <span className="text-caption text-muted-foreground">
    {keys.map((k, i) => {
      const href = cidr ? conditionHostsHref(k, cidr) : familyCellHostsHref([k], null);
      const label = conditionName(k);
      return (
        <React.Fragment key={k}>
          {i > 0 && ' · '}
          {href
            ? <Link to={href} className="hover:text-info hover:underline" title={`View${cidr ? ` ${cidr}` : ''} hosts with ${label}`}>{label}</Link>
            : label}
        </React.Fragment>
      );
    })}
  </span>
);

// Render the systemic response as a shareable Markdown summary (for pasting
// into a ticket / Slack / email).  Mirrors the on-page sections.
function systemicToMarkdown(data: SystemicInsightsResponse, projectName?: string): string {
  const lines: string[] = [`# Patterns${projectName ? ` — ${projectName}` : ''}`, ''];
  if (!data.adopted) return [...lines, '_No scoped subnets yet._'].join('\n');
  const e = data.estate;
  if (e) {
    lines.push(
      `Hosts in scope: ${e.hosts_in_scope} · Subnets: ${e.subnets} · Sites: ${e.sites} · Estate-wide weaknesses: ${e.blind_spot_count}`,
      '',
    );
  }
  const families = data.family_summary ?? [];
  if (families.length) {
    lines.push('## Pattern families', '');
    for (const f of families) {
      lines.push(
        `- **${f.family_label}** (${spreadOf(f).label.toLowerCase()}) — ${f.affected_hosts} hosts (${pct(f.host_fraction)}), ` +
        `${f.subnet_spread} subnets, ${f.site_spread} sites. Hypothesis: ${f.root_cause_hypothesis} Control: ${f.recommended_control}`,
      );
    }
    lines.push('');
  }
  const conditions = data.conditions ?? [];
  if (conditions.length) {
    lines.push('## Weaknesses', '', '| Weakness | Family | Hosts | Subnets | Sites | Spread |', '|---|---|---:|---:|---:|---|');
    for (const c of conditions) {
      lines.push(
        `| ${c.label.replace(/\|/g, '/')} | ${(c.family_label ?? '—').replace(/\|/g, '/')} | ` +
        `${c.affected_hosts} (${pct(c.host_fraction)}) | ${c.subnet_spread} | ${c.site_spread} | ${spreadOf(c).label} |`,
      );
    }
    lines.push('');
  }
  const outliers = data.segment_outliers ?? [];
  if (outliers.length) {
    lines.push('## Subnets that stand out', '', '| Subnet | Site | Hosts | Density | Conditions |', '|---|---|---:|---:|---|');
    for (const o of outliers) {
      lines.push(`| ${o.cidr} | ${o.site ?? '—'} | ${o.host_count} | ${o.times_median != null ? `${o.times_median}× median` : `${o.issue_density}/host`} | ${(o.conditions || []).map(conditionName).join(', ') || '—'} |`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

const Patterns: React.FC = () => {
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
      toast.error(formatApiError(e, 'Could not create the briefing.'));
    } finally {
      setExporting(false);
    }
  }, [toast]);

  const load = useCallback(() => {
    setLoading(true);
    getSystemicInsights()
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Could not load the patterns.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load, currentProject?.id]);

  const estate = data?.estate;
  const families = useMemo(() => data?.family_summary ?? [], [data]);
  // Estate-wide first, then as the server ranked them (by spread).
  const conditions = useMemo(
    () => [...(data?.conditions ?? [])].sort((a, b) => SPREAD_ORDER[a.classification] - SPREAD_ORDER[b.classification]),
    [data],
  );
  const outliers = useMemo(() => data?.segment_outliers ?? [], [data]);
  const profiles = useMemo(() => data?.diagnostic_profiles ?? [], [data]);
  const estateWide = conditions.filter((c) => c.classification === 'estate_wide');
  const recurring = conditions.filter((c) => c.classification !== 'isolated');

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Patterns</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Which weaknesses recur across the in-scope estate — the ones that suggest a shared cause worth raising with the
            people who run it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button size="sm" variant="outline" onClick={handleExportReport} disabled={loading || exporting}>
            {exporting ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <FileText className="size-3.5" aria-hidden />}
            Create briefing
          </Button>
          <Button size="sm" variant="outline" onClick={handleCopyMarkdown} disabled={loading || !data?.adopted}>
            <Copy className="size-3.5" aria-hidden /> Copy summary
          </Button>
          <Button size="sm" variant="outline" onClick={handleDownloadJson} disabled={loading || !data?.adopted}>
            <Download className="size-3.5" aria-hidden /> JSON
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
          <AlertTitle>Couldn't load the patterns</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : !data?.adopted ? (
        <PostureEmpty Icon={ShieldAlert} title="No scoped subnets yet" action={{ to: '/scopes', label: 'Manage scopes' }}>
          Patterns measure how far a weakness spreads across subnets and sites, so they need scoped subnets. Define a
          scope with subnets and come back.
        </PostureEmpty>
      ) : (
        <div className="space-y-lg">
          <PatternsLead estateWide={estateWide} recurring={recurring} estate={estate} />

          <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
            <PostureMeasure label="Hosts in scope" value={(estate?.hosts_in_scope ?? 0).toLocaleString()}
              info="Only hosts inside a scoped subnet are analysed — spread is measured against them, not every host in the project.">
              {estate ? `${plural(estate.subnets, 'subnet')} · ${plural(estate.sites, 'site')}` : '—'}
            </PostureMeasure>
            <PostureMeasure label="Estate-wide weaknesses" value={estateWide.length.toLocaleString()}
              info="A weakness is estate-wide when it affects a meaningful share of in-scope hosts AND spans most sites (or the whole estate in a single-site project) — likely an organisation-level gap.">
              {estateWide.length ? 'Marked first under Weaknesses' : 'none reach most of the estate'}
            </PostureMeasure>
            <PostureMeasure label="Recurring weaknesses" value={recurring.length.toLocaleString()}
              info="Weaknesses seen on more than a handful of hosts: estate-wide plus recurring (systemic but confined). Isolated ones are listed but not counted here.">
              {plural(conditions.length - recurring.length, 'isolated one')} also listed
            </PostureMeasure>
            <PostureMeasure label="Subnets that stand out" value={outliers.length.toLocaleString()}
              info="Subnets whose issues per host are at least twice the estate's own median — anomalies, not just the largest ranges.">
              {outliers.length ? 'Listed below' : 'none well above the median'}
            </PostureMeasure>
          </div>

          {families.length > 0 && <FamiliesSection families={families} />}
          {conditions.length > 0 && <WeaknessesSection conditions={conditions} />}

          {outliers.length > 0 && (
            <PostureSection title={<>Subnets that stand out
              <InfoTip text="Issue density = condition incidences ÷ hosts in the subnet, compared with the median across every scoped subnet. Flagged at 2× the median (or, when the median is 0, above an absolute floor)." /></>}
              description="Issues per host well above the estate's own median — worth a look even when the range is small.">
              <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
                <thead>
                  <tr className="text-left text-caption text-muted-foreground">
                    <th className="w-[20%] pb-xxs pr-md font-medium">Subnet</th>
                    <th className="w-[16%] pb-xxs pr-md font-medium">Site</th>
                    <th className="w-[9%] pb-xxs pr-md text-right font-medium">Hosts</th>
                    <th className="w-[15%] pb-xxs pr-md text-right font-medium">Density</th>
                    <th className="pb-xxs font-medium">What is there</th>
                  </tr>
                </thead>
                <tbody>
                  {outliers.map((o) => (
                    <tr key={o.subnet_id} className="border-t border-border/60 align-top">
                      <td className="py-xs pr-md">
                        <Link to={subnetHostsHref(o.cidr)} title={`View hosts in ${o.cidr}`}
                          className="block truncate font-mono text-info hover:underline">{o.cidr}</Link>
                      </td>
                      <td className="truncate py-xs pr-md text-caption" title={o.site ?? undefined}>
                        {o.site ?? <span className="italic text-muted-foreground">unassigned</span>}
                      </td>
                      <td className="py-xs pr-md text-right tabular-nums">{o.host_count}</td>
                      <td className="py-xs pr-md text-right tabular-nums" title={`Estate median ${o.estate_median_density} per host`}>
                        {o.times_median != null ? `${o.times_median}× median` : `${o.issue_density} per host`}
                      </td>
                      <td className="py-xs"><ConditionLinks keys={o.conditions} cidr={o.cidr} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </PostureSection>
          )}

          {profiles.length > 0 && (
            <PostureSection title={<>What co-occurs, by subnet
              <InfoTip text="Which conditions appear together within a subnet, and the question that raises — e.g. only end-of-life systems → how is OS lifecycle handled here? A deliberate legacy enclave, different asset roles or uneven scan depth can produce the same signature: a lead to check, not a conclusion." /></>}
              description="A subnet's combination of weaknesses, and the question it raises.">
              <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
                <thead>
                  <tr className="text-left text-caption text-muted-foreground">
                    <th className="w-[20%] pb-xxs pr-md font-medium">Subnet</th>
                    <th className="w-[14%] pb-xxs pr-md font-medium">Site</th>
                    <th className="w-[26%] pb-xxs pr-md font-medium">Together</th>
                    <th className="pb-xxs font-medium">Worth checking</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((d) => (
                    <tr key={d.subnet_id} className="border-t border-border/60 align-top">
                      <td className="py-xs pr-md">
                        <Link to={subnetHostsHref(d.cidr)} title={`View hosts in ${d.cidr}`}
                          className="block truncate font-mono text-info hover:underline">{d.cidr}</Link>
                      </td>
                      <td className="truncate py-xs pr-md text-caption" title={d.site ?? undefined}>
                        {safeFallback(d.site, 'unassigned')}
                      </td>
                      <td className="py-xs pr-md"><ConditionLinks keys={d.conditions} cidr={d.cidr} /></td>
                      <td className="py-xs text-caption text-foreground">
                        <span className="line-clamp-3 break-words" title={d.root_cause.text}>
                          <span className="font-medium">{ROOT_CAUSE_LABEL[d.root_cause.kind] ?? d.root_cause.kind}: </span>
                          {d.root_cause.text}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </PostureSection>
          )}
        </div>
      )}
    </div>
  );
};

const PatternsLead: React.FC<{
  estateWide: SystemicCondition[];
  recurring: SystemicCondition[];
  estate: SystemicInsightsResponse['estate'];
}> = ({ estateWide, recurring, estate }) => {
  let tone: LeadTone = 'clear';
  let sentence: React.ReactNode;
  if (estateWide.length > 0) {
    tone = 'critical';
    const names = estateWide.slice(0, 3).map((c) => c.label);
    const more = estateWide.length - names.length;
    sentence = <>{plural(estateWide.length, 'weakness', 'weaknesses')} {estateWide.length === 1 ? 'reaches' : 'reach'} most of the
      estate: {names.join('; ')}{more > 0 ? `; and ${more} more` : ''}.</>;
  } else if (recurring.length > 0) {
    tone = 'warning';
    sentence = <>{plural(recurring.length, 'weakness', 'weaknesses')} {recurring.length === 1 ? 'recurs' : 'recur'} across
      subnets, but none reaches most of the estate.</>;
  } else {
    sentence = <>No weakness recurs across the estate.</>;
  }
  return (
    <PostureLead tone={tone} restsOn={<>
      Measured over {plural(estate?.hosts_in_scope ?? 0, 'host')} inside scoped subnets. Spread is what was observed; a
      shared cause is a hypothesis to confirm. {tone === 'clear' && <>That is only as strong as the evidence collected —{' '}
      <Link to="/posture/evidence" className="text-info hover:underline">see Evidence</Link>.</>}
    </>}>
      {sentence}
    </PostureLead>
  );
};

const FamiliesSection: React.FC<{ families: SystemicFamily[] }> = ({ families }) => (
  <PostureSection title={<>Pattern families
    <InfoTip text="Weaknesses grouped into program-level families. Each shows how many in-scope hosts the family affects, how far it spreads, the likely shared cause (a hypothesis) and the program-level control that would address it. Worst-first by spread." /></>}
    description="The weaknesses grouped by what they have in common — each with a cause to test and the control that would fix it at the source.">
    <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
      <thead>
        <tr className="text-left text-caption text-muted-foreground">
          <th className="w-[24%] pb-xxs pr-md font-medium">Family</th>
          <th className="w-[12%] pb-xxs pr-md text-right font-medium">Hosts</th>
          <th className="w-[14%] pb-xxs pr-md font-medium">Reach</th>
          <th className="pb-xxs font-medium">Hypothesis → control</th>
        </tr>
      </thead>
      <tbody>
        {families.map((f) => {
          const href = familyCellHostsHref(f.conditions, null);
          const spread = spreadOf(f);
          return (
            <tr key={f.family} className="border-t border-border/60 align-top">
              <td className="py-xs pr-md">
                <div className="flex min-w-0 items-center gap-xs">
                  <span className="min-w-0 truncate font-medium text-foreground" title={f.family_label}>{f.family_label}</span>
                  <Badge variant={spread.variant} className="shrink-0">{spread.label}</Badge>
                </div>
                <ConditionLinks keys={f.conditions} />
              </td>
              <td className="py-xs pr-md text-right tabular-nums">
                {href
                  ? <Link to={href} className="font-medium text-info hover:underline" title="View these hosts">{f.affected_hosts}</Link>
                  : f.affected_hosts}
                <span className="block text-caption text-muted-foreground">{pct(f.host_fraction)} of scope</span>
              </td>
              <td className="py-xs pr-md text-caption">
                {plural(f.subnet_spread, 'subnet')}<br />{plural(f.site_spread, 'site')}
              </td>
              <td className="py-xs text-caption">
                <p className="break-words text-muted-foreground">{f.root_cause_hypothesis}</p>
                <p className="mt-xxs break-words text-foreground">→ {f.recommended_control}</p>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </PostureSection>
);

const WeaknessesSection: React.FC<{ conditions: SystemicCondition[] }> = ({ conditions }) => (
  <PostureSection title={<>Weaknesses
    <InfoTip text="Every recurring weakness and how far it spreads. Hosts = affected in-scope hosts and their share of the estate; Reach = distinct subnets and sites. Estate-wide = a large share of hosts across most sites; Recurring = systemic but confined; Isolated = a handful of incidents." /></>}
    description="Each weakness on its own, estate-wide ones first, with the action that addresses it.">
    <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
      <thead>
        <tr className="text-left text-caption text-muted-foreground">
          <th className="w-[30%] pb-xxs pr-md font-medium">Weakness</th>
          <th className="w-[12%] pb-xxs pr-md text-right font-medium">Hosts</th>
          <th className="w-[14%] pb-xxs pr-md font-medium">Reach</th>
          <th className="pb-xxs font-medium">Recommended action</th>
        </tr>
      </thead>
      <tbody>
        {conditions.map((c) => {
          const href = conditionHostsHref(c.key);
          const spread = spreadOf(c);
          return (
            <tr key={c.key} className="border-t border-border/60 align-top" data-spread={c.classification}>
              <td className="py-xs pr-md">
                <div className="flex min-w-0 items-center gap-xs">
                  <span className="min-w-0 truncate font-medium text-foreground" title={c.label}>{c.label}</span>
                  <Badge variant={spread.variant} className="shrink-0">{spread.label}</Badge>
                </div>
                <span className="block truncate text-caption text-muted-foreground" title={`${c.family_label ?? ''} — ${c.vector}`}>
                  {c.family_label ? `${c.family_label} · ` : ''}{c.vector}
                </span>
              </td>
              <td className="py-xs pr-md text-right tabular-nums">
                {href
                  ? <Link to={href} className="font-medium text-info hover:underline" title="View these hosts">{c.affected_hosts}</Link>
                  : c.affected_hosts}
                <span className="block text-caption text-muted-foreground">{pct(c.host_fraction)} of scope</span>
              </td>
              <td className="py-xs pr-md text-caption">
                {plural(c.subnet_spread, 'subnet')}<br />{plural(c.site_spread, 'site')}
              </td>
              <td className="py-xs text-caption text-foreground">
                <span className="line-clamp-3 break-words" title={c.recommended_action}>{safeFallback(c.recommended_action, '—')}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </PostureSection>
);

export default Patterns;
