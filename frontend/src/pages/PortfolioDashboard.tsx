import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, FolderOpen, RefreshCw, SquareArrowOutUpRight, Users } from 'lucide-react';
import ProjectMembersSheet from '../components/ProjectMembersSheet';
import PortfolioTeam from '../components/PortfolioTeam';
import {
  getPortfolioDashboard,
  PortfolioDashboardResponse,
  PortfolioSummary,
  ProjectCard,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useAuth } from '../contexts/AuthContext';
import { CardListSkeleton } from '../components/PageSkeleton';
import { formatStatusLabel } from '../utils/statusMeta';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import SeverityBar from '../components/ui/SeverityBar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import { cn } from '../utils/cn';
import { formatApiError } from '../utils/apiErrors';

// Worst-first severity rank for the default ordering — the most damning
// projects float to the top without any filtering (focus, not a to-do list).
const HEALTH_RANK: Record<string, number> = { critical: 0, warning: 1, stale: 2, healthy: 3, unknown: 4 };

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'muted' | 'secondary' | 'outline';

// Health is a backend-derived rollup (critical findings > exposure/low-review
// > staleness > healthy). Surfacing it gives the "is this project OK at a
// glance?" answer the per-column numbers don't.
const HEALTH_META: Record<string, { tone: Tone; label: string }> = {
  critical: { tone: 'destructive', label: 'Critical' },
  warning: { tone: 'warning', label: 'Warning' },
  stale: { tone: 'muted', label: 'Stale' },
  healthy: { tone: 'success', label: 'Healthy' },
  // Fail NEUTRAL, not reassuring: a null/malformed/unrecognized health value
  // must NOT render as green "Healthy" on a security dashboard.
  unknown: { tone: 'muted', label: 'Health unavailable' },
};

const healthMeta = (health: string | null | undefined): { tone: Tone; label: string } =>
  HEALTH_META[health ?? 'unknown'] ?? HEALTH_META.unknown;

// One-line explanation of WHAT drove the (single, worst-signal) health
// rollup — surfaced as the Health badge's tooltip so "Critical" isn't an
// unexplained label.  Mirrors the backend derivation order.
const healthWhy = (card: ProjectCard): string => {
  const v = card.vuln_summary;
  switch (card.health) {
    case 'critical':
      return `${v.critical} critical finding${v.critical === 1 ? '' : 's'}`;
    case 'warning':
      return v.high > 0
        ? `${v.high} high finding${v.high === 1 ? '' : 's'}`
        : `${Math.round(card.review_progress_pct)}% of hosts reviewed`;
    case 'stale':
      return card.days_since_last_scan != null
        ? `No scan in ${card.days_since_last_scan} days`
        : 'No scans yet';
    case 'healthy':
      return 'No outstanding risk signals';
    default:
      // Null / malformed / unrecognized health — don't claim "no risk".
      return 'Health data unavailable';
  }
};

// Health tone → theme colour, for the health rail + per-card accent.
const TONE_HSL: Record<string, string> = {
  destructive: 'hsl(var(--destructive))',
  warning: 'hsl(var(--warning))',
  success: 'hsl(var(--success))',
  muted: 'hsl(var(--muted-foreground))',
  info: 'hsl(var(--info))',
};
const healthHsl = (health: string | null | undefined): string =>
  TONE_HSL[healthMeta(health).tone] ?? TONE_HSL.muted;

const HEALTH_ORDER = ['critical', 'warning', 'stale', 'healthy', 'unknown'] as const;
const HEALTH_LABEL: Record<string, string> = {
  critical: 'Critical', warning: 'Warning', stale: 'Stale', healthy: 'Healthy', unknown: 'Unknown',
};

const freshness = (card: ProjectCard): string =>
  card.days_since_last_scan == null
    ? 'No scans'
    : card.days_since_last_scan === 0 ? 'Scanned today' : `Scanned ${card.days_since_last_scan}d ago`;

