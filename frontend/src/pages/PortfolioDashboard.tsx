import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, FolderOpen, RefreshCw, SquareArrowOutUpRight } from 'lucide-react';
import {
  getPortfolioDashboard,
  PortfolioDashboardResponse,
  ProjectCard,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useAuth } from '../contexts/AuthContext';
import { CardListSkeleton } from '../components/PageSkeleton';
import { NavigableTableRow } from '../components/NavigableTableRow';
import { formatStatusLabel } from '../utils/statusMeta';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';
import { formatApiError } from '../utils/apiErrors';
import { stickyBelowChrome } from '../utils/uiStyles';

type SortField = 'name' | 'hosts' | 'open_ports' | 'scans' | 'last_scan' | 'review';
type SortDir = 'asc' | 'desc';

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'muted' | 'secondary' | 'outline';

const projectStatusTone = (status: string | null | undefined): Tone => {
  switch (status) {
    case 'active':
      return 'success';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'info';
    case 'archived':
      return 'muted';
    default:
      return 'muted';
  }
};

// Health is a backend-derived rollup (critical findings > exposure/low-review
// > staleness > healthy). Surfacing it gives the "is this project OK at a
// glance?" answer the per-column numbers don't.
const HEALTH_META: Record<string, { tone: Tone; label: string }> = {
  critical: { tone: 'destructive', label: 'Critical' },
  warning: { tone: 'warning', label: 'Warning' },
  stale: { tone: 'muted', label: 'Stale' },
  healthy: { tone: 'success', label: 'Healthy' },
};

const healthMeta = (health: string | null | undefined): { tone: Tone; label: string } =>
  HEALTH_META[health ?? 'healthy'] ?? HEALTH_META.healthy;

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
    default:
      return 'No outstanding risk signals';
  }
};

// P4 — attention reason codes → display.
const ATTENTION_META: Record<string, { label: string; tone: Tone }> = {
  blocked_session: { label: 'Blocked run', tone: 'destructive' },
  pending_review: { label: 'Pending review', tone: 'warning' },
  no_data: { label: 'No data', tone: 'muted' },
  // The remaining reasons (critical_findings / high_findings / stale /
  // unreviewed) drive the "Needs attention" filter but are NOT rendered as
  // chips — each already has a dedicated column (Findings / Last Scan /
  // Review) or the Health badge, so a chip would just duplicate it.
};
// Only these workflow signals — unrepresented by any other column — render
// as chips, plus pending_review which renders as the Review action below.
const ATTENTION_CHIP_CODES = ['blocked_session', 'no_data'];

