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
import { Link } from 'react-router-dom';
import {
  AlertTriangle, ArrowUpRight, Loader2, RefreshCw, Search, ShieldAlert,
  ShieldCheck, Telescope, Layers,
} from 'lucide-react';

import {
  getPosture, type PostureResponse, type PriorityItem, type Severity,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { ArcGauge, SeverityStack, PrevalenceBar } from '../components/posture/PostureCharts';
import RiskBubbleMatrix from '../components/posture/RiskBubbleMatrix';
import {
  SEVERITY_HSL, SEVERITY_LABEL, LABEL_TONE, PRIORITY_KIND, tierHsl, TIER_LABEL,
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

const SecurityPosture: React.FC = () => {
  const { currentProject } = useProject();
  const [data, setData] = useState<PostureResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getPosture()
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Could not load security posture.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load, currentProject?.id]);

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
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
        </Button>
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

          <div className="grid gap-md lg:grid-cols-[1.4fr_1fr]">
            <RiskConcentration data={data} />
            <ManagementPriorities priorities={data.priorities} decisions={data.decisions} />
          </div>

          <div className="grid gap-md lg:grid-cols-2">
            <SystemicWeaknesses data={data} />
            <FindingDisposition data={data} />
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
            <p className="text-caption uppercase tracking-wide text-muted-foreground">Posture</p>
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
const HeadlineMeasures: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const h = data.headline;
  const ownPct = h.ownership.pct;
  return (
    <div className="grid gap-md sm:grid-cols-2 xl:grid-cols-4">
      {/* Confirmed exposure */}
      <Card>
        <CardContent className="space-y-sm p-md">
          <div className="flex items-baseline justify-between gap-xs">
            <span className="text-caption text-muted-foreground">Confirmed exposure</span>
            <ShieldAlert className="size-4 text-muted-foreground" aria-hidden />
          </div>
          <p className="text-page-title font-bold tabular-nums text-foreground">
            {h.confirmed_exposure.active_findings}
          </p>
          <SeverityStack counts={h.confirmed_exposure.by_severity} showLegend />
          <p className="text-caption text-muted-foreground">
            active curated findings ·{' '}
            <span title="Scanner-detected vulnerabilities — not analyst-confirmed">
              {h.detected_exposure.vuln_count} scanner-detected
            </span>
          </p>
        </CardContent>
      </Card>

      {/* Assessment coverage */}
      <Card>
        <CardContent className="flex items-center gap-md p-md">
          <ArcGauge pct={h.review_coverage.pct} color="hsl(var(--info))" label="reviewed" />
          <div className="min-w-0 space-y-xxs">
            <p className="text-caption text-muted-foreground">Assessment coverage</p>
            <p className="text-body font-semibold text-foreground">
              {h.review_coverage.reviewed} / {h.review_coverage.total}
            </p>
            <p className="text-caption text-muted-foreground">hosts reviewed</p>
            <p className="text-caption text-muted-foreground">
              {h.review_coverage.validated_hosts} validated by a test
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Ownership */}
      <Card>
        <CardContent className="flex items-center gap-md p-md">
          <ArcGauge
            pct={ownPct}
            color={ownPct != null && ownPct < 60 ? 'hsl(var(--warning))' : 'hsl(var(--success))'}
            label="owned"
          />
          <div className="min-w-0 space-y-xxs">
            <p className="text-caption text-muted-foreground">Ownership</p>
            <p className="text-body font-semibold text-foreground">
              {h.ownership.owned} / {h.ownership.total}
            </p>
            <p className="text-caption text-muted-foreground">active findings owned</p>
            {h.ownership.unowned > 0 && (
              <Badge variant="warning" className="mt-xxs">{h.ownership.unowned} unowned</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Systemic weaknesses */}
      <Card>
        <CardContent className="space-y-sm p-md">
          <div className="flex items-baseline justify-between gap-xs">
            <span className="text-caption text-muted-foreground">Systemic weaknesses</span>
            <Layers className="size-4 text-muted-foreground" aria-hidden />
          </div>
          <p className="text-page-title font-bold tabular-nums text-foreground">
            {h.systemic.blind_spot_count}
          </p>
          <p className="text-caption text-muted-foreground">
            estate blind spots · {h.systemic.condition_count} recurring condition
            {h.systemic.condition_count === 1 ? '' : 's'}
          </p>
          <Link to="/insights/systemic" className="inline-flex items-center gap-xxs text-caption text-info hover:underline">
            Investigate <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        </CardContent>
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Where risk concentrates — the bubble matrix (with fallbacks).
// ---------------------------------------------------------------------------
const RiskConcentration: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const adopted = data.sites.adopted;
  const plottable = data.sites.items.filter((s) => s.host_count > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Where risk concentrates</CardTitle>
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
          <RiskBubbleMatrix sites={data.sites.items} />
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
        <span>Management priorities</span>
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
          <span>Systemic weaknesses</span>
          <Link to="/insights/systemic" className="text-caption text-info hover:underline">All →</Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {!data.systemic.adopted || conditions.length === 0 ? (
          <p className="text-caption text-muted-foreground">
            No weakness recurs widely enough across the estate to read as systemic.
          </p>
        ) : conditions.map((c) => {
          const color = c.is_blind_spot ? 'hsl(var(--destructive))' : 'hsl(var(--warning))';
          return (
            <div key={c.key} className="space-y-xs">
              <div className="flex items-baseline justify-between gap-xs">
                <span className="min-w-0 truncate text-metadata font-medium text-foreground" title={c.label}>
                  {c.label}
                </span>
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
  const STATUS_ORDER = ['open', 'confirmed', 'retest', 'remediated', 'false_positive', 'accepted_risk'];
  const statuses = STATUS_ORDER.filter((s) => d.by_status[s]);
  const maxStatus = Math.max(1, ...statuses.map((s) => d.by_status[s]));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-xs">
          <span>Finding disposition</span>
          <Link to="/findings" className="text-caption text-info hover:underline">Findings →</Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {/* Active source split — analyst-confirmed vs scanner, never summed. */}
        <div className="grid grid-cols-2 gap-sm">
          <div className="rounded-control border border-border p-sm">
            <p className="text-page-title font-bold tabular-nums text-foreground">{d.analyst_active}</p>
            <p className="text-caption text-muted-foreground">analyst-confirmed active</p>
          </div>
          <div className="rounded-control border border-dashed border-border p-sm">
            <p className="text-page-title font-bold tabular-nums text-muted-foreground">{d.scanner_active}</p>
            <p className="text-caption text-muted-foreground">scanner-sourced active</p>
          </div>
        </div>

        {statuses.length === 0 ? (
          <p className="text-caption text-muted-foreground">No findings recorded yet.</p>
        ) : (
          <div className="space-y-xs">
            {statuses.map((s) => {
              const sev = d.by_status_severity[s] ?? {};
              return (
                <div key={s} className="flex items-center gap-sm">
                  <span className="w-28 shrink-0 truncate text-caption capitalize text-muted-foreground">
                    {s.replace('_', ' ')}
                  </span>
                  <div className="flex-1" style={{ maxWidth: `${(d.by_status[s] / maxStatus) * 100}%` }}>
                    <SeverityStack counts={sev} height={14} />
                  </div>
                  <span className="w-8 shrink-0 text-right text-caption tabular-nums text-foreground">
                    {d.by_status[s]}
                  </span>
                </div>
              );
            })}
          </div>
        )}
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
        <CardTitle>Sites requiring attention</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[20%]">Site</TableHead>
                <TableHead className="w-[12%]">Tier</TableHead>
                <TableHead className="w-[8%] text-right">Hosts</TableHead>
                <TableHead className="w-[22%]">Confirmed exposure</TableHead>
                <TableHead className="w-[12%] text-right">Reviewed</TableHead>
                <TableHead className="w-[26%]">Recommended action</TableHead>
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
                      {s.unassigned ? <span className="italic text-muted-foreground">Unassigned</span> : safeFallback(s.site, '—')}
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
                          <div className="min-w-0 flex-1"><SeverityStack counts={s.exposure.by_severity} height={10} /></div>
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
