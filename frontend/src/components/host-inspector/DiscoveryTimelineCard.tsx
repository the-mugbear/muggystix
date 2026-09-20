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
import { InspectorSection } from './InspectorSection';

const formatDateTime = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString() : 'Unknown date';

// A scan is one line since v5.241.0, so the preview affords five.
const COLLAPSED_COUNT = 5;

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
    <InspectorSection
      id="host-detail-discovery"
      title={`Discovered in ${sorted.length} scan${sorted.length === 1 ? '' : 's'}`}
      icon={<History className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
    >
      <div className="divide-y divide-border">
        {shown.map((entry) => {
          // SOC correlation needs the scan window (when the tool was probing),
          // not the ingest time; fall back to discovered_at only when the
          // parser couldn't extract start/end (masscan list, some gnmap).
          const hasWindow = entry.scan_start || entry.scan_end;
          return (
            // v5.241.0 — one divided line per scan (type · file · when), not a
            // bordered two-line box; the command is a second line only when
            // the scan recorded one.
            <div key={`disc-${entry.scan_id}-${entry.discovered_at ?? ''}`} className="py-xxs">
              <div className="flex min-w-0 items-center gap-xs">
                <Badge variant="outline" className="shrink-0">{entry.scan_type || entry.tool_name || 'Scan'}</Badge>
                <span className="min-w-0 flex-1 truncate text-caption"
                  title={entry.scan_filename || `Scan #${entry.scan_id}`}>
                  {entry.scan_filename || `Scan #${entry.scan_id}`}
                </span>
                {hasWindow ? (
                  <span className="shrink-0 text-caption tabular-nums text-muted-foreground"
                    title="When the tool was probing (scan start → scan end)">
                    {entry.scan_start ? formatDateTime(entry.scan_start) : '—'}
                    {' → '}
                    {entry.scan_end ? formatDateTime(entry.scan_end) : '—'}
                  </span>
                ) : (
                  <span className="shrink-0 text-caption tabular-nums text-muted-foreground"
                    title="Scan tool did not record start/end; this is when the file was ingested.">
                    ingested {formatDateTime(entry.discovered_at)}
                  </span>
                )}
              </div>
              {entry.command_line && (
                <div className="flex min-w-0 items-center gap-1 text-caption text-muted-foreground">
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
                </div>
              )}
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
      </div>
    </InspectorSection>
  );
};

export default DiscoveryTimelineCard;