const PortfolioDashboard: React.FC = () => {
  const navigate = useNavigate();
  const { projects, selectProject } = useProject();
  const { hasRole } = useAuth();

  const [data, setData] = useState<PortfolioDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [sortBy, setSortBy] = useState<SortField>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [reloadNonce, setReloadNonce] = useState(0);
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

  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      setSortDir(field === 'name' ? 'asc' : 'desc');
    }
  };

  const filteredProjects = useMemo(() => {
    if (!data) return [];
    let list = data.projects;
    if (statusFilter) list = list.filter((p) => p.status === statusFilter);
    if (attentionOnly) list = list.filter((p) => p.attention_reasons.length > 0);
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...list].sort((a, b) => {
      switch (sortBy) {
        case 'hosts':
          return (a.host_count - b.host_count) * dir;
        case 'open_ports':
          return (a.open_port_count - b.open_port_count) * dir;
        case 'scans':
          return (a.scan_count - b.scan_count) * dir;
        case 'last_scan':
          return (a.last_scan_at || '').localeCompare(b.last_scan_at || '') * dir;
        case 'review':
          return (a.review_progress_pct - b.review_progress_pct) * dir;
        default:
          return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [data, statusFilter, attentionOnly, sortBy, sortDir]);

  const statusCounts = useMemo(() => {
    if (!data) return {};
    const counts: Record<string, number> = {};
    for (const p of data.projects) counts[p.status] = (counts[p.status] || 0) + 1;
    return counts;
  }, [data]);

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

  const SortableHeader = ({
    field,
    label,
    className,
  }: {
    field: SortField;
    label: string;
    className?: string;
  }) => (
    <TableHead
      className={className}
      aria-sort={sortBy === field ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        onClick={() => handleSort(field)}
        className="inline-flex items-center gap-xxs rounded-control text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {label}
        {sortBy === field ? (
          sortDir === 'asc' ? (
            <ArrowUp className="size-3" aria-hidden />
          ) : (
            <ArrowDown className="size-3" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="size-3 opacity-50" aria-hidden />
        )}
      </button>
    </TableHead>
  );

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-col gap-xs lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-page-title font-semibold">Projects</h1>
          <div className="mt-xxs flex items-center gap-xs">
            <span className="relative inline-flex">
              <FolderOpen className="size-5 text-muted-foreground" aria-hidden />
              <Badge
                variant="default"
                className="absolute -right-3 -top-2 h-4 min-w-4 justify-center px-xxs text-micro"
              >
                {summary.total_projects}
              </Badge>
            </span>
            <p className="text-metadata text-muted-foreground">
              Cross-project overview with aggregate statistics.
            </p>
          </div>
        </div>
      </div>

      <Card className="sticky z-10 mb-md" style={stickyBelowChrome}>
        <CardContent className="flex flex-col gap-sm p-md lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="font-semibold">
              {filteredProjects.length} project{filteredProjects.length === 1 ? '' : 's'}
              {statusFilter ? ` with status "${statusFilter.replace('_', ' ')}"` : ''}
            </p>
            <div className="mt-xxs flex flex-wrap gap-xs">
              <Badge variant="outline">{summary.total_hosts.toLocaleString()} hosts</Badge>
              <Badge variant="outline">{summary.total_open_ports.toLocaleString()} open ports</Badge>
              <Badge variant="outline">{summary.total_scans} scans</Badge>
              {summary.total_unreviewed > 0 && (
                <Badge variant="outline" className="border-warning/40 text-warning">
                  {summary.total_unreviewed} unreviewed
                </Badge>
              )}
              {summary.projects_with_critical > 0 && (
                <Badge variant="destructive">{summary.projects_with_critical} with critical</Badge>
              )}
              {summary.pending_approvals_total > 0 && (
                <Badge variant="warning">{summary.pending_approvals_total} pending review</Badge>
              )}
              {summary.blocked_sessions_total > 0 && (
                <Badge variant="destructive">{summary.blocked_sessions_total} blocked run{summary.blocked_sessions_total === 1 ? '' : 's'}</Badge>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-sm">
            {/* P4 — "needs attention" triage filter (URL-synced). */}
            <Button
              size="sm"
              variant={attentionOnly ? 'default' : 'outline'}
              aria-pressed={attentionOnly}
              onClick={() => setAttentionOnly(!attentionOnly)}
            >
              <AlertTriangle className="size-4" aria-hidden />
              Needs attention
              {summary.projects_requiring_attention > 0 && (
                <span className="ml-xxs rounded-full bg-background/30 px-xxs text-micro">
                  {summary.projects_requiring_attention}
                </span>
              )}
            </Button>
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
        </CardContent>
      </Card>

      {filteredProjects.length === 0 ? (
        <Card>
          <CardContent className="p-xl text-center">
            <FolderOpen className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">
              {attentionOnly
                ? 'No projects currently need attention. 🎉'
                : statusFilter
                ? 'No projects match the selected filter.'
                : 'No projects available.'}
            </p>
            <div className="mt-sm flex justify-center gap-xs">
              {attentionOnly ? (
                <Button size="sm" variant="outline" onClick={() => setAttentionOnly(false)}>
                  Show all projects
                </Button>
              ) : statusFilter ? (
                <Button size="sm" variant="outline" onClick={() => setStatusFilter('')}>
                  Show all projects
                </Button>
              ) : (
                summary.total_projects === 0 &&
                /* v4.8.0 — project creation is admin-only (backend
                   require_role ADMIN); the old `|| hasRole('analyst')`
                   showed the button to users who'd just get a 403, and
                   'analyst' is no longer a global role anyway. */
                hasRole('admin') && (
                  <Button size="sm" onClick={() => navigate('/system-settings')}>
                    Create your first project
                  </Button>
                )
              )}
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHeader field="name" label="Project" className="w-[20%]" />
                    <TableHead className="w-[9%]">Status</TableHead>
                    <TableHead className="w-[9%]">Health</TableHead>
                    <SortableHeader field="hosts" label="Hosts" className="w-[9%]" />
                    <SortableHeader field="open_ports" label="Open Ports" className="w-[9%]" />
                    <SortableHeader field="scans" label="Scans" className="w-[7%]" />
                    <SortableHeader field="last_scan" label="Last Scan" className="w-[11%]" />
                    <SortableHeader field="review" label="Review" className="w-[12%]" />
                    <TableHead className="w-[14%]">Findings</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredProjects.map((card) => {
                    const vulnTotal =
                      card.vuln_summary.critical +
                      card.vuln_summary.high +
                      card.vuln_summary.medium +
                      card.vuln_summary.low;
                    // v2.43.0 — UX review #2: dropped role="link" and
                    // whole-row click.  The project-switch + navigate
                    // action is fired from an explicit <button> in the
                    // primary cell so AT users hear "Open project NAME"
                    // instead of inheriting the row's grid semantics.
                    return (
                      <NavigableTableRow key={card.id}>
                        <TableCell className="min-w-0 p-0">
                          <button
                            type="button"
                            onClick={() => handleProjectClick(card)}
                            className="block w-full px-md py-xs text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            aria-label={`Open project ${card.name}`}
                          >
                            <p className="truncate font-semibold">{card.name}</p>
                            {card.description && (
                              <p className="line-clamp-2 text-caption text-muted-foreground">
                                {card.description}
                              </p>
                            )}
                          </button>
                        </TableCell>
                        <TableCell>
                          <Badge variant={projectStatusTone(card.status)}>
                            {formatStatusLabel(card.status)}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-xxs">
                            {/* Health = single worst-signal rollup; the
                                title explains what drove it (the per-signal
                                detail lives in the Findings / Last Scan /
                                Review columns, so chips here only show
                                workflow exceptions not shown elsewhere). */}
                            <span title={`${healthMeta(card.health).label} — ${healthWhy(card)}`}>
                              <Badge variant={healthMeta(card.health).tone}>
                                {healthMeta(card.health).label}
                              </Badge>
                            </span>
                            {ATTENTION_CHIP_CODES.some((c) => card.attention_reasons.includes(c)) && (
                              <div className="flex flex-wrap gap-xxs">
                                {ATTENTION_CHIP_CODES.filter((c) => card.attention_reasons.includes(c)).map((c) => (
                                  <Badge key={c} variant={ATTENTION_META[c].tone} className="text-micro">
                                    {ATTENTION_META[c].label}
                                  </Badge>
                                ))}
                              </div>
                            )}
                            {card.pending_plan_reviews > 0 && (
                              <button
                                type="button"
                                onClick={() => switchAndGo(card, '/test-plans')}
                                className="inline-flex items-center gap-xxs rounded text-micro text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                Review {card.pending_plan_reviews}
                                <SquareArrowOutUpRight className="size-3" aria-hidden />
                              </button>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="truncate">
                            {card.host_count}
                            {card.up_host_count !== card.host_count && (
                              <span className="text-caption text-muted-foreground">
                                {' '}
                                ({card.up_host_count} up)
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="truncate">{card.open_port_count.toLocaleString()}</div>
                        </TableCell>
                        <TableCell>
                          <div className="truncate">{card.scan_count}</div>
                        </TableCell>
                        <TableCell>
                          <div className="truncate">
                            {card.days_since_last_scan != null
                              ? `${card.days_since_last_scan}d ago`
                              : 'Never'}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-xxs">
                            <div
                              className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={Math.round(card.review_progress_pct)}
                              aria-valuetext={`${Math.round(card.review_progress_pct)} percent of hosts reviewed${
                                card.unreviewed_hosts > 0
                                  ? `, ${card.unreviewed_hosts.toLocaleString()} unreviewed`
                                  : ''
                              }`}
                            >
                              <div
                                className={cn(
                                  'h-full transition-all',
                                  card.review_progress_pct >= 80
                                    ? 'bg-success'
                                    : card.review_progress_pct >= 30
                                    ? 'bg-warning'
                                    : 'bg-primary',
                                )}
                                style={{
                                  width: `${Math.min(card.review_progress_pct, 100)}%`,
                                }}
                              />
                            </div>
                            <p className="text-caption text-muted-foreground">
                              {card.review_progress_pct.toFixed(0)}%
                              {card.unreviewed_hosts > 0 &&
                                ` (${card.unreviewed_hosts} pending)`}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell>
                          {vulnTotal > 0 ? (
                            <div className="flex flex-wrap gap-xxs">
                              {card.vuln_summary.critical > 0 && (
                                <Badge
                                  variant="severity-critical"
                                  aria-label={`${card.vuln_summary.critical} critical`}
                                >
                                  <span aria-hidden>C:{card.vuln_summary.critical}</span>
                                </Badge>
                              )}
                              {card.vuln_summary.high > 0 && (
                                <Badge
                                  variant="severity-high"
                                  aria-label={`${card.vuln_summary.high} high`}
                                >
                                  <span aria-hidden>H:{card.vuln_summary.high}</span>
                                </Badge>
                              )}
                              {card.vuln_summary.medium > 0 && (
                                <Badge
                                  variant="severity-medium"
                                  aria-label={`${card.vuln_summary.medium} medium`}
                                >
                                  <span aria-hidden>M:{card.vuln_summary.medium}</span>
                                </Badge>
                              )}
                              {card.vuln_summary.low > 0 && (
                                <Badge
                                  variant="severity-low"
                                  aria-label={`${card.vuln_summary.low} low`}
                                >
                                  <span aria-hidden>L:{card.vuln_summary.low}</span>
                                </Badge>
                              )}
                            </div>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                      </NavigableTableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default PortfolioDashboard;
