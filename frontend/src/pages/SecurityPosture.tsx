/**
 * Security Posture — the manager-facing roll-up.
 *
 * A well-supported snapshot (not a time series — tests are rarely rerun): one
 * deterministic label + its reasons, four headline measures, where risk
 * concentrates, the ranked decisions, and the systemic/disposition/site
 * breakdowns. Composes the attention + systemic + finding + agent aggregates
 * (GET /posture); links DOWN into Insights / Systemic / Findings for the detail.
 *
 * UI-style-guide: tables are table-fixed with truncating cells; every state
 * (loading / error / empty) renders a safe fallback; no page-level overflow.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle, ArrowUpRight, Clock, Eye, Info, Loader2, RefreshCw, ShieldAlert,
  ShieldCheck, Telescope, Layers, UserCheck,
} from 'lucide-react';

import {
  getPosture, type PostureResponse, type PriorityItem, type Severity,
} from '../services/api';
import { conditionHostsHref } from '../services/api/insights';
import { buildFindingsUrl, buildHostsUrl, reviewedHostsUrl } from '../utils/drilldownLinks';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { Meter, PrevalenceBar } from '../components/posture/PostureCharts';
import SeverityBar from '../components/ui/SeverityBar';
import RiskBubbleMatrix from '../components/posture/RiskBubbleMatrix';
import DispositionPipeline from '../components/posture/DispositionPipeline';
import {
  SEVERITY_HSL, LABEL_TONE, PRIORITY_KIND, tierHsl, TIER_LABEL,
} from '../components/posture/postureTheme';

const LABEL_ICON = {
  action_required: AlertTriangle,
  needs_assessment: Telescope,
  no_urgent_signals: ShieldCheck,
} as const;

const SevDot: React.FC<{ severity: Severity }> = ({ severity }) => (
  <span className="inline-block size-2.5 shrink-0 rounded-full"
    style={{ background: SEVERITY_HSL[severity] }} aria-hidden />
);

// Plain-English "what is this / how it's derived" help — this is a management
// surface, so every metric explains itself on an explicit (i), not by making
// the operator guess. (Distinct from hiding the DATA behind hover.)
const InfoTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button type="button" aria-label="What is this and how is it derived?"
        className="inline-flex shrink-0 rounded text-muted-foreground/70 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Info className="size-3.5" aria-hidden />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left text-caption leading-snug">{text}</TooltipContent>
  </Tooltip>
);

// Evidence currency — how fresh the snapshot is. Stale/absent scans are
// themselves a posture signal, so this rides next to the headline.
const EvidenceCurrency: React.FC<{ evidence: PostureResponse['evidence'] }> = ({ evidence }) => {
  const days = evidence.scan_staleness_days;
  const text = evidence.scan_count === 0
    ? 'No scans yet'
    : days == null ? `${evidence.scan_count} scans`
      : days === 0 ? `${evidence.scan_count} scans · last today`
        : `${evidence.scan_count} scans · last ${days}d ago`;
  const stale = days != null && days >= 14;
  return (
    <span className={`inline-flex items-center gap-xxs text-caption ${stale || evidence.scan_count === 0 ? 'text-warning' : 'text-muted-foreground'}`}
      title="Evidence currency — how fresh this snapshot is">
      <Clock className="size-3" aria-hidden /> {text}
    </span>
  );
};

const SecurityPosture: React.FC = () => {
  const { currentProject } = useProject();
  const [data, setData] = useState<PostureResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [reloadNonce, setReloadNonce] = useState(0);
  const load = useCallback(() => setReloadNonce((n) => n + 1), []);

  // Each fetch aborts the previous in-flight one — a rapid project switch
  // (A→B→A) or Refresh previously raced, letting a slower response win and
  // painting another project's posture onto this one. Keyed on the project id
  // so a switch re-fetches; the abort guard makes the last *intended* response
  // the one that lands.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getPosture({ signal: controller.signal })
      .then((d) => {
        if (controller.signal.aborted) return;
        setData(d); setError(null);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setError(formatApiError(e, 'Could not load security posture.'));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, [currentProject?.id, reloadNonce]);

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Security Posture</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            A management snapshot — exposure, blast radius, assessment completeness, ownership, and
            the next decision. Every number is explainable and links to the detail.
          </p>
        </div>
        <div className="flex flex-col items-end gap-xs">
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
          </Button>
          {data && <EvidenceCurrency evidence={data.evidence} />}
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Composing posture…</p>
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load posture</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : data ? (
        <>
          <PostureLabelBanner data={data} />
          <HeadlineMeasures data={data} />

          {/* The matrix is the hero — full width so it breathes. */}
          <RiskConcentration data={data} />

          {/* Priorities beside the systemic + disposition stack (their combined
              height balances the tall priorities list). */}
          <div className="grid items-start gap-md lg:grid-cols-2">
            <ManagementPriorities priorities={data.priorities} decisions={data.decisions} />
            <div className="space-y-md">
              <SystemicWeaknesses data={data} />
              <FindingDisposition data={data} />
            </div>
          </div>

          <SitesRequiringAttention data={data} />
        </>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Banner — the deterministic label + its top reasons.
