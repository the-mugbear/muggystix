/**
 * Ports — the host's open services as one dense table, with per-port
 * connection-helper commands and per-endpoint TLS evidence (cert expiry,
 * self-signed / weak-protocol state, the CA-validated subject org), joined
 * from the host's web interfaces by ``port_id``.
 *
 * v5.240.0 — density pass. The median host has three open ports, and the old
 * card spent a title, an accordion trigger and eight columns on them:
 *  - no State column: every row of the open table said "open" (the reason
 *    nmap gave is on the port cell's tooltip);
 *  - port and protocol are one cell (``443/tcp``);
 *  - how the service was detected is a tooltip, not a second line that doubled
 *    every row's height;
 *  - the TLS column exists only when some port has TLS evidence;
 *  - closed and filtered ports are one summary line until asked for.
 * The freed width goes to Version, the column an analyst actually reads.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { copyToClipboard } from '../../utils/clipboard';
import {
  ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, Copy, Lock, Network, ShieldCheck, Terminal,
} from 'lucide-react';

import {
  getHostNetexecResults, getHostWebInterfaces, getHostWebPaths,
  type HostVulnerability, type NetexecResult, type Port, type WebInterface, type WebPath,
} from '../../services/api';
import { foldNetexecRows } from '../NetExecCard';
import { SEVERITY_BADGE_VARIANT, type Severity } from '../../utils/severity';
import {
  evidenceForPort, summariseAccess, unplacedEvidence, worstSeverity, type ServiceEvidence,
} from '../../utils/serviceEvidence';
import ServiceEvidencePanel from './ServiceEvidencePanel';
import { getConnectionHelpers, isSafeHostname, type ConnectionHelper } from '../../utils/connectionHelpers';
import {
  EXPIRY_WARN_DAYS, daysUntil, endpointsByPort, summariseEndpointTls,
  type EndpointTls, type PortEndpoint,
} from '../../utils/portEndpoints';
import { formatDate, formatRelativeTime } from '../../utils/relativeTime';
import { NOT_REVALIDATED_LABEL, NOT_REVALIDATED_TITLE, portFreshness } from '../../utils/evidenceFreshness';
import { useToast } from '../../contexts/ToastContext';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { InspectorSection } from './InspectorSection';

const stateBadgeVariant = (
  state: string | null,
): 'success' | 'destructive' | 'warning' | 'outline' => {
  switch (state) {
    case 'up':
    case 'open':
      return 'success';
    case 'down':
    case 'closed':
      return 'destructive';
    case 'filtered':
      return 'warning';
    default:
      return 'outline';
  }
};

/** nmap's tunnel attribute is "ssl" when the service runs inside TLS. */
const isTlsTunnel = (tunnel?: string | null): boolean => !!tunnel && /ssl|tls/i.test(tunnel);

/** One endpoint's cert / TLS facts. */
const TlsFacts: React.FC<{ tls: EndpointTls }> = ({ tls }) => {
  const days = daysUntil(tls.cert_not_after);
  const expired = days !== null && days < 0;
  const expiringSoon = days !== null && days >= 0 && days <= EXPIRY_WARN_DAYS;
  return (
    <div className="flex min-w-0 flex-col gap-xxs">
      <div className="flex flex-wrap items-center gap-xxs">
        {tls.tls_weak_protocol === true && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="destructive" tabIndex={0}>weak TLS</Badge>
            </TooltipTrigger>
            <TooltipContent>Offers a deprecated protocol (SSLv2/SSLv3/TLS 1.0/1.1) — downgrade / interception risk.</TooltipContent>
          </Tooltip>
        )}
        {tls.cert_self_signed === true && (
          <Badge variant="outline" className="border-warning/40 text-warning">self-signed</Badge>
        )}
      </div>
      {days !== null && (
        <span className={expired || expiringSoon ? 'text-caption text-destructive' : 'text-caption text-muted-foreground'}>
          {expired
            ? `cert expired ${Math.abs(days)}d ago`
            : `cert expires ${days}d`}
        </span>
      )}
      {tls.cert_subject_org && (
        <span className="flex min-w-0 items-center gap-xxs text-caption text-muted-foreground"
          title={`Certificate subject organisation (CA-validated): ${tls.cert_subject_org}`}>
          <ShieldCheck className="size-3 shrink-0 text-success" aria-hidden />
          <span className="truncate">{tls.cert_subject_org}</span>
        </span>
      )}
    </div>
  );
};

