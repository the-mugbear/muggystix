/**
 * Side-by-side comparison of two recon sessions.
 *
 * v2.52.0 rewrite — the old version fetched both sessions' full
 * per-host arrays and diffed them client-side.  At 40k × 40k hosts
 * that path served ~60 MB of JSON and rendered a table no human
 * could actually parse, so the page was both slow and useless.
 *
 * The new approach:
 *
 *   1. ``ReconHostStats`` from each session, rendered side by side
 *      with a Δ column.  Tells the user "B found 55 more hosts than
 *      A", "B added one new tool (eyewitness)", "B saw 37 more hosts
 *      running RDP than A" — the questions an operator actually asks.
 *
 *   2. A new ``GET /recon-sessions/{a}/diff/{b}`` endpoint returns
 *      the IP set difference (capped) plus full counts.  We render
 *      the first 50 new/removed hosts each, with deep-links into
 *      Inventory pre-filtered to each side for the full list.
 *
 * No client-side diff loops, no 40k-row tables.  The full host list
 * lives at /hosts where it's been all along — the comparison view's
 * job is to show *what changed*, not to enumerate every row.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, SquareArrowOutUpRight } from 'lucide-react';
import {
  ReconHostStats,
  ReconSessionDetail,
  ReconSessionDiff,
  diffReconSessions,
  getReconSession,
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

// ─────────────────────────────────────────────────────────────────
// Delta rendering helpers — used by every counter row + service row.

interface DeltaProps {
  /** Difference (b - a).  Positive: B has more (success). */
  delta: number;
}

const Delta: React.FC<DeltaProps> = ({ delta }) => {
  if (delta === 0) {
    return <span className="text-caption text-muted-foreground">—</span>;
  }
  const sign = delta > 0 ? '+' : '';
  const tone =
    delta > 0 ? 'text-success' : 'text-destructive';
  return (
    <span className={`font-mono ${tone}`}>
      {sign}
      {delta.toLocaleString()}
    </span>
  );
};

// Build the lookup-by-key map used to align rows across A and B for
// services/ports/tools.  Returns a Map keyed by whatever the caller
// supplies (service name, "port/protocol", tool name).
function indexBy<T, K>(items: T[], key: (item: T) => K): Map<K, T> {
  const m = new Map<K, T>();
  items.forEach((it) => m.set(key(it), it));
  return m;
}

function unionKeys<T, K>(a: T[], b: T[], key: (item: T) => K): K[] {
  const keys = new Set<K>();
  a.forEach((it) => keys.add(key(it)));
  b.forEach((it) => keys.add(key(it)));
  return [...keys];
}

// ─────────────────────────────────────────────────────────────────
// Per-side summary card — uses host_stats; no per-host fetch.

