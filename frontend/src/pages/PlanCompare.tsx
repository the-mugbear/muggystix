import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import {
  TestPlanDetail,
  TestPlanEntryResponse,
  getTestPlan,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { TableSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Separator } from '../components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

type EntryVerdict = 'a_only' | 'b_only' | 'both_match' | 'both_diff';
type VerdictTone = 'warning' | 'info' | 'success' | 'muted';

interface EntryDiffRow {
  host_id: number;
  host_ip: string | null;
  host_hostname: string | null;
  in_a: TestPlanEntryResponse | null;
  in_b: TestPlanEntryResponse | null;
  verdict: EntryVerdict;
  diff_notes: string[];
}

const verdictTone = (v: EntryVerdict): VerdictTone => {
  if (v === 'a_only' || v === 'b_only') return 'warning';
  if (v === 'both_diff') return 'info';
  return 'success';
};

const verdictLabel = (v: EntryVerdict): string => {
  if (v === 'a_only') return 'A only';
  if (v === 'b_only') return 'B only';
  if (v === 'both_diff') return 'differs';
  return 'identical';
};

function compareEntries(
  a: TestPlanEntryResponse,
  b: TestPlanEntryResponse,
): { match: boolean; notes: string[] } {
  const notes: string[] = [];
  if (a.priority !== b.priority) notes.push(`priority A=${a.priority} / B=${b.priority}`);
  if (a.test_phase !== b.test_phase) notes.push(`phase A=${a.test_phase} / B=${b.test_phase}`);
  const aTests = a.proposed_tests ?? [];
  const bTests = b.proposed_tests ?? [];
  if (aTests.length !== bTests.length) {
    notes.push(`tests A=${aTests.length} / B=${bTests.length}`);
  } else {
    const aJson = JSON.stringify(aTests);
    const bJson = JSON.stringify(bTests);
    if (aJson !== bJson) notes.push('tests differ (same count)');
  }
  if (a.status !== b.status) notes.push(`status A=${a.status} / B=${b.status}`);
  return { match: notes.length === 0, notes };
}

function diffPlans(planA: TestPlanDetail, planB: TestPlanDetail): EntryDiffRow[] {
  const byA = new Map<number, TestPlanEntryResponse>(
    planA.entries.map((e) => [e.host_id, e]),
  );
  const byB = new Map<number, TestPlanEntryResponse>(
    planB.entries.map((e) => [e.host_id, e]),
  );
  const hostIds = new Set<number>([...byA.keys(), ...byB.keys()]);
  const rows: EntryDiffRow[] = [];
  hostIds.forEach((host_id) => {
    const a = byA.get(host_id) ?? null;
    const b = byB.get(host_id) ?? null;
    const baseHost = a ?? b!;
    if (a && !b) {
      rows.push({
        host_id,
        host_ip: baseHost.host_ip ?? null,
        host_hostname: baseHost.host_hostname ?? null,
        in_a: a,
        in_b: null,
        verdict: 'a_only',
        diff_notes: [],
      });
      return;
    }
    if (b && !a) {
      rows.push({
        host_id,
        host_ip: baseHost.host_ip ?? null,
        host_hostname: baseHost.host_hostname ?? null,
        in_a: null,
        in_b: b,
        verdict: 'b_only',
        diff_notes: [],
      });
      return;
    }
    if (a && b) {
      const { match, notes } = compareEntries(a, b);
      rows.push({
        host_id,
        host_ip: baseHost.host_ip ?? null,
        host_hostname: baseHost.host_hostname ?? null,
        in_a: a,
        in_b: b,
        verdict: match ? 'both_match' : 'both_diff',
        diff_notes: notes,
      });
    }
  });

  const order: Record<EntryVerdict, number> = {
    a_only: 0,
    b_only: 1,
    both_diff: 2,
    both_match: 3,
  };
  return rows.sort((x, y) => {
    const ko = order[x.verdict] - order[y.verdict];
    if (ko !== 0) return ko;
    return (x.host_ip ?? '').localeCompare(y.host_ip ?? '', undefined, { numeric: true });
  });
}

const PlanCard: React.FC<{ label: string; plan: TestPlanDetail }> = ({ label, plan }) => {
  const navigate = useNavigate();
  return (
    <Card>
      <CardContent className="flex flex-col gap-xs p-md">
        <div className="flex flex-wrap items-center gap-xs">
          <Badge>{label}</Badge>
          <span className="text-subheading font-semibold">Plan #{plan.id}</span>
          <Badge variant="outline">{plan.status}</Badge>
        </div>
        <p className="truncate text-metadata">{plan.title || '—'}</p>
        {(plan.generated_by_model || plan.agent_name) && (
          <p className="text-caption text-muted-foreground">
            By <strong>{plan.generated_by_model ?? plan.agent_name}</strong>
            {plan.generated_by_tool && ` via ${plan.generated_by_tool}`}
            {plan.created_by_username && ` · ${plan.created_by_username}`}
          </p>
        )}
        {plan.source_kind === 'recon_session' && plan.source_recon_session_id && (
          <p className="text-caption text-muted-foreground">
            Source:{' '}
            <button
              type="button"
              onClick={() => navigate(`/recon/runs/${plan.source_recon_session_id}`)}
              className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Badge variant="secondary" className="cursor-pointer">
                recon #{plan.source_recon_session_id}
              </Badge>
            </button>
          </p>
        )}
        <Separator />
        <div className="flex flex-wrap gap-md text-caption">
          <span>
            <strong>{plan.entries.length}</strong> entries
          </span>
          <span>v{plan.version}</span>
          <span>{new Date(plan.created_at).toLocaleDateString()}</span>
        </div>
      </CardContent>
    </Card>
  );
};

const PlanCompare: React.FC = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const aRaw = params.get('a');
  const bRaw = params.get('b');
  const aId = aRaw ? parseInt(aRaw, 10) : NaN;
  const bId = bRaw ? parseInt(bRaw, 10) : NaN;

  const [planA, setPlanA] = useState<TestPlanDetail | null>(null);
  const [planB, setPlanB] = useState<TestPlanDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (Number.isNaN(aId) || Number.isNaN(bId)) {
      setError('Both ?a= and ?b= must be test plan IDs.');
      setLoading(false);
      return;
    }
    if (aId === bId) {
      setError('Choose two different plans to compare.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [respA, respB] = await Promise.all([getTestPlan(aId), getTestPlan(bId)]);
        if (!cancelled) {
          setPlanA(respA);
          setPlanB(respB);
        }
      } catch (err) {
        if (!cancelled) {
          setError(formatApiError(err, 'Failed to load one or both plans.'));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [aId, bId]);

  const rows = useMemo(() => {
    if (!planA || !planB) return [];
    return diffPlans(planA, planB);
  }, [planA, planB]);

  const counts = useMemo(() => {
    const c = { a_only: 0, b_only: 0, both_diff: 0, both_match: 0 };
    rows.forEach((r) => {
      c[r.verdict] += 1;
    });
    return c;
  }, [rows]);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-xs">
        <Button size="sm" variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-4" aria-hidden /> Back
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-section-title font-semibold">Compare test plans</h1>
          <p className="text-metadata text-muted-foreground">
            Side-by-side diff of two test plans — useful for evaluating two different agent
            generations of "the right thing to test" against the same host set.
          </p>
        </div>
      </div>

      {loading && (
        <>
          <div className="mb-md grid grid-cols-1 gap-md md:grid-cols-2">
            <div className="h-44 rounded-panel bg-muted/40 animate-pulse" />
            <div className="h-44 rounded-panel bg-muted/40 animate-pulse" />
          </div>
          <TableSkeleton rows={5} columns={4} />
        </>
      )}

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {planA && planB && (
        <>
          <div className="mb-md grid grid-cols-1 gap-sm md:grid-cols-2">
            <PlanCard label="A" plan={planA} />
            <PlanCard label="B" plan={planB} />
          </div>

          <Card className="mb-md">
            <CardContent className="p-sm">
              <div className="flex flex-wrap gap-xs">
                <Badge variant="warning">{counts.a_only} A only</Badge>
                <Badge variant="warning">{counts.b_only} B only</Badge>
                <Badge variant="info">{counts.both_diff} differ</Badge>
                <Badge variant="outline" className="border-success/40 text-success">
                  {counts.both_match} identical
                </Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-28">Verdict</TableHead>
                      <TableHead className="w-40">IP</TableHead>
                      <TableHead>Hostname</TableHead>
                      <TableHead className="w-28">A priority</TableHead>
                      <TableHead className="w-28">B priority</TableHead>
                      <TableHead className="w-24 text-right">Tests A</TableHead>
                      <TableHead className="w-24 text-right">Tests B</TableHead>
                      <TableHead>Differences</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={8} className="text-metadata text-muted-foreground">
                          Neither plan has any entries to compare.
                        </TableCell>
                      </TableRow>
                    ) : (
                      rows.map((r) => (
                        <TableRow key={r.host_id}>
                          <TableCell>
                            <Badge variant={verdictTone(r.verdict)}>{verdictLabel(r.verdict)}</Badge>
                          </TableCell>
                          <TableCell className="font-mono">{r.host_ip || '—'}</TableCell>
                          <TableCell className="truncate">{r.host_hostname || '—'}</TableCell>
                          <TableCell>
                            {r.in_a ? (
                              r.in_a.priority
                            ) : (
                              <span className="text-caption text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {r.in_b ? (
                              r.in_b.priority
                            ) : (
                              <span className="text-caption text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {r.in_a ? (r.in_a.proposed_tests?.length ?? 0) : '—'}
                          </TableCell>
                          <TableCell className="text-right">
                            {r.in_b ? (r.in_b.proposed_tests?.length ?? 0) : '—'}
                          </TableCell>
                          <TableCell>
                            {r.diff_notes.length === 0 ? (
                              <span className="text-caption text-muted-foreground">—</span>
                            ) : (
                              <span className="text-caption">{r.diff_notes.join(' · ')}</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};

export default PlanCompare;
