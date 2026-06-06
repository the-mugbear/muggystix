import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, FolderOpen, RefreshCw } from 'lucide-react';
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

  const reload = () => setReloadNonce((n) => n + 1);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getPortfolioDashboard()
      .then(setData)
      .catch((err) => setError(formatApiError(err, 'Failed to load portfolio.')))
      .finally(() => setLoading(false));
  }, [reloadNonce]);

  const handleProjectClick = (card: ProjectCard) => {
    const proj = projects.find((p) => p.id === card.id);
    if (proj) selectProject(proj);
    // /dashboard was a permanent redirect to /operations — clicking a
    // project card was producing a visible route flicker and landing
    // somewhere unintended.  Navigate directly to the operations hub
    // (audit C5).
    navigate('/operations');
  };

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
  }, [data, statusFilter, sortBy, sortDir]);

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
            </div>
          </div>

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
        </CardContent>
      </Card>

      {filteredProjects.length === 0 ? (
        <Card>
          <CardContent className="p-xl text-center">
            <FolderOpen className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">
              {statusFilter ? 'No projects match the selected filter.' : 'No projects available.'}
            </p>
            <div className="mt-sm flex justify-center gap-xs">
              {statusFilter ? (
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
                    <SortableHeader field="name" label="Project" className="w-[22%]" />
                    <TableHead className="w-[10%]">Status</TableHead>
                    <SortableHeader field="hosts" label="Hosts" className="w-[10%]" />
                    <SortableHeader field="open_ports" label="Open Ports" className="w-[10%]" />
                    <SortableHeader field="scans" label="Scans" className="w-[8%]" />
                    <SortableHeader field="last_scan" label="Last Scan" className="w-[12%]" />
                    <SortableHeader field="review" label="Review" className="w-[14%]" />
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
