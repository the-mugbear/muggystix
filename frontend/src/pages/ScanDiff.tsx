/**
 * Compare scans — what changed between a baseline scan (A) and a later one
 * (B): hosts B saw that A did not and the reverse, ports that opened or
 * changed, hosts whose up / down state flipped.  Reconstructed on the server
 * from the per-scan history (`GET /scans/compare`).
 *
 * 5.410.0 (comprehensive review 2026-10-16, C1 / A8): the Posture layout
 * (a lead, a strip of measures, sections over thin rules — style guide §7),
 * where it was the one data page on cards.  Every list is the first
 * `row_cap` rows by address, and says so against its exact count.  "New
 * hosts" and "Dropped hosts" open their EXACT list on Hosts — the Hosts
 * language's `scan:<B> AND NOT scan:<A>` and the reverse, the condition the
 * server counts with (they opened every host of one scan).  Every Open and
 * View is a link (middle-click, copy), and a failure says so with Retry.
 */
import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, GitCompareArrows } from 'lucide-react';

import {
  compareScans,
  getScans,
  type Scan,
  type ScanDiffHostRow,
  type ScanDiffHostStateChange,
  type ScanDiffPortChange,
  type ScanDiffResponse,
  type ScanDiffSide,
} from '../services/api';
import PostureLead from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { formatTimestamp } from '../utils/relativeTime';

export const COMPARE_PATH = '/scans/compare';
/** The picker lists this many scans, newest first. */
export const PICKER_LIMIT = 200;

/** Hosts B saw and A did not (`present` = B, `absent` = A), as the Hosts
 *  list — the same condition the server counts with. */
export const hostsOnlyIn = (present: number, absent: number): string =>
  buildHostsUrl({ q: `scan:${present} AND NOT scan:${absent}` });

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

const scanLabel = (s: { id: number; filename: string; created_at?: string | null }): string => {
  const when = formatTimestamp(s.created_at, '');
  return `#${s.id} · ${s.filename}${when ? ` · ${when}` : ''}`;
};

/** "N hosts." or "The first 500 of 1,234, by address." */
const shownOf = (count: number, shown: number, noun: string, many?: string) =>
  shown < count
    ? `The first ${shown.toLocaleString()} of ${count.toLocaleString()}, by address.`
    : `${plural(count, noun, many)}.`;

const FailedRead: React.FC<{ message: string; onRetry: () => void }> = ({ message, onRetry }) => (
  <p role="alert" className="flex flex-wrap items-center gap-xs text-metadata text-destructive">
    <span className="min-w-0 break-words">{message}</span>
    <Button variant="ghost" size="sm" onClick={onRetry}>Retry</Button>
  </p>
);

const OpenHost: React.FC<{ hostId: number }> = ({ hostId }) => (
  <Link to={`/hosts/${hostId}`} className="text-caption text-primary hover:underline">Open</Link>
);

// ─────────────────────────────────────────────────────────────────
// The two scans, side by side, and the difference.

/** B minus A, or null when either side recorded no figure. */
const minus = (b: number | null, a: number | null): number | null => (a == null || b == null ? null : b - a);

const Delta: React.FC<{ delta: number | null }> = ({ delta }) => {
  if (delta == null || delta === 0) return <span className="text-muted-foreground">—</span>;
  // More in B reads as a larger surface.
  return (
    <span className={`font-mono ${delta > 0 ? 'text-warning' : 'text-success'}`}>
      {delta > 0 ? '+' : ''}{delta.toLocaleString()}
    </span>
  );
};

