/**
 * Freshness indicator with manual + optional auto-refresh.
 *
 * Surfaces "Updated 20s ago" alongside a refresh button so operators
 * always know how stale a dashboard or job list is.  A page that polls
 * in the background on request shows the "Auto" switch in the same
 * control, without round-tripping through a settings menu.
 *
 * Pages provide:
 *   - the timestamp of the last successful fetch (a query's `dataUpdatedAt`)
 *   - the refresh callback (the query's `refetch`)
 *   - a label (e.g. "Dashboard" or "Jobs") for the auto-refresh tooltip
 *
 * **Auto-refresh is the page's query polling** (5.351.0): the page holds the
 * switch (`autoRefresh` + `onAutoRefreshChange`) and gives its query
 * `...pollEvery(autoRefresh ? intervalMs : null)` (lib/query).  This
 * component runs no timer: it only shows the switch (`pages/Scans.tsx` is
 * the one caller of the non-compact form).
 */

import React from 'react';
import { RefreshCw } from 'lucide-react';
import { cn } from '../utils/cn';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Switch } from './ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useNow } from '../hooks/useNow';
import { formatRelativeTime } from '../utils/relativeTime';

export interface LastUpdatedProps {
  /** Timestamp of the most recent successful fetch (Date or ISO string). null = never fetched. */
  lastFetched: Date | string | null;
  /** Called when the user clicks refresh. */
  onRefresh: () => void;
  /** True while a fetch is in flight — disables the refresh button and dims the timestamp. */
  isLoading?: boolean;
  /** Auto-refresh interval in milliseconds, as the switch's tooltip says it. Default 60000 (60s). */
  intervalMs?: number;
  /** Whether the page's query is polling (the switch's position).  Non-compact only. */
  autoRefresh?: boolean;
  /** The reader moved the switch: the page turns its query's poll on or off.  Non-compact only. */
  onAutoRefreshChange?: (on: boolean) => void;
  /** Short label used in the auto-refresh switch tooltip ("Auto-refresh dashboard"). */
  label?: string;
  /** Compact mode hides the auto-refresh toggle and only shows the timestamp + button. */
  compact?: boolean;
}

/** Anything under five seconds reads as "just now": this is a refresh
 *  indicator, and a flickering "0s ago" is noise rather than information. */
function formatRelative(value: Date | string | null): string {
  return formatRelativeTime(value, {
    withSeconds: true,
    justNowBelowMs: 5_000,
    fallback: 'never',
  });
}

export const LastUpdated: React.FC<LastUpdatedProps> = ({
  lastFetched,
  onRefresh,
  isLoading = false,
  intervalMs = 60000,
  autoRefresh = false,
  onAutoRefreshChange,
  label = 'data',
  compact = false,
}) => {
  // Shared 10s "now" tick — every LastUpdated on the page subscribes
  // to the same underlying setInterval registered by useNow, instead
  // of each instance owning its own (audit PRF·L2).
  useNow(10_000);

  const relative = formatRelative(lastFetched);
  const switchId = React.useId();

  return (
    <div className="flex flex-wrap items-center gap-xs">
      <span
        className={cn('text-caption text-muted-foreground', isLoading && 'opacity-50')}
      >
        Updated {relative}
      </span>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            onClick={onRefresh}
            disabled={isLoading}
            // Include the target in the label so multiple refreshable
            // modules on the same page are distinguishable to screen
            // readers ("Refresh dashboard" vs "Refresh notifications").
            aria-label={label ? `Refresh ${label}` : 'Refresh'}
          >
            <RefreshCw className={cn('size-4', isLoading && 'animate-spin')} aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{isLoading ? 'Refreshing…' : 'Refresh now'}</TooltipContent>
      </Tooltip>
      {!compact && (
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center gap-xxs">
              {/* Named for what it switches ("Auto-refresh ingestion jobs"):
                  the visible "Auto" alone said nothing out of context.  The
                  name starts with the visible word, so speech input finds it. */}
              <Switch
                id={switchId}
                checked={autoRefresh}
                onCheckedChange={onAutoRefreshChange}
                aria-label={`Auto-refresh ${label}`}
              />
              <Label htmlFor={switchId} className="text-caption text-muted-foreground">
                Auto
              </Label>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            Auto-refresh {label} every {Math.round(intervalMs / 1000)}s
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
};

export default LastUpdated;