// ---------------------------------------------------------------------------
const PostureLabelBanner: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const tone = LABEL_TONE[data.label];
  const Icon = LABEL_ICON[data.label];
  return (
    <Card className={`border-l-4 ${tone.borderClass} ${tone.tintClass}`}>
      <CardContent className="flex flex-col gap-sm p-md md:flex-row md:items-center md:gap-lg">
        <div className="flex items-center gap-sm">
          <Icon className={`size-7 shrink-0 ${tone.textClass}`} aria-hidden />
          <div>
            <p className="flex items-center gap-xxs text-caption uppercase tracking-wide text-muted-foreground">
              Posture
              <InfoTip text="A deterministic label, not a score. Action required = any unowned critical/high finding, estate blind spot, hot tier-1/2 site, or blocked run. Needs assessment = low review coverage, pending approvals, or untriaged scan data. Otherwise No urgent signals. The lines on the right are the top contributing reasons." />
            </p>
            <p className={`text-subheading font-bold ${tone.textClass}`}>{tone.text}</p>
          </div>
        </div>
        <ul className="min-w-0 flex-1 space-y-xxs md:border-l md:border-border md:pl-lg">
          {data.reasons.length === 0 ? (
            <li className="text-caption text-muted-foreground">No outstanding signals.</li>
          ) : data.reasons.map((r, i) => (
            <li key={i} className="flex items-start gap-xs text-metadata text-foreground">
              <span className="mt-1"><SevDot severity={r.severity} /></span>
              <span className="min-w-0">{r.text}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Headline measures — four cards, each with a micro-visual.
// ---------------------------------------------------------------------------
// One consistent stat-card shell so the row reads as a set: label + icon, a
// big number, a thin supporting visual, then a caption. (Replaces the mixed
// donut-gauge / stacked-bar cards that looked off against each other.)
const StatCard: React.FC<{
  label: string;
  icon: React.ReactNode;
  value: React.ReactNode;
  info: string;
  visual?: React.ReactNode;
  children?: React.ReactNode;
  /** Drill-down for the headline number (§26) — renders it as a link. */
  to?: string;
  toLabel?: string;
}> = ({ label, icon, value, info, visual, children, to, toLabel }) => (
  <Card>
    <CardContent className="flex h-full flex-col gap-sm p-md">
      <div className="flex items-center justify-between gap-xs">
        <span className="flex items-center gap-xxs text-caption text-muted-foreground">
          {label} <InfoTip text={info} />
        </span>
        <span className="text-muted-foreground" aria-hidden>{icon}</span>
      </div>
      {to ? (
        <Link to={to} aria-label={toLabel ?? `${label} — view`}
          className="text-page-title font-bold tabular-nums leading-none text-foreground hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          {value}
        </Link>
      ) : (
        <p className="text-page-title font-bold tabular-nums leading-none text-foreground">{value}</p>
      )}
      <div className="flex h-6 items-center">{visual}</div>
      <div className="mt-auto">{children}</div>
    </CardContent>
  </Card>
);

const HeadlineMeasures: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const h = data.headline;
  const ownPct = h.ownership.pct;
  const conditions = data.systemic.conditions;
  return (
    <div className="grid gap-md sm:grid-cols-2 xl:grid-cols-4">
      {/* Active findings (curated) */}
      <StatCard
        label="Active findings"
        icon={<ShieldAlert className="size-4" />}
        value={h.active_exposure.active_findings}
        info="Curated findings still open, confirmed, or in retest — issues an analyst has accepted as real. Excludes resolved (remediated / false-positive / accepted-risk) and raw scanner detections (counted separately below)."
        to={buildFindingsUrl({ status: 'active' })}
        toLabel={`${h.active_exposure.active_findings} active findings — view`}
        visual={<SeverityBar counts={h.active_exposure.by_severity} variant="compact"
          segmentHref={(sev) => buildFindingsUrl({ status: 'active', severity: sev })} />}
      >
        <p className="text-caption text-muted-foreground">
          curated · open / confirmed / retest ·{' '}
          <span title="Scanner-detected vulnerabilities — raw, not analyst-curated. Shown separately, never summed.">
            {h.detected_exposure.vuln_count.toLocaleString()} scanner-detected
          </span>
        </p>
      </StatCard>

      {/* Assessment coverage */}
      <StatCard
        label="Assessment coverage"
        icon={<Eye className="size-4" />}
        value={h.review_coverage.pct == null ? '—' : `${h.review_coverage.pct}%`}
        info="Share of discovered hosts an analyst has marked Reviewed — derived as reviewed ÷ total hosts. 'Validated' counts hosts with a completed test (a stronger signal than review)."
        to={reviewedHostsUrl(true)}
        toLabel="Reviewed hosts — view"
        visual={<Meter pct={h.review_coverage.pct} color="hsl(var(--info))" />}
      >
        <p className="text-caption text-muted-foreground">
          {h.review_coverage.reviewed.toLocaleString()} / {h.review_coverage.total.toLocaleString()} hosts reviewed
          {' · '}{h.review_coverage.validated_hosts.toLocaleString()} validated
        </p>
        {h.review_coverage.total - h.review_coverage.reviewed > 0 && (
          <Link to={reviewedHostsUrl(false)} className="text-caption text-info hover:underline">
            {(h.review_coverage.total - h.review_coverage.reviewed).toLocaleString()} unreviewed →
          </Link>
        )}
      </StatCard>

      {/* Ownership */}
      <StatCard
        label="Ownership"
        icon={<UserCheck className="size-4" />}
        value={ownPct == null ? '—' : `${ownPct}%`}
        info="Share of active findings with an assigned owner — derived as owned ÷ active findings. Unowned findings have nobody accountable to drive them to closure."
        visual={<Meter pct={ownPct} color={ownPct != null && ownPct < 60 ? 'hsl(var(--warning))' : 'hsl(var(--success))'} />}
      >
        <p className="text-caption text-muted-foreground">
          {h.ownership.owned} owned
          {h.ownership.unowned > 0 && (
            <Link to={buildFindingsUrl({ status: 'active', owner: 'unowned' })}
              className="text-warning hover:underline"> · {h.ownership.unowned} unowned →</Link>
          )}
        </p>
      </StatCard>

      {/* Systemic weaknesses */}
      <StatCard
        label="Systemic weaknesses"
        icon={<Layers className="size-4" />}
        value={h.systemic.adopted ? h.systemic.blind_spot_count : '—'}
        info="Weaknesses that recur estate-wide (e.g. SMB signing disabled on many hosts). Counted as 'blind spots' when one condition spans a meaningful share of hosts AND most sites. Derived from the systemic-insights analysis; needs scoped subnets to assess."
        visual={h.systemic.adopted ? (
          <div className="flex flex-wrap items-center gap-1">
            {conditions.length === 0
              ? <span className="text-caption text-muted-foreground">no recurring conditions</span>
              : conditions.slice(0, 10).map((c) => (
                <span key={c.key} className="size-2.5 rounded-full"
                  title={`${c.label} — ${Math.round(c.host_fraction * 100)}% of hosts`}
                  style={{ background: c.is_blind_spot ? 'hsl(var(--destructive))' : 'hsl(var(--warning))' }} />
              ))}
          </div>
        ) : <span className="text-caption text-warning">Not assessed</span>}
      >
        {h.systemic.adopted ? (
          <Link to="/insights/systemic" className="inline-flex items-center gap-xxs text-caption text-info hover:underline">
            estate blind spots · {h.systemic.condition_count} condition{h.systemic.condition_count === 1 ? '' : 's'}
            <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        ) : (
          <Link to="/scopes" className="inline-flex items-center gap-xxs text-caption text-info hover:underline">
            Needs scoped subnets <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        )}
      </StatCard>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Where risk concentrates — the bubble matrix (with fallbacks).
// ---------------------------------------------------------------------------
const RiskConcentration: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const navigate = useNavigate();
  const adopted = data.sites.adopted;
  const plottable = data.sites.items.filter((s) => s.host_count > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-xs">
          Where risk concentrates
          <InfoTip text="Each bubble is a site. X = % of its hosts reviewed; Y = finding incidences per host (each affected host counts); bubble size = host count; colour = criticality tier. The shaded top-left is the danger zone — under-reviewed AND high-exposure." />
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!adopted ? (
          <div className="py-lg text-center">
            <Telescope className="mx-auto mb-sm size-7 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-foreground">No sites configured yet.</p>
            <p className="mx-auto mt-xxs max-w-sm text-caption text-muted-foreground">
              Group subnets into sites to see exposure vs. review coverage by location.
              Until then, see{' '}
              <Link to="/insights" className="text-info hover:underline">Subnet Insights</Link>.
            </p>
          </div>
        ) : plottable.length === 0 ? (
          <p className="py-lg text-center text-caption text-muted-foreground">
            No discovered hosts in any configured site yet.
          </p>
        ) : (
          <RiskBubbleMatrix sites={data.sites.items}
            onSelectSite={(s) => { if (s.site) navigate(buildHostsUrl({ sites: s.site })); }} />
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Management priorities — the ranked decision list.
// ---------------------------------------------------------------------------
const ManagementPriorities: React.FC<{
  priorities: PriorityItem[];
  decisions: PostureResponse['decisions'];
}> = ({ priorities, decisions }) => (
  <Card>
    <CardHeader>
      <CardTitle className="flex items-center justify-between gap-xs">
        <span className="flex items-center gap-xs">
          Management priorities
          <InfoTip text="The ranked next decisions, worst-first. Combines unowned critical/high findings, estate blind spots, hot tier-1/2 sites, blocked runs, low review coverage, untriaged scan data, and pending approvals — the same signals that set the posture label above." />
        </span>
        {(decisions.pending_approvals > 0 || decisions.blocked_sessions > 0) && (
          <span className="flex gap-xxs">
            {decisions.pending_approvals > 0 && (
              <Badge variant="info">{decisions.pending_approvals} to approve</Badge>
            )}
            {decisions.blocked_sessions > 0 && (
              <Badge variant="warning">{decisions.blocked_sessions} blocked</Badge>
            )}
          </span>
        )}
      </CardTitle>
    </CardHeader>
    <CardContent className="p-0">
      {priorities.length === 0 ? (
        <p className="p-md text-caption text-muted-foreground">Nothing demands a decision right now.</p>
      ) : (
        <ol className="divide-y divide-border">
          {priorities.map((p, i) => {
            const kind = PRIORITY_KIND[p.kind] ?? { label: p.kind, severity: p.severity };
            const row = (
              <div className="flex items-start gap-sm px-md py-sm">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-caption font-semibold tabular-nums text-muted-foreground">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-xs">
                    <SevDot severity={p.severity} />
                    <span className="min-w-0 truncate font-medium text-foreground" title={p.title}>
                      {p.title}
                    </span>
                    <Badge variant="muted" className="shrink-0">{kind.label}</Badge>
                  </div>
                  <p className="mt-xxs truncate text-caption text-muted-foreground" title={p.blast_radius}>
                    {p.blast_radius}
                    {p.owner && <span className="text-foreground"> · owner {p.owner}</span>}
                  </p>
                  <p className="mt-xxs truncate text-caption text-foreground" title={p.action}>
                    → {p.action}
                  </p>
                </div>
                {p.link && <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
              </div>
            );
            return (
              <li key={`${p.kind}-${i}`}>
                {p.link
                  ? <Link to={p.link} className="block hover:bg-muted/50">{row}</Link>
                  : row}
              </li>
            );
          })}
        </ol>
      )}
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// Systemic weaknesses — prevalence bars.
// ---------------------------------------------------------------------------
const SystemicWeaknesses: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const conditions = [...data.systemic.conditions].sort((a, b) => b.host_fraction - a.host_fraction).slice(0, 6);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-xs">
          <span className="flex items-center gap-xs">
            Systemic weaknesses
            <InfoTip text="How widely each recurring weakness spreads — % of in-scope hosts affected, plus how many subnets and sites it touches. An 'estate blind spot' spans most of the estate, pointing at a process gap rather than a one-off." />
          </span>
          <Link to="/insights/systemic" className="text-caption text-info hover:underline">All →</Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {!data.systemic.adopted ? (
          <p className="text-caption text-warning">
            Systemic posture can't be assessed yet — no scoped subnets.{' '}
            <Link to="/scopes" className="text-info hover:underline">Manage scopes</Link>.
          </p>
        ) : conditions.length === 0 ? (
          <p className="text-caption text-muted-foreground">
            Assessed — no weakness recurs widely enough across the estate to read as systemic.
          </p>
        ) : conditions.map((c) => {
          const color = c.is_blind_spot ? 'hsl(var(--destructive))' : 'hsl(var(--warning))';
          // Shared-vuln blind spots (key vuln:<plugin_id>) have no host predicate.
          const href = conditionHostsHref(c.key);
          return (
            <div key={c.key} className="space-y-xs">
              <div className="flex items-baseline justify-between gap-xs">
                {href ? (
                  <Link to={href} className="min-w-0 truncate text-metadata font-medium text-info hover:underline"
                    title={`${c.label} — view affected hosts`}>
                    {c.label}
                  </Link>
                ) : (
                  <span className="min-w-0 truncate text-metadata font-medium text-foreground" title={c.label}>
                    {c.label}
                  </span>
                )}
                <span className="shrink-0 text-caption tabular-nums text-muted-foreground">
                  {c.affected_hosts} hosts ({Math.round(c.host_fraction * 100)}%)
                </span>
              </div>
              <PrevalenceBar fraction={c.host_fraction} color={color} />
              <div className="flex flex-wrap items-center gap-xxs">
                {c.is_blind_spot && <Badge variant="destructive">estate blind spot</Badge>}
                <Badge variant="muted">{c.subnet_spread} subnets</Badge>
                <Badge variant="muted">{c.site_spread} sites</Badge>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Finding disposition — scanner-confirmed kept visually separate.
// ---------------------------------------------------------------------------
const FindingDisposition: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const d = data.disposition;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-xs">
          <span className="flex items-center gap-xs">
            Finding disposition
            <InfoTip text="Where curated findings sit in their lifecycle. Active = open / confirmed / retest; Resolved = remediated / false-positive / accepted-risk. The two figures above split active findings by ORIGIN (analyst-raised vs scanner-sourced), which is independent of status." />
          </span>
          <Link to="/findings" className="text-caption text-info hover:underline">Findings →</Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {/* Active split by SOURCE/origin, not disposition — never summed.
            (How a finding originated, independent of its confirmation status.) */}
        <div className="grid grid-cols-2 gap-sm">
          {/* No single "not scanner" predicate, so non-scanner stays passive —
              a plausible-but-wrong drill-down is worse than none (§26). */}
          <div className="rounded-control border border-border p-sm">
            <p className="text-page-title font-bold tabular-nums text-foreground">{d.non_scanner_active}</p>
            <p className="text-caption text-muted-foreground">non-scanner active</p>
          </div>
          <Link to={buildFindingsUrl({ status: 'active', source: 'scanner' })}
            className="rounded-control border border-dashed border-border p-sm hover:bg-muted/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <p className="text-page-title font-bold tabular-nums text-muted-foreground">{d.scanner_active}</p>
            <p className="text-caption text-muted-foreground">scanner-sourced active →</p>
          </Link>
        </div>
        <p className="text-caption text-muted-foreground">By origin (note / manual / execution vs scanner) — not confirmation status.</p>

        <DispositionPipeline byStatus={d.by_status}
          statusHref={(status) => buildFindingsUrl({ status: status as never })} />
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Sites requiring attention — worst-first table.
// ---------------------------------------------------------------------------
const SitesRequiringAttention: React.FC<{ data: PostureResponse }> = ({ data }) => {
  if (!data.sites.adopted || data.sites.items.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-xs">
          Sites requiring attention
          <InfoTip text="Configured sites worst-first by tier-weighted exposure (severity-weighted active findings scaled by site criticality) and neglect. 'Reviewed' is the % of the site's hosts marked Reviewed. Sites with zero discovered hosts are included — absence of results is not safety." />
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[17%]">Site</TableHead>
                <TableHead className="w-[11%]">Tier</TableHead>
                <TableHead className="w-[7%] text-right">Hosts</TableHead>
                <TableHead className="w-[19%]">Active exposure</TableHead>
                <TableHead className="w-[10%] text-right">Reviewed</TableHead>
                <TableHead className="w-[14%]">Owner</TableHead>
                <TableHead className="w-[22%]">Recommended action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.sites.items.map((s, i) => {
                const reviewed = s.host_count - s.neglect.unreviewed_hosts;
                const reviewPct = s.host_count > 0 ? Math.round((reviewed / s.host_count) * 100) : null;
                return (
                  <TableRow key={s.site_id ?? `unassigned-${i}`}>
                    <TableCell className="truncate font-medium text-foreground"
                      title={s.unassigned ? 'Unassigned' : (s.site ?? undefined)}>
                      {s.unassigned ? (
                        <span className="italic text-muted-foreground">Unassigned</span>
                      ) : s.site ? (
                        <Link to={buildHostsUrl({ sites: s.site })} className="text-info hover:underline"
                          title={`${s.site} — view hosts`}>
                          {s.site}
                        </Link>
                      ) : '—'}
                    </TableCell>
                    <TableCell>
                      {s.criticality_tier ? (
                        <span className="inline-flex items-center gap-xxs text-caption">
                          <span className="size-2.5 rounded-full" style={{ background: tierHsl(s.criticality_tier) }} aria-hidden />
                          {TIER_LABEL[s.criticality_tier]?.split(' — ')[0] ?? `Tier ${s.criticality_tier}`}
                        </span>
                      ) : <span className="text-caption text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-foreground">{s.host_count}</TableCell>
                    <TableCell>
                      {s.exposure.active_findings === 0 ? (
                        <span className="text-caption text-muted-foreground">none</span>
                      ) : (
                        <div className="flex items-center gap-xs">
                          <span className="w-8 shrink-0 text-caption tabular-nums text-foreground">
                            {s.exposure.active_findings}
                          </span>
                          <div className="min-w-0 flex-1"><SeverityBar counts={s.exposure.by_severity} variant="compact" /></div>
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {reviewPct == null ? (
                        <span className="text-caption text-muted-foreground">—</span>
                      ) : (
                        <span className={`text-caption tabular-nums ${reviewPct < 50 ? 'text-warning' : 'text-foreground'}`}>
                          {reviewPct}%
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="truncate text-caption"
                      title={s.owner_name ?? undefined}>
                      {s.owner_name
                        ? <span className="text-foreground">{s.owner_name}</span>
                        : <span className="text-warning">unassigned</span>}
                    </TableCell>
                    <TableCell className="truncate text-caption text-muted-foreground"
                      title={s.recommended_action.text}>
                      {s.recommended_action.text}
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

export default SecurityPosture;