const SideRow: React.FC<{ label: string; side: ScanDiffSide }> = ({ label, side }) => (
  <TableRow>
    <TableCell className="min-w-0">
      <span className="flex min-w-0 items-baseline gap-xs">
        <span className="shrink-0 font-semibold">{label}</span>
        <Link to={`/scans/${side.scan_id}`} className="min-w-0 truncate text-primary hover:underline" title={side.filename}>
          #{side.scan_id} · {side.filename}
        </Link>
      </span>
      <span className="block truncate text-caption text-muted-foreground">
        {[side.tool_name, side.scan_type, formatTimestamp(side.created_at, '')].filter(Boolean).join(' · ') || '—'}
      </span>
    </TableCell>
    <TableCell className="text-right">{side.total_hosts.toLocaleString()}</TableCell>
    {/* Null: the scan recorded no host's state (a DNS import) — not "0 up". */}
    <TableCell className="text-right" title={side.up_hosts == null ? 'This scan did not record whether hosts were up' : undefined}>
      {side.up_hosts == null ? '—' : side.up_hosts.toLocaleString()}
    </TableCell>
    <TableCell className="text-right">{side.total_ports.toLocaleString()}</TableCell>
    <TableCell className="text-right">{side.open_ports.toLocaleString()}</TableCell>
  </TableRow>
);

const Sides: React.FC<{ diff: ScanDiffResponse }> = ({ diff }) => {
  const { scan_a: a, scan_b: b } = diff;
  return (
    <PostureSection title="The two scans" description="What each scan observed; the last row is B minus A.">
      <div className="overflow-x-auto">
        <Table className="min-w-[44rem]" style={{ tableLayout: 'fixed' }} data-testid="compare-sides">
          <TableHeader>
            <TableRow>
              <TableHead>Scan</TableHead>
              <TableHead className="w-28 text-right">Hosts</TableHead>
              <TableHead className="w-28 text-right">Up</TableHead>
              <TableHead className="w-28 text-right">Ports</TableHead>
              <TableHead className="w-28 text-right">Open ports</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <SideRow label="A" side={a} />
            <SideRow label="B" side={b} />
            <TableRow>
              <TableCell className="text-muted-foreground">Change</TableCell>
              <TableCell className="text-right"><Delta delta={b.total_hosts - a.total_hosts} /></TableCell>
              <TableCell className="text-right"><Delta delta={minus(b.up_hosts, a.up_hosts)} /></TableCell>
              <TableCell className="text-right"><Delta delta={b.total_ports - a.total_ports} /></TableCell>
              <TableCell className="text-right"><Delta delta={b.open_ports - a.open_ports} /></TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </PostureSection>
  );
};

// ─────────────────────────────────────────────────────────────────
// Lists.

