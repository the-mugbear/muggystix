import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeftRight, RefreshCw, Rocket, Search, SquareArrowOutUpRight } from 'lucide-react';
import { ReconSessionRow, ScopeSummary, getScopes, listReconSessions } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useCompareSelection } from '../hooks/useCompareSelection';
import { useReconPlan } from '../hooks/useReconPlan';
import { NavigableTableCell, NavigableTableRow } from '../components/NavigableTableRow';
import { useToast } from '../contexts/ToastContext';
import { Alert, AlertDescription } from '../components/ui/alert';
import { TableSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import StartReconDialog from '../components/StartReconDialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';

type StatusFilter = '' | 'active' | 'completed' | 'failed' | 'abandoned';

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: '', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'abandoned', label: 'Abandoned' },
];

type BadgeTone = 'success' | 'info' | 'destructive' | 'warning' | 'muted';

const statusTone = (s: string): BadgeTone => {
  if (s === 'active') return 'success';
  if (s === 'completed') return 'info';
  if (s === 'failed') return 'destructive';
  if (s === 'abandoned' || s === 'paused') return 'warning';
  return 'muted';
};

const fmtTime = (iso?: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const ReconRunsList: React.FC = () => {
  const navigate = useNavigate();
  const toast = useToast();
  const recon = useReconPlan();
  const [rows, setRows] = useState<ReconSessionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  // v2.44.1 (UX review #7): selection now uses the shared
  // useCompareSelection hook so ReconRunsList and ExecutionsList
  // behave identically (both block the 3rd pick with a toast hint
  // instead of silently ejecting the oldest selected row).
  const compareSelection = useCompareSelection<number>({ kind: 'recon runs' });
  const selected = compareSelection.selected;
  // FRX·H4: client-side search over agent username + model name.
  const [searchText, setSearchText] = useState('');
  const debouncedSearchText = useDebouncedValue(searchText, 300);

  // Scope-picker state for the new "Start Recon" affordance.  Scopes
  // are fetched on demand (first click) rather than on every page
  // mount — keeps the list-page load light for the common case where
  // the user is just browsing existing runs.
  const [scopePickerOpen, setScopePickerOpen] = useState(false);
  const [availableScopes, setAvailableScopes] = useState<ScopeSummary[] | null>(null);
  const [scopesLoading, setScopesLoading] = useState(false);

  const handleStartRecon = async () => {
    // Fetch the project's scopes if we haven't already.  If exactly
    // one scope exists, skip the picker and open the recon dialog
    // directly — saves the operator a click in the common single-scope
    // case while still supporting multi-scope projects.
    let scopes = availableScopes;
    if (!scopes) {
      setScopesLoading(true);
      try {
        scopes = await getScopes();
        setAvailableScopes(scopes);
      } catch (err) {
        toast.error(formatApiError(err, 'Could not load scopes.'));
        setScopesLoading(false);
        return;
      } finally {
        setScopesLoading(false);
      }
    }
    if (!scopes || scopes.length === 0) {
      toast.warning('No scopes in this project yet — add subnets in Inventory → Scopes first.');
      return;
    }
    if (scopes.length === 1) {
      recon.openFor(scopes[0].id, scopes[0].name);
    } else {
      setScopePickerOpen(true);
    }
  };

  const handlePickScope = (scope: ScopeSummary) => {
    setScopePickerOpen(false);
    recon.openFor(scope.id, scope.name);
  };

  // Reload the runs list once a freshly-minted session is acknowledged
  // and closed, so the new row appears in the table without a manual
  // refresh.
  useEffect(() => {
    if (recon.scopeId === null && !loading) {
      // Hook returned to its idle state — refresh the list.  Cheap and
      // makes the "Start → see your run" loop instant.
      reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recon.scopeId]);

  // v2.86.10 — total comes from the X-Total-Count response header now;
  // the page surfaces "Showing N of T" so the user knows when there are
  // matches beyond the loaded slice.
  const [totalRows, setTotalRows] = useState(0);

  // Monotonic request id so a slow earlier search can't overwrite the
  // results of a newer one (filter/search fire rapidly; responses race).
  // Only the latest request's resolution is allowed to touch state.
  const reqIdRef = useRef(0);

  const reload = () => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    listReconSessions({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(debouncedSearchText.trim() ? { search: debouncedSearchText.trim() } : {}),
    })
      .then((resp) => {
        if (reqIdRef.current !== reqId) return;
        setRows(resp.items);
        setTotalRows(resp.total);
      })
      .catch((err) => {
        if (reqIdRef.current !== reqId) return;
        setError(formatApiError(err, 'Failed to load recon runs.'));
      })
      .finally(() => {
        if (reqIdRef.current !== reqId) return;
        setLoading(false);
      });
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, debouncedSearchText]);

  const toggleSelect = compareSelection.toggle;
  const compareEnabled = compareSelection.isCompareReady;
  const onCompare = () => {
    if (!compareEnabled) return;
    navigate(`/recon/compare?a=${selected[0]}&b=${selected[1]}`);
  };

  const sortedRows = useMemo(() => {
    // v2.86.10 — search is server-side now (passed to listReconSessions).
    // The client-side filter loop was an artifact of the bare-array
    // back-compat path and could miss matches beyond the capped slice.
    return [...rows].sort((a, b) => {
      if (a.status === 'active' && b.status !== 'active') return -1;
      if (b.status === 'active' && a.status !== 'active') return 1;
      const ta = a.started_at ? new Date(a.started_at).getTime() : 0;
      const tb = b.started_at ? new Date(b.started_at).getTime() : 0;
      return tb - ta;
    });
  }, [rows]);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Recon Runs</h1>
          <p className="text-metadata text-muted-foreground">
            Every recon session in this project. Select two rows to compare them.
          </p>
        </div>
        <Button onClick={handleStartRecon} disabled={scopesLoading}>
          <Rocket className="size-4" aria-hidden />
          {scopesLoading ? 'Loading…' : 'Start Recon'}
        </Button>
        <Button
          variant={compareEnabled ? 'default' : 'outline'}
          disabled={!compareEnabled}
          onClick={onCompare}
        >
          <ArrowLeftRight className="size-4" aria-hidden />
          {compareEnabled ? 'Compare selected (2)' : `Compare (${selected.length}/2 selected)`}
        </Button>
        <Button size="sm" variant="outline" onClick={reload}>
          <RefreshCw className="size-4" aria-hidden /> Refresh
        </Button>
      </div>

      <div className="mb-sm flex flex-wrap items-center gap-xs" role="group" aria-label="Status filter">
        <span className="text-caption text-muted-foreground">Status:</span>
        {STATUS_OPTIONS.map((opt) => {
          const active = statusFilter === opt.value;
          return (
            <button
              key={opt.value || 'all'}
              type="button"
              aria-pressed={active}
              onClick={() => setStatusFilter(opt.value)}
              className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Badge variant={active ? 'default' : 'outline'}>
                {opt.label}
              </Badge>
            </button>
          );
        })}
        <div className="relative ml-auto min-w-56">
          {/* FRX·H4: client-side search over agent username + model. */}
          <Search
            className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Search agent or model…"
            aria-label="Search recon runs"
            className="pl-xl"
          />
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* v2.86.10 — "Showing N of T" hint so the user knows whether the
          server returned everything that matches their filters or the
          response is capped.  Only renders when there are results and
          there's actually a gap between loaded and total. */}
      {!loading && rows.length > 0 && totalRows > rows.length && (
        <p className="mb-xs text-caption text-muted-foreground">
          Showing {rows.length} of {totalRows} runs. Refine filters / search to narrow.
        </p>
      )}

      {loading && rows.length === 0 ? (
        <Card>
          <CardContent className="p-0">
            <TableSkeleton rows={8} columns={8} />
          </CardContent>
        </Card>
      ) : sortedRows.length === 0 ? (
        <Card>
          <CardContent className="p-0">
            <div className="empty-state-panel flex flex-col items-center gap-sm p-xxl text-center">
              <Rocket className="size-12 text-muted-foreground" aria-hidden />
              <p className="text-subheading font-semibold">No recon sessions yet</p>
              <p className="max-w-md text-metadata text-muted-foreground">
                Start an agentic recon from a scope to populate hosts in this project. The
                resulting runs appear here for review and comparison.
              </p>
              <Button onClick={() => navigate('/scopes')} size="sm">
                Open Scopes
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Mobile cards — match the responsive list pattern used by
              Test Plans so all three workflow lists behave the same on
              narrow viewports. */}
          <div className="flex flex-col gap-xs md:hidden">
            {sortedRows.map((row) => {
              const isSelected = selected.includes(row.id);
              const attribution = [
                row.generated_by_model,
                row.generated_by_tool,
                row.started_by_username ? `by ${row.started_by_username}` : null,
              ]
                .filter(Boolean)
                .join(' · ');
              return (
                <Card key={row.id} className={cn(isSelected && 'border-primary')}>
                  <CardContent className="flex flex-col gap-xs p-sm">
                    <div className="flex items-start gap-xs">
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleSelect(row.id)}
                        aria-label={`Select recon run ${row.id} for compare`}
                        className="mt-xxs"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono font-semibold">#{row.id}</span>
                          <Badge variant={statusTone(row.status)} className="whitespace-nowrap">
                            {row.status}
                          </Badge>
                          {row.is_stale && (
                            <Badge variant="warning" className="whitespace-nowrap">
                              Possibly interrupted
                            </Badge>
                          )}
                        </div>
                        {attribution && (
                          <p className="mt-xxs break-words text-caption text-muted-foreground">
                            {attribution}
                          </p>
                        )}
                        <div className="mt-xs flex flex-wrap gap-md text-caption text-muted-foreground">
                          <span>{fmtTime(row.started_at)}</span>
                          <span>{row.hosts_discovered} hosts</span>
                          <span>{row.uploads_submitted} uploads</span>
                        </div>
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="self-start"
                      onClick={() => navigate(`/recon/runs/${row.id}`)}
                    >
                      Open
                      <SquareArrowOutUpRight className="size-3" aria-hidden />
                    </Button>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block">
            <Card>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12" />
                    <TableHead className="w-20">ID</TableHead>
                    {/* w-28 (112px) overflowed when the "Possibly
                        interrupted" stale badge rendered alongside the
                        status badge (badges total ~240px when both
                        present).  Widened to w-56 + flex-wrap so the
                        stale badge wraps onto a second line on narrow
                        viewports rather than bleeding into Scope. */}
                    <TableHead className="w-56">Status</TableHead>
                    <TableHead>Model / tool</TableHead>
                    <TableHead className="w-28">Started</TableHead>
                    <TableHead className="w-20 text-right">Hosts</TableHead>
                    <TableHead className="w-24 text-right">Uploads</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedRows.map((row) => {
                    const isSelected = selected.includes(row.id);
                    // v2.43.0 — UX review #2: NavigableTableRow + primary
                    // cell <Link> replace the interactive <tr> antipattern.
                    return (
                      <NavigableTableRow key={row.id} selected={isSelected}>
                        <TableCell className="w-12">
                          <Checkbox
                            checked={isSelected}
                            onCheckedChange={() => toggleSelect(row.id)}
                            aria-label={`Select recon run ${row.id} for compare`}
                          />
                        </TableCell>
                        <NavigableTableCell
                          to={`/recon/runs/${row.id}`}
                          ariaLabel={`Open recon run ${row.id}`}
                          className="font-mono"
                        >
                          #{row.id}
                        </NavigableTableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-xxs">
                            <Badge
                              variant={statusTone(row.status)}
                              className="whitespace-nowrap"
                            >
                              {row.status}
                            </Badge>
                            {row.is_stale && (
                              <Badge variant="warning" className="whitespace-nowrap">
                                Possibly interrupted
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="grid min-w-0 max-w-[28rem] gap-xxs text-caption">
                            {row.generated_by_model && (
                              <div className="grid min-w-0 grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-xs">
                                <span className="text-micro font-semibold uppercase text-muted-foreground">
                                  Model
                                </span>
                                <span className="truncate font-mono text-foreground" title={row.generated_by_model}>
                                  {row.generated_by_model}
                                </span>
                              </div>
                            )}
                            {row.generated_by_tool && (
                              <div className="grid min-w-0 grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-xs">
                                <span className="text-micro font-semibold uppercase text-muted-foreground">
                                  Tool
                                </span>
                                <span className="truncate text-foreground" title={row.generated_by_tool}>
                                  {row.generated_by_tool}
                                </span>
                              </div>
                            )}
                            {row.started_by_username && (
                              <div className="grid min-w-0 grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-xs">
                                <span className="text-micro font-semibold uppercase text-muted-foreground">
                                  By
                                </span>
                                <span className="truncate text-muted-foreground" title={row.started_by_username}>
                                  {row.started_by_username}
                                </span>
                              </div>
                            )}
                            {!row.generated_by_model && !row.generated_by_tool && !row.started_by_username && (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-caption">{fmtTime(row.started_at)}</TableCell>
                        <TableCell className="text-right">{row.hosts_discovered}</TableCell>
                        <TableCell className="text-right">{row.uploads_submitted}</TableCell>
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => navigate(`/recon/runs/${row.id}`)}
                          >
                            Open
                            <SquareArrowOutUpRight className="size-3" aria-hidden />
                          </Button>
                        </TableCell>
                      </NavigableTableRow>
                    );
                  })}
                </TableBody>
              </Table>
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {/* Scope picker — only opens when the project has multiple
          scopes. The single-scope case skips this dialog and opens
          the recon dialog directly (see handleStartRecon). */}
      <Dialog open={scopePickerOpen} onOpenChange={setScopePickerOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <Rocket className="size-5 text-primary" aria-hidden />
              Pick a scope for recon
            </DialogTitle>
            <DialogDescription>
              Choose which scope the agent should run reconnaissance against. Each scope's
              subnets are the only target list the agent is authorised to touch.
            </DialogDescription>
          </DialogHeader>
          <ul className="flex max-h-72 flex-col divide-y divide-border overflow-y-auto">
            {(availableScopes ?? []).map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => handlePickScope(s)}
                  className="flex w-full flex-wrap items-center justify-between gap-sm rounded-control px-sm py-xs text-left text-metadata hover:bg-accent focus:outline-none focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="min-w-0 flex-1">
                    <span className="truncate font-medium text-foreground">{s.name}</span>
                    {s.description && (
                      <span className="ml-xs text-caption text-muted-foreground">
                        {s.description}
                      </span>
                    )}
                  </span>
                  <Badge variant="outline">id {s.id}</Badge>
                </button>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScopePickerOpen(false)}>
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* The shared recon dialog — same UI used by Scopes.tsx.  Opens
          when `recon.scopeId` becomes non-null (handled inside the
          useReconPlan hook via openFor). */}
      <StartReconDialog recon={recon} />
    </div>
  );
};

export default ReconRunsList;
