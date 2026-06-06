import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeftRight,
  ClipboardList,
  RefreshCw,
  Search,
  SquareArrowOutUpRight,
} from 'lucide-react';
import {
  ExecutionSessionRow,
  listExecutionSessionsProjectWide,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useCompareSelection } from '../hooks/useCompareSelection';
import { useSearchFocus } from '../hooks/useSearchFocus';
import { useToast } from '../contexts/ToastContext';
import { NavigableTableCell, NavigableTableRow } from '../components/NavigableTableRow';
import { Alert, AlertDescription } from '../components/ui/alert';
import { TableSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../utils/cn';

type StatusFilter = '' | 'active' | 'paused' | 'completed' | 'failed' | 'abandoned';

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: '', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'paused', label: 'Paused' },
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

const ExecutionsList: React.FC = () => {
  const navigate = useNavigate();
  const toast = useToast();
  const [rows, setRows] = useState<ExecutionSessionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  // v2.44.1 (UX review #7): selection now uses the shared
  // useCompareSelection hook (same on ReconRunsList).  The hand-rolled
  // toast + dedup logic moved into the hook.
  const compareSelection = useCompareSelection<number>({ kind: 'execution runs' });
  const selected = compareSelection.selected;
  // FRX·H4: client-side search over agent username + model name.
  const [searchText, setSearchText] = useState('');
  const debouncedSearchText = useDebouncedValue(searchText, 300);
  // v2.43.0 — UX review #7: page-level search inputs subscribe to the
  // global `/` shortcut so the documented "press / to focus search"
  // behavior actually fires.
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  useSearchFocus(searchInputRef);

  // v2.86.10 — total comes from X-Total-Count; surface "Showing N of T"
  // so older rows aren't silently truncated behind a server-side cap.
  const [totalRows, setTotalRows] = useState(0);

  const reload = () => {
    setLoading(true);
    setError(null);
    listExecutionSessionsProjectWide({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(debouncedSearchText.trim() ? { search: debouncedSearchText.trim() } : {}),
    })
      .then((resp) => {
        setRows(resp.items);
        setTotalRows(resp.total);
      })
      .catch((err) => setError(formatApiError(err, 'Failed to load executions.')))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, debouncedSearchText]);

  const toggleSelect = compareSelection.toggle;

  const samePlan = useMemo(() => {
    if (selected.length !== 2) return null;
    const a = rows.find((r) => r.id === selected[0]);
    const b = rows.find((r) => r.id === selected[1]);
    if (!a || !b) return null;
    return a.test_plan_id === b.test_plan_id;
  }, [selected, rows]);

  const compareEnabled = selected.length === 2 && samePlan === true;
  const compareHint =
    selected.length < 2
      ? `Select two executions of the same plan (${selected.length}/2)`
      : samePlan === false
      ? 'Different plans — pick two from the same plan'
      : 'Compare selected (2)';

  const onCompare = () => {
    if (!compareEnabled) return;
    const planId = rows.find((r) => r.id === selected[0])?.test_plan_id;
    if (!planId) return;
    navigate(`/test-plans/${planId}/compare?a=${selected[0]}&b=${selected[1]}`);
  };

  const sortedRows = useMemo(() => {
    // v2.86.10 — search is server-side now; was previously a client-side
    // filter that could miss matches beyond the response cap.
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
          <h1 className="text-page-title font-semibold">Executions</h1>
          <p className="text-metadata text-muted-foreground">
            Every execution session in this project. Select two from the same plan to compare them.
          </p>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <Button
                variant={compareEnabled ? 'default' : 'outline'}
                disabled={!compareEnabled}
                onClick={onCompare}
              >
                <ArrowLeftRight className="size-4" aria-hidden />
                {compareEnabled ? 'Compare selected (2)' : `Compare (${selected.length}/2)`}
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>{compareHint}</TooltipContent>
        </Tooltip>
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
            ref={searchInputRef}
            type="search"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Search agent or model… (press / to focus)"
            aria-label="Search execution sessions"
            className="pl-xl"
          />
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!loading && rows.length > 0 && totalRows > rows.length && (
        <p className="mb-xs text-caption text-muted-foreground">
          Showing {rows.length} of {totalRows} executions. Refine filters / search to narrow.
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
            <div className="flex flex-col items-center gap-sm p-xxl text-center">
              <ClipboardList className="size-12 text-muted-foreground" aria-hidden />
              <p className="text-subheading font-semibold">No execution sessions yet</p>
              <p className="max-w-md text-metadata text-muted-foreground">
                Execution sessions are created when you click <strong>Execute</strong> on an
                approved test plan. Approve a plan and start one to populate this list.
              </p>
              <Button onClick={() => navigate('/test-plans')} size="sm">
                Open Test Plans
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
                        aria-label={`Select execution ${row.id} for compare`}
                        className="mt-xxs"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono font-semibold">#{row.id}</span>
                          <Badge variant={statusTone(row.status)} className="whitespace-nowrap">
                            {row.status}
                          </Badge>
                        </div>
                        <p className="mt-xxs break-words text-metadata">
                          {row.plan_title || '—'}{' '}
                          <span className="text-caption text-muted-foreground">
                            #{row.test_plan_id}
                            {row.plan_version != null && ` v${row.plan_version}`}
                          </span>
                        </p>
                        {attribution && (
                          <p className="mt-xxs break-words text-caption text-muted-foreground">
                            {attribution}
                          </p>
                        )}
                        <div className="mt-xs flex flex-wrap gap-md text-caption text-muted-foreground">
                          <span>{fmtTime(row.started_at)}</span>
                          <span>{row.result_count} tests</span>
                          <span>
                            {row.finding_count > 0 ? `${row.finding_count} findings` : 'no findings'}
                          </span>
                        </div>
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="self-start"
                      onClick={() => navigate(`/executions/${row.id}`)}
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
                    {/* w-28 (112px) borderline-overflowed for `completed`/
                        `abandoned` (9 chars + chip padding).  Bumped to
                        w-32 + whitespace-nowrap on the badge for headroom. */}
                    <TableHead className="w-32">Status</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Model / tool</TableHead>
                    <TableHead className="w-28">Started</TableHead>
                    <TableHead className="w-20 text-right">Tests</TableHead>
                    <TableHead className="w-24 text-right">Findings</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedRows.map((row) => {
                    const isSelected = selected.includes(row.id);
                    // v2.43.0 — UX review #2: NavigableTableRow replaces
                    // the role="link"/onClick pattern.  The detail link
                    // lives in the #id primary cell.
                    return (
                      <NavigableTableRow key={row.id} selected={isSelected}>
                        <TableCell className="w-12">
                          <Checkbox
                            checked={isSelected}
                            onCheckedChange={() => toggleSelect(row.id)}
                            aria-label={`Select execution ${row.id} for compare`}
                          />
                        </TableCell>
                        <NavigableTableCell
                          to={`/executions/${row.id}`}
                          ariaLabel={`Open execution session ${row.id}`}
                          className="font-mono"
                        >
                          #{row.id}
                        </NavigableTableCell>
                        <TableCell>
                          <Badge variant={statusTone(row.status)} className="whitespace-nowrap">
                            {row.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="truncate">
                          {row.plan_title || '—'}{' '}
                          <span className="text-caption text-muted-foreground">
                            #{row.test_plan_id}
                            {row.plan_version != null && ` v${row.plan_version}`}
                          </span>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-xxs">
                            {row.generated_by_model && (
                              <Badge variant="outline">{row.generated_by_model}</Badge>
                            )}
                            {row.generated_by_tool && (
                              <Badge variant="outline">{row.generated_by_tool}</Badge>
                            )}
                            {row.started_by_username && (
                              // "by" prefix instead of a "·" separator —
                              // the bullet renders as a period-like glyph
                              // at caption font size and was being misread
                              // as part of the username (e.g. ".admin").
                              <span className="text-caption text-muted-foreground">
                                by {row.started_by_username}
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-caption">{fmtTime(row.started_at)}</TableCell>
                        <TableCell className="text-right">{row.result_count}</TableCell>
                        <TableCell className="text-right">
                          {row.finding_count > 0 ? (
                            <Badge variant="warning">{row.finding_count}</Badge>
                          ) : (
                            '—'
                          )}
                        </TableCell>
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => navigate(`/executions/${row.id}`)}
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
    </div>
  );
};

export default ExecutionsList;