const HostSection: React.FC<{
  title: string;
  count: number;
  rows: ScanDiffHostRow[];
  /** The Hosts list of exactly these hosts. */
  allHref: string;
  empty: string;
  testId: string;
}> = ({ title, count, rows, allHref, empty, testId }) => (
  <PostureSection
    title={<>{title} <SectionCount>{count.toLocaleString()}</SectionCount></>}
    description={count === 0 ? empty : shownOf(count, rows.length, 'host')}
    actions={count > 0 ? (
      <Button asChild size="sm" variant="outline">
        <Link to={allHref} data-testid={`${testId}-all`}>{`All ${plural(count, 'host')} on Hosts`}</Link>
      </Button>
    ) : undefined}
  >
    {count > 0 && (
      <div className="overflow-x-auto">
        <Table className="min-w-[32rem]" style={{ tableLayout: 'fixed' }} data-testid={testId}>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Address</TableHead>
              <TableHead>Host name</TableHead>
              <TableHead className="w-20"><span className="sr-only">Open</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((h) => (
              <TableRow key={h.host_id}>
                <TableCell className="font-mono">{h.ip_address}</TableCell>
                <TableCell className="truncate" title={h.hostname || undefined}>{h.hostname || '—'}</TableCell>
                <TableCell className="text-right"><OpenHost hostId={h.host_id} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )}
  </PostureSection>
);

const StateSection: React.FC<{ count: number; rows: ScanDiffHostStateChange[] }> = ({ count, rows }) => (
  <PostureSection
    title={<>Up / down changes <SectionCount>{count.toLocaleString()}</SectionCount></>}
    description={count === 0 ? 'No host seen by both scans changed state.' : shownOf(count, rows.length, 'host')}
  >
    {count > 0 && (
      <div className="overflow-x-auto">
        <Table className="min-w-[36rem]" style={{ tableLayout: 'fixed' }}>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Address</TableHead>
              <TableHead>Host name</TableHead>
              <TableHead className="w-36">A → B</TableHead>
              <TableHead className="w-20"><span className="sr-only">Open</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((h) => (
              <TableRow key={h.host_id}>
                <TableCell className="font-mono">{h.ip_address}</TableCell>
                <TableCell className="truncate" title={h.hostname || undefined}>{h.hostname || '—'}</TableCell>
                <TableCell className="text-caption text-muted-foreground">{h.state_a || '—'} → {h.state_b || '—'}</TableCell>
                <TableCell className="text-right"><OpenHost hostId={h.host_id} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )}
  </PostureSection>
);

const PortSection: React.FC<{
  title: string;
  /** What the rows are, in one line (after the count). */
  meaning: string;
  count: number;
  rows: ScanDiffPortChange[];
  empty: string;
}> = ({ title, meaning, count, rows, empty }) => (
  <PostureSection
    title={<>{title} <SectionCount>{count.toLocaleString()}</SectionCount></>}
    description={count === 0 ? empty : `${shownOf(count, rows.length, 'port')} ${meaning}`}
  >
    {count > 0 && (
      <div className="overflow-x-auto">
        <Table className="min-w-[40rem]" style={{ tableLayout: 'fixed' }}>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Address</TableHead>
              <TableHead className="w-28">Port</TableHead>
              <TableHead>Service</TableHead>
              <TableHead className="w-40">A → B</TableHead>
              <TableHead className="w-20"><span className="sr-only">Open</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={`${r.host_id}-${r.port_number}-${r.protocol}`}>
                <TableCell className="font-mono">{r.ip_address}</TableCell>
                <TableCell className="font-mono">{r.port_number}/{r.protocol || '—'}</TableCell>
                <TableCell className="truncate" title={r.service_name || undefined}>{r.service_name || '—'}</TableCell>
                <TableCell className="text-caption text-muted-foreground">
                  {r.state_a || 'not seen'} → {r.state_b || 'not seen'}
                </TableCell>
                <TableCell className="text-right"><OpenHost hostId={r.host_id} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )}
  </PostureSection>
);

// ─────────────────────────────────────────────────────────────────
// The answer.

const Comparison: React.FC<{ diff: ScanDiffResponse }> = ({ diff }) => {
  const a = diff.scan_a.scan_id;
  const b = diff.scan_b.scan_id;
  const c = diff.counts;
  const changed = c.new_hosts + c.dropped_hosts + c.newly_open_ports + c.closed_ports + c.host_state_changes;
  return (
    <div className="flex min-w-0 flex-col gap-lg">
      <PostureLead
        tone={changed ? 'info' : 'neutral'}
        restsOn={`A is scan #${a}, B is scan #${b}. Each list shows the first ${diff.row_cap.toLocaleString()} rows by address; the counts are exact.`}
      >
        {changed
          ? `B saw ${plural(c.new_hosts, 'host')} A did not and missed ${c.dropped_hosts.toLocaleString()} A saw; ${plural(c.newly_open_ports, 'port')} newly open.`
          : 'Nothing changed between the two scans on the hosts and ports both observed.'}
      </PostureLead>

      <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x" data-testid="compare-measures">
        <PostureMeasure
          label="New hosts"
          value={c.new_hosts.toLocaleString()}
          to={hostsOnlyIn(b, a)}
          toLabel="Show the hosts B saw that A did not"
          info="Hosts scan B observed and scan A did not. New since A — a host an earlier scan found counts too."
        >
          <p>in B, not in A</p>
        </PostureMeasure>
        <PostureMeasure
          label="Dropped hosts"
          value={c.dropped_hosts.toLocaleString()}
          to={hostsOnlyIn(a, b)}
          toLabel="Show the hosts A saw that B did not"
          info="Hosts scan A observed and scan B did not. Not seen is not gone: B may have covered fewer targets."
        >
          <p>in A, not in B</p>
        </PostureMeasure>
        <PostureMeasure
          label="Newly open ports"
          value={c.newly_open_ports.toLocaleString()}
          info="Ports open in B that were not open (or not seen) in A."
        >
          <p>{plural(c.closed_ports, 'port')} open in A changed or unclear in B</p>
        </PostureMeasure>
        <PostureMeasure
          label="Up / down changes"
          value={c.host_state_changes.toLocaleString()}
          info="Hosts both scans observed whose up / down state differs."
        >
          <p>of the hosts both scans saw</p>
        </PostureMeasure>
      </div>

      <Sides diff={diff} />

      <HostSection
        title="New hosts (in B, not in A)"
        count={c.new_hosts}
        rows={diff.new_hosts}
        allHref={hostsOnlyIn(b, a)}
        empty="B saw no host that A did not."
        testId="compare-new-hosts"
      />
      <HostSection
        title="Dropped hosts (in A, not in B)"
        count={c.dropped_hosts}
        rows={diff.dropped_hosts}
        allHref={hostsOnlyIn(a, b)}
        empty="B saw every host A saw."
        testId="compare-dropped-hosts"
      />
      <StateSection count={c.host_state_changes} rows={diff.host_state_changes} />
      <PortSection
        title="Newly open ports"
        meaning="Open in B; not open, or not seen, in A."
        count={c.newly_open_ports}
        rows={diff.newly_open_ports}
        empty="No port is newly open in B."
      />
      {/* v5.204.2 — B's observation can be closed, filtered, or an
          inconclusive state like open|filtered, and this list holds all of
          them: neither the title nor the count may claim the port is not
          open; the A → B column says what B saw. */}
      <PortSection
        title="Different or unclear in B"
        meaning="Open in A; B saw something else — closed, filtered or inconclusive (A → B says which)."
        count={c.closed_ports}
        rows={diff.closed_ports}
        empty="Every port open in A that B tested was still open."
      />
      {/* v5.204.0 — split out of "closed": a port B never probed (a narrower
          range, another tool, a host outside B's targets) is unknown, never
          evidence that it was closed. */}
      <PortSection
        title="Not tested in B"
        meaning="Open in A; B has no observation of the port — its state is unknown, not closed."
        count={c.not_observed_ports}
        rows={diff.not_observed_ports}
        empty="B observed every port that was open in A."
      />
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────
// The picker — when the address names no two different scans.

const ScanPicker: React.FC = () => {
  const navigate = useNavigate();
  const projectId = useProjectId();
  // The scans chosen, kept whole: a search that no longer lists one must not
  // take it out of its picker (review 2026-10-21, B3).
  const [aScan, setAScan] = useState<Scan | null>(null);
  const [bScan, setBScan] = useState<Scan | null>(null);
  // The newest PICKER_LIMIT scans, or the newest that match the search — the
  // Scans page's own search (file name, tool…), on the server, so a scan
  // older than the newest 200 can be found and compared.
  const [find, setFind] = useState('');
  const search = useDebouncedValue(find.trim(), 300);
  const list = useQuery({
    queryKey: ['getScans', projectId, { search }, { limit: PICKER_LIMIT }],
    queryFn: ({ signal }) => getScans(projectId, 0, PICKER_LIMIT, { search: search || undefined, signal }),
    placeholderData: keepPreviousData,
  });
  const scans: Scan[] | null = list.data ?? null;
  const aSel = aScan ? String(aScan.id) : '';
  const bSel = bScan ? String(bScan.id) : '';
  const canCompare = !!aSel && !!bSel && aSel !== bSel;

  const pick = (
    id: string, label: string, chosen: Scan | null, onChange: (s: Scan | null) => void, placeholder: string,
  ) => {
    const options = [...(chosen && !(scans ?? []).some((s) => s.id === chosen.id) ? [chosen] : []), ...(scans ?? [])];
    return (
      <div className="flex min-w-0 flex-col gap-xxs">
        <Label htmlFor={id}>{label}</Label>
        <Select value={chosen ? String(chosen.id) : ''} disabled={!scans}
          onValueChange={(v) => onChange(options.find((s) => String(s.id) === v) ?? null)}>
          <SelectTrigger id={id} className="w-full"><SelectValue placeholder={placeholder} /></SelectTrigger>
          <SelectContent>
            {options.map((s) => (
              <SelectItem key={s.id} value={String(s.id)}>{scanLabel(s)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  };

  const capped = !!scans && scans.length >= PICKER_LIMIT;
  return (
    <PostureSection
      title="Pick two scans"
      description={`A is the baseline (earlier), B the comparison (later).${capped
        ? ` The lists hold the newest ${PICKER_LIMIT.toLocaleString()} scans${search ? ' that match' : ''} — search to find an older one.`
        : ''}`}
    >
      {list.isError ? (
        <FailedRead message={queryErrorText(list.error, 'Could not load the scans.') ?? 'Could not load the scans.'} onRetry={() => void list.refetch()} />
      ) : scans && !search && scans.length < 2 ? (
        <p className="text-metadata text-muted-foreground">This project needs at least two scans to compare.</p>
      ) : (
        <div className="flex max-w-2xl flex-col gap-sm" data-testid="compare-picker">
          <div className="flex min-w-0 flex-col gap-xxs">
            <Label htmlFor="compare-find">Find a scan</Label>
            <Input id="compare-find" type="search" value={find} onChange={(e) => setFind(e.target.value)}
              placeholder="File name or tool — narrows both lists" />
            {search && scans && scans.length === 0 && (
              <p className="text-caption text-muted-foreground">No scan matches “{search}”.</p>
            )}
          </div>
          {pick('compare-a', 'Baseline (A)', aScan, setAScan, 'Choose the baseline scan')}
          {pick('compare-b', 'Comparison (B)', bScan, setBScan, 'Choose the comparison scan')}
          {aSel && bSel && aSel === bSel && <p className="text-caption text-destructive">Choose two different scans.</p>}
          <div>
            <Button disabled={!canCompare} onClick={() => navigate(`${COMPARE_PATH}?a=${aSel}&b=${bSel}`)}>
              <GitCompareArrows className="size-4" aria-hidden /> Compare
            </Button>
          </div>
        </div>
      )}
    </PostureSection>
  );
};

// ─────────────────────────────────────────────────────────────────
// Page

const ScanDiff: React.FC = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const aId = Number.parseInt(params.get('a') ?? '', 10);
  const bId = Number.parseInt(params.get('b') ?? '', 10);
  const haveParams = !Number.isNaN(aId) && !Number.isNaN(bId) && aId !== bId;

  const projectId = useProjectId();
  const comparison = useQuery({
    queryKey: ['compareScans', projectId, aId, bId],
    queryFn: ({ signal }) => compareScans(projectId, aId, bId, signal),
    enabled: haveParams,
  });

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      <div className="flex flex-wrap items-start gap-xs">
        <Button size="sm" variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-4" aria-hidden /> Back
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Compare scans</h1>
          <p className="text-metadata text-muted-foreground">
            What changed from one scan to another: hosts, open ports and up / down state.
          </p>
        </div>
        {haveParams && (
          <Button asChild size="sm" variant="ghost">
            <Link to={COMPARE_PATH}><GitCompareArrows className="size-4" aria-hidden /> Pick different scans</Link>
          </Button>
        )}
      </div>

      {!haveParams ? (
        <ScanPicker />
      ) : comparison.isError ? (
        <FailedRead
          message={queryErrorText(comparison.error, 'Could not compare the two scans.') ?? 'Could not compare the two scans.'}
          onRetry={() => void comparison.refetch()}
        />
      ) : !comparison.data ? (
        <p role="status" className="text-metadata text-muted-foreground">Comparing the two scans…</p>
      ) : (
        <Comparison diff={comparison.data} />
      )}
    </div>
  );
};

export default ScanDiff;
