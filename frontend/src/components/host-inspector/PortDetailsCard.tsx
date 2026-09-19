/**
 * Port Details — the host's ports grouped by state (open / closed / filtered),
 * sortable by number, with per-port connection-helper commands.
 *
 * Extracted from HostInspector while adding the per-port **TLS column**: cert
 * expiry, self-signed / weak-protocol state, and the CA-validated subject org,
 * joined from the host's web interfaces by ``port_id``. Previously that cert
 * evidence lived only in the (URL-grouped) Provenance card, so an operator
 * triaging port 8443 saw the service banner but nothing about its certificate.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { copyToClipboard } from '../../utils/clipboard';
import {
  ArrowDown, ArrowUp, ArrowUpDown, Copy, HelpCircle, Lock, Network, ShieldAlert, ShieldCheck, Terminal,
} from 'lucide-react';

import { getHostWebInterfaces, type Port } from '../../services/api';
import { getConnectionHelpers, isSafeHostname, type ConnectionHelper } from '../../utils/connectionHelpers';
import {
  EXPIRY_WARN_DAYS, daysUntil, endpointsByPort, summariseEndpointTls,
  type EndpointTls, type PortEndpoint,
} from '../../utils/portEndpoints';
import { formatRelativeTime } from '../../utils/relativeTime';
import { NOT_REVALIDATED_LABEL, NOT_REVALIDATED_TITLE, portFreshness } from '../../utils/evidenceFreshness';
import { useToast } from '../../contexts/ToastContext';
import {
  Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '../ui/accordion';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

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
  webError?: boolean;
}> = ({ endpoints, portNumber, tunnel, webError }) => {
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
    // tunnel attribute so a TLS-wrapped service on a non-standard port is still
    // marked, and keep "no evidence" (—) distinct from "evidence failed to load".
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
    if (webError) {
      return (
        <Tooltip>
          <TooltipTrigger asChild>
            <HelpCircle className="size-4 text-muted-foreground" tabIndex={0} aria-label="TLS evidence unavailable" />
          </TooltipTrigger>
          <TooltipContent>TLS evidence couldn’t be loaded for this host — reopen it to retry.</TooltipContent>
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
}

const PortDetailsCard: React.FC<PortDetailsCardProps> = ({
  hostId, hostIp, hostname = null, hostLastSeen = null, openPorts, closedPorts, filteredPorts, connectionHelpersByPort,
}) => {
  const toast = useToast();
  const [portSortDir, setPortSortDir] = useState<'asc' | 'desc' | null>(null);
  const [endpoints, setEndpoints] = useState<Map<number, PortEndpoint[]>>(new Map());
  const [webError, setWebError] = useState(false);
  // Which endpoint a port's commands address; absent = the default below.
  const [helperTarget, setHelperTarget] = useState<Record<number, string>>({});

  // Join the host's web interfaces onto ports by ``port_id`` as NAMED
  // ENDPOINTS (utils/portEndpoints): newest observation per (port, name),
  // nothing merged across names. Non-fatal: on failure the column falls back
  // to nmap's tunnel attribute and marks the load as failed (so a fetch error
  // reads differently from "no TLS evidence") rather than breaking the port
  // table. State is reset per host — the inspector stays mounted across
  // prev/next, so a stale map would otherwise bleed onto the next host.
  useEffect(() => {
    let cancelled = false;
    setEndpoints(new Map());
    setHelperTarget({});
    setWebError(false);
    getHostWebInterfaces(hostId)
      .then((interfaces) => {
        if (!cancelled) setEndpoints(endpointsByPort(interfaces));
      })
      .catch(() => { if (!cancelled) setWebError(true); });
    return () => { cancelled = true; };
  }, [hostId]);

  const sortPorts = useMemo(
    () => <T extends { port_number: number | null }>(arr: T[]): T[] => {
      if (!portSortDir) return arr;
      const s = [...arr].sort((a, b) => (a.port_number ?? 0) - (b.port_number ?? 0));
      return portSortDir === 'desc' ? s.reverse() : s;
    },
    [portSortDir],
  );

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

  return (
    <Card id="host-detail-ports">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Network className="size-5 text-primary" aria-hidden />
          <CardTitle>Port Details</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <Accordion type="multiple" defaultValue={openPorts.length > 0 ? ['open'] : []}>
          {openPorts.length > 0 && (
            <AccordionItem value="open">
              <AccordionTrigger>Open Ports ({openPorts.length})</AccordionTrigger>
              <AccordionContent>
                <div className="overflow-x-auto">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <PortSortHead className="w-[8%]" />
                        <TableHead className="w-[7%]">Proto</TableHead>
                        <TableHead className="w-[16%]">Service</TableHead>
                        <TableHead className="w-[22%]">Version</TableHead>
                        <TableHead className="w-[9%]">State</TableHead>
                        <TableHead className="w-[12%]" title="When this port itself was last observed. Older than the host's last observation means newer evidence did not revalidate it — not that it was checked and found closed.">Seen</TableHead>
                        <TableHead className="w-[16%]">TLS</TableHead>
                        <TableHead className="w-[10%] text-center">Helpers</TableHead>
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
                        return (
                          <TableRow key={port.id}>
                            <TableCell>{port.port_number}</TableCell>
                            <TableCell>{port.protocol}</TableCell>
                            <TableCell className="truncate" title={port.service_name || undefined}>
                              <div className="truncate">{port.service_name || 'Unknown'}</div>
                              {(port.service_method || (port.service_conf != null && String(port.service_conf) !== '')) && (
                                <div className="truncate text-caption text-muted-foreground" title="How the service was detected (and nmap confidence 0–10)">
                                  {[
                                    port.service_method,
                                    port.service_conf != null && String(port.service_conf) !== ''
                                      ? `conf ${port.service_conf}`
                                      : null,
                                  ].filter(Boolean).join(' · ')}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="max-w-[16rem] truncate" title={port.service_extrainfo || undefined}>
                              {port.service_product && port.service_version
                                ? `${port.service_product} ${port.service_version}`
                                : port.service_product || 'N/A'}
                              {port.service_extrainfo && (
                                <span className="ml-xxs text-caption text-muted-foreground">
                                  ({port.service_extrainfo})
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge variant={stateBadgeVariant(port.state)}>
                                {port.state || 'unknown'}
                              </Badge>
                              {port.reason && (
                                <div className="truncate text-caption text-muted-foreground" title={`Why this port is ${port.state || 'in this state'}: ${port.reason}`}>
                                  {port.reason}
                                </div>
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
                            <TableCell>
                              <TlsCell
                                endpoints={portEndpoints}
                                portNumber={port.port_number}
                                tunnel={port.service_tunnel}
                                webError={webError}
                              />
                            </TableCell>
                            <TableCell className="text-center">
                              <Popover>
                                <PopoverTrigger asChild>
                                  <Button variant="ghost" size="icon"
                                    aria-label={`Connection helpers for port ${port.port_number}`}>
                                    <Terminal className="size-4" aria-hidden />
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-[34rem] max-w-[90vw]" align="start">
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
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </AccordionContent>
            </AccordionItem>
          )}

          {closedPorts.length > 0 && (
            <AccordionItem value="closed">
              <AccordionTrigger>Closed Ports ({closedPorts.length})</AccordionTrigger>
              <AccordionContent>
                <div className="overflow-x-auto">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <PortSortHead className="w-[15%]" />
                        <TableHead className="w-[15%]">Proto</TableHead>
                        <TableHead className="w-[45%]">Service</TableHead>
                        <TableHead className="w-[25%]">State</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortPorts(closedPorts).map((port) => (
                        <TableRow key={port.id}>
                          <TableCell>{port.port_number}</TableCell>
                          <TableCell>{port.protocol}</TableCell>
                          <TableCell>{port.service_name || 'Unknown'}</TableCell>
                          <TableCell>
                            <Badge variant={stateBadgeVariant(port.state)}>
                              {port.state || 'unknown'}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </AccordionContent>
            </AccordionItem>
          )}

          {filteredPorts.length > 0 && (
            <AccordionItem value="filtered">
              <AccordionTrigger>Filtered Ports ({filteredPorts.length})</AccordionTrigger>
              <AccordionContent>
                <div className="overflow-x-auto">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <PortSortHead className="w-[15%]" />
                        <TableHead className="w-[15%]">Proto</TableHead>
                        <TableHead className="w-[45%]">Service</TableHead>
                        <TableHead className="w-[25%]">State</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortPorts(filteredPorts).map((port) => (
                        <TableRow key={port.id}>
                          <TableCell>{port.port_number}</TableCell>
                          <TableCell>{port.protocol}</TableCell>
                          <TableCell>{port.service_name || 'Unknown'}</TableCell>
                          <TableCell>
                            <Badge variant={stateBadgeVariant(port.state)}>
                              {port.state || 'unknown'}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </AccordionContent>
            </AccordionItem>
          )}
        </Accordion>
      </CardContent>
    </Card>
  );
};

export default PortDetailsCard;
