import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { ArrowLeft, Computer, Shield, Terminal, ExternalLink, Loader2, RefreshCw, Upload, ChevronRight } from 'lucide-react';
import { getScan, getHostsByScan } from '../services/api';
import type { Host } from '../services/api';
import CommandExplanation from '../components/CommandExplanation';
import { Card, CardContent } from '../components/ui/card';
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
import { formatApiError } from '../utils/apiErrors';
import { formatHostForUrl } from '../utils/webLinks';

type BadgeTone = 'success' | 'destructive' | 'warning' | 'muted';
const hostStateVariant = (s: string | null): BadgeTone =>
  s === 'up' ? 'success' : s === 'down' ? 'destructive' : 'muted';
const portStateVariant = (s: string | null): BadgeTone =>
  s === 'open' ? 'success' : s === 'closed' ? 'destructive' : s === 'filtered' ? 'warning' : 'muted';

// v4.7.8 — web-link detection is now SERVICE-AWARE, not a bare
// 80/443 check.  httpx (and any service-detecting scan) commonly
// finds web services on non-standard ports — 8080, 8443, 3000,
// 8000, etc.  Pre-fix `isWebPort` only matched 80/443 so those got
// no link, and `getWebUrl` keyed `https` solely off port===443 so
// an 8443 service was mislabelled `http`.  We now consult the
// port's detected service_name first and fall back to a known
// web-port set.
const WEB_PORT_FALLBACK = new Set([80, 443, 8080, 8443, 8000, 8008, 8888, 3000, 81, 4443, 9443]);
type WebPortLike = { port_number: number; service_name?: string | null };

const isWebPort = (port: WebPortLike): boolean => {
  const svc = (port.service_name || '').toLowerCase();
  if (svc.includes('http') || svc === 'https' || svc.includes('ssl/http')) return true;
  return WEB_PORT_FALLBACK.has(port.port_number);
};

const isHttpsPort = (port: WebPortLike): boolean => {
  const svc = (port.service_name || '').toLowerCase();
  if (svc.includes('https') || svc.includes('ssl')) return true;
  return [443, 8443, 9443, 4443].includes(port.port_number);
};

// v4.7.8 — the URL now ALWAYS carries the port.  Pre-fix the port was
// stripped for 80/443, so a host with both 80 and 443 web services
// rendered as two near-identical links to the same host with no port
// visible — the operator-reported symptom.  An explicit `:80` / `:443`
// is technically redundant but unambiguous, and every link in the
// row is now visibly distinct.
const getWebUrl = (ip: string, port: WebPortLike): string => {
  const protocol = isHttpsPort(port) ? 'https' : 'http';
  return `${protocol}://${formatHostForUrl(ip)}:${port.port_number}`;
};

const fmtDateTime = (v?: Date | string | null) => {
  if (!v) return 'Unknown';
  const d = v instanceof Date ? v : new Date(v);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return d.toLocaleString();
};

const fmtDuration = (ms?: number | null) => {
  if (!ms || ms <= 0) return 'Instant';
  const sec = Math.floor(ms / 1000);
  const min = Math.floor(sec / 60);
  const hr = Math.floor(min / 60);
  const day = Math.floor(hr / 24);
  if (day > 0) return `${day}d ${hr % 24}h`;
  if (hr > 0) return `${hr}h ${min % 60}m`;
  if (min > 0) return `${min}m ${sec % 60}s`;
  return `${sec}s`;
};

