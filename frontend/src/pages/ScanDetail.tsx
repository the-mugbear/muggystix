import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Computer, Shield, Terminal, ExternalLink, RefreshCw, Upload, Globe } from 'lucide-react';
import { getScan, getScans, getHostsByScan, getScanDnsRecords, getScanHostSnapshots, getScanPorts } from '../services/api';
import type { ScanHost, DNSRecord, Scan as ScanSummaryRow, ScanHostSnapshot, ScanPortObservation } from '../services/api';
import { usePagedList, type PagedList } from '../hooks/usePagedList';
import type { ListPage } from '../hooks/useListQuery';
import { useUrlPage } from '../hooks/useUrlPage';
import { PagedFooter, UnavailableLine } from '../components/operations/QueueParts';
import { TableSkeleton } from '../components/PageSkeleton';
import ImportResult from '../components/scans/ImportResult';
import CommandExplanation from '../components/CommandExplanation';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import SeverityBar from '../components/ui/SeverityBar';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { formatHostForUrl } from '../utils/webLinks';
import { webScheme, type WebPortLike } from '../utils/webPort';
import { ScanTimeSourceNote } from '../components/scans/ScanTimeCells';
import ScannedPorts from '../components/scans/ScannedPorts';
import { describeScanRun, describeUpload } from '../utils/scanTime';

type BadgeTone = 'success' | 'destructive' | 'warning' | 'muted';
const hostStateVariant = (s: string | null): BadgeTone =>
  s === 'up' ? 'success' : s === 'down' ? 'destructive' : 'muted';
const portStateVariant = (s: string | null): BadgeTone =>
  s === 'open' ? 'success' : s === 'closed' ? 'destructive' : s === 'filtered' ? 'warning' : 'muted';

// A port's web link: `utils/webPort` decides whether it is a web port and
// which scheme (the one rule, the server's web-targets rule — R1d).  The URL
// always carries the port, so a host with both 80 and 443 shows two visibly
// different links.
const getWebUrl = (ip: string, port: WebPortLike): string | null => {
  const scheme = webScheme(port);
  return scheme ? `${scheme}://${formatHostForUrl(ip)}:${port.port_number}` : null;
};

const WebLinkButton: React.FC<{ url: string }> = ({ url }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => window.open(url, '_blank', 'noopener,noreferrer')}
        aria-label={`Open ${url} in new tab`}
      >
        <ExternalLink className="size-3.5 text-primary" aria-hidden />
      </Button>
    </TooltipTrigger>
    <TooltipContent>Open {url}</TooltipContent>
  </Tooltip>
);


/** Rows a page of each of the page's lists. */
export const SCAN_DETAIL_PAGE_SIZE = 50;

const TABS = ['hosts', 'ports', 'dns', 'command'] as const;
type ScanTab = typeof TABS[number];

