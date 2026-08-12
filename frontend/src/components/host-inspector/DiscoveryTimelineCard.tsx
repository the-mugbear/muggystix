/**
 * Discovery timeline — every scan that observed this host, newest first.
 *
 * Moved out of the host's identity header (where it competed with the ports /
 * findings an operator actually opens the host for) into the lower audit tier.
 * The old inline card hard-capped at 3 with no way to see the rest; this shows
 * the 3 most recent by default and expands to the full history on demand — the
 * scan window, tool, and command line are the SOC-correlation evidence, so
 * "seen in 20 scans" must be fully reachable, not truncated.
 */
import React, { useMemo, useState } from 'react';
import { copyToClipboard } from '../../utils/clipboard';
import { ChevronDown, ChevronUp, Copy, History } from 'lucide-react';

import type { HostDiscovery } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';

const formatDateTime = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString() : 'Unknown date';

const COLLAPSED_COUNT = 3;

const DiscoveryTimelineCard: React.FC<{ discoveries: HostDiscovery[] }> = ({ discoveries }) => {
  const toast = useToast();
  const [expanded, setExpanded] = useState(false);

  const sorted = useMemo(
    () =>
      [...discoveries].sort((a, b) => {
        const t = (e: HostDiscovery) =>
          new Date(e.scan_end || e.scan_start || e.discovered_at || 0).getTime();
        return t(b) - t(a);
      }),
    [discoveries],
  );

  if (sorted.length === 0) return null;

  const shown = expanded ? sorted : sorted.slice(0, COLLAPSED_COUNT);
  const hiddenCount = sorted.length - COLLAPSED_COUNT;

  return (
    <Card id="host-detail-discovery">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <History className="size-5 text-muted-foreground" aria-hidden />
          <CardTitle>
            Discovered in {sorted.length} scan{sorted.length === 1 ? '' : 's'}
          </CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-xs">
        {shown.map((entry) => {
          // SOC correlation needs the scan window (when the tool was probing),
          // not the ingest time; fall back to discovered_at only when the
          // parser couldn't extract start/end (masscan list, some gnmap).
          const hasWindow = entry.scan_start || entry.scan_end;
          return (
            <div
              key={`disc-${entry.scan_id}-${entry.discovered_at ?? ''}`}
              className="rounded-control border border-border/60 bg-background/40 px-xs py-xxs"
            >
              <div className="flex items-center gap-xs">
                <Badge variant="outline">{entry.scan_type || entry.tool_name || 'Scan'}</Badge>
                <span className="min-w-0 flex-1 truncate text-caption"
                  title={entry.scan_filename || `Scan #${entry.scan_id}`}>
                  {entry.scan_filename || `Scan #${entry.scan_id}`}
                </span>
              </div>
              <dl className="mt-xxs grid grid-cols-[auto_1fr] gap-x-xs gap-y-0 text-metadata text-muted-foreground">
                {hasWindow ? (
                  <>
                    <dt className="font-medium">Scan start:</dt>
                    <dd className="tabular-nums">{entry.scan_start ? formatDateTime(entry.scan_start) : '—'}</dd>
                    <dt className="font-medium">Scan end:</dt>
                    <dd className="tabular-nums">{entry.scan_end ? formatDateTime(entry.scan_end) : '—'}</dd>
                  </>
                ) : (
                  <>
                    <dt className="font-medium" title="Scan tool did not record start/end; this is when the file was ingested.">
                      Ingested:
                    </dt>
                    <dd className="tabular-nums">{formatDateTime(entry.discovered_at)}</dd>
                  </>
                )}
                {entry.command_line && (
                  <>
                    <dt className="font-medium">Command:</dt>
                    <dd className="flex min-w-0 items-center gap-1">
                      <span className="min-w-0 truncate font-mono" title={entry.command_line}>
                        {entry.command_line}
                      </span>
                      <Button variant="ghost" size="icon"
                        className="size-6 shrink-0 text-muted-foreground hover:text-foreground"
                        aria-label="Copy scan command to clipboard" title="Copy command"
                        onClick={() => {
                          copyToClipboard(entry.command_line as string).then((ok) => {
                            if (ok) toast.info('Command copied', { autoHideMs: 1500 });
                          });
                        }}>
                        <Copy className="size-3.5" aria-hidden />
                      </Button>
                    </dd>
                  </>
                )}
              </dl>
            </div>
          );
        })}

        {hiddenCount > 0 && (
          <Button variant="ghost" size="sm" className="w-full justify-center"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}>
            {expanded
              ? <>Show fewer <ChevronUp className="size-3.5" aria-hidden /></>
              : <>Show all {sorted.length} scans <ChevronDown className="size-3.5" aria-hidden /></>}
          </Button>
        )}
      </CardContent>
    </Card>
  );
};

export default DiscoveryTimelineCard;
