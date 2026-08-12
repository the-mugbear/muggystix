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
  ArrowDown, ArrowUp, ArrowUpDown, Copy, Network, ShieldAlert, ShieldCheck, Terminal,
} from 'lucide-react';

import {
  getHostWebInterfaces, type Port, type WebInterface,
} from '../../services/api';
import type { ConnectionHelper } from '../../utils/connectionHelpers';
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

const EXPIRY_WARN_DAYS = 30;
const daysUntil = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 86_400_000);
};

/** The cert / TLS facts we surface per port — latest observation wins. */
interface PortTls {
  cert_not_after?: string | null;
  cert_self_signed?: boolean | null;
  cert_subject_org?: string | null;
  tls_weak_protocol?: boolean | null;
}

const hasTlsSignal = (w: WebInterface): boolean =>
  w.cert_not_after != null || w.cert_self_signed != null
  || !!w.cert_subject_org || w.tls_weak_protocol === true;

const TlsCell: React.FC<{ tls?: PortTls }> = ({ tls }) => {
  if (!tls) return <span className="text-caption text-muted-foreground">—</span>;
  const days = daysUntil(tls.cert_not_after);
  const expired = days !== null && days < 0;
  const expiringSoon = days !== null && days >= 0 && days <= EXPIRY_WARN_DAYS;
  return (
    <div className="flex flex-col gap-xxs">
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
        <span className="flex items-center gap-xxs truncate text-caption text-muted-foreground"
          title={`Certificate subject organisation (CA-validated): ${tls.cert_subject_org}`}>
          <ShieldCheck className="size-3 shrink-0 text-success" aria-hidden />
          {tls.cert_subject_org}
        </span>
      )}
    </div>
  );
};

interface PortDetailsCardProps {
  hostId: number;
  hostIp: string | null;
  openPorts: Port[];
  closedPorts: Port[];
  filteredPorts: Port[];
  connectionHelpersByPort: Map<number, ConnectionHelper[]>;
}

const PortDetailsCard: React.FC<PortDetailsCardProps> = ({
  hostId, hostIp, openPorts, closedPorts, filteredPorts, connectionHelpersByPort,
}) => {
  const toast = useToast();
  const [portSortDir, setPortSortDir] = useState<'asc' | 'desc' | null>(null);
  const [tlsByPort, setTlsByPort] = useState<Map<number, PortTls>>(new Map());

  // Join cert/TLS evidence from the host's web interfaces onto ports by
  // ``port_id``, latest observation winning. Non-fatal: on failure the TLS
  // column simply stays empty rather than breaking the port table.
  useEffect(() => {
    let cancelled = false;
    getHostWebInterfaces(hostId)
      .then((interfaces) => {
        if (cancelled) return;
        const map = new Map<number, PortTls & { _ts: number }>();
        for (const w of interfaces) {
          if (w.port_id == null || !hasTlsSignal(w)) continue;
          const ts = w.last_seen ? new Date(w.last_seen).getTime() : 0;
          const prev = map.get(w.port_id);
          if (!prev || ts >= prev._ts) {
            map.set(w.port_id, {
              _ts: ts,
              cert_not_after: w.cert_not_after,
              cert_self_signed: w.cert_self_signed,
              cert_subject_org: w.cert_subject_org,
              tls_weak_protocol: w.tls_weak_protocol,
            });
          }
        }
        setTlsByPort(map);
      })
      .catch(() => { /* TLS column stays empty; not worth a toast */ });
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
                        <PortSortHead className="w-[9%]" />
                        <TableHead className="w-[8%]">Proto</TableHead>
                        <TableHead className="w-[17%]">Service</TableHead>
                        <TableHead className="w-[26%]">Version</TableHead>
                        <TableHead className="w-[10%]">State</TableHead>
                        <TableHead className="w-[18%]">TLS</TableHead>
                        <TableHead className="w-[12%] text-center">Helpers</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortPorts(openPorts).map((port) => {
                        const helpers = connectionHelpersByPort.get(port.id) ?? [];
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
                            <TableCell>
                              <TlsCell tls={tlsByPort.get(port.id)} />
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
                                    <h4 className="mb-xs text-subheading">
                                      Commands for {hostIp}:{port.port_number}
                                    </h4>
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
