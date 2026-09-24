/**
 * Segments — "which sites and subnets need attention first?"
 *
 * v5.262.0 — the Posture layout (sections over thin rules, no cards), read top
 * to bottom: the worst segment named in one sentence, four measures (active
 * findings and the three hygiene signals), then ONE ranked table with a
 * Site | Subnet lens.  The lens opens on Site when the project defines sites,
 * otherwise on Subnet.  Every number stays decomposed and explainable — no
 * opaque score on screen; the order is exposure (active findings by severity,
 * weighted by the site's criticality tier), then open assessment work and
 * hygiene, then size.  A site's tier is shown as its own mark, never folded
 * into a count.
 *
 * UI-style-guide: tables are table-fixed with explicit widths; CIDR / site /
 * action cells truncate or clamp; every state (loading / error / not-adopted /
 * empty) renders a safe fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, Copy, Download, FileText, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';

import {
  getSubnetInsights,
  getPosture,
  conditionHostsHref,
  subnetHostsHref,
  downloadSystemicReport,
  type SubnetInsight,
  type SubnetInsightsResponse,
  type PostureSite,
} from '../services/api';
import { buildFindingsUrl, buildHostsUrl } from '../utils/drilldownLinks';
import SeverityBar from '../components/ui/SeverityBar';
import { tierHsl, TIER_LABEL } from '../components/posture/postureTheme';
import { formatApiError } from '../utils/apiErrors';
import { copyToClipboard, downloadTextFile } from '../utils/clipboard';
import { useToast } from '../contexts/ToastContext';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import LastUpdated from '../components/LastUpdated';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import PostureEmpty from '../components/posture/PostureEmpty';

type Lens = 'site' | 'subnet';
type Exposure = { active_findings: number; by_severity: Partial<Record<'critical' | 'high' | 'medium' | 'low' | 'info', number>> };

const PAGE_SIZE = 50;
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

// Render the current page of subnets as a shareable Markdown table.
function subnetsToMarkdown(data: SubnetInsightsResponse, projectName?: string): string {
  const lines: string[] = [`# Segments — subnets${projectName ? ` — ${projectName}` : ''}`, ''];
  if (!data.adopted) return [...lines, '_No scoped subnets yet._'].join('\n');
  const t = data.totals;
  if (t) {
    lines.push(
      `Subnets: ${t.subnet_count} · Hosts in scope: ${t.hosts_in_scope} · Active findings: ${t.active_findings} · ` +
      `End-of-life OS: ${t.eol_os_hosts} · Certificate issues: ${t.cert_issue_hosts} · Weak authentication: ${t.weak_auth_hosts}`,
      '',
    );
  }
  lines.push(
    `_Worst-first; showing ${data.subnets.length} of ${data.total} subnets._`, '',
    '| Subnet | Site | Hosts | Active findings | EOL | Cert | Weak auth | Risky | Next action |',
    '|---|---|---:|---:|---:|---:|---:|---:|---|',
  );
  for (const s of data.subnets) {
    const h = s.hygiene;
    const action = (s.recommended_action?.text || '').replace(/\|/g, '/');
    lines.push(
      `| ${s.cidr} | ${s.site ?? '—'} | ${s.host_count} | ${s.exposure.active_findings} | ` +
      `${h.eol_os_hosts} | ${h.cert_issue_hosts} | ${h.weak_auth_hosts} | ${h.risky_service_hosts} | ${action} |`,
    );
  }
  return lines.join('\n');
}

/** The site's criticality tier: a coloured dot and "Tier N" — both lenses. */
const TierMark: React.FC<{ tier: number | null | undefined }> = ({ tier }) =>
  tier ? (
    <span className="inline-flex shrink-0 items-center gap-xxs text-caption text-muted-foreground" title={TIER_LABEL[tier]}>
      <span className="size-2 rounded-full" style={{ background: tierHsl(tier) }} aria-hidden />
      T{tier}
    </span>
  ) : null;

const ExposureCell: React.FC<{ exposure: Exposure }> = ({ exposure }) =>
  exposure.active_findings === 0 ? (
    <span className="text-caption text-muted-foreground">none</span>
  ) : (
    <div className="flex min-w-0 items-center gap-xs">
      <span className="shrink-0 tabular-nums text-foreground">{exposure.active_findings}</span>
      <SeverityBar counts={exposure.by_severity} variant="compact" />
    </div>
  );