const SessionCard: React.FC<{ label: string; detail: ReconSessionDetail }> = ({
  label,
  detail,
}) => {
  const s = detail.summary;
  const stats = detail.host_stats;
  return (
    <Card>
      <CardContent className="flex flex-col gap-xs p-md">
        <div className="flex flex-wrap items-center gap-xs">
          <Badge>{label}</Badge>
          <span className="text-subheading font-semibold">Recon #{s.id}</span>
          <Badge variant="outline">{s.status}</Badge>
        </div>
        <p className="text-caption text-muted-foreground">
          Scope #{s.scope_id}
          {s.scope_name ? ` · ${s.scope_name}` : ''}
        </p>
        {s.generated_by_model && (
          <p className="text-caption text-muted-foreground">
            By <strong>{s.generated_by_model}</strong>
            {s.generated_by_tool && ` via ${s.generated_by_tool}`}
          </p>
        )}
        {s.started_at && (
          <p className="text-caption text-muted-foreground">
            Started {new Date(s.started_at).toLocaleString()}
          </p>
        )}
        <Separator />
        <div className="flex flex-wrap gap-md text-caption">
          <span>
            <strong>{s.uploads_submitted}</strong> uploads
          </span>
          <span>
            <strong>{stats.host_count.toLocaleString()}</strong> hosts
          </span>
          <span>
            <strong>{stats.host_count_with_open_ports.toLocaleString()}</strong>{' '}
            with open ports
          </span>
        </div>
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Stats delta panel — renders counts/tools/services/ports A vs B.

const StatsDeltaPanel: React.FC<{
  statsA: ReconHostStats;
  statsB: ReconHostStats;
}> = ({ statsA, statsB }) => {
  // By-tool union: render every tool that ran in either side.
  const toolKeys = useMemo(
    () => unionKeys(statsA.by_tool, statsB.by_tool, (t) => t.tool_name),
    [statsA.by_tool, statsB.by_tool],
  );
  const toolA = useMemo(
    () => indexBy(statsA.by_tool, (t) => t.tool_name),
    [statsA.by_tool],
  );
  const toolB = useMemo(
    () => indexBy(statsB.by_tool, (t) => t.tool_name),
    [statsB.by_tool],
  );

  // Service union — sort by B's host count desc, then A's, then name.
  const serviceKeys = useMemo(() => {
    const keys = unionKeys(
      statsA.top_services,
      statsB.top_services,
      (s) => s.service_name,
    );
    const aMap = indexBy(statsA.top_services, (s) => s.service_name);
    const bMap = indexBy(statsB.top_services, (s) => s.service_name);
    return keys.sort((x, y) => {
      const bx = bMap.get(x)?.host_count ?? 0;
      const by = bMap.get(y)?.host_count ?? 0;
      if (bx !== by) return by - bx;
      const ax = aMap.get(x)?.host_count ?? 0;
      const ay = aMap.get(y)?.host_count ?? 0;
      if (ax !== ay) return ay - ax;
      return x.localeCompare(y);
    });
  }, [statsA.top_services, statsB.top_services]);

  const serviceA = useMemo(
    () => indexBy(statsA.top_services, (s) => s.service_name),
    [statsA.top_services],
  );
  const serviceB = useMemo(
    () => indexBy(statsB.top_services, (s) => s.service_name),
    [statsB.top_services],
  );

  const hostsDelta = statsB.host_count - statsA.host_count;
  const openHostsDelta =
    statsB.host_count_with_open_ports - statsA.host_count_with_open_ports;

  return (
    <Card className="mb-md">
      <CardContent className="flex flex-col gap-md p-md">
        <h2 className="text-subheading font-semibold">Delta</h2>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="w-28 text-right">Run A</TableHead>
              <TableHead className="w-28 text-right">Run B</TableHead>
              <TableHead className="w-20 text-right">Δ</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>Distinct hosts</TableCell>
              <TableCell className="text-right">
                {statsA.host_count.toLocaleString()}
              </TableCell>
              <TableCell className="text-right">
                {statsB.host_count.toLocaleString()}
              </TableCell>
              <TableCell className="text-right">
                <Delta delta={hostsDelta} />
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Hosts with open ports</TableCell>
              <TableCell className="text-right">
                {statsA.host_count_with_open_ports.toLocaleString()}
              </TableCell>
              <TableCell className="text-right">
                {statsB.host_count_with_open_ports.toLocaleString()}
              </TableCell>
              <TableCell className="text-right">
                <Delta delta={openHostsDelta} />
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>

        {toolKeys.length > 0 && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold uppercase tracking-wide text-muted-foreground">
              By tool
            </h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tool</TableHead>
                  <TableHead className="w-24 text-right">A hosts</TableHead>
                  <TableHead className="w-24 text-right">B hosts</TableHead>
                  <TableHead className="w-20 text-right">Δ</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {toolKeys.map((t) => {
                  const a = toolA.get(t);
                  const b = toolB.get(t);
                  const aHosts = a?.host_count ?? 0;
                  const bHosts = b?.host_count ?? 0;
                  return (
                    <TableRow key={t}>
                      <TableCell className="font-mono">
                        {t}
                        {!a && (
                          <Badge variant="info" className="ml-xxs">
                            new in B
                          </Badge>
                        )}
                        {!b && (
                          <Badge variant="warning" className="ml-xxs">
                            dropped in B
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {aHosts.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        {bHosts.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <Delta delta={bHosts - aHosts} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {serviceKeys.length > 0 && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold uppercase tracking-wide text-muted-foreground">
              Top services
            </h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Service</TableHead>
                  <TableHead className="w-24 text-right">A hosts</TableHead>
                  <TableHead className="w-24 text-right">B hosts</TableHead>
                  <TableHead className="w-20 text-right">Δ</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {serviceKeys.map((svc) => {
                  const a = serviceA.get(svc)?.host_count ?? 0;
                  const b = serviceB.get(svc)?.host_count ?? 0;
                  return (
                    <TableRow key={svc}>
                      <TableCell className="font-mono">{svc}</TableCell>
                      <TableCell className="text-right">
                        {a.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        {b.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <Delta delta={b - a} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <p className="mt-xxs text-caption text-muted-foreground">
              Top 10 by host count per side; union of both lists shown here.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Differing-hosts samples — capped, with deep-links to Inventory.

const HostSampleCard: React.FC<{
  title: string;
  count: number;
  limit: number;
  sample: { host_id: number; ip_address: string; hostname?: string | null }[];
  inventoryHref: string;
  emptyMessage: string;
}> = ({ title, count, limit, sample, inventoryHref, emptyMessage }) => {
  const navigate = useNavigate();
  return (
    <Card>
      <CardContent className="flex flex-col gap-sm p-md">
        <div className="flex items-start justify-between gap-sm">
          <div>
            <h3 className="text-subheading font-semibold">{title}</h3>
            <p className="text-caption text-muted-foreground">
              {count === 0
                ? emptyMessage
                : count <= limit
                  ? `${count.toLocaleString()} host${count === 1 ? '' : 's'}.`
                  : `Showing first ${sample.length.toLocaleString()} of ${count.toLocaleString()}.`}
            </p>
          </div>
          {count > 0 && (
            <Button
              size="sm"
              variant="default"
              onClick={() => navigate(inventoryHref)}
            >
              View in Inventory
              <SquareArrowOutUpRight className="ml-xxs size-3" aria-hidden />
            </Button>
          )}
        </div>
        {count > 0 && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">IP</TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sample.map((h) => (
                  <TableRow key={h.host_id}>
                    <TableCell className="font-mono">{h.ip_address}</TableCell>
                    <TableCell className="truncate">
                      {h.hostname || '—'}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => navigate(`/hosts/${h.host_id}`)}
                      >
                        Open
                        <SquareArrowOutUpRight
                          className="ml-xxs size-3"
                          aria-hidden
                        />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Page

const SAMPLE_LIMIT = 50;

const ReconCompare: React.FC = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const aRaw = params.get('a');
  const bRaw = params.get('b');
  const aId = aRaw ? parseInt(aRaw, 10) : NaN;
  const bId = bRaw ? parseInt(bRaw, 10) : NaN;

  const [detailA, setDetailA] = useState<ReconSessionDetail | null>(null);
  const [detailB, setDetailB] = useState<ReconSessionDetail | null>(null);
  const [diff, setDiff] = useState<ReconSessionDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (Number.isNaN(aId) || Number.isNaN(bId)) {
      setError('Both ?a= and ?b= must be recon session IDs.');
      setLoading(false);
      return;
    }
    if (aId === bId) {
      setError('Choose two different recon sessions to compare.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        // Three calls in parallel — both detail bundles (for the
        // summary cards + per-side stats) plus the server-side diff
        // (for the IP set difference samples).  None of these load
        // the per-host arrays; all three responses are tiny.
        const [respA, respB, respDiff] = await Promise.all([
          getReconSession(aId),
          getReconSession(bId),
          diffReconSessions(aId, bId, SAMPLE_LIMIT),
        ]);
        if (!cancelled) {
          setDetailA(respA);
          setDetailB(respB);
          setDiff(respDiff);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            formatApiError(err, 'Failed to load one or both recon sessions.'),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [aId, bId]);

  // Build inventory deep-links from each detail's scan IDs so "view
  // all" lands on /hosts pre-filtered to that side's scans.
  const inventoryHrefA = useMemo(() => {
    if (!detailA) return '/hosts';
    const ids = detailA.uploads
      .map((u) => u.scan_id)
      .filter((id): id is number => typeof id === 'number');
    return ids.length > 0 ? `/hosts?scan_ids=${ids.join(',')}` : '/hosts';
  }, [detailA]);
  const inventoryHrefB = useMemo(() => {
    if (!detailB) return '/hosts';
    const ids = detailB.uploads
      .map((u) => u.scan_id)
      .filter((id): id is number => typeof id === 'number');
    return ids.length > 0 ? `/hosts?scan_ids=${ids.join(',')}` : '/hosts';
  }, [detailB]);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-xs">
        <Button size="sm" variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-4" aria-hidden /> Back
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-section-title font-semibold">Compare recon runs</h1>
          <p className="text-metadata text-muted-foreground">
            Stats delta + capped samples of hosts unique to each side. Use the
            "View in Inventory" buttons to browse the full host lists.
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

      {detailA && detailB && diff && (
        <>
          <div className="mb-md grid grid-cols-1 gap-sm md:grid-cols-2">
            <SessionCard label="A" detail={detailA} />
            <SessionCard label="B" detail={detailB} />
          </div>

          <StatsDeltaPanel
            statsA={detailA.host_stats}
            statsB={detailB.host_stats}
          />

          <div className="mb-md flex flex-wrap items-center gap-xs">
            <Badge variant="warning">
              {diff.in_a_not_b_count.toLocaleString()} only in A
            </Badge>
            <Badge variant="info">
              {diff.in_b_not_a_count.toLocaleString()} only in B
            </Badge>
            <Badge
              variant="outline"
              className="border-success/40 text-success"
            >
              {diff.shared_count.toLocaleString()} shared
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-md md:grid-cols-2">
            <HostSampleCard
              title="Only in Run A"
              count={diff.in_a_not_b_count}
              limit={diff.limit}
              sample={diff.in_a_not_b_sample}
              inventoryHref={inventoryHrefA}
              emptyMessage="Every host in A is also in B."
            />
            <HostSampleCard
              title="Only in Run B"
              count={diff.in_b_not_a_count}
              limit={diff.limit}
              sample={diff.in_b_not_a_sample}
              inventoryHref={inventoryHrefB}
              emptyMessage="Every host in B is also in A."
            />
          </div>
        </>
      )}
    </div>
  );
};

export default ReconCompare;
