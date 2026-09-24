import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FolderOpen, RefreshCw, Search, Users } from 'lucide-react';
import ProjectMembersSheet from '../components/ProjectMembersSheet';
import PortfolioTeam from '../components/PortfolioTeam';
import {
  getPortfolioDashboard,
  PortfolioDashboardResponse,
  ProjectCard,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useAuth } from '../contexts/AuthContext';
import { formatStatusLabel } from '../utils/statusMeta';
import { formatRelativeTime } from '../utils/relativeTime';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import { Input } from '../components/ui/input';
import PostureLead from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import PostureEmpty from '../components/posture/PostureEmpty';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';
import { formatApiError } from '../utils/apiErrors';

/**
 * Portfolio (v5.275.0) — the members' cross-project view, on the Posture
 * layout (UI_STYLE_GUIDE §7) like Oversight: a filter row, a lead sentence,
 * four measures, and the projects as a table, worst first.
 *
 * It was a card of eight bordered number tiles over a grid of ~450 px project
 * tiles.  Most tiles repeated each other ("6 need attention" = every project;
 * "3 with critical" = the health bar above), the health reason sat on hover
 * only, and each tile stated review three ways that did not add up ("64 of
 * 416 tested · 387 unreviewed" beside "7%").  Review is now one vocabulary:
 * reviewed · in review · not started, of all hosts.
 */

// Worst-first rank for the default ordering.
const HEALTH_RANK: Record<string, number> = { critical: 0, warning: 1, stale: 2, healthy: 3, unknown: 4 };

// v5.275.0 — the label follows what testing FOUND (v2.389.0 dropped review
// coverage from it): critical, high, or neither.  `stale` is the API's code
// for QUIET — still marked active, nothing imported for a fortnight: a
// question for the manager (finished? mark it completed), never a verdict.
const HEALTH_LABEL: Record<string, string> = {
  critical: 'Critical', warning: 'High', stale: 'Quiet', healthy: 'No critical or high', unknown: 'Unavailable',
};
const HEALTH_TEXT: Record<string, string> = {
  critical: 'text-destructive', warning: 'text-warning', stale: 'text-muted-foreground',
  healthy: 'text-muted-foreground', unknown: 'text-muted-foreground',
};

const n = (v: number) => v.toLocaleString();
const plural = (v: number, one: string, many = `${one}s`) => `${n(v)} ${v === 1 ? one : many}`;
const SEVS = ['critical', 'high', 'medium', 'low'] as const;

/** Which representation drove a critical/high label, in words. */
const severityWhy = (card: ProjectCard, sev: 'critical' | 'high'): string => {
  const f = card.findings[sev];
  const u = card.unjudged_observations[sev];
  const parts: string[] = [];
  if (f > 0) parts.push(`${plural(f, `${sev} finding`)}`);
  if (u > 0) parts.push(`${plural(u, `${sev} scanner observation`)} not yet judged`);
  return parts.join(' · ');
};

/** The reason under the health label, or null when the label says it all. */
const healthWhy = (card: ProjectCard): string | null => {
  switch (card.health) {
    case 'critical':
      return severityWhy(card, 'critical');
    case 'warning':
      return severityWhy(card, 'high');
    case 'stale':
      return card.days_since_last_scan != null
        ? `Still marked active; no import for ${card.days_since_last_scan} days. Finished? Mark it completed.`
        : 'Still marked active; nothing imported yet.';
    case 'healthy':
      // "No critical or high" already says it; only the empty project needs more.
      return card.host_count === 0 ? 'No hosts imported yet.' : null;
    default:
      // Null / malformed health must not read as "no risk".
      return 'Health data unavailable.';
  }
};

const hasSeverity = (card: ProjectCard, sev: 'critical' | 'high'): boolean =>
  card.findings[sev] > 0 || card.unjudged_observations[sev] > 0;

// The "Show" filter.  Each option's count and its rows use the SAME predicate.
const SHOW_OPTIONS: { key: string; label: string; pred: (p: ProjectCard) => boolean }[] = [
  { key: 'critical', label: 'With critical', pred: (p) => hasSeverity(p, 'critical') },
  { key: 'high', label: 'With critical or high', pred: (p) => hasSeverity(p, 'critical') || hasSeverity(p, 'high') },
  { key: 'pending', label: 'Plans awaiting approval', pred: (p) => p.pending_plan_reviews > 0 },
  { key: 'blocked', label: 'Blocked runs', pred: (p) => p.blocked_sessions > 0 },
  { key: 'stale', label: 'Active, no import in 14 days', pred: (p) => p.is_stale },
  { key: 'no_data', label: 'No hosts yet', pred: (p) => p.host_count === 0 },
];