/** "3 critical and 2 high active findings" — the worst segment, in words. */
function exposureWords(e: Exposure): string | null {
  const c = e.by_severity.critical ?? 0;
  const h = e.by_severity.high ?? 0;
  if (c || h) {
    const parts = [c ? `${c} critical` : null, h ? `${h} high` : null].filter(Boolean);
    return `${parts.join(' and ')} active finding${c + h === 1 ? '' : 's'}`;
  }
  return e.active_findings ? plural(e.active_findings, 'active finding') : null;
}

function hygieneWords(h: SubnetInsight['hygiene']): string | null {
  const parts = [
    h.eol_os_hosts ? `${plural(h.eol_os_hosts, 'host')} on end-of-life OS` : null,
    h.weak_auth_hosts ? `${plural(h.weak_auth_hosts, 'host')} with weak authentication` : null,
    h.cert_issue_hosts ? `${plural(h.cert_issue_hosts, 'host')} with certificate issues` : null,
  ].filter(Boolean) as string[];
  return parts.length ? parts.join(', ') : null;
}

const Segments: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [lens, setLens] = useState<Lens | null>(null);
  const [subnetData, setSubnetData] = useState<SubnetInsightsResponse | null>(null);
  const [subnetError, setSubnetError] = useState<string | null>(null);
  const [sites, setSites] = useState<PostureSite[] | null>(null);
  const [sitesError, setSitesError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [nonce, setNonce] = useState(0);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  // A project switch starts over: first page, and the lens is chosen again.
  useEffect(() => { setOffset(0); setLens(null); setSubnetData(null); setSites(null); }, [currentProject?.id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([getSubnetInsights(PAGE_SIZE, offset), getPosture()]).then(([sub, pos]) => {
      if (cancelled) return;
      if (sub.status === 'fulfilled') { setSubnetData(sub.value); setSubnetError(null); }
      else setSubnetError(formatApiError(sub.reason, 'Could not load the subnets.'));
      if (pos.status === 'fulfilled') {
        setSites(pos.value.sites.adopted ? pos.value.sites.items : []);
        setSitesError(null);
      } else setSitesError(formatApiError(pos.reason, 'Could not load the sites.'));
      if (sub.status === 'fulfilled' || pos.status === 'fulfilled') setLoadedAt(new Date());
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [offset, nonce, currentProject?.id]);

  // Open on Site when the project defines sites, else on Subnet — until the
  // operator picks a lens.
  const namedSites = useMemo(() => (sites ?? []).filter((s) => !s.unassigned), [sites]);
  const effectiveLens: Lens = lens ?? (namedSites.length > 0 ? 'site' : 'subnet');

  const handleCopyMarkdown = useCallback(async () => {
    if (!subnetData) return;
    const ok = await copyToClipboard(subnetsToMarkdown(subnetData, currentProject?.name));
    toast[ok ? 'success' : 'error'](ok ? 'Subnet table copied as Markdown' : 'Could not copy to clipboard');
  }, [subnetData, currentProject?.name, toast]);

  const handleDownloadJson = useCallback(() => {
    if (!subnetData) return;
    downloadTextFile(`subnet_insights_${new Date().toISOString().split('T')[0]}.json`,
      JSON.stringify(subnetData, null, 2), 'application/json');
  }, [subnetData]);

  const totals = subnetData?.totals;
  const firstLoad = loading && !subnetData && !subnetError;

  return (
    <div className="space-y-md p-md md:p-lg">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Segments</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Which sites and subnets need attention first — ranked worst-first, with every part of the ranking shown.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button size="sm" variant="outline" onClick={handleCopyMarkdown} disabled={loading || !subnetData?.adopted}
            title="The subnet table, as Markdown">
            <Copy className="size-3.5" aria-hidden /> Copy subnet table
          </Button>
          <Button size="sm" variant="outline" onClick={handleDownloadJson} disabled={loading || !subnetData?.adopted}>
            <Download className="size-3.5" aria-hidden /> JSON
          </Button>
          <LastUpdated compact lastFetched={loadedAt} onRefresh={reload} isLoading={loading} label="segments" />
        </div>
      </div>

      {firstLoad ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Ranking segments…</p>
        </div>
      ) : subnetError && !subnetData ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load the segments</AlertTitle>
          <AlertDescription>
            <p className="break-words">{subnetError}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={reload}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : !subnetData?.adopted ? (
        <PostureEmpty Icon={ShieldAlert} title="No scoped subnets yet" action={{ to: '/scopes', label: 'Manage scopes' }}>
          Segments group hosts by network range and site. Define a scope with subnets, and group them into sites to
          compare locations.
        </PostureEmpty>
      ) : (
        <div className="space-y-lg">
          <SegmentsLead lens={effectiveLens} subnets={subnetData.subnets} sites={namedSites}
            subnetCount={totals?.subnet_count ?? subnetData.total} hostsInScope={totals?.hosts_in_scope ?? 0} />

          {totals && (
            <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
              <PostureMeasure label="Active findings in scope" value={totals.active_findings.toLocaleString()}
                info="Findings under investigation or confirmed on hosts inside scoped subnets, by severity."
                to={buildFindingsUrl({ status: 'active' })} toLabel="Active findings — view">
                <SeverityBar counts={totals.by_severity} variant="compact"
                  segmentHref={(s) => buildFindingsUrl({ status: 'active', severity: s })} />
              </PostureMeasure>
              <PostureMeasure label="End-of-life OS" value={totals.eol_os_hosts.toLocaleString()}
                info="Hosts whose detected operating system is past its vendor end-of-life date — a hygiene signal independent of findings."
                to={totals.eol_os_hosts ? conditionHostsHref('eol_os') ?? undefined : undefined} toLabel="Hosts on end-of-life OS — view">
                hosts
              </PostureMeasure>
              <PostureMeasure label="Weak authentication" value={totals.weak_auth_hosts.toLocaleString()}
                info="Hosts that accepted weak or guest authentication (NetExec)."
                to={totals.weak_auth_hosts ? conditionHostsHref('weak_auth') ?? undefined : undefined} toLabel="Hosts with weak authentication — view">
                hosts
              </PostureMeasure>
              <PostureMeasure label="Certificate issues" value={totals.cert_issue_hosts.toLocaleString()}
                info="Hosts serving an expired or self-signed TLS certificate."
                to={totals.cert_issue_hosts ? conditionHostsHref('tls_hygiene') ?? undefined : undefined} toLabel="Hosts with certificate issues — view">
                hosts
              </PostureMeasure>
            </div>
          )}

          <PostureSection
            title={<>Worst first
              <InfoTip text="Ranked by exposure first — active findings weighted by severity (critical 10, high 5, medium 2, low 1) and by the site's criticality tier (×2 for tier 1 … ×0.5 for tier 4) — then by open assessment work (unowned findings, unreviewed hosts) and hygiene, then by size. No single score is shown: each part is a column." /></>}
            description={effectiveLens === 'site'
              ? 'Each configured site with its criticality tier. Subnets in no site are rolled up as Unassigned.'
              : 'Each scoped subnet. Open a row for its open work and hygiene detail.'}
            actions={
              <div className="inline-flex rounded-md border border-border p-0.5" role="tablist" aria-label="Segment lens">
                {(['site', 'subnet'] as const).map((m) => (
                  <button key={m} type="button" role="tab" aria-selected={effectiveLens === m} onClick={() => setLens(m)}
                    className={`rounded px-md py-0.5 text-caption font-medium capitalize transition-colors ${effectiveLens === m ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                    {m}
                  </button>
                ))}
              </div>
            }
          >
            {effectiveLens === 'site' ? (
              <SiteTable sites={sites} error={sitesError} onRetry={reload} />
            ) : (
              <SubnetTable data={subnetData} offset={offset} loading={loading} onPage={setOffset} />
            )}
          </PostureSection>
        </div>
      )}
    </div>
  );
};

const SegmentsLead: React.FC<{
  lens: Lens; subnets: SubnetInsight[]; sites: PostureSite[]; subnetCount: number; hostsInScope: number;
}> = ({ lens, subnets, sites, subnetCount, hostsInScope }) => {
  // The first row of the active lens is the worst (the server ranks them).
  const worstSite = lens === 'site' ? sites[0] : undefined;
  const worstSubnet = lens === 'subnet' ? subnets[0] : undefined;
  const name = worstSite ? (worstSite.site ?? 'Unassigned')
    : worstSubnet ? `${worstSubnet.cidr}${worstSubnet.site ? ` (${worstSubnet.site})` : ''}` : null;
  const exposure = worstSite?.exposure ?? worstSubnet?.exposure;
  const exp = exposure ? exposureWords(exposure) : null;
  const hyg = worstSubnet ? hygieneWords(worstSubnet.hygiene) : null;

  let tone: LeadTone = 'clear';
  let sentence: React.ReactNode = `No ${lens} carries active findings${lens === 'subnet' ? ' or hygiene issues' : ''}.`;
  if (name && (exp || hyg)) {
    const crit = (exposure?.by_severity.critical ?? 0) + (exposure?.by_severity.high ?? 0);
    tone = crit > 0 ? 'critical' : 'warning';
    sentence = <>Start with <span className="break-all">{name}</span>: {[exp, hyg].filter(Boolean).join('; ')}.</>;
  }
  return (
    <PostureLead tone={tone} restsOn={<>
      {plural(subnetCount, 'scoped subnet')}{lens === 'site' ? ` in ${plural(sites.length, 'site')}` : ''} · {plural(hostsInScope, 'host')} in
      scope. Ranked by exposure, then open work and hygiene, then size.
    </>}>
      {sentence}
    </PostureLead>
  );
};

const SiteTable: React.FC<{ sites: PostureSite[] | null; error: string | null; onRetry: () => void }> = ({ sites, error, onRetry }) => {
  const toast = useToast();
  const [briefingSite, setBriefingSite] = useState<string | null>(null);
  // Per-site briefing: the executive systemic report scoped to one site — what
  // a site owner takes to their meeting.
  const createSiteBriefing = async (site: string) => {
    setBriefingSite(site);
    try {
      await downloadSystemicReport(site);
    } catch (e) {
      toast.error(formatApiError(e, `Could not create the briefing for ${site}.`));
    } finally {
      setBriefingSite(null);
    }
  };

  if (error) {
    return (
      <div className="flex flex-wrap items-center gap-sm">
        <p className="text-caption text-destructive">{error}</p>
        <Button size="sm" variant="outline" onClick={onRetry}><RefreshCw className="size-3.5" aria-hidden /> Retry</Button>
      </div>
    );
  }
  if (!sites) return <p className="text-caption text-muted-foreground">Loading sites…</p>;
  if (!sites.some((s) => !s.unassigned)) {
    return (
      <p className="text-metadata text-muted-foreground">
        No sites are defined. Group subnets into sites on{' '}
        <Link to="/scopes" className="text-info hover:underline">Scopes</Link> to compare locations — the Subnet lens
        ranks the network ranges meanwhile.
      </p>
    );
  }
  return (
    <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
      <thead>
        <tr className="text-left text-caption text-muted-foreground">
          <th className="w-[24%] pb-xxs pr-md font-medium">Site</th>
          <th className="w-[9%] pb-xxs pr-md text-right font-medium">Hosts</th>
          <th className="w-[20%] pb-xxs pr-md font-medium">Active findings</th>
          <th className="w-[17%] pb-xxs pr-md font-medium">Open work</th>
          <th className="pb-xxs font-medium">Next step</th>
        </tr>
      </thead>
      <tbody>
        {sites.map((s) => {
          const hostsHref = s.site && !s.unassigned ? buildHostsUrl({ sites: s.site }) : undefined;
          return (
            <tr key={s.site_id ?? (s.unassigned ? 'unassigned' : s.site ?? 'na')} className="border-t border-border/60 align-top">
              <td className="py-xs pr-md">
                <div className="flex min-w-0 items-center gap-xs">
                  <span className="min-w-0 truncate font-medium text-foreground" title={s.site ?? 'Unassigned'}>
                    {s.unassigned ? <span className="italic text-muted-foreground">Unassigned</span> : safeFallback(s.site, '—')}
                  </span>
                  <TierMark tier={s.criticality_tier} />
                </div>
                <span className="block truncate text-caption text-muted-foreground" title={s.owner_name ?? undefined}>
                  {s.owner_name ? `Owner ${s.owner_name}` : ''}
                  {s.site && !s.unassigned && (
                    <button type="button" onClick={() => void createSiteBriefing(s.site as string)}
                      disabled={briefingSite !== null} aria-label={`Create briefing for ${s.site}`}
                      className={`${s.owner_name ? 'ml-xs ' : ''}inline-flex items-center gap-xxs rounded text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60`}>
                      {briefingSite === s.site ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <FileText className="size-3" aria-hidden />}
                      Briefing
                    </button>
                  )}
                </span>
              </td>
              <td className="py-xs pr-md text-right tabular-nums">
                {hostsHref ? <Link to={hostsHref} className="text-info hover:underline">{s.host_count}</Link> : s.host_count}
                {s.coverage_gap != null && s.coverage_gap > 0 && (
                  <span className="block text-caption text-warning" title="Expected hosts not yet discovered">{s.coverage_gap} not found</span>
                )}
              </td>
              <td className="py-xs pr-md"><ExposureCell exposure={s.exposure} /></td>
              <td className="py-xs pr-md text-caption">
                <OpenWork unowned={s.neglect.unowned_active_findings} unreviewed={s.neglect.unreviewed_hosts} />
              </td>
              <td className="py-xs text-caption text-foreground">
                <span className="line-clamp-2 break-words" title={s.recommended_action?.text}>{safeFallback(s.recommended_action?.text, '—')}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

const OpenWork: React.FC<{ unowned: number; unreviewed: number }> = ({ unowned, unreviewed }) =>
  unowned + unreviewed === 0 ? (
    <span className="text-muted-foreground">none</span>
  ) : (
    <span className="text-foreground">
      {[unowned ? `${unowned} unowned` : null, unreviewed ? `${unreviewed} unreviewed` : null].filter(Boolean).join(' · ')}
    </span>
  );

const SubnetTable: React.FC<{
  data: SubnetInsightsResponse; offset: number; loading: boolean; onPage: (o: number) => void;
}> = ({ data, offset, loading, onPage }) => {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (id: number) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  if (data.subnets.length === 0) {
    return (
      <p className="text-metadata text-muted-foreground">
        No hosts are mapped to any scoped subnet yet. Upload a scan or run recon, then come back.
      </p>
    );
  }
  return (
    <>
      <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
        <thead>
          <tr className="text-left text-caption text-muted-foreground">
            <th className="w-8 pb-xxs" aria-label="Details" />
            <th className="w-[20%] pb-xxs pr-md font-medium">Subnet</th>
            <th className="w-[14%] pb-xxs pr-md font-medium">Site</th>
            <th className="w-[7%] pb-xxs pr-md text-right font-medium">Hosts</th>
            <th className="w-[16%] pb-xxs pr-md font-medium">Active findings</th>
            <th className="w-[18%] pb-xxs pr-md font-medium">
              <span className="inline-flex items-center gap-xxs">Hygiene
                <InfoTip text="Latent weaknesses independent of findings: end-of-life OS, expired or self-signed certificates, weak or guest authentication, and risky exposed services. A subnet with no findings can still rank high here — absence of findings is not health." />
              </span>
            </th>
            <th className="pb-xxs font-medium">Next step</th>
          </tr>
        </thead>
        <tbody>
          {data.subnets.map((s) => (
            <SubnetRow key={s.subnet_id} s={s} open={expanded.has(s.subnet_id)} onToggle={() => toggle(s.subnet_id)} />
          ))}
        </tbody>
      </table>
      {data.total > PAGE_SIZE && (
        <div className="mt-sm flex items-center justify-between gap-sm text-caption text-muted-foreground">
          <span>Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total} subnets, worst first</span>
          <div className="flex items-center gap-xs">
            <Button size="sm" variant="outline" disabled={loading || offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
            <Button size="sm" variant="outline" disabled={loading || offset + PAGE_SIZE >= data.total} onClick={() => onPage(offset + PAGE_SIZE)}>Next</Button>
          </div>
        </div>
      )}
    </>
  );
};

const SubnetRow: React.FC<{ s: SubnetInsight; open: boolean; onToggle: () => void }> = ({ s, open, onToggle }) => {
  const h = s.hygiene;
  // Each hygiene figure opens that subnet's hosts with the condition; risky
  // services have no host predicate, so that one opens the subnet's hosts.
  const items = [
    { key: 'eol_os', n: h.eol_os_hosts, label: 'end-of-life' },
    { key: 'weak_auth', n: h.weak_auth_hosts, label: 'weak auth' },
    { key: 'tls_hygiene', n: h.cert_issue_hosts, label: 'certificate' },
    { key: null, n: h.risky_service_hosts, label: 'risky services' },
  ].filter((i) => i.n > 0);
  return (
    <>
      <tr className={`border-t border-border/60 align-top ${s.no_coverage ? 'bg-warning/5' : ''}`}>
        <td className="py-xs">
          <button type="button" onClick={onToggle} aria-expanded={open}
            aria-label={open ? `Hide details for ${s.cidr}` : `Show details for ${s.cidr}`}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
            {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
          </button>
        </td>
        <td className="py-xs pr-md">
          <Link to={subnetHostsHref(s.cidr)} title={`View hosts in ${s.cidr}`}
            className="block truncate font-mono font-medium text-info hover:underline">{s.cidr}</Link>
          <span className="block truncate text-caption text-muted-foreground" title={s.scope_name}>{safeFallback(s.scope_name, 'no scope')}</span>
        </td>
        <td className="py-xs pr-md">
          <div className="flex min-w-0 items-center gap-xs">
            <span className="min-w-0 truncate text-caption text-foreground" title={s.site ?? undefined}>
              {s.site ?? <span className="italic text-muted-foreground">unassigned</span>}
            </span>
            <TierMark tier={s.criticality_tier} />
          </div>
        </td>
        <td className="py-xs pr-md text-right tabular-nums">
          <span className={s.no_coverage ? 'font-medium text-warning' : 'text-foreground'}
            title={s.no_coverage ? `No hosts found in ${s.usable_addresses} usable addresses` : `${s.usable_addresses} usable addresses`}>
            {s.host_count}
          </span>
        </td>
        <td className="py-xs pr-md"><ExposureCell exposure={s.exposure} /></td>
        <td className="py-xs pr-md text-caption">
          {items.length === 0 ? <span className="text-muted-foreground">none</span> : (
            <span className="text-foreground">
              {items.map((i, idx) => {
                const href = i.key ? conditionHostsHref(i.key, s.cidr) : subnetHostsHref(s.cidr);
                return (
                  <React.Fragment key={i.label}>
                    {idx > 0 && ' · '}
                    {href ? <Link to={href} className="hover:text-info hover:underline">{i.n} {i.label}</Link> : `${i.n} ${i.label}`}
                  </React.Fragment>
                );
              })}
            </span>
          )}
        </td>
        <td className={`py-xs text-caption ${s.recommended_action.kind === 'ok' ? 'text-muted-foreground' : 'text-foreground'}`}>
          <span className="line-clamp-2 break-words" title={s.recommended_action.text}>{s.recommended_action.text}</span>
        </td>
      </tr>
      {open && (
        <tr className="bg-muted/30">
          <td />
          <td colSpan={6} className="py-sm pr-md">
            <div className="grid gap-md md:grid-cols-3">
              <div className="min-w-0">
                <p className="mb-xxs text-caption font-semibold text-muted-foreground">Open work</p>
                <p className="text-caption text-foreground">
                  {s.neglect.unowned_active_findings} active finding{s.neglect.unowned_active_findings === 1 ? '' : 's'} with no owner ·{' '}
                  {s.neglect.unreviewed_hosts} host{s.neglect.unreviewed_hosts === 1 ? '' : 's'} not reviewed
                </p>
              </div>
              <div className="min-w-0">
                <p className="mb-xxs text-caption font-semibold text-muted-foreground">End-of-life OS</p>
                {h.eol_os_detail.length === 0 ? <p className="text-caption text-muted-foreground">none</p> : (
                  <ul className="space-y-0.5 text-caption">
                    {h.eol_os_detail.map((e) => (
                      <li key={e.host_id} className="truncate" title={`${e.ip_address ?? ''} — ${e.os_name ?? ''}`}>
                        <Link to={`/hosts/${e.host_id}`} className="font-mono text-info hover:underline">{safeFallback(e.ip_address, '?')}</Link>{' '}
                        <span className="text-muted-foreground">{e.eol_label} (end of life {e.eol_date})</span>
                      </li>
                    ))}
                    {h.eol_os_hosts > h.eol_os_detail.length && (
                      <li className="text-muted-foreground">and {h.eol_os_hosts - h.eol_os_detail.length} more</li>
                    )}
                  </ul>
                )}
              </div>
              <div className="min-w-0">
                <p className="mb-xxs text-caption font-semibold text-muted-foreground">Risky services</p>
                {h.risky_services.length === 0 ? <p className="text-caption text-muted-foreground">none</p> : (
                  <p className="break-words text-caption text-foreground">
                    {h.risky_services.map((r) => `${r.label} on ${plural(r.host_count, 'host')}`).join(' · ')}
                  </p>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

export default Segments;
