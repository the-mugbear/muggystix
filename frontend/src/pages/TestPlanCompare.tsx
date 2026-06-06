import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Minus,
} from 'lucide-react';
import {
  AllEntryResultsResponse,
  EntryResultsBundle,
  getAllEntryResults,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { TableSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';

interface EntryRollup {
  executed: number;
  skipped: number;
  failed: number;
  not_applicable: number;
  pending: number;
  findings: number;
  critical_findings: number;
  sanity_passed: number;
  sanity_total: number;
  commands: string[];
}

function rollup(bundle: EntryResultsBundle | undefined): EntryRollup {
  const r: EntryRollup = {
    executed: 0,
    skipped: 0,
    failed: 0,
    not_applicable: 0,
    pending: 0,
    findings: 0,
    critical_findings: 0,
    sanity_passed: 0,
    sanity_total: 0,
    commands: [],
  };
  if (!bundle) return r;
  for (const t of bundle.tests) {
    if (t.status === 'executed') r.executed += 1;
    else if (t.status === 'skipped') r.skipped += 1;
    else if (t.status === 'failed') r.failed += 1;
    else if (t.status === 'not_applicable') r.not_applicable += 1;
    else r.pending += 1;
    if (t.is_finding) r.findings += 1;
    if (t.is_finding && t.severity?.toLowerCase() === 'critical') r.critical_findings += 1;
    if (t.command_run) r.commands.push(t.command_run);
  }
  r.sanity_total = bundle.sanity_checks.length;
  r.sanity_passed = bundle.sanity_checks.filter((c) => c.passed).length;
  return r;
}

type DiffVerdict = 'same' | 'minor' | 'major' | 'a_only' | 'b_only';
type Tone = 'success' | 'warning' | 'destructive' | 'info' | 'muted';

function diffEntries(
  a: EntryResultsBundle | undefined,
  b: EntryResultsBundle | undefined,
): DiffVerdict {
  if (!a && !b) return 'same';
  if (!a) return 'b_only';
  if (!b) return 'a_only';
  const ra = rollup(a);
  const rb = rollup(b);
  if (ra.findings !== rb.findings) return 'major';
  if (ra.critical_findings !== rb.critical_findings) return 'major';
  if ((ra.sanity_passed > 0) !== (rb.sanity_passed > 0)) return 'major';
  if (ra.executed !== rb.executed) return 'minor';
  if (ra.failed !== rb.failed) return 'minor';
  if (ra.commands.join('|') !== rb.commands.join('|')) return 'minor';
  return 'same';
}

const verdictMeta: Record<DiffVerdict, { label: string; tone: Tone }> = {
  same: { label: 'same', tone: 'success' },
  minor: { label: 'minor diff', tone: 'info' },
  major: { label: 'major diff', tone: 'destructive' },
  a_only: { label: 'A only', tone: 'warning' },
  b_only: { label: 'B only', tone: 'warning' },
};

const SessionHeader: React.FC<{
  label: string;
  bundle: AllEntryResultsResponse | null;
}> = ({ label, bundle }) => {
  if (!bundle) {
    return (
      <div className="flex items-center gap-xs p-sm text-metadata text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading {label}…
      </div>
    );
  }
  return (
    <div className="p-sm">
      <div className="mb-xxs flex flex-wrap items-center gap-xs">
        <span className="text-micro uppercase tracking-wide text-muted-foreground">{label}</span>
        <Badge variant="outline">#{bundle.execution_session_id}</Badge>
        <Badge variant={bundle.execution_session_status === 'active' ? 'success' : 'muted'}>
          {bundle.execution_session_status}
        </Badge>
      </div>
      <p className="truncate text-subheading font-semibold">
        {bundle.generated_by_model || '(model not reported)'}
        {bundle.generated_by_tool && (
          <span className="ml-xs text-metadata text-muted-foreground">
            via {bundle.generated_by_tool}
          </span>
        )}
      </p>
      <p className="text-caption text-muted-foreground">
        {bundle.started_by_username && <>started by {bundle.started_by_username} · </>}
        {bundle.started_at && <>{new Date(bundle.started_at).toLocaleString()}</>}
        {bundle.completed_at && (
          <> · completed {new Date(bundle.completed_at).toLocaleString()}</>
        )}
      </p>
    </div>
  );
};

const RollupCell: React.FC<{ r: EntryRollup }> = ({ r }) => (
  <div className="flex flex-wrap gap-xxs">
    {r.executed > 0 && <Badge variant="success">{r.executed} exec</Badge>}
    {r.skipped > 0 && <Badge variant="outline">{r.skipped} skip</Badge>}
    {r.failed > 0 && <Badge variant="destructive">{r.failed} fail</Badge>}
    {r.not_applicable > 0 && <Badge variant="outline">{r.not_applicable} N/A</Badge>}
    {r.findings > 0 && (
      <Badge variant="warning">
        {r.findings} finding{r.findings === 1 ? '' : 's'}
      </Badge>
    )}
    {r.critical_findings > 0 && <Badge variant="destructive">{r.critical_findings} crit</Badge>}
    {r.sanity_total === 0 ? (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline">
            <Minus className="size-3" aria-hidden /> no sanity
          </Badge>
        </TooltipTrigger>
        <TooltipContent>No sanity check on file</TooltipContent>
      </Tooltip>
    ) : (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant={r.sanity_passed > 0 ? 'success' : 'destructive'}>
            sanity {r.sanity_passed}/{r.sanity_total}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {r.sanity_passed}/{r.sanity_total} sanity check(s) passed
        </TooltipContent>
      </Tooltip>
    )}
    {r.commands.length > 0 && r.commands.length <= 2
      ? r.commands.map((c, i) => (
          <Tooltip key={i}>
            <TooltipTrigger asChild>
              <Badge variant="outline" className="break-all font-mono">
                {c.length > 40 ? c.slice(0, 40) + '…' : c}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>{c}</TooltipContent>
          </Tooltip>
        ))
      : r.commands.length > 0 && (
          <Badge variant="outline" className="font-mono">
            {r.commands.length} commands
          </Badge>
        )}
  </div>
);

const TestPlanCompare: React.FC = () => {
  const { planId } = useParams<{ planId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const idNum = Number(planId);
  const sessionAId = Number(searchParams.get('a'));
  const sessionBId = Number(searchParams.get('b'));

  const [bundleA, setBundleA] = useState<AllEntryResultsResponse | null>(null);
  const [bundleB, setBundleB] = useState<AllEntryResultsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // v2.86.6 — paginate the per-entry bundles instead of fetching every
  // entry's results for both sessions on page entry.  Pre-fix a
  // 5000-entry plan made the comparison page fetch ~10000 entry-rows
  // (tests + sanity checks) on initial load.  Both sessions order by
  // entry_id ASC so a synchronized skip/limit keeps aMap/bMap aligned
  // for the diff merge.
  // v4.52.0 — split into two page sizes (matching TestPlanLayout +
  // ExecutionDetail): small initial page for fast first paint of the
  // comparison, larger Load-more chunks so each subsequent click
  // amortizes its parallel round-trip across both sessions.
  const INITIAL_ENTRIES_PAGE_SIZE = 50;
  const LOAD_MORE_ENTRIES_PAGE_SIZE = 200;

  useEffect(() => {
    if (!idNum || !sessionAId || !sessionBId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getAllEntryResults(idNum, sessionAId, { entriesLimit: INITIAL_ENTRIES_PAGE_SIZE }),
      getAllEntryResults(idNum, sessionBId, { entriesLimit: INITIAL_ENTRIES_PAGE_SIZE }),
    ])
      .then(([a, b]) => {
        setBundleA(a);
        setBundleB(b);
      })
      .catch((e) => setError(formatApiError(e, 'Failed to load comparison.')))
      .finally(() => setLoading(false));
  }, [idNum, sessionAId, sessionBId]);

  // Append the next page of entry results for both sessions in parallel.
  // We read off the live ``bundleA`` length so re-entrant clicks don't
  // double-fetch the same page.
  const loadMoreEntries = useCallback(async () => {
    if (!bundleA || !bundleB || loadingMore || !idNum || !sessionAId || !sessionBId) return;
    const loaded = bundleA.entries.length;
    const total = bundleA.entries_total ?? loaded;
    if (loaded >= total) return;
    setLoadingMore(true);
    try {
      const [a, b] = await Promise.all([
        getAllEntryResults(idNum, sessionAId, { entriesSkip: loaded, entriesLimit: LOAD_MORE_ENTRIES_PAGE_SIZE }),
        getAllEntryResults(idNum, sessionBId, { entriesSkip: loaded, entriesLimit: LOAD_MORE_ENTRIES_PAGE_SIZE }),
      ]);
      // Merge: keep the existing slice and append the new page.  Dedup
      // by entry_id defensively in case the user double-clicked before
      // the previous request settled — both sessions order by entry_id
      // ASC so the existing+new union should still be sorted, but the
      // diff merge below recomputes anyway.
      setBundleA((prev) => prev ? {
        ...a,
        entries: [...prev.entries, ...a.entries.filter(
          (e) => !prev.entries.some((p) => p.entry_id === e.entry_id),
        )],
      } : a);
      setBundleB((prev) => prev ? {
        ...b,
        entries: [...prev.entries, ...b.entries.filter(
          (e) => !prev.entries.some((p) => p.entry_id === e.entry_id),
        )],
      } : b);
    } catch (e) {
      setError(formatApiError(e, 'Failed to load more comparison entries.'));
    } finally {
      setLoadingMore(false);
    }
  }, [bundleA, bundleB, loadingMore, idNum, sessionAId, sessionBId]);

  const totalEntries = bundleA?.entries_total ?? bundleA?.entries.length ?? 0;
  const loadedEntries = bundleA?.entries.length ?? 0;
  const hasMoreEntries = loadedEntries < totalEntries;

  const rows = useMemo(() => {
    const aMap = new Map<number, EntryResultsBundle>();
    const bMap = new Map<number, EntryResultsBundle>();
    bundleA?.entries.forEach((e) => aMap.set(e.entry_id, e));
    bundleB?.entries.forEach((e) => bMap.set(e.entry_id, e));
    const ids = new Set<number>([...aMap.keys(), ...bMap.keys()]);
    return Array.from(ids)
      .map((id) => {
        const a = aMap.get(id);
        const b = bMap.get(id);
        return {
          entry_id: id,
          host_ip: a?.host_ip ?? b?.host_ip ?? null,
          host_hostname: a?.host_hostname ?? b?.host_hostname ?? null,
          a,
          b,
          verdict: diffEntries(a, b),
        };
      })
      .sort((x, y) => {
        const order: DiffVerdict[] = ['major', 'a_only', 'b_only', 'minor', 'same'];
        const oi = order.indexOf(x.verdict);
        const oj = order.indexOf(y.verdict);
        if (oi !== oj) return oi - oj;
        return x.entry_id - y.entry_id;
      });
  }, [bundleA, bundleB]);

  const verdictCounts = useMemo(() => {
    const c: Record<DiffVerdict, number> = { same: 0, minor: 0, major: 0, a_only: 0, b_only: 0 };
    rows.forEach((r) => (c[r.verdict] += 1));
    return c;
  }, [rows]);

  if (!sessionAId || !sessionBId) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="warning">
          <AlertDescription>
            Pass <code className="font-mono">?a=&lt;session_id&gt;&amp;b=&lt;session_id&gt;</code>{' '}
            in the URL to compare two execution sessions. Open this page from the session picker on
            TestPlanDetail for the buttons that build the URL for you.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex items-center gap-xs">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => navigate(`/test-plans/${idNum}`)}
          aria-label="Back to test plan"
        >
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-section-title font-semibold">Compare executions of plan #{idNum}</h1>
          <p className="text-metadata text-muted-foreground">
            Per-entry diff of two execution sessions — see what changed when the same plan was run
            by different agents or models.
          </p>
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card className="mb-sm">
        <CardContent className="grid grid-cols-1 divide-y divide-border p-0 md:grid-cols-2 md:divide-x md:divide-y-0">
          <SessionHeader label="Session A" bundle={bundleA} />
          <SessionHeader label="Session B" bundle={bundleB} />
        </CardContent>
      </Card>

      {loading && rows.length === 0 ? (
        <>
          <div className="mb-md grid grid-cols-1 gap-md md:grid-cols-2">
            <div className="h-44 rounded-panel bg-muted/40 animate-pulse" />
            <div className="h-44 rounded-panel bg-muted/40 animate-pulse" />
          </div>
          <TableSkeleton rows={5} columns={4} />
        </>
      ) : (
        <>
          <div className="mb-sm flex flex-wrap gap-xs">
            <Badge variant={verdictCounts.major > 0 ? 'destructive' : 'outline'}>
              <AlertCircle className="size-3" aria-hidden /> Major diff: {verdictCounts.major}
            </Badge>
            <Badge variant={verdictCounts.minor > 0 ? 'info' : 'outline'}>
              Minor diff: {verdictCounts.minor}
            </Badge>
            <Badge variant={verdictCounts.same > 0 ? 'success' : 'outline'}>
              <CheckCircle2 className="size-3" aria-hidden /> Same: {verdictCounts.same}
            </Badge>
            {verdictCounts.a_only + verdictCounts.b_only > 0 && (
              <Badge variant="warning">
                Only one side: {verdictCounts.a_only + verdictCounts.b_only}
              </Badge>
            )}
          </div>

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-28">Diff</TableHead>
                      <TableHead className="w-52">Host</TableHead>
                      <TableHead>Session A</TableHead>
                      <TableHead>Session B</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.length === 0 && !loading ? (
                      <TableRow>
                        <TableCell colSpan={4} className="py-md text-center text-metadata text-muted-foreground">
                          Neither session recorded any entries.
                        </TableCell>
                      </TableRow>
                    ) : (
                      rows.map((row) => {
                        const ra = rollup(row.a);
                        const rb = rollup(row.b);
                        const meta = verdictMeta[row.verdict];
                        return (
                          <TableRow key={row.entry_id}>
                            <TableCell>
                              <Badge variant={meta.tone}>{meta.label}</Badge>
                            </TableCell>
                            <TableCell className="truncate">
                              <p className="font-mono">{row.host_ip || '—'}</p>
                              {row.host_hostname && (
                                <p className="truncate text-caption text-muted-foreground">
                                  {row.host_hostname}
                                </p>
                              )}
                            </TableCell>
                            <TableCell>
                              {row.a ? (
                                <RollupCell r={ra} />
                              ) : (
                                <span className="text-caption text-muted-foreground">
                                  (entry absent from Session A)
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              {row.b ? (
                                <RollupCell r={rb} />
                              ) : (
                                <span className="text-caption text-muted-foreground">
                                  (entry absent from Session B)
                                </span>
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </div>
              {/* v2.86.6 — Load more affordance when the comparison
                  spans more than ENTRIES_PAGE_SIZE entries.  Fetches
                  the next page from BOTH sessions in parallel so the
                  diff merge above stays consistent. */}
              {hasMoreEntries && (
                <div className="mt-md flex flex-col items-center gap-xs px-md pb-md">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadMoreEntries}
                    disabled={loadingMore}
                  >
                    {loadingMore
                      ? 'Loading…'
                      : `Load more (${totalEntries - loadedEntries} remaining)`}
                  </Button>
                  <p className="text-caption text-muted-foreground">
                    Showing {loadedEntries} of {totalEntries} entries.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};

export default TestPlanCompare;