const ScanDetail: React.FC = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const fromHost = (location.state as { fromHost?: { id: number; ip: string } } | null)?.fromHost;
  // The tab and the Hosts tab's view are the address's (review 2026-10-16,
  // A9): Back from a host returns to them, and a link can name them.  The
  // view defaults to the scan's own record — this page is a scan artifact,
  // and rendering current inventory under "this scan recorded" is what made
  // it unreliable as evidence (v5.184.0).
  const [params, setParams] = useSearchParams();
  const tab: ScanTab = (TABS as readonly string[]).includes(params.get('tab') ?? '') ? params.get('tab') as ScanTab : 'hosts';
  const hostView: 'as_scanned' | 'current' = params.get('view') === 'current' ? 'current' : 'as_scanned';
  // One address write per choice; a new tab or view starts on its first page.
  const choose = (next: { tab?: ScanTab; view?: 'as_scanned' | 'current' }) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      out.delete('page');
      if (next.tab) {
        if (next.tab === 'hosts') out.delete('tab'); else out.set('tab', next.tab);
      }
      if (next.view) {
        if (next.view === 'current') out.set('view', 'current'); else out.delete('view');
      }
      return out;
    }, { replace: true });
  };

  // The page is the scan, the names it resolved and its summary row, shown
  // together; each tab's list is paged and read when it is open.
  const id = scanId ? parseInt(scanId) : NaN;
  const known = !Number.isNaN(id);
  const projectId = useProjectId();
  const scanQuery = useQuery({ queryKey: ['getScan', projectId, id], queryFn: ({ signal }) => getScan(projectId, id, signal), enabled: known });
  const dnsQuery = useQuery({ queryKey: ['getScanDnsRecords', projectId, id], queryFn: ({ signal }) => getScanDnsRecords(projectId, id, signal), enabled: known });
  // v5.222.0 — the inventory's per-scan summary (hosts added, conflicts,
  // import quality) for the Import result card; not fatal if it fails.
  const summaryQuery = useQuery({
    queryKey: ['getScans', projectId, { ids: [id] }, { limit: 1 }],
    queryFn: ({ signal }) => getScans(projectId, 0, 1, { ids: [id], signal }),
    enabled: known,
  });
  const required = [scanQuery, dnsQuery];
  const failed = required.find((q) => q.isError);
  const error = queryErrorText(failed?.error, 'Failed to load scan details.');
  // The summary is waited for too, so the page appears whole.
  const loading = !error && (required.some((q) => q.isPending) || summaryQuery.isPending);
  const retry = () => {
    for (const q of [...required, summaryQuery]) void q.refetch();
  };

  const scan = scanQuery.data ?? null;
  const dnsRecords: DNSRecord[] = dnsQuery.data?.items ?? [];
  const dnsTotal = dnsQuery.data?.total ?? 0;
  const summaryRow: ScanSummaryRow | null = summaryQuery.data?.[0] ?? null;
  // The scan's own counts (review 2026-10-16, A7): the page no longer loads
  // a thousand hosts and their ports to count them.
  const totalHostCount = scan?.total_hosts ?? 0;

  const urlPage = useUrlPage();
  const listOptions = (open: boolean) => ({
    pageSize: SCAN_DETAIL_PAGE_SIZE, page: urlPage, within: projectId, enabled: known && !!scan && open,
  });
  const snapshotList = usePagedList<ScanHostSnapshot>(
    'getScanHostSnapshots',
    ({ offset, limit, signal }) => getScanHostSnapshots(projectId, id, { offset, limit }, signal),
    [projectId, id],
    listOptions(tab === 'hosts' && hostView === 'as_scanned'),
  );
  // The hosts' current records: the route answers a bare page, and the
  // scan's host count is how many there are (one row each).  The count is
  // not a dep: the list starts only once the scan has loaded, so it is
  // settled by the first read, and a dep that changed as the scan arrived
  // sent an address that names a page back to the first.
  const currentList = usePagedList<ScanHost>(
    'getHostsByScan',
    async ({ offset, limit, signal }) => ({
      items: await getHostsByScan(projectId, id, { skip: offset, limit }, signal),
      total: totalHostCount,
    }),
    [projectId, id],
    listOptions(tab === 'hosts' && hostView === 'current'),
  );
  const portList = usePagedList<ScanPortObservation>(
    'getScanPorts',
    ({ offset, limit, signal }) => getScanPorts(projectId, id, { offset, limit }, signal),
    [projectId, id],
    listOptions(tab === 'ports'),
  );

  if (loading) {
    return <DetailSkeleton />;
  }
  if (error) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <div className="flex flex-wrap gap-xs">
          {/* Audit FBK·H8 — Retry re-runs the same reads without forcing a
              route remount. */}
          <Button size="sm" variant="outline" onClick={retry}>
            <RefreshCw className="size-4" aria-hidden /> Retry
          </Button>
          <Button onClick={() => navigate('/scans')}>Back to Scans</Button>
        </div>
      </div>
    );
  }
  if (!scan) {
    return (
      <div className="p-md md:p-lg text-center">
        <p className="mb-sm text-subheading text-destructive">Scan not found</p>
        <Button onClick={() => navigate('/scans')}>Back to Scans</Button>
      </div>
    );
  }

  const upHostCount = scan.up_hosts ?? 0;
  const totalPortCount = scan.total_ports ?? 0;
  const openPortCount = scan.open_ports ?? 0;
  // Per-scan rollups — absent on scans ingested before the summaries
  // existed, so both are guarded at the render site.
  const portBreakdown = scan.port_breakdown ?? null;
  const vulnSummary = scan.vulnerability_summary ?? null;
  const snapshots: ScanHostSnapshot[] = snapshotList.rows ?? [];
  const hosts: ScanHost[] = currentList.rows ?? [];
  const ports: ScanPortObservation[] = portList.rows ?? [];
  // Whether the scan authenticated to each host is a column only when the scan
  // said so for someone on this page: an nmap scan gets no column of blanks.
  const showCredentialed = snapshots.some((row) => row.credentialed != null);
  const openHost = (hostId: number) =>
    navigate(`/hosts/${hostId}`, { state: { fromScan: { id: Number(scanId), filename: scan.filename } } });
  // The open list's state above its table: loading, or its failure with Retry.
  const listState = (list: { rows: unknown[] | null; error: string | null; reload: () => Promise<void> }) =>
    list.error ? (
      <UnavailableLine onRetry={() => void list.reload()}>{list.error}</UnavailableLine>
    ) : list.rows === null ? (
      <TableSkeleton />
    ) : null;
  type Paged = Pick<PagedList<unknown, ListPage<unknown>>, 'rows' | 'total' | 'page' | 'pageSize' | 'setPage' | 'loading'>;
  const footer = (list: Paged, noun: string, openAll?: { to: string; label: string }) =>
    list.rows && list.total > 0 ? (
      <PagedFooter
        pager={{ page: list.page, pageSize: list.pageSize, total: list.total, onPage: list.setPage }}
        shown={list.rows.length}
        noun={noun}
        busy={list.loading}
        openAll={openAll}
      />
    ) : null;

  // v5.205.0 — same wording as the /scans list: when the scan ran per its own
  // output (or that the file doesn't say), where that time came from, and the
  // upload time separately. The old card fell back to the upload time as
  // "Start" and read a naive UTC value as the viewer's local time.
  const run = describeScanRun(scan);
  const upload = describeUpload(scan);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <Button
          variant="outline"
          size="sm"
          // Audit FRX·H9 — when there's no fromHost referrer use the
          // browser history back so the Scans page restores its filter
          // + scroll state.  Explicit fromHost navigation still takes
          // priority so deep-links from the host detail page work.
          onClick={() => (fromHost ? navigate(`/hosts/${fromHost.id}`) : navigate(-1))}
        >
          <ArrowLeft className="size-4" aria-hidden /> {fromHost ? `Back to ${fromHost.ip}` : 'Back'}
        </Button>
        <h1 className="break-words text-page-title">{scan.filename}</h1>
      </div>

      {/* v5.288.0 — one strip of measures on a shared baseline (the Posture
          layout, UI_STYLE_GUIDE §7), not four bordered stat cards. */}
      <div
        className="mb-lg grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x"
        data-testid="scan-measures"
      >
        <PostureMeasure
          label="Hosts up"
          info="Hosts this scan reported up, of every host it observed. Many tools report no up/down state at all."
          value={<span className="tabular-nums">{upHostCount.toLocaleString()}/{totalHostCount.toLocaleString()}</span>}
        >
          {totalHostCount > 0 && upHostCount === 0 && 'none reported up'}
        </PostureMeasure>
        <PostureMeasure
          label="Open ports"
          info="Open ports this scan observed, with the TCP/UDP split when the scan carries it."
          value={<span className="tabular-nums">{openPortCount.toLocaleString()}</span>}
        >
          {portBreakdown && (portBreakdown.open_tcp_ports > 0 || portBreakdown.open_udp_ports > 0) && (
            <span className="tabular-nums">
              {portBreakdown.open_tcp_ports} TCP · {portBreakdown.open_udp_ports} UDP
            </span>
          )}
        </PostureMeasure>
        <PostureMeasure
          label="Total ports"
          info="Every port this scan recorded, in any state (open, closed, filtered)."
          value={<span className="tabular-nums">{totalPortCount.toLocaleString()}</span>}
        />
        <PostureMeasure
          label="Scan window"
          info="When the scan ran, per its own output, and when the file was uploaded."
          value={<span className="text-metadata font-normal">{run.startLabel ?? 'Not recorded in the file'}</span>}
        >
          {run.endLabel && <p className="break-words">Ended: {run.endLabel}</p>}
          {run.durationLabel && <p>Duration: {run.durationLabel}</p>}
          <p className="break-words" title={upload.explanation}>Uploaded: {upload.label}</p>
          <ScanTimeSourceNote
            className="mt-xxs"
            label={run.sourceLabel}
            explanation={run.explanation}
            utc={run.utcLabel}
            caution={run.kind === 'tool_clock'}
          />
        </PostureMeasure>
      </div>

      {/* What the scan was asked to probe (nmap's scaninfo). The tool's own
          range string is unbounded, so the component clamps it. */}
      <ScannedPorts scanInfo={scan.scan_info} className="mb-lg" />

      {/* Scanner-observation severity rollup — only for scans that recorded any. */}
      {vulnSummary && vulnSummary.total > 0 && (
        <PostureSection
          className="mb-lg"
          title={<>Scanner observations <SectionCount>{vulnSummary.total.toLocaleString()}</SectionCount></>}
        >
          <SeverityBar counts={vulnSummary} variant="inline" />
        </PostureSection>
      )}

      {/* v5.222.0 — what this upload did to the inventory, durable on the
          scan page: hosts added / already known / conflicts / new ports /
          import quality, each opening the records it counts. */}
      {summaryRow && (
        <PostureSection className="mb-lg" title="Import result">
          <ImportResult scan={summaryRow} />
        </PostureSection>
      )}

      <PostureSection title="What this scan recorded">
          <Tabs value={tab === 'dns' && dnsTotal === 0 ? 'hosts' : tab} onValueChange={(v) => choose({ tab: v as ScanTab })}>
            <TabsList>
              {/* Audit RSP·L6 — surface counts in the tab labels so
                  operators can see how much data each tab contains
                  before clicking through.  Each count is the scan's, and
                  the tab lists exactly those rows, page by page. */}
              <TabsTrigger value="hosts"><Computer className="size-4" aria-hidden /> Hosts ({totalHostCount.toLocaleString()})</TabsTrigger>
              <TabsTrigger value="ports"><Shield className="size-4" aria-hidden /> Ports ({totalPortCount.toLocaleString()})</TabsTrigger>
              {dnsTotal > 0 && (
                <TabsTrigger value="dns"><Globe className="size-4" aria-hidden /> DNS Records ({dnsTotal.toLocaleString()})</TabsTrigger>
              )}
              <TabsTrigger value="command"><Terminal className="size-4" aria-hidden /> Command</TabsTrigger>
            </TabsList>
            <TabsContent value="hosts">
              {totalHostCount === 0 ? (
                <div className="rounded-panel border border-border py-xl text-center text-muted-foreground">
                  {/* Audit FBK·H8 — empty-state explains the likely
                      cause and offers a re-upload path (no /upload
                      route — upload happens via the dialog on /scans). */}
                  <div className="flex flex-col items-center gap-xs">
                    <p>No hosts found</p>
                    <p className="text-caption">
                      This scan recorded no hosts above the scanner's threshold.
                    </p>
                    <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
                      <Upload className="size-4" aria-hidden /> Upload another scan
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  {/* As-scanned vs current (v5.184.0). These are different
                      questions and the page used to answer the second one
                      under the first one's heading: a host that was down when
                      scanned and is up today read as up "in" this scan, and
                      ports discovered months later appeared inside it. */}
                  <div className="mb-sm flex flex-wrap items-center gap-xs">
                    <div className="inline-flex rounded-control border border-border p-0.5">
                      <Button
                        size="sm"
                        variant={hostView === 'as_scanned' ? 'default' : 'ghost'}
                        onClick={() => choose({ view: 'as_scanned' })}
                        aria-pressed={hostView === 'as_scanned'}
                      >
                        As scanned
                      </Button>
                      <Button
                        size="sm"
                        variant={hostView === 'current' ? 'default' : 'ghost'}
                        onClick={() => choose({ view: 'current' })}
                        aria-pressed={hostView === 'current'}
                      >
                        Current inventory
                      </Button>
                    </div>
                    <p className="text-caption text-muted-foreground">
                      {hostView === 'as_scanned'
                        ? 'What this scan observed. Does not change as later scans run.'
                        : 'These hosts as they are today — later scans and remediation included.'}
                    </p>
                  </div>
                  {listState(hostView === 'as_scanned' ? snapshotList : currentList)}
                  {hostView === 'as_scanned' && snapshotList.rows?.length === 0 && snapshotList.total === 0 && (
                    <Alert className="mb-sm">
                      <AlertDescription>
                        No per-host observations were recorded for this scan, so there is
                        nothing to show as scanned. Scans ingested before observation
                        records existed only have a current-inventory view.
                      </AlertDescription>
                    </Alert>
                  )}
                  <div className="rounded-panel border border-border">
                    {hostView === 'as_scanned' ? (
                      <Table className="table-fixed">
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-1/5">IP Address</TableHead>
                            <TableHead className="w-1/4">Hostname at scan</TableHead>
                            <TableHead className="w-28">State at scan</TableHead>
                            <TableHead className="w-24">First seen</TableHead>
                            {showCredentialed && (
                              <TableHead
                                className="w-36"
                                title="Whether this scan logged in to the host. Blank: the scan did not say."
                              >
                                Authenticated
                              </TableHead>
                            )}
                            <TableHead className="w-1/4">Ports observed</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {snapshots.map((row) => (
                            <TableRow key={row.host_id}>
                              <TableCell>
                                <div className="max-w-full truncate min-w-0">
                                  <button
                                    type="button"
                                    onClick={() => openHost(row.host_id)}
                                    className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                  >
                                    {row.ip_address}
                                  </button>
                                </div>
                              </TableCell>
                              <TableCell>
                                {/* v5.288.0 — "No hostname", as everywhere else (was "N/A"). */}
                                <div
                                  className={`max-w-full truncate min-w-0${row.hostname_at_scan ? '' : ' text-muted-foreground'}`}
                                  title={row.hostname_at_scan || undefined}
                                >
                                  {row.hostname_at_scan || 'No hostname'}
                                </div>
                              </TableCell>
                              <TableCell>
                                <Badge
                                  variant={hostStateVariant(row.state_at_scan)}
                                  className="whitespace-nowrap"
                                >
                                  {row.state_at_scan || 'unknown'}
                                </Badge>
                              </TableCell>
                              <TableCell>
                                {row.host_created ? (
                                  <Badge variant="outline" className="whitespace-nowrap">
                                    New here
                                  </Badge>
                                ) : row.hostname_changed ? (
                                  <Badge
                                    variant="outline"
                                    className="whitespace-nowrap"
                                    title="Already in the inventory; this scan changed its display name."
                                  >
                                    Renamed here
                                  </Badge>
                                ) : (
                                  <span className="text-caption text-muted-foreground">
                                    Seen before
                                  </span>
                                )}
                              </TableCell>
                              {showCredentialed && (
                                <TableCell>
                                  {/* A text state, not a badge; null stays
                                      blank — "did not say" is not "no". */}
                                  <div className="max-w-full truncate min-w-0 text-metadata">
                                    {row.credentialed === true && 'Credentialed'}
                                    {row.credentialed === false && (
                                      <span className="text-muted-foreground">Not credentialed</span>
                                    )}
                                  </div>
                                </TableCell>
                              )}
                              <TableCell>
                                <div className="max-w-full truncate min-w-0 tabular-nums">
                                  {row.open_port_count} open
                                  {row.observed_port_count > row.open_port_count && (
                                    <span className="text-muted-foreground">
                                      {' '}
                                      of {row.observed_port_count} seen
                                    </span>
                                  )}
                                </div>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    ) : (
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-1/5">IP Address</TableHead>
                          <TableHead className="w-1/4">Hostname</TableHead>
                          <TableHead className="w-24">State</TableHead>
                          <TableHead className="w-1/4">OS</TableHead>
                          <TableHead className="w-1/5">Open Ports</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {hosts.map((host) => (
                          <TableRow key={host.id}>
                            <TableCell>
                              <div className="max-w-full truncate min-w-0">
                                <button
                                  type="button"
                                  onClick={() => openHost(host.id)}
                                  className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                >
                                  {host.ip_address}
                                </button>
                              </div>
                            </TableCell>
                            <TableCell>
                              <div
                                className={`max-w-full truncate min-w-0${host.hostname ? '' : ' text-muted-foreground'}`}
                                title={host.hostname || undefined}
                              >
                                {host.hostname || 'No hostname'}
                              </div>
                            </TableCell>
                            <TableCell>
                              <Badge variant={hostStateVariant(host.state)} className="whitespace-nowrap">{host.state || 'unknown'}</Badge>
                            </TableCell>
                            <TableCell>
                              <div className="max-w-full truncate min-w-0">{host.os_name || 'Unknown'}</div>
                            </TableCell>
                            <TableCell>
                              <div className="flex items-center gap-xs">
                                <span>{host.ports.filter((p) => p.state === 'open').length}</span>
                                {host.ports
                                  .filter((p) => p.state === 'open')
                                  .map((p) => ({ id: p.id, url: getWebUrl(host.ip_address, p) }))
                                  .map(({ id, url }) => url && <WebLinkButton key={id} url={url} />)}
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    )}
                  </div>
                  {hostView === 'as_scanned'
                    ? footer(snapshotList, 'hosts')
                    : footer(currentList, 'hosts', { to: `/hosts?scan_ids=${scan.id}`, label: 'These hosts on the Hosts page' })}
                </>
              )}
            </TabsContent>
            <TabsContent value="ports">
              {totalPortCount === 0 ? (
                <div className="rounded-panel border border-border py-xl text-center text-muted-foreground">
                  {/* Audit FBK·H8 — see Hosts-tab note above. */}
                  <div className="flex flex-col items-center gap-xs">
                    <p>No ports found</p>
                    <p className="text-caption">
                      This scan recorded no ports above the scanner's threshold.
                    </p>
                    <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
                      <Upload className="size-4" aria-hidden /> Upload another scan
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  {/* Every port the scan recorded, as it recorded it — the
                      state and service of the scan's own observation, never
                      the port's record today (review 2026-10-16, A7). */}
                  <p className="mb-sm text-caption text-muted-foreground">
                    What this scan observed on each port. Does not change as later scans run.
                  </p>
                  {listState(portList)}
                  <div className="rounded-panel border border-border">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow>
                          <TableHead>Host</TableHead>
                          <TableHead className="w-24">Port</TableHead>
                          <TableHead className="w-24">Protocol</TableHead>
                          <TableHead className="w-28">State at scan</TableHead>
                          <TableHead className="w-1/4">Service</TableHead>
                          <TableHead className="w-1/4">Version</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {ports.map((port) => {
                          const webUrl = port.state_at_scan === 'open'
                            ? getWebUrl(port.ip_address, { port_number: port.port_number, service_name: port.service_name })
                            : null;
                          return (
                            <TableRow key={`${port.host_id}-${port.port_number}-${port.protocol}`}>
                              <TableCell>
                                <div className="max-w-full truncate min-w-0">
                                  <button
                                    type="button"
                                    onClick={() => openHost(port.host_id)}
                                    className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                  >
                                    {port.ip_address}
                                  </button>
                                </div>
                              </TableCell>
                              <TableCell>
                                <div className="flex items-center gap-xs">
                                  <span>{port.port_number}</span>
                                  {webUrl && <WebLinkButton url={webUrl} />}
                                </div>
                              </TableCell>
                              <TableCell>{port.protocol ?? '—'}</TableCell>
                              <TableCell>
                                <Badge variant={portStateVariant(port.state_at_scan)} className="whitespace-nowrap">{port.state_at_scan || 'unknown'}</Badge>
                              </TableCell>
                              <TableCell>
                                <div className="max-w-full truncate min-w-0" title={port.service_name ?? undefined}>{port.service_name || '—'}</div>
                              </TableCell>
                              <TableCell>
                                <div className="max-w-full truncate min-w-0">
                                  {[port.service_product, port.service_version].filter(Boolean).join(' ') || '—'}
                                </div>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                  {footer(portList, 'ports')}
                </>
              )}
            </TabsContent>
            {dnsTotal > 0 && (
              <TabsContent value="dns">
                {/* DNS-resolution scans (e.g. dnsx) persist every answer,
                    but only A/AAAA become hosts — so CNAME/MX/NS/TXT
                    records are listed here where they'd otherwise be
                    invisible. */}
                {dnsRecords.length < dnsTotal && (
                  <Alert className="mb-sm">
                    <AlertDescription>
                      Showing the first {dnsRecords.length.toLocaleString()} of{' '}
                      {dnsTotal.toLocaleString()} DNS records. Export the scan for the full set.
                    </AlertDescription>
                  </Alert>
                )}
                <div className="rounded-panel border border-border">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-20">Type</TableHead>
                        <TableHead className="w-1/4">Name</TableHead>
                        <TableHead>Value</TableHead>
                        <TableHead className="w-40">Resolver</TableHead>
                        <TableHead className="w-20">TTL</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {dnsRecords.map((r) => (
                        <TableRow key={r.id}>
                          <TableCell>
                            <Badge variant="secondary" className="whitespace-nowrap">{r.record_type}</Badge>
                          </TableCell>
                          <TableCell>
                            <div className="max-w-full truncate min-w-0 font-mono">{r.domain}</div>
                          </TableCell>
                          <TableCell>
                            <div className="max-w-full truncate min-w-0 font-mono">{r.value}</div>
                          </TableCell>
                          <TableCell>
                            <div className="max-w-full truncate min-w-0 text-muted-foreground">{r.resolver_name || '—'}</div>
                          </TableCell>
                          <TableCell className="text-muted-foreground">{r.ttl ?? '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </TabsContent>
            )}
            <TabsContent value="command">
              <CommandExplanation scanId={parseInt(scanId!)} />
            </TabsContent>
          </Tabs>
      </PostureSection>
    </div>
  );
};

export default ScanDetail;
