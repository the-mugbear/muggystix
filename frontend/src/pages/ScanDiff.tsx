/**
 * Attack-surface delta between two scans.
 *
 * Answers "what changed since the last scan?" — new/dropped hosts,
 * newly-open vs closed ports, host state flips — reconstructed from the
 * per-scan history tables on the backend (GET /scans/compare).  Result
 * lists are capped server-side; the count badges carry exact totals and
 * "View in Inventory" deep-links into /hosts for the full set.
 *
 * Mirrors ReconCompare.tsx: the page's job is to show *what changed*,
 * not to enumerate every row.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, GitCompareArrows, SquareArrowOutUpRight } from 'lucide-react';
import {
  Scan,
  ScanDiffHostRow,
  ScanDiffHostStateChange,
  ScanDiffPortChange,
  ScanDiffResponse,
  compareScans,
  getScans,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { TableSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Separator } from '../components/ui/separator';
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

// ─────────────────────────────────────────────────────────────────
// Delta helper — positive (B has more) reads as growth of the surface.

const Delta: React.FC<{ delta: number }> = ({ delta }) => {
  if (delta === 0) return <span className="text-caption text-muted-foreground">—</span>;
  const sign = delta > 0 ? '+' : '';
  const tone = delta > 0 ? 'text-warning' : 'text-success';
  return (
    <span className={`font-mono ${tone}`}>
      {sign}
      {delta.toLocaleString()}
    </span>
  );
};

const scanLabel = (s: { id: number; filename: string; created_at?: string | null }): string => {
  const when = s.created_at ? new Date(s.created_at).toLocaleString() : '';
  return `#${s.id} · ${s.filename}${when ? ` · ${when}` : ''}`;
};

// ─────────────────────────────────────────────────────────────────
// Per-side summary card.

const SideCard: React.FC<{ label: string; side: ScanDiffResponse['scan_a'] }> = ({
  label,
  side,
}) => (
  <Card>
    <CardContent className="flex flex-col gap-xs p-md">
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        <Badge>{label}</Badge>
        <span className="text-subheading font-semibold">Scan #{side.scan_id}</span>
        {side.tool_name && <Badge variant="outline">{side.tool_name}</Badge>}
      </div>
      <p className="min-w-0 truncate text-caption text-muted-foreground" title={side.filename}>
        {side.filename}
      </p>
      {side.created_at && (
        <p className="text-caption text-muted-foreground">
          {new Date(side.created_at).toLocaleString()}
        </p>
      )}
      <Separator />
      <div className="flex flex-wrap gap-md text-caption">
        <span>
          <strong>{side.total_hosts.toLocaleString()}</strong> hosts
        </span>
        <span>
          <strong>{side.up_hosts.toLocaleString()}</strong> up
        </span>
        <span>
          <strong>{side.open_ports.toLocaleString()}</strong> open ports
        </span>
      </div>
    </CardContent>
  </Card>
);

// ─────────────────────────────────────────────────────────────────
// Host sample card (new / dropped).

const HostListCard: React.FC<{
  title: string;
  count: number;
  cap: number;
  rows: ScanDiffHostRow[];
  inventoryHref?: string;
  emptyMessage: string;
}> = ({ title, count, cap, rows, inventoryHref, emptyMessage }) => {
  const navigate = useNavigate();
  return (
    <Card>
      <CardContent className="flex flex-col gap-sm p-md">
        <div className="flex items-start justify-between gap-sm">
          <div className="min-w-0">
            <h3 className="text-subheading font-semibold">{title}</h3>
            <p className="text-caption text-muted-foreground">
              {count === 0
                ? emptyMessage
                : count <= cap
                  ? `${count.toLocaleString()} host${count === 1 ? '' : 's'}.`
                  : `Showing first ${rows.length.toLocaleString()} of ${count.toLocaleString()}.`}
            </p>
          </div>
          {count > 0 && inventoryHref && (
            <Button size="sm" variant="default" onClick={() => navigate(inventoryHref)}>
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
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((h) => (
                  <TableRow key={h.host_id}>
                    <TableCell className="font-mono">{h.ip_address}</TableCell>
                    <TableCell className="max-w-0 truncate" title={h.hostname || undefined}>
                      {h.hostname || '—'}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => navigate(`/hosts/${h.host_id}`)}
                      >
                        Open
                        <SquareArrowOutUpRight className="ml-xxs size-3" aria-hidden />
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
// Port-change card (newly-open / closed).

const PortListCard: React.FC<{
  title: string;
  subtitle: string;
  count: number;
  cap: number;
  rows: ScanDiffPortChange[];
  emptyMessage: string;
}> = ({ title, subtitle, count, cap, rows, emptyMessage }) => {
  const navigate = useNavigate();
  return (
    <Card>
      <CardContent className="flex flex-col gap-sm p-md">
        <div className="min-w-0">
          <h3 className="text-subheading font-semibold">{title}</h3>
          <p className="text-caption text-muted-foreground">
            {count === 0
              ? emptyMessage
              : count <= cap
                ? `${count.toLocaleString()} port${count === 1 ? '' : 's'} — ${subtitle}`
                : `Showing first ${rows.length.toLocaleString()} of ${count.toLocaleString()} — ${subtitle}`}
          </p>
        </div>
        {count > 0 && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">Host</TableHead>
                  <TableHead className="w-28">Port</TableHead>
                  <TableHead>Service</TableHead>
                  <TableHead className="w-32">A → B</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={`${r.host_id}-${r.port_number}-${r.protocol}`}>
                    <TableCell className="font-mono">{r.ip_address}</TableCell>
                    <TableCell className="font-mono">
                      {r.port_number}/{r.protocol || '—'}
                    </TableCell>
                    <TableCell className="max-w-0 truncate" title={r.service_name || undefined}>
                      {r.service_name || '—'}
                    </TableCell>
                    <TableCell className="text-caption text-muted-foreground">
                      {(r.state_a || '∅')} → {(r.state_b || '∅')}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => navigate(`/hosts/${r.host_id}`)}
                      >
                        Open
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
// Host state-change card.

const HostStateCard: React.FC<{
  count: number;
  cap: number;
  rows: ScanDiffHostStateChange[];
}> = ({ count, cap, rows }) => {
  const navigate = useNavigate();
  if (count === 0) return null;
  return (
    <Card className="mb-md">
      <CardContent className="flex flex-col gap-sm p-md">
        <div>
          <h3 className="text-subheading font-semibold">Host state changes</h3>
          <p className="text-caption text-muted-foreground">
            {count <= cap
              ? `${count.toLocaleString()} host${count === 1 ? '' : 's'} changed up/down state.`
              : `Showing first ${rows.length.toLocaleString()} of ${count.toLocaleString()}.`}
          </p>
        </div>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-40">IP</TableHead>
                <TableHead>Hostname</TableHead>
                <TableHead className="w-32">A → B</TableHead>
                <TableHead className="w-16" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((h) => (
                <TableRow key={h.host_id}>
                  <TableCell className="font-mono">{h.ip_address}</TableCell>
                  <TableCell className="max-w-0 truncate" title={h.hostname || undefined}>
                    {h.hostname || '—'}
                  </TableCell>
                  <TableCell className="text-caption text-muted-foreground">
                    {(h.state_a || '∅')} → {(h.state_b || '∅')}
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="ghost" onClick={() => navigate(`/hosts/${h.host_id}`)}>
                      Open
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Scan picker — shown when ?a=/?b= are missing or invalid.

const ScanPicker: React.FC = () => {
  const navigate = useNavigate();
  const [scans, setScans] = useState<Scan[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aSel, setASel] = useState<string>('');
  const [bSel, setBSel] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    getScans(0, 200)
      .then((rows) => {
        if (!cancelled) setScans(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Failed to load scans.'));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const canCompare = aSel && bSel && aSel !== bSel;

  return (
    <Card className="mx-auto max-w-2xl">
      <CardContent className="flex flex-col gap-md p-md">
        <div>
          <h2 className="text-subheading font-semibold">Pick two scans to compare</h2>
          <p className="text-caption text-muted-foreground">
            A is the baseline (earlier); B is the comparison (later). The delta shows what changed
            from A to B.
          </p>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {scans && scans.length < 2 ? (
          <p className="text-metadata text-muted-foreground">
            This project needs at least two scans to compare.
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-xs">
              <label className="text-metadata font-medium">Baseline (A)</label>
              <Select value={aSel} onValueChange={setASel} disabled={!scans}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select baseline scan" />
                </SelectTrigger>
                <SelectContent>
                  {(scans || []).map((s) => (
                    <SelectItem key={s.id} value={String(s.id)}>
                      {scanLabel(s)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-xs">
              <label className="text-metadata font-medium">Comparison (B)</label>
              <Select value={bSel} onValueChange={setBSel} disabled={!scans}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select comparison scan" />
                </SelectTrigger>
                <SelectContent>
                  {(scans || []).map((s) => (
                    <SelectItem key={s.id} value={String(s.id)}>
                      {scanLabel(s)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {aSel && bSel && aSel === bSel && (
              <p className="text-caption text-destructive">Choose two different scans.</p>
            )}
            <div>
              <Button
                disabled={!canCompare}
                onClick={() => navigate(`/scans/compare?a=${aSel}&b=${bSel}`)}
              >
                <GitCompareArrows className="size-4" aria-hidden />
                Compare
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Page

const ScanDiff: React.FC = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const aRaw = params.get('a');
  const bRaw = params.get('b');
  const aId = aRaw ? parseInt(aRaw, 10) : NaN;
  const bId = bRaw ? parseInt(bRaw, 10) : NaN;
  const haveParams = !Number.isNaN(aId) && !Number.isNaN(bId) && aId !== bId;

  const [diff, setDiff] = useState<ScanDiffResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!haveParams) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await compareScans(aId, bId);
        if (!cancelled) setDiff(resp);
      } catch (err) {
        if (!cancelled) setError(formatApiError(err, 'Failed to compare the selected scans.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [haveParams, aId, bId]);

  const hostsDelta = diff ? diff.scan_b.total_hosts - diff.scan_a.total_hosts : 0;
  const upDelta = diff ? diff.scan_b.up_hosts - diff.scan_a.up_hosts : 0;
  const portsDelta = diff ? diff.scan_b.total_ports - diff.scan_a.total_ports : 0;
  const openDelta = diff ? diff.scan_b.open_ports - diff.scan_a.open_ports : 0;

  const newHostsHref = useMemo(
    () => (diff ? `/hosts?scan_ids=${diff.scan_b.scan_id}` : undefined),
    [diff],
  );
  const droppedHostsHref = useMemo(
    () => (diff ? `/hosts?scan_ids=${diff.scan_a.scan_id}` : undefined),
    [diff],
  );

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-xs">
        <Button size="sm" variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-4" aria-hidden /> Back
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-section-title font-semibold">Compare scans</h1>
          <p className="text-metadata text-muted-foreground">
            Attack-surface delta between two scans — new and dropped hosts, port openness changes,
            and host state flips.
          </p>
        </div>
        {haveParams && (
          <Button size="sm" variant="ghost" onClick={() => navigate('/scans/compare')}>
            <GitCompareArrows className="size-4" aria-hidden />
            Pick different scans
          </Button>
        )}
      </div>

      {!haveParams && <ScanPicker />}

      {haveParams && loading && (
        <>
          <div className="mb-md grid grid-cols-1 gap-md md:grid-cols-2">
            <div className="h-40 rounded-panel bg-muted/40 animate-pulse" />
            <div className="h-40 rounded-panel bg-muted/40 animate-pulse" />
          </div>
          <TableSkeleton rows={4} columns={4} />
        </>
      )}

      {haveParams && error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {haveParams && diff && !loading && (
        <>
          <div className="mb-md grid grid-cols-1 gap-sm md:grid-cols-2">
            <SideCard label="A" side={diff.scan_a} />
            <SideCard label="B" side={diff.scan_b} />
          </div>

          <Card className="mb-md">
            <CardContent className="flex flex-col gap-md p-md">
              <h2 className="text-subheading font-semibold">Delta</h2>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Metric</TableHead>
                    <TableHead className="w-28 text-right">A</TableHead>
                    <TableHead className="w-28 text-right">B</TableHead>
                    <TableHead className="w-20 text-right">Δ</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell>Hosts observed</TableCell>
                    <TableCell className="text-right">{diff.scan_a.total_hosts.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{diff.scan_b.total_hosts.toLocaleString()}</TableCell>
                    <TableCell className="text-right"><Delta delta={hostsDelta} /></TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Hosts up</TableCell>
                    <TableCell className="text-right">{diff.scan_a.up_hosts.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{diff.scan_b.up_hosts.toLocaleString()}</TableCell>
                    <TableCell className="text-right"><Delta delta={upDelta} /></TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Ports observed</TableCell>
                    <TableCell className="text-right">{diff.scan_a.total_ports.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{diff.scan_b.total_ports.toLocaleString()}</TableCell>
                    <TableCell className="text-right"><Delta delta={portsDelta} /></TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Open ports</TableCell>
                    <TableCell className="text-right">{diff.scan_a.open_ports.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{diff.scan_b.open_ports.toLocaleString()}</TableCell>
                    <TableCell className="text-right"><Delta delta={openDelta} /></TableCell>
                  </TableRow>
                </TableBody>
              </Table>
              <div className="flex flex-wrap items-center gap-xs">
                <Badge variant="info">{diff.counts.new_hosts.toLocaleString()} new hosts</Badge>
                <Badge variant="warning">{diff.counts.dropped_hosts.toLocaleString()} dropped hosts</Badge>
                <Badge variant="info">{diff.counts.newly_open_ports.toLocaleString()} newly-open ports</Badge>
                <Badge variant="warning">{diff.counts.closed_ports.toLocaleString()} closed ports</Badge>
                <Badge variant="outline">{diff.counts.host_state_changes.toLocaleString()} state changes</Badge>
              </div>
            </CardContent>
          </Card>

          <HostStateCard count={diff.counts.host_state_changes} cap={diff.row_cap} rows={diff.host_state_changes} />

          <div className="mb-md grid grid-cols-1 gap-md md:grid-cols-2">
            <HostListCard
              title="New hosts (in B, not A)"
              count={diff.counts.new_hosts}
              cap={diff.row_cap}
              rows={diff.new_hosts}
              inventoryHref={newHostsHref}
              emptyMessage="No hosts appeared that weren't already in A."
            />
            <HostListCard
              title="Dropped hosts (in A, not B)"
              count={diff.counts.dropped_hosts}
              cap={diff.row_cap}
              rows={diff.dropped_hosts}
              inventoryHref={droppedHostsHref}
              emptyMessage="Every host in A was also observed in B."
            />
          </div>

          <div className="grid grid-cols-1 gap-md md:grid-cols-2">
            <PortListCard
              title="Newly-open ports"
              subtitle="open in B, not open (or absent) in A"
              count={diff.counts.newly_open_ports}
              cap={diff.row_cap}
              rows={diff.newly_open_ports}
              emptyMessage="No ports newly opened in B."
            />
            <PortListCard
              title="Closed ports"
              subtitle="open in A, not open (or absent) in B"
              count={diff.counts.closed_ports}
              cap={diff.row_cap}
              rows={diff.closed_ports}
              emptyMessage="No ports closed since A."
            />
          </div>
        </>
      )}
    </div>
  );
};

export default ScanDiff;