/**
 * The TLS column.  A port with ONE endpoint shows its facts (and the name
 * they belong to).  A port serving SEVERAL named endpoints shows a count-only
 * roll-up and opens the per-endpoint evidence: one website's certificate is
 * never presented as the port's, and the row keeps its height.
 */
const TlsCell: React.FC<{
  endpoints: PortEndpoint[];
  portNumber: number | null;
  tunnel?: string | null;
}> = ({ endpoints, portNumber, tunnel }) => {
  const withTls = endpoints.filter((e) => e.tls);
  if (withTls.length > 1) {
    const s = summariseEndpointTls(endpoints);
    const flags = [
      s.weak > 0 ? `${s.weak} weak TLS` : null,
      s.expired > 0 ? `${s.expired} expired` : null,
      s.expiringSoon > 0 ? `${s.expiringSoon} expiring` : null,
      s.selfSigned > 0 ? `${s.selfSigned} self-signed` : null,
    ].filter(Boolean) as string[];
    return (
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex w-full min-w-0 flex-col items-start rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`TLS evidence for ${withTls.length} endpoints on port ${portNumber ?? ''}`}
          >
            <span className="truncate text-caption text-primary underline-offset-2 hover:underline">
              {withTls.length} endpoints
            </span>
            <span className={`truncate text-caption ${s.weak + s.expired > 0 ? 'text-destructive' : flags.length ? 'text-warning' : 'text-muted-foreground'}`}>
              {flags.length ? flags.join(' · ') : 'no TLS issues recorded'}
            </span>
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-[34rem] max-w-[90vw]" align="start">
          <div className="max-h-[24rem] overflow-y-auto p-xs">
            <h4 className="text-subheading">TLS evidence by endpoint · port {portNumber}</h4>
            <p className="mb-xs text-caption text-muted-foreground">
              Each website on this port has its own certificate. Nothing here is merged across names.
            </p>
            <ul className="divide-y divide-border">
              {withTls.map((e) => (
                <li key={e.key} className="flex items-start gap-sm py-xs">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-mono text-caption text-foreground" title={e.name ?? e.url}>
                      {e.name ?? 'address only (default site)'}
                    </p>
                    <p className="truncate text-caption text-muted-foreground" title={e.url}>
                      {e.source} · {formatRelativeTime(e.last_seen, { fallback: 'time unknown' })} · {e.url}
                    </p>
                  </div>
                  <div className="w-[11rem] shrink-0">
                    <TlsFacts tls={e.tls!} />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </PopoverContent>
      </Popover>
    );
  }
  const only = withTls[0];
  const tls = only?.tls ?? undefined;
  if (!tls) {
    // No web-interface / cert evidence for this port. Fall back to nmap's
    // tunnel attribute so a TLS-wrapped service on a non-standard port is
    // still marked.
    if (isTlsTunnel(tunnel)) {
      return (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="outline" className="border-info/40 text-info" tabIndex={0}>
              <Lock className="mr-xxs size-3" aria-hidden />TLS
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            nmap saw this service running inside TLS (tunnel=ssl). No certificate detail was
            captured — run a web/cert probe for issuer and expiry.
          </TooltipContent>
        </Tooltip>
      );
    }
    return <span className="text-caption text-muted-foreground">—</span>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xxs">
      <TlsFacts tls={tls} />
      {/* Whose certificate this is: the name it was served for, the source
          that saw it and when — kept with the fact, not implied. */}
      <span
        className="truncate text-caption text-muted-foreground"
        title={`${only.name ?? 'address only (default site)'} · ${only.source} · ${only.url}`}
      >
        {only.name ?? 'default site'} · {formatRelativeTime(only.last_seen, { fallback: 'time unknown' })}
      </span>
    </div>
  );
};

/**
 * A service's row summary (v5.297.0): its worst weakness, whether anything
 * logged in, and how much web / path / script evidence it has.  Any part
 * opens the service's panel.
 */
const ServiceSummary: React.FC<{ evidence: ServiceEvidence; scripts: number; onOpen: () => void }> = ({
  evidence, scripts, onOpen,
}) => {
  const worst = worstSeverity(evidence.weaknesses);
  const access = summariseAccess(foldNetexecRows(evidence.access).map((o) => ({ auth_success: o.latest.auth_success })));
  const parts: React.ReactNode[] = [];
  if (worst) {
    const rest = evidence.weaknesses.length - worst.count;
    parts.push(
      <Badge key="w" variant={(SEVERITY_BADGE_VARIANT[worst.severity as Severity] ?? 'outline') as never}>
        {worst.count} {worst.severity}{rest > 0 ? ` +${rest}` : ''}
      </Badge>,
    );
  }
  if (access.worked.length > 0) {
    parts.push(<Badge key="a" variant="success">{access.worked.length} login{access.worked.length === 1 ? '' : 's'} worked</Badge>);
  } else if (access.failed.length > 0) {
    parts.push(<span key="f" className="text-caption text-muted-foreground">{access.failed.length} failed logins</span>);
  }
  // Distinct pages: a URL each tool saw in several scans is one page.
  const pages = new Set(evidence.web.map((w) => `${w.source}|${w.url}`)).size;
  const counts = [
    pages ? `web ${pages}` : null,
    evidence.paths.length ? `${evidence.paths.length} paths` : null,
    scripts ? `${scripts} script${scripts === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' · ');
  if (counts) parts.push(<span key="c" className="text-caption text-muted-foreground">{counts}</span>);
  if (parts.length === 0) return <span className="text-caption text-muted-foreground">—</span>;
  return (
    <button type="button" onClick={onOpen}
      className="flex min-w-0 flex-wrap items-center gap-xxs rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {parts}
    </button>
  );
};

interface PortDetailsCardProps {
  hostId: number;
  hostIp: string | null;
  hostname?: string | null;
  /** The host's newest observation, so a port can say whether the latest
   *  sweep saw it (v5.224.0). */
  hostLastSeen?: string | null;
  openPorts: Port[];
  closedPorts: Port[];
  filteredPorts: Port[];
  connectionHelpersByPort: Map<number, ConnectionHelper[]>;
  /** v5.297.0 — the evidence each service's panel groups under its port. */
  vulnerabilities?: HostVulnerability[];
  netexecCount?: number;
  webPathCount?: number;
}

const PortDetailsCard: React.FC<PortDetailsCardProps> = ({
  hostId, hostIp, hostname = null, hostLastSeen = null, openPorts, closedPorts, filteredPorts, connectionHelpersByPort,
  vulnerabilities = [], netexecCount = 0, webPathCount = 0,
}) => {
  const toast = useToast();
  const [portSortDir, setPortSortDir] = useState<'asc' | 'desc' | null>(null);
  const [endpoints, setEndpoints] = useState<Map<number, PortEndpoint[]>>(new Map());
  const [webError, setWebError] = useState(false);
  // Which endpoint a port's commands address; absent = the default below.
  const [helperTarget, setHelperTarget] = useState<Record<number, string>>({});
  const [showNotOpen, setShowNotOpen] = useState(false);
  // v5.297.0 — the host's evidence, loaded once and split by port.
  const [webRows, setWebRows] = useState<WebInterface[]>([]);
  const [netexecRows, setNetexecRows] = useState<NetexecResult[]>([]);
  const [pathRows, setPathRows] = useState<WebPath[]>([]);
  const [evidenceError, setEvidenceError] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Join the host's web interfaces onto ports by ``port_id`` as NAMED
  // ENDPOINTS (utils/portEndpoints): newest observation per (port, name),
  // nothing merged across names. Non-fatal: on failure the column falls back
  // to nmap's tunnel attribute and the section says the load failed (so a
  // fetch error reads differently from "no TLS evidence") rather than breaking
  // the port table. State is reset per host — the inspector stays mounted
  // across prev/next, so a stale map would otherwise bleed onto the next host.
  useEffect(() => {
    let cancelled = false;
    setEndpoints(new Map());
    setWebRows([]);
    setHelperTarget({});
    setWebError(false);
    setShowNotOpen(false);
    getHostWebInterfaces(hostId)
      .then((interfaces) => {
        if (cancelled) return;
        setEndpoints(endpointsByPort(interfaces));
        setWebRows(interfaces);
      })
      .catch(() => { if (!cancelled) setWebError(true); });
    return () => { cancelled = true; };
  }, [hostId]);

  // NetExec / SMBMap results and discovered paths: only when the host has any.
  useEffect(() => {
    let cancelled = false;
    setNetexecRows([]);
    setPathRows([]);
    setEvidenceError(false);
    setExpanded(new Set());
    const loads: Promise<unknown>[] = [];
    if (netexecCount > 0) {
      loads.push(getHostNetexecResults(hostId).then((r) => { if (!cancelled) setNetexecRows(r); }));
    }
    if (webPathCount > 0) {
      loads.push(getHostWebPaths(hostId).then((r) => { if (!cancelled) setPathRows(r); }));
    }
    Promise.all(loads).catch(() => { if (!cancelled) setEvidenceError(true); });
    return () => { cancelled = true; };
  }, [hostId, netexecCount, webPathCount]);

  const all = useMemo(
    () => ({ vulnerabilities, netexec: netexecRows, web: webRows, paths: pathRows }),
    [vulnerabilities, netexecRows, webRows, pathRows],
  );
  const unplaced = useMemo(
    () => unplacedEvidence(openPorts, [...openPorts, ...closedPorts, ...filteredPorts], all),
    [openPorts, closedPorts, filteredPorts, all],
  );
  // A host with one open port has one thing to look at: open it.
  const isExpanded = (port: Port) => expanded.has(port.id) || openPorts.length === 1;
  const toggle = (port: Port) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(port.id)) next.delete(port.id); else next.add(port.id);
    return next;
  });

  const sortPorts = useMemo(
    () => <T extends { port_number: number | null }>(arr: T[]): T[] => {
      if (!portSortDir) return arr;
      const s = [...arr].sort((a, b) => (a.port_number ?? 0) - (b.port_number ?? 0));
      return portSortDir === 'desc' ? s.reverse() : s;
    },
    [portSortDir],
  );

  // A column of dashes is noise: on a host with no TLS evidence (a database
  // server, a Windows workstation) the column is not drawn at all.
  const showTls = openPorts.some(
    (p) => (endpoints.get(p.id) ?? []).some((e) => e.tls) || isTlsTunnel(p.service_tunnel),
  );

  // v5.297.0 — what each service carries, summarised on its row; the row
  // opens to the evidence itself (ServiceEvidencePanel).
  const evidenceFor = (port: Port) => evidenceForPort(port, all);
  const showEvidence = openPorts.some((p) => {
    const e = evidenceFor(p);
    return e.weaknesses.length + e.access.length + e.web.length + e.paths.length + (p.scripts?.length ?? 0) > 0;
  });
  // Version takes whatever the optional columns leave (fixed layout).
  const versionWidth = 100 - 12 - 18 - 12 - 8 - (showTls ? 14 : 0) - (showEvidence ? 18 : 0);
  const columnCount = 5 + (showTls ? 1 : 0) + (showEvidence ? 1 : 0);

  const PortSortHead: React.FC<{ className?: string }> = ({ className }) => (
    <TableHead className={className}
      aria-sort={portSortDir ? (portSortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button"
        onClick={() => setPortSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
        className="inline-flex items-center gap-xxs rounded text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        Port
        {portSortDir
          ? (portSortDir === 'asc' ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)
          : <ArrowUpDown className="size-3 opacity-40" aria-hidden />}
      </button>
    </TableHead>
  );

  const notOpen = [...closedPorts, ...filteredPorts];
  // Counted per state as nmap wrote it: "3 closed · 2 open|filtered".
  const stateCounts = new Map<string, number>();
  for (const p of notOpen) {
    const s = p.state || 'unknown';
    stateCounts.set(s, (stateCounts.get(s) ?? 0) + 1);
  }
  const notOpenSummary = [...stateCounts].map(([s, n]) => `${n} ${s}`).join(' · ');

  return (
    <InspectorSection
      id="host-detail-ports"
      title="Services"
      titleHint="One row per open port. Open a row for everything known about that service: its weaknesses, what logged in, its web pages and paths, and the tools' output."
      icon={<Network className="size-4 shrink-0 text-primary" aria-hidden />}
      count={openPorts.length}
    >
      {openPorts.length === 0 ? (
        <p className="text-metadata text-muted-foreground">No open ports observed.</p>
      ) : (
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <PortSortHead className="w-[12%]" />
                <TableHead className="w-[18%]">Service</TableHead>
                <TableHead style={{ width: `${versionWidth}%` }}>Version</TableHead>
                <TableHead className="w-[12%]" title="When this port itself was last observed. Older than the host's last observation means newer evidence did not revalidate it — not that it was checked and found closed.">Seen</TableHead>
                {showTls && <TableHead className="w-[14%]">TLS</TableHead>}
                {showEvidence && <TableHead className="w-[18%]">What&rsquo;s here</TableHead>}
                <TableHead className="w-[8%] text-center">
                  <span className="sr-only">Connection helpers</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortPorts(openPorts).map((port) => {
                const portEndpoints = endpoints.get(port.id) ?? [];
                // Only names that are plainly hostnames: they go into
                // commands the operator pastes into a shell.
                const names = portEndpoints.map((e) => e.name).filter(isSafeHostname);
                // With named endpoints on the port, an address alone
                // reaches the DEFAULT site — usually not the website
                // the evidence is about. Default to the first name;
                // the operator can switch, including back to the address.
                const target = helperTarget[port.id] ?? names[0] ?? '';
                const helpers = target && hostIp
                  ? getConnectionHelpers(hostIp, port, hostname, { vhost: target })
                  : connectionHelpersByPort.get(port.id) ?? [];
                const fresh = portFreshness(port, hostLastSeen);
                const detection = [
                  port.service_method ? `detected by ${port.service_method}` : null,
                  port.service_conf != null && String(port.service_conf) !== ''
                    ? `nmap confidence ${port.service_conf}/10`
                    : null,
                ].filter(Boolean).join(' · ');
                const version = port.service_product && port.service_version
                  ? `${port.service_product} ${port.service_version}`
                  : port.service_product || '';
                const open = isExpanded(port);
                return (
                  <React.Fragment key={port.id}>
                  <TableRow className={open ? 'border-b-0' : undefined}>
                    <TableCell className="truncate font-mono text-metadata">
                      <button
                        type="button"
                        onClick={() => toggle(port)}
                        aria-expanded={open}
                        aria-label={`${open ? 'Hide' : 'Show'} what is known about port ${port.port_number}`}
                        className="inline-flex items-center gap-xxs rounded text-left hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={port.reason ? `${port.state || 'open'} — ${port.reason}` : undefined}
                      >
                        {open
                          ? <ChevronDown className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                          : <ChevronRight className="size-3 shrink-0 text-muted-foreground" aria-hidden />}
                        {port.port_number}
                        <span className="text-muted-foreground">/{port.protocol}</span>
                      </button>
                    </TableCell>
                    <TableCell
                      className="truncate"
                      title={[port.service_name, detection].filter(Boolean).join(' — ') || undefined}
                    >
                      {port.service_name || <span className="text-muted-foreground">unknown</span>}
                    </TableCell>
                    <TableCell
                      className="truncate"
                      title={[version, port.service_extrainfo].filter(Boolean).join(' — ') || undefined}
                    >
                      {/* With no product, the extra info IS the cell — it read
                          "— ([401] /console (512B))", a dash and then a value. */}
                      {version || (!port.service_extrainfo && <span className="text-muted-foreground">—</span>)}
                      {port.service_extrainfo && (
                        <span className={version ? 'ml-xxs text-caption text-muted-foreground' : 'text-caption text-muted-foreground'}>
                          {version ? `(${port.service_extrainfo})` : port.service_extrainfo}
                        </span>
                      )}
                    </TableCell>
                    {/* v5.224.0 — the port's own freshness, not the host's. */}
                    <TableCell className="min-w-0">
                      <div className="truncate text-caption" title={port.last_seen ?? port.first_seen ?? undefined}>
                        {fresh.seen ?? '—'}
                      </div>
                      {fresh.notRevalidated && (
                        <div className="truncate text-caption text-warning" title={NOT_REVALIDATED_TITLE}>
                          {NOT_REVALIDATED_LABEL}
                        </div>
                      )}
                    </TableCell>
                    {showTls && (
                      <TableCell>
                        <TlsCell
                          endpoints={portEndpoints}
                          portNumber={port.port_number}
                          tunnel={port.service_tunnel}
                        />
                      </TableCell>
                    )}
                    {showEvidence && (
                      <TableCell className="min-w-0">
                        <ServiceSummary evidence={evidenceFor(port)} scripts={port.scripts?.length ?? 0}
                          onOpen={() => { if (!open) toggle(port); }} />
                      </TableCell>
                    )}
                    {/* A full-size icon button (36px) set the height of every
                        row; the row is as tall as its text now. */}
                    {/* A 28px button is the height of the row's text line, so it
                        takes the same padding and top alignment as its
                        neighbours — middle-aligned it sat below the text. */}
                    <TableCell className="text-center">
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button variant="ghost" size="icon" className="size-7"
                            aria-label={`Connection helpers for port ${port.port_number}`}>
                            <Terminal className="size-4" aria-hidden />
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-[34rem] max-w-[90vw]" align="end">
                          <div className="max-h-[24rem] overflow-y-auto p-xs">
                            <h4 className="mb-xs break-words text-subheading">
                              Commands for {target || hostIp}:{port.port_number}
                            </h4>
                            {names.length > 0 && (
                              <div className="mb-xs">
                                <label className="text-caption text-muted-foreground" htmlFor={`helper-target-${port.id}`}>
                                  Endpoint — this port answers as {names.length === 1 ? 'a named site' : `${names.length} named sites`};
                                  the address alone reaches the default one.
                                </label>
                                <select
                                  id={`helper-target-${port.id}`}
                                  className="mt-xxs flex h-8 w-full rounded-control border border-input bg-background px-xs font-mono text-caption"
                                  value={target}
                                  onChange={(e) => setHelperTarget((prev) => ({ ...prev, [port.id]: e.target.value }))}
                                >
                                  {names.map((n) => <option key={n} value={n}>{n}</option>)}
                                  <option value="">{hostIp} (address only — default site)</option>
                                </select>
                              </div>
                            )}
                            <div className="space-y-xs">
                              {helpers.map((helper, idx) => (
                                <div key={idx} className="flex items-start gap-xs rounded-control bg-muted/30 p-xs">
                                  <div className="min-w-0 flex-1">
                                    <p className="text-caption text-muted-foreground">
                                      {helper.tool} — {helper.description}
                                    </p>
                                    <div className="mt-xxs max-h-[8rem] overflow-y-auto rounded-control bg-muted/30 p-xs">
                                      <code className="block whitespace-pre-wrap break-words font-mono text-caption">
                                        {helper.command}
                                      </code>
                                    </div>
                                  </div>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button variant="ghost" size="icon" className="shrink-0"
                                        aria-label="Copy command to clipboard"
                                        onClick={() => {
                                          copyToClipboard(helper.command).then((ok) => {
                                            if (ok) toast.info('Copied to clipboard', { autoHideMs: 1500 });
                                          });
                                        }}>
                                        <Copy className="size-4" aria-hidden />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Copy to clipboard</TooltipContent>
                                  </Tooltip>
                                </div>
                              ))}
                            </div>
                          </div>
                        </PopoverContent>
                      </Popover>
                    </TableCell>
                  </TableRow>
                  {open && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={columnCount} className="bg-muted/20 pb-sm pl-lg">
                        <ServiceEvidencePanel hostId={hostId} port={port} evidence={evidenceFor(port)} />
                      </TableCell>
                    </TableRow>
                  )}
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* A failed evidence load must not read as "no TLS here". */}
      {webError && (
        <p className="pt-xs text-caption text-muted-foreground">
          TLS evidence couldn’t be loaded for this host — reopen it to retry.
        </p>
      )}
      {evidenceError && (
        <p className="pt-xs text-caption text-muted-foreground">
          Some of this host&rsquo;s evidence (NetExec results, discovered paths) couldn&rsquo;t be loaded — reopen it to retry.
        </p>
      )}

      {/* v5.299.0 — access results, web pages and paths on no open port
          above: a port since closed, one nothing scanned (a web tool reached
          it), or none named.  They were loaded and never shown. */}
      {unplaced.length > 0 && (
        <div className="space-y-sm pt-sm">
          <h4 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
            Evidence on no open port
          </h4>
          {unplaced.map((g) => (
            <div key={g.portNumber ?? 'none'} className="min-w-0 border-l-2 border-border pl-sm">
              <p className="pb-xxs text-metadata">
                {g.portNumber == null ? (
                  <span className="text-muted-foreground">No port recorded</span>
                ) : (
                  <>
                    <span className="font-mono">{g.portNumber}{g.port ? `/${g.port.protocol}` : ''}</span>
                    <span className="text-caption text-muted-foreground">
                      {' · '}
                      {g.port
                        ? `${g.port.state || 'state unknown'} now${g.port.last_seen ? ` (port last seen ${formatDate(g.port.last_seen)})` : ''}`
                        : 'not in the port list — no port scan recorded it'}
                    </span>
                  </>
                )}
              </p>
              <ServiceEvidencePanel hostId={hostId} port={g.port} evidence={g.evidence} />
            </div>
          ))}
        </div>
      )}

      {/* Closed / filtered: a count until asked for. They are rarely what the
          analyst came for, and each used to cost an accordion and a table. */}
      {notOpen.length > 0 && (
        <div className="pt-xs">
          <button
            type="button"
            onClick={() => setShowNotOpen((v) => !v)}
            aria-expanded={showNotOpen}
            className="rounded text-caption text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {notOpenSummary} · {showNotOpen ? 'hide' : 'show'}
          </button>
          {showNotOpen && (
            <div className="overflow-x-auto pt-xs">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <PortSortHead className="w-[15%]" />
                    <TableHead className="w-[45%]">Service</TableHead>
                    <TableHead className="w-[40%]">State</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortPorts(notOpen).map((port) => (
                    <TableRow key={port.id}>
                      <TableCell className="truncate font-mono text-metadata">
                        {port.port_number}
                        <span className="text-muted-foreground">/{port.protocol}</span>
                      </TableCell>
                      <TableCell className="truncate" title={port.service_name || undefined}>
                        {port.service_name || <span className="text-muted-foreground">unknown</span>}
                      </TableCell>
                      <TableCell className="min-w-0">
                        <div className="flex min-w-0 items-center gap-xs">
                          <Badge variant={stateBadgeVariant(port.state)}>{port.state || 'unknown'}</Badge>
                          {port.reason && (
                            <span className="min-w-0 truncate text-caption text-muted-foreground" title={port.reason}>
                              {port.reason}
                            </span>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      )}
    </InspectorSection>
  );
};

export default PortDetailsCard;