const ScanDetail: React.FC = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const fromHost = (location.state as { fromHost?: { id: number; ip: string } } | null)?.fromHost;
  const [scan, setScan] = useState<any>(null);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState('hosts');
  // Audit FBK·H8 — reload nonce drives the fetch effect so the Retry
  // button on the error path can re-run the same load without a full
  // route re-mount.
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    if (!scanId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getScan(parseInt(scanId)), getHostsByScan(parseInt(scanId))])
      .then(([s, h]) => {
        if (!cancelled) {
          setScan(s);
          setHosts(h);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Failed to load scan details.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, reloadNonce]);

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
          {/* Audit FBK·H8 — Retry re-runs the same fetch via the
              reload-nonce dep without forcing a route remount. */}
          <Button size="sm" variant="outline" onClick={() => setReloadNonce((n) => n + 1)}>
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

  const upHosts = hosts.filter((h) => h.state === 'up').length;
  const totalPorts = hosts.reduce((acc, h) => acc + h.ports.length, 0);
  const openPorts = hosts.reduce((acc, h) => acc + h.ports.filter((p) => p.state === 'open').length, 0);

  // Prefer the scan-level aggregates from getScan (accurate, matches the
  // /scans list badge) over counts derived from the fetched host list,
  // which is capped (getHostsByScan limit) and undercounts large scans.
  const totalHostCount = scan.total_hosts ?? hosts.length;
  const upHostCount = scan.up_hosts ?? upHosts;
  const totalPortCount = scan.total_ports ?? totalPorts;
  const openPortCount = scan.open_ports ?? openPorts;
  // The rendered host/port tables are a sample when the scan has more
  // hosts than we fetched — surface that so the counts don't look buggy.
  const hostsCapped = hosts.length < totalHostCount;

  const rawStart = scan.start_time ? new Date(scan.start_time) : new Date(scan.created_at);
  const validStart = Number.isNaN(rawStart.getTime()) ? new Date(scan.created_at) : rawStart;
  const rawEnd = scan.end_time ? new Date(scan.end_time) : validStart;
  const validEnd = Number.isNaN(rawEnd.getTime()) || rawEnd < validStart ? validStart : rawEnd;
  const scanDurationMs = Math.max(validEnd.getTime() - validStart.getTime(), 0);

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

      <div className="mb-md grid grid-cols-2 gap-sm md:grid-cols-4">
        <StatCard label="Hosts Up" value={`${upHostCount}/${totalHostCount}`} />
        <StatCard label="Open Ports" value={openPortCount} />
        <StatCard label="Total Ports" value={totalPortCount} />
        <Card>
          <CardContent className="p-md">
            <p className="text-caption text-muted-foreground">Scan Window</p>
            <p className="text-metadata text-foreground">Start: {fmtDateTime(validStart)}</p>
            <p className="text-caption text-muted-foreground">End: {fmtDateTime(validEnd)}</p>
            <p className="text-caption text-muted-foreground">Duration: {fmtDuration(scanDurationMs)}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-md">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              {/* Audit RSP·L6 — surface counts in the tab labels so
                  operators can see how much data each tab contains
                  before clicking through. */}
              <TabsTrigger value="hosts"><Computer className="size-4" aria-hidden /> Hosts ({totalHostCount})</TabsTrigger>
              <TabsTrigger value="ports"><Shield className="size-4" aria-hidden /> All Ports ({totalPortCount})</TabsTrigger>
              <TabsTrigger value="command"><Terminal className="size-4" aria-hidden /> Command</TabsTrigger>
            </TabsList>
            <TabsContent value="hosts">
              {hosts.length === 0 ? (
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
                  {hostsCapped && (
                    <Alert className="mb-sm">
                      <AlertDescription className="flex flex-wrap items-center gap-xs">
                        <span>
                          Showing the first {hosts.length.toLocaleString()} of{' '}
                          {totalHostCount.toLocaleString()} hosts in this scan.
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => navigate(`/hosts?scan_ids=${scan.id}`)}
                        >
                          View all on Hosts page
                        </Button>
                      </AlertDescription>
                    </Alert>
                  )}
                  {/* Mobile card list (audit RSP·CRIT-13) — table
                      column scrolling is hostile on small viewports,
                      so render the same data as a stacked card list
                      below md. */}
                  <ul className="space-y-xs md:hidden">
                    {hosts.map((host) => {
                      const openCount = host.ports.filter((p) => p.state === 'open').length;
                      return (
                        <li key={host.id}>
                          <button
                            type="button"
                            onClick={() =>
                              navigate(`/hosts/${host.id}`, {
                                state: { fromScan: { id: Number(scanId), filename: scan?.filename } },
                              })
                            }
                            className="flex w-full items-center gap-sm overflow-hidden rounded-panel border border-border p-sm text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <div className="min-w-0 flex-1 space-y-xxs">
                              <div className="truncate text-metadata font-semibold text-foreground">
                                {host.hostname || 'N/A'}
                              </div>
                              <div className="truncate font-mono text-caption text-muted-foreground">
                                {host.ip_address}
                              </div>
                              <div className="truncate text-caption text-muted-foreground">
                                {host.os_name || 'Unknown OS'} — {openCount} open port{openCount === 1 ? '' : 's'}
                              </div>
                            </div>
                            <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="hidden md:block">
                    <div className="rounded-panel border border-border">
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
                                    onClick={() =>
                                      navigate(`/hosts/${host.id}`, {
                                        state: { fromScan: { id: Number(scanId), filename: scan?.filename } },
                                      })
                                    }
                                    className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                  >
                                    {host.ip_address}
                                  </button>
                                </div>
                              </TableCell>
                              <TableCell>
                                <div className="max-w-full truncate min-w-0">{host.hostname || 'N/A'}</div>
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
                                    .filter((p) => p.state === 'open' && isWebPort(p))
                                    .map((p) => (
                                      <Tooltip key={p.id}>
                                        <TooltipTrigger asChild>
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() =>
                                              window.open(
                                                getWebUrl(host.ip_address, p),
                                                '_blank',
                                                'noopener,noreferrer',
                                              )
                                            }
                                            aria-label={`Open ${getWebUrl(host.ip_address, p)} in new tab`}
                                          >
                                            <ExternalLink className="size-3.5 text-primary" aria-hidden />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>
                                          Open {getWebUrl(host.ip_address, p)}
                                        </TooltipContent>
                                      </Tooltip>
                                    ))}
                                </div>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                </>
              )}
            </TabsContent>
            <TabsContent value="ports">
              {hosts.flatMap((h) => h.ports).length === 0 ? (
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
                  {/* Mobile card list (audit RSP·CRIT-13). */}
                  <ul className="space-y-xs md:hidden">
                    {hosts.flatMap((host) =>
                      host.ports.map((port) => (
                        <li key={`${host.id}-${port.id}`}>
                          <button
                            type="button"
                            onClick={() =>
                              navigate(`/hosts/${host.id}`, {
                                state: { fromScan: { id: Number(scanId), filename: scan?.filename } },
                              })
                            }
                            className="flex w-full items-center gap-sm overflow-hidden rounded-panel border border-border p-sm text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <div className="min-w-0 flex-1 space-y-xxs">
                              <div className="truncate font-mono text-metadata font-semibold text-foreground">
                                {host.ip_address}:{port.port_number}
                              </div>
                              <div className="truncate text-caption text-muted-foreground">
                                {port.service_name || 'Unknown service'}
                              </div>
                              <div>
                                <Badge variant={portStateVariant(port.state)} className="whitespace-nowrap">{port.state || 'unknown'}</Badge>
                              </div>
                            </div>
                            <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                          </button>
                        </li>
                      )),
                    )}
                  </ul>
                  <div className="hidden md:block">
                    <div className="rounded-panel border border-border">
                      <Table className="table-fixed">
                        <TableHeader>
                          <TableRow>
                            <TableHead>Host</TableHead>
                            <TableHead className="w-24">Port</TableHead>
                            <TableHead className="w-24">Protocol</TableHead>
                            <TableHead className="w-24">State</TableHead>
                            <TableHead className="w-1/4">Service</TableHead>
                            <TableHead className="w-1/4">Version</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {hosts.flatMap((host) =>
                            host.ports.map((port) => (
                              <TableRow key={`${host.id}-${port.id}`}>
                                <TableCell>
                                  <div className="max-w-full truncate min-w-0">
                                    <button
                                      type="button"
                                      onClick={() =>
                                        navigate(`/hosts/${host.id}`, {
                                          state: { fromScan: { id: Number(scanId), filename: scan?.filename } },
                                        })
                                      }
                                      className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                    >
                                      {host.ip_address}
                                    </button>
                                  </div>
                                </TableCell>
                                <TableCell>
                                  <div className="flex items-center gap-xs">
                                    <span>{port.port_number}</span>
                                    {port.state === 'open' && isWebPort(port) && (
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() =>
                                              window.open(
                                                getWebUrl(host.ip_address, port),
                                                '_blank',
                                                'noopener,noreferrer',
                                              )
                                            }
                                            aria-label={`Open ${getWebUrl(host.ip_address, port)} in new tab`}
                                          >
                                            <ExternalLink className="size-3.5 text-primary" aria-hidden />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>
                                          Open {getWebUrl(host.ip_address, port)}
                                        </TooltipContent>
                                      </Tooltip>
                                    )}
                                  </div>
                                </TableCell>
                                <TableCell>{port.protocol}</TableCell>
                                <TableCell>
                                  <Badge variant={portStateVariant(port.state)} className="whitespace-nowrap">{port.state || 'unknown'}</Badge>
                                </TableCell>
                                <TableCell>
                                  <div className="max-w-full truncate min-w-0">{port.service_name || 'Unknown'}</div>
                                </TableCell>
                                <TableCell>
                                  <div className="max-w-full truncate min-w-0">
                                    {port.service_product && port.service_version
                                      ? `${port.service_product} ${port.service_version}`
                                      : port.service_product || 'N/A'}
                                  </div>
                                </TableCell>
                              </TableRow>
                            )),
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                </>
              )}
            </TabsContent>
            <TabsContent value="command">
              <CommandExplanation scanId={parseInt(scanId!)} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
};

const StatCard: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <Card>
    <CardContent className="p-md">
      <p className="text-caption text-muted-foreground">{label}</p>
      {/* Audit RSP·M13 — at md grid-cols-4 the locale-formatted big
          numbers can overflow text-page-title; drop a step on md and
          truncate to keep the card width-stable. */}
      <p className="truncate text-section-title font-semibold text-foreground md:text-page-title">{value}</p>
    </CardContent>
  </Card>
);

export default ScanDetail;