const notStarted = (p: ProjectCard) => Math.max(0, p.host_count - p.hosts_reviewed - p.hosts_in_review);

const findingsLine = (p: ProjectCard): string => {
  const total = SEVS.reduce((acc, s) => acc + p.findings[s], 0);
  if (total === 0) return 'No findings';
  return `${plural(total, 'finding')}: ${SEVS.filter((s) => p.findings[s] > 0).map((s) => `${n(p.findings[s])} ${s}`).join(' · ')}`;
};

const unjudgedLine = (p: ProjectCard): string | null => {
  const parts = SEVS.filter((s) => p.unjudged_observations[s] > 0).map((s) => `${n(p.unjudged_observations[s])} ${s}`);
  // Label first, so no noun has to agree with a list of numbers.
  return parts.length ? `Scanner observations not yet judged: ${parts.join(' · ')}` : null;
};

const ProjectsTable: React.FC<{
  rows: ProjectCard[];
  onOpen: (p: ProjectCard) => void;
  onMembers: (p: ProjectCard) => void;
}> = ({ rows, onOpen, onMembers }) => (
  <div className="overflow-x-auto border-t border-border">
    <Table aria-label="Your projects, worst first" className="min-w-[1040px]" style={{ tableLayout: 'fixed' }}>
      {/* v5.288.0 — the short-content columns are sized to their content
          (Review's "1,234 in review · 12,345 not started", the Waiting header
          on one line, a host count) and "What testing found" takes the rest.
          As percentages Hosts was wider than it needed and Review / Waiting
          wrapped at ~1500px. */}
      <colgroup>
        <col style={{ width: '22%' }} data-col="project" />
        <col data-col="found" />
        <col style={{ width: '16rem' }} data-col="review" />
        <col style={{ width: '6rem' }} data-col="hosts" />
        <col style={{ width: '15rem' }} data-col="waiting" />
      </colgroup>
      <TableHeader>
        <TableRow>
          <TableHead>Project</TableHead>
          <TableHead>
            <span className="inline-flex items-center gap-xxs">
              What testing found
              <InfoTip text="Critical or High when a finding at that severity exists, or scanner output at that severity nobody has judged yet (the reason is spelled out). Findings are issues — one finding on many hosts counts once; false positives are left out. Scanner observations are issue × host rows no finding covers on their host." />
            </span>
          </TableHead>
          <TableHead>
            <span className="inline-flex items-center gap-xxs">
              Review
              <InfoTip text="Hosts reviewed (review concluded), in review, and not started, out of every host in the project. Each host counts once." />
            </span>
          </TableHead>
          <TableHead>Hosts</TableHead>
          <TableHead className="whitespace-nowrap">Waiting · last import</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((p) => {
          const unjudged = unjudgedLine(p);
          const why = healthWhy(p);
          return (
            <TableRow key={p.id} className="align-top" data-project-id={p.id}>
              <TableCell>
                <button type="button" onClick={() => onOpen(p)} title={`Open ${p.name}`}
                  className="block max-w-full truncate text-left font-medium text-foreground hover:text-info focus:outline-none focus-visible:underline">
                  {p.name}
                </button>
                <span className="mt-xxs flex flex-wrap items-center gap-x-xs text-caption text-muted-foreground">
                  <span>{formatStatusLabel(p.status)}</span>
                  <button type="button" onClick={() => onMembers(p)}
                    className="inline-flex items-center gap-xxs rounded hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={`Members of ${p.name}`}>
                    <Users className="size-3" aria-hidden /> {plural(p.member_count, 'member')}
                  </button>
                </span>
              </TableCell>
              <TableCell className="min-w-0">
                <p className={cn('break-words text-metadata', HEALTH_TEXT[p.health] ?? HEALTH_TEXT.unknown)}>
                  <span className="font-semibold">{HEALTH_LABEL[p.health] ?? HEALTH_LABEL.unknown}</span>
                  {(p.health === 'critical' || p.health === 'warning') && why && <> — {why}</>}
                </p>
                {p.health !== 'critical' && p.health !== 'warning' && why && (
                  <p className="break-words text-caption text-muted-foreground">{why}</p>
                )}
                <p className="break-words text-caption text-muted-foreground">{findingsLine(p)}</p>
                {unjudged && <p className="break-words text-caption text-muted-foreground">{unjudged}</p>}
              </TableCell>
              <TableCell className="text-caption tabular-nums">
                {p.host_count === 0 ? (
                  <span className="text-muted-foreground">No hosts</span>
                ) : (
                  <>
                    <p className="text-metadata text-foreground">{n(p.hosts_reviewed)} of {n(p.host_count)} reviewed</p>
                    <p className="whitespace-nowrap text-muted-foreground">{n(p.hosts_in_review)} in review · {n(notStarted(p))} not started</p>
                  </>
                )}
              </TableCell>
              <TableCell className="text-caption tabular-nums">
                <p className="text-metadata text-foreground">{n(p.host_count)}</p>
                <p className="text-muted-foreground">{n(p.up_host_count)} up</p>
              </TableCell>
              <TableCell className="text-caption">
                {/* One chip style for every waiting item — outlined, left-aligned —
                    so a neutral count never reads as loose, indented text. */}
                <span className="flex flex-wrap justify-start gap-xxs" data-testid="waiting-chips">
                  {p.pending_plan_reviews > 0 && <Badge variant="warning-outline">{plural(p.pending_plan_reviews, 'plan')} to approve</Badge>}
                  {p.blocked_sessions > 0 && <Badge variant="destructive-outline">{plural(p.blocked_sessions, 'blocked run')}</Badge>}
                  {p.active_sessions > 0 && <Badge variant="info-outline">{plural(p.active_sessions, 'active run')}</Badge>}
                  {p.open_tasks > 0 && <Badge variant="outline">{plural(p.open_tasks, 'open task')}</Badge>}
                </span>
                {/* Provenance, not a judgment: an import date is never coloured. */}
                <p className="mt-xxs text-muted-foreground">
                  {p.last_scan_at ? `Last import ${formatRelativeTime(p.last_scan_at, { absoluteAfterDays: 30 })}` : 'No imports'}
                </p>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  </div>
);

const PortfolioDashboard: React.FC = () => {
  const navigate = useNavigate();
  const { projects, selectProject } = useProject();
  const { hasRole } = useAuth();

  const [data, setData] = useState<PortfolioDashboardResponse | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [search, setSearch] = useState('');
  // SOC-P1/P2 — project whose members sheet is open.
  const [membersCard, setMembersCard] = useState<ProjectCard | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // URL-synced so a triage view is shareable: ?view=team, ?status=, ?show=.
  const view = searchParams.get('view') === 'team' ? 'team' : 'projects';
  const statusFilter = searchParams.get('status') ?? '';
  const show = searchParams.get('show') ?? '';
  const setParam = (key: string, value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set(key, value);
    else params.delete(key);
    setSearchParams(params, { replace: true });
  };

  const reload = () => setReloadNonce((x) => x + 1);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getPortfolioDashboard()
      .then((d) => { setData(d); setFetchedAt(new Date().toISOString()); })
      .catch((err) => setError(formatApiError(err, 'Failed to load portfolio.')))
      .finally(() => setLoading(false));
  }, [reloadNonce]);

  // Row actions switch the active project BEFORE navigating so the
  // destination opens scoped to it.
  const openProject = (card: ProjectCard) => {
    const proj = projects.find((p) => p.id === card.id);
    if (proj) selectProject(proj);
    navigate('/operations');
  };

  const showPred = SHOW_OPTIONS.find((o) => o.key === show)?.pred;
  const rows = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    let list = data.projects;
    if (statusFilter) list = list.filter((p) => p.status === statusFilter);
    if (showPred) list = list.filter(showPred);
    if (q) list = list.filter((p) => p.name.toLowerCase().includes(q));
    // Worst first: health, then critical signal, then high, then name.
    const crit = (p: ProjectCard) => p.findings.critical + p.unjudged_observations.critical;
    const high = (p: ProjectCard) => p.findings.high + p.unjudged_observations.high;
    return [...list].sort((a, b) =>
      (HEALTH_RANK[a.health] ?? 9) - (HEALTH_RANK[b.health] ?? 9)
      || crit(b) - crit(a) || high(b) - high(a) || a.name.localeCompare(b.name));
  }, [data, statusFilter, showPred, search]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of data?.projects ?? []) counts[p.status] = (counts[p.status] || 0) + 1;
    return counts;
  }, [data]);

  const viewTabs = (
    <div className="inline-flex overflow-hidden rounded-control border border-border" role="group" aria-label="Portfolio view">
      {(['projects', 'team'] as const).map((v) => (
        <button
          key={v}
          type="button"
          aria-pressed={view === v}
          onClick={() => setParam('view', v === 'team' ? 'team' : '')}
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

  // Team view is self-contained (PortfolioTeam fetches its own data).
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

  const s = data?.summary;
  const all = data?.projects ?? [];
  const withCritical = all.filter((p) => hasSeverity(p, 'critical')).length;
  const withHighOnly = all.filter((p) => !hasSeverity(p, 'critical') && hasSeverity(p, 'high')).length;
  const leadTone = withCritical > 0 ? 'critical' : withHighOnly > 0 ? 'warning' : all.length ? 'clear' : 'neutral';
  const notStartedTotal = s ? Math.max(0, s.total_hosts - s.total_reviewed - s.total_in_review) : 0;
  // Portfolio lists non-archived projects only, so every one not in progress is completed.
  const completedProjects = s ? Math.max(0, s.total_projects - s.active_projects) : 0;
  const filtered = !!(statusFilter || show || search.trim());

  return (
    <div className="space-y-md p-md md:p-lg">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title font-semibold">Portfolio</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Your projects: what testing has found, how far review has got, and what is waiting on someone.
          </p>
        </div>
        <div className="flex flex-col items-end gap-xs">
          <div className="flex items-center gap-sm">
            {viewTabs}
            <Button size="sm" variant="outline" onClick={reload} disabled={loading}>
              <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden /> Refresh
            </Button>
          </div>
          {fetchedAt && (
            <span className="text-caption text-muted-foreground">
              Updated {formatRelativeTime(fetchedAt, { justNowBelowMs: 60_000 })}
            </span>
          )}
        </div>
      </div>

      {/* Filters: one row above everything they scope, closed by a rule. */}
      <div className="flex flex-wrap items-end gap-sm border-b border-border pb-sm">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" aria-label="Search projects" placeholder="Search projects…" value={search}
            onChange={(e) => setSearch(e.target.value)} className="pl-xl" />
        </div>
        <Select value={show || 'all'} onValueChange={(v) => setParam('show', v === 'all' ? '' : v)}>
          <SelectTrigger className="w-56" aria-label="Show projects"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All projects ({all.length})</SelectItem>
            {SHOW_OPTIONS.map((o) => (
              <SelectItem key={o.key} value={o.key}>{o.label} ({all.filter(o.pred).length})</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={statusFilter || 'all'} onValueChange={(v) => setParam('status', v === 'all' ? '' : v)}>
          <SelectTrigger className="w-44" aria-label="Filter projects by status"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any status</SelectItem>
            {Object.entries(statusCounts).map(([status, count]) => (
              <SelectItem key={status} value={status}>{formatStatusLabel(status)} ({count})</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {filtered && (
          <Button size="sm" variant="ghost" onClick={() => { setSearch(''); setSearchParams(new URLSearchParams(), { replace: true }); }}>
            Reset
          </Button>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>{error}{data ? ' Showing the last figures that loaded.' : ''}</span>
            <Button size="sm" variant="outline" onClick={reload}><RefreshCw className="size-4" aria-hidden /> Retry</Button>
          </AlertDescription>
        </Alert>
      )}

      {!data && loading && <p role="status" className="text-metadata text-muted-foreground">Loading your projects…</p>}

      {data && s && (
        <>
          {all.length === 0 ? (
            <PostureEmpty Icon={FolderOpen} title="No projects yet"
              action={hasRole('admin') ? { to: '/system-settings', label: 'Create a project' } : undefined}>
              You are not a member of any project yet. An administrator adds you to one.
            </PostureEmpty>
          ) : (
            <>
              <PostureLead tone={leadTone}
                restsOn={`${n(s.total_reviewed)} of ${n(s.total_hosts)} hosts with review concluded and ${n(s.total_in_review)} in review, across ${plural(all.length, 'project')}.`}>
                {/* A finding is already a judgement; only the scanner output
                    half is "not yet judged". */}
                {withCritical > 0
                  ? `${n(withCritical)} of ${plural(all.length, 'project')} ${withCritical === 1 ? 'has' : 'have'} a critical finding or critical scanner output not yet judged.`
                  : withHighOnly > 0
                    ? `No critical signal; ${n(withHighOnly)} of ${plural(all.length, 'project')} ${withHighOnly === 1 ? 'has' : 'have'} high findings or high scanner output not yet judged.`
                    : `Nothing critical or high has been found, or is waiting to be judged, in your ${plural(all.length, 'project')}.`}
              </PostureLead>

              <div className="grid gap-md border-b border-border pb-md sm:grid-cols-2 lg:grid-cols-4 lg:divide-x lg:divide-border">
                <PostureMeasure label="Projects in progress" value={n(s.active_projects)}
                  info="Projects whose status is active; the rest listed here are completed. Archived projects are not on Portfolio. &quot;No import in 14 days&quot; is about the project's activity, never the evidence: still marked active with nothing imported for 14 days — is it finished? Mark it completed.">
                  {/* What is NOT in progress, and the one question the count raises. */}
                  {completedProjects === 0 ? 'None completed' : `${n(completedProjects)} completed`}
                  {s.stale_projects > 0 && (
                    <> · <button type="button" className="text-info hover:underline" onClick={() => setParam('show', 'stale')}>{n(s.stale_projects)} active, no import in 14 days</button></>
                  )}
                </PostureMeasure>
                <PostureMeasure label="Hosts with review concluded" value={<>{n(s.total_reviewed)} <span className="text-metadata font-normal text-muted-foreground">of {n(s.total_hosts)}</span></>}
                  info="Hosts whose review someone has concluded (marked reviewed), out of every host in your projects. In review = someone has started; not started = nobody has. Not the same as Oversight's &quot;Targets tested&quot;, which counts hosts in review AND reviewed.">
                  {n(s.total_in_review)} in review · {n(notStartedTotal)} not started
                </PostureMeasure>
                <PostureMeasure label="Critical and high findings"
                  value={n(s.findings.critical + s.findings.high)}
                  info="Findings are judged issues: one finding on many hosts counts once; false positives are left out. Beside them, the critical and high scanner observations (issue × host) no finding covers yet — different units, never added together.">
                  {n(s.findings.critical)} critical · {n(s.findings.high)} high
                  <br />
                  {plural(s.unjudged_observations.critical + s.unjudged_observations.high, 'critical/high scanner observation')} not yet judged
                </PostureMeasure>
                <PostureMeasure label="Waiting on someone" value={n(s.pending_approvals_total + s.blocked_sessions_total)}
                  info="Agent test plans awaiting a human approval, and execution runs that are paused or failed.">
                  <button type="button" className="text-info hover:underline disabled:text-muted-foreground disabled:no-underline"
                    disabled={s.pending_approvals_total === 0} onClick={() => setParam('show', 'pending')}>
                    {plural(s.pending_approvals_total, 'plan')} to approve
                  </button>
                  {' · '}
                  <button type="button" className="text-info hover:underline disabled:text-muted-foreground disabled:no-underline"
                    disabled={s.blocked_sessions_total === 0} onClick={() => setParam('show', 'blocked')}>
                    {plural(s.blocked_sessions_total, 'blocked run')}
                  </button>
                </PostureMeasure>
              </div>

              <PostureSection
                title={<>Projects <SectionCount>{filtered ? `${n(rows.length)} of ${n(all.length)}` : n(all.length)}</SectionCount></>}
                description="Worst first: critical, then high, then by name. Open a project from its name.">
                {rows.length === 0 ? (
                  <p className="py-sm text-metadata text-muted-foreground">
                    No project matches these filters.{' '}
                    <button type="button" className="text-info hover:underline"
                      onClick={() => { setSearch(''); setSearchParams(new URLSearchParams(), { replace: true }); }}>
                      Show all projects
                    </button>
                  </p>
                ) : (
                  <ProjectsTable rows={rows} onOpen={openProject} onMembers={setMembersCard} />
                )}
              </PostureSection>
            </>
          )}
        </>
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