// ---------------------------------------------------------------------------
// Portfolio health band — the "how is my whole portfolio?" hero. A health
// distribution rail + aggregate scale + clickable attention rollups.
// ---------------------------------------------------------------------------
const AttnTile: React.FC<{ label: string; value: number; tone: string; onClick?: () => void }> = ({
  label, value, tone, onClick,
}) => {
  const active = value > 0;
  const color = active ? TONE_HSL[tone] ?? TONE_HSL.muted : undefined;
  const inner = (
    <>
      <span className="text-body font-bold tabular-nums" style={{ color }}>{value.toLocaleString()}</span>
      <span className="text-caption text-muted-foreground">{label}</span>
    </>
  );
  const cls = 'flex min-w-24 flex-1 flex-col rounded-control border border-border px-sm py-xs text-left';
  return onClick && active
    ? <button type="button" onClick={onClick} className={cn(cls, 'transition-colors hover:bg-accent')}>{inner}</button>
    : <div className={cn(cls, !active && 'opacity-60')}>{inner}</div>;
};

const PortfolioHero: React.FC<{
  summary: PortfolioSummary;
  projects: ProjectCard[];
  isAdmin: boolean;
  onNeedsAttention: () => void;
  onNoAdmin: () => void;
}> = ({ summary, projects, isAdmin, onNeedsAttention, onNoAdmin }) => {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const p of projects) c[p.health] = (c[p.health] ?? 0) + 1;
    return c;
  }, [projects]);
  const present = HEALTH_ORDER.filter((h) => counts[h]);
  const total = projects.length;

  return (
    <Card>
      <CardContent className="space-y-md p-md">
        <div className="grid gap-md lg:grid-cols-[1.6fr_1fr]">
          {/* Health distribution rail */}
          <div>
            <div className="mb-xs flex items-baseline justify-between gap-xs">
              <span className="text-caption text-muted-foreground">Project health</span>
              <span className="text-caption text-muted-foreground tabular-nums">{total} project{total === 1 ? '' : 's'}</span>
            </div>
            <div className="flex h-6 w-full overflow-hidden rounded-full bg-muted" role="img"
              aria-label={present.map((h) => `${counts[h]} ${HEALTH_LABEL[h]}`).join(', ') || 'no projects'}>
              {present.map((h, i) => (
                <div key={h} title={`${HEALTH_LABEL[h]}: ${counts[h]}`}
                  className={cn('flex items-center justify-center', i > 0 && 'border-l border-background')}
                  style={{ width: `${(counts[h] / total) * 100}%`, background: TONE_HSL[healthMeta(h).tone] }}>
                  {counts[h] / total > 0.1 && (
                    <span className="text-[0.7rem] font-semibold text-white" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
                      {counts[h]}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-sm flex flex-wrap gap-x-md gap-y-xs">
              {HEALTH_ORDER.filter((h) => counts[h]).map((h) => (
                <span key={h} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                  <span className="size-2.5 rounded-full" style={{ background: TONE_HSL[healthMeta(h).tone] }} aria-hidden />
                  <span className="font-medium text-foreground">{counts[h]}</span> {HEALTH_LABEL[h]}
                </span>
              ))}
            </div>
          </div>

          {/* Aggregate scale */}
          <div className="grid grid-cols-3 gap-sm">
            {[
              { label: 'Hosts', value: summary.total_hosts },
              { label: 'Open ports', value: summary.total_open_ports },
              { label: 'Unreviewed', value: summary.total_unreviewed },
            ].map((s) => (
              <div key={s.label} className="flex flex-col justify-center rounded-control border border-border px-sm py-xs">
                <span className="text-subheading font-bold tabular-nums text-foreground">{s.value.toLocaleString()}</span>
                <span className="text-caption text-muted-foreground">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Attention rollups */}
        <div className="flex flex-wrap gap-sm">
          <AttnTile label="Need attention" value={summary.projects_requiring_attention} tone="warning" onClick={onNeedsAttention} />
          <AttnTile label="With critical" value={summary.projects_with_critical} tone="destructive" />
          <AttnTile label="Stale" value={summary.stale_projects} tone="muted" />
          <AttnTile label="No data" value={summary.projects_no_data} tone="muted" />
          <AttnTile label="Pending approvals" value={summary.pending_approvals_total} tone="info" />
          <AttnTile label="Blocked runs" value={summary.blocked_sessions_total} tone="destructive" />
          {isAdmin && (
            <AttnTile label="No admin" value={summary.projects_without_admin} tone="destructive" onClick={onNoAdmin} />
          )}
        </div>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Project tile — a rich, visual replacement for the old table row.
// ---------------------------------------------------------------------------
const ProjectTile: React.FC<{
  card: ProjectCard;
  onOpen: () => void;
  onMembers: () => void;
}> = ({ card, onOpen, onMembers }) => {
  const hm = healthMeta(card.health);
  const color = healthHsl(card.health);
  const v = card.vuln_summary;
  const vulnTotal = v.critical + v.high + v.medium + v.low;
  const pct = Math.round(card.review_progress_pct);
  return (
    <Card className="flex flex-col overflow-hidden border-l-4" style={{ borderLeftColor: color }}>
      <CardContent className="flex flex-1 flex-col gap-sm p-md">
        {/* Header */}
        <div className="flex items-start justify-between gap-xs">
          <div className="min-w-0">
            <button onClick={onOpen}
              className="block max-w-full truncate text-left text-body font-semibold text-foreground hover:text-info focus:outline-none focus-visible:underline">
              {card.name}
            </button>
            <div className="mt-xxs flex flex-wrap items-center gap-xs">
              <span className="inline-flex items-center gap-xxs text-caption font-medium" style={{ color }} title={healthWhy(card)}>
                <span className="size-2 rounded-full" style={{ background: color }} aria-hidden />
                {hm.label}
              </span>
              <Badge variant="outline">{formatStatusLabel(card.status)}</Badge>
            </div>
          </div>
          <button type="button" onClick={onMembers} title="View members"
            className="inline-flex shrink-0 items-center gap-xxs rounded-control border border-border px-xs py-xxs text-caption text-muted-foreground hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Users className="size-3.5" aria-hidden /> {card.member_count}
          </button>
        </div>

        {/* Exposure */}
        <div>
          <div className="mb-xxs flex items-baseline justify-between gap-xs">
            <span className="text-caption text-muted-foreground">Exposure</span>
            <span className="text-caption tabular-nums text-muted-foreground">{vulnTotal.toLocaleString()} vulns</span>
          </div>
          {vulnTotal > 0
            ? <SeverityBar counts={v} variant="inline" />
            : <p className="text-caption text-muted-foreground">No vulnerabilities detected.</p>}
        </div>

        {/* Review coverage */}
        <div>
          <div className="mb-xxs flex items-baseline justify-between gap-xs">
            <span className="text-caption text-muted-foreground">Review coverage</span>
            <span className="text-caption tabular-nums text-foreground">{pct}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="img" aria-label={`${pct}% reviewed`}>
            <div className="h-full rounded-full"
              style={{ width: `${pct}%`, background: pct < 50 ? 'hsl(var(--warning))' : 'hsl(var(--info))' }} />
          </div>
          {card.unreviewed_hosts > 0 && (
            <p className="mt-xxs text-caption text-muted-foreground">{card.unreviewed_hosts.toLocaleString()} unreviewed</p>
          )}
        </div>

        {/* Scale + freshness */}
        <div className="flex flex-wrap gap-x-md gap-y-xxs text-caption text-muted-foreground">
          <span>{card.host_count.toLocaleString()} hosts <span className="opacity-70">({card.up_host_count.toLocaleString()} up)</span></span>
          <span>{card.open_port_count.toLocaleString()} ports</span>
          <span className={card.is_stale ? 'text-warning' : ''}>{freshness(card)}</span>
        </div>

        {/* Signal chips */}
        {(card.pending_plan_reviews > 0 || card.open_tasks > 0 || card.active_sessions > 0
          || card.blocked_sessions > 0 || !card.has_admin) && (
          <div className="flex flex-wrap gap-xxs">
            {card.pending_plan_reviews > 0 && <Badge variant="warning">{card.pending_plan_reviews} pending review</Badge>}
            {card.blocked_sessions > 0 && <Badge variant="destructive">{card.blocked_sessions} blocked</Badge>}
            {card.active_sessions > 0 && <Badge variant="info">{card.active_sessions} active run{card.active_sessions === 1 ? '' : 's'}</Badge>}
            {card.open_tasks > 0 && <Badge variant="muted">{card.open_tasks} open task{card.open_tasks === 1 ? '' : 's'}</Badge>}
            {!card.has_admin && <Badge variant="destructive">No admin</Badge>}
          </div>
        )}

        <Button size="sm" variant="outline" className="mt-auto w-full" onClick={onOpen}>
          Open project <SquareArrowOutUpRight className="size-3.5" aria-hidden />
        </Button>
      </CardContent>
    </Card>
  );
};

const PortfolioDashboard: React.FC = () => {
  const navigate = useNavigate();
  const { projects, selectProject } = useProject();
  const { hasRole } = useAuth();

  const [data, setData] = useState<PortfolioDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [reloadNonce, setReloadNonce] = useState(0);
  // SOC-P1/P2 — project whose members sheet is open.
  const [membersCard, setMembersCard] = useState<ProjectCard | null>(null);
  // P4 — "needs attention" filter, URL-synced (?attention=1) so a
  // triage view is shareable/bookmarkable.
  const [searchParams, setSearchParams] = useSearchParams();
  const attentionOnly = searchParams.get('attention') === '1';
  const setAttentionOnly = (on: boolean) => {
    const params = new URLSearchParams(searchParams);
    if (on) params.set('attention', '1');
    else params.delete('attention');
    setSearchParams(params, { replace: true });
  };
  // SOC-P4 — Projects | Team view toggle (URL-synced ?view=team).
  const view = searchParams.get('view') === 'team' ? 'team' : 'projects';
  const setView = (v: 'projects' | 'team') => {
    const params = new URLSearchParams(searchParams);
    if (v === 'team') params.set('view', 'team');
    else params.delete('view');
    setSearchParams(params, { replace: true });
  };
  // SOC-P3 — admin-only "projects without an admin" governance filter.
  const noAdminOnly = searchParams.get('no_admin') === '1';
  const setNoAdminOnly = (on: boolean) => {
    const params = new URLSearchParams(searchParams);
    if (on) params.set('no_admin', '1');
    else params.delete('no_admin');
    setSearchParams(params, { replace: true });
  };

  const reload = () => setReloadNonce((n) => n + 1);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getPortfolioDashboard()
      .then(setData)
      .catch((err) => setError(formatApiError(err, 'Failed to load portfolio.')))
      .finally(() => setLoading(false));
  }, [reloadNonce]);

  // P4 — row actions must switch the active project BEFORE navigating so
  // the destination opens scoped to the right project.
  const switchAndGo = (card: ProjectCard, to: string) => {
    const proj = projects.find((p) => p.id === card.id);
    if (proj) selectProject(proj);
    navigate(to);
  };

  const handleProjectClick = (card: ProjectCard) => switchAndGo(card, '/operations');

  const filteredProjects = useMemo(() => {
    if (!data) return [];
    let list = data.projects;
    if (statusFilter) list = list.filter((p) => p.status === statusFilter);
    if (attentionOnly) list = list.filter((p) => p.attention_reasons.length > 0);
    if (noAdminOnly) list = list.filter((p) => !p.has_admin);
    // Always worst-first — health severity, then critical findings, then the
    // number of attention signals, then name. The visual grid leads with the
    // most damning projects (focus, not a sortable to-do list).
    return [...list].sort((a, b) => {
      const hr = (HEALTH_RANK[a.health] ?? 9) - (HEALTH_RANK[b.health] ?? 9);
      if (hr !== 0) return hr;
      const cr = b.vuln_summary.critical - a.vuln_summary.critical;
      if (cr !== 0) return cr;
      const ar = b.attention_reasons.length - a.attention_reasons.length;
      if (ar !== 0) return ar;
      return a.name.localeCompare(b.name);
    });
  }, [data, statusFilter, attentionOnly, noAdminOnly]);

  const statusCounts = useMemo(() => {
    if (!data) return {};
    const counts: Record<string, number> = {};
    for (const p of data.projects) counts[p.status] = (counts[p.status] || 0) + 1;
    return counts;
  }, [data]);

  // SOC-P4 — Projects | Team segmented toggle, shared across views.
  const viewTabs = (
    <div className="inline-flex overflow-hidden rounded-control border border-border" role="group" aria-label="Portfolio view">
      {(['projects', 'team'] as const).map((v) => (
        <button
          key={v}
          type="button"
          aria-pressed={view === v}
          onClick={() => setView(v)}
          className={cn(
            'px-sm py-xxs text-metadata capitalize transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            v === 'team' && 'border-l border-border',
            view === v ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
          )}
        >
          {v}
        </button>
      ))}
    </div>
  );

  // Team view is self-contained (PortfolioTeam fetches its own data), so it
  // renders independently of the project-dashboard load state.
  if (view === 'team') {
    return (
      <div className="p-md md:p-lg">
        <div className="mb-md flex flex-wrap items-center justify-between gap-sm">
          <h1 className="text-page-title font-semibold">Team</h1>
          {viewTabs}
        </div>
        <PortfolioTeam />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-md md:p-lg">
        {/* Reserve the sticky-filter-card badge row height while data
            loads so the page doesn't visibly shift when summary
            badges resolve (audit PRF·H1). */}
        <Card className="mb-md">
          <CardContent className="flex flex-col gap-sm p-md lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-xs">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-5 w-24 animate-pulse rounded bg-muted" />
              ))}
            </div>
          </CardContent>
        </Card>
        <CardListSkeleton count={4} cardHeight={180} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>{error}</span>
            <Button size="sm" variant="outline" onClick={reload}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!data) return null;

  const { summary } = data;

  return (
    <div className="space-y-md p-md md:p-lg">
      <div className="flex flex-col gap-xs lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-page-title font-semibold">Portfolio</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Cross-project overview, worst-first — health, exposure, coverage and ownership at a glance.
          </p>
        </div>
        <div className="flex items-center gap-sm">
          {viewTabs}
          <Button size="sm" variant="outline" onClick={reload}>
            <RefreshCw className="size-4" aria-hidden /> Refresh
          </Button>
        </div>
      </div>

      <PortfolioHero
        summary={summary}
        projects={data.projects}
        isAdmin={hasRole('admin')}
        onNeedsAttention={() => setAttentionOnly(true)}
        onNoAdmin={() => setNoAdminOnly(true)}
      />

      <div className="flex flex-wrap items-center justify-between gap-sm">
        <p className="text-metadata text-muted-foreground">
          {filteredProjects.length} project{filteredProjects.length === 1 ? '' : 's'}
          {statusFilter ? ` · status "${statusFilter.replace('_', ' ')}"` : ''}
          {attentionOnly ? ' · needs attention' : ''}
          {noAdminOnly ? ' · no admin' : ''}
        </p>
        <div className="flex flex-wrap items-center gap-sm">
          <Button size="sm" variant={attentionOnly ? 'default' : 'outline'}
            aria-pressed={attentionOnly} onClick={() => setAttentionOnly(!attentionOnly)}>
            <AlertTriangle className="size-4" aria-hidden /> Needs attention
          </Button>
          {hasRole('admin') && (
            <Button size="sm" variant={noAdminOnly ? 'default' : 'outline'}
              aria-pressed={noAdminOnly} onClick={() => setNoAdminOnly(!noAdminOnly)}>
              <Users className="size-4" aria-hidden /> No admins
            </Button>
          )}
          <div className="min-w-40">
            <Select value={statusFilter || 'all'} onValueChange={(v) => setStatusFilter(v === 'all' ? '' : v)}>
              <SelectTrigger aria-label="Filter projects by status">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All ({summary.total_projects})</SelectItem>
                {Object.entries(statusCounts).map(([status, count]) => (
                  <SelectItem key={status} value={status}>
                    {status.replace('_', ' ')} ({count})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {filteredProjects.length === 0 ? (
        <Card>
          <CardContent className="p-xl text-center">
            <FolderOpen className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">
              {noAdminOnly ? 'Every project has an admin. \U0001F389'
                : attentionOnly ? 'No projects currently need attention. \U0001F389'
                : statusFilter ? 'No projects match the selected filter.'
                : 'No projects available.'}
            </p>
            <div className="mt-sm flex justify-center gap-xs">
              {noAdminOnly ? (
                <Button size="sm" variant="outline" onClick={() => setNoAdminOnly(false)}>Show all projects</Button>
              ) : attentionOnly ? (
                <Button size="sm" variant="outline" onClick={() => setAttentionOnly(false)}>Show all projects</Button>
              ) : statusFilter ? (
                <Button size="sm" variant="outline" onClick={() => setStatusFilter('')}>Show all projects</Button>
              ) : (
                summary.total_projects === 0 && hasRole('admin') && (
                  <Button size="sm" onClick={() => navigate('/system-settings')}>Create your first project</Button>
                )
              )}
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-md sm:grid-cols-2 xl:grid-cols-3">
          {filteredProjects.map((card) => (
            <ProjectTile
              key={card.id}
              card={card}
              onOpen={() => handleProjectClick(card)}
              onMembers={() => setMembersCard(card)}
            />
          ))}
        </div>
      )}

      <ProjectMembersSheet
        projectId={membersCard?.id ?? null}
        projectName={membersCard?.name ?? ''}
        canManage={hasRole('admin') || membersCard?.user_role === 'admin'}
        open={membersCard !== null}
        onOpenChange={(o) => { if (!o) setMembersCard(null); }}
        onChanged={reload}
      />
    </div>
  );
};

export default PortfolioDashboard;
