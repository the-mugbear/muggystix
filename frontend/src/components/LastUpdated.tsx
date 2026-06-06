/**
 * Freshness indicator with manual + optional auto-refresh.
 *
 * Surfaces "Updated 20s ago" alongside a refresh button so operators
 * always know how stale a dashboard or job list is.  Pages that want
 * background polling can pass `defaultAutoRefresh` and the user can
 * toggle it from the same control without round-tripping through a
 * settings menu.
 *
 * The component owns the auto-refresh interval — it calls `onRefresh`
 * on tick when enabled.  Pages just have to provide:
 *   - the timestamp of the last successful fetch
 *   - the refresh callback (must be stable; wrap in useCallback if it
 *     closes over state)
 *   - a label (e.g. "Dashboard" or "Jobs") for the auto-refresh
 *     tooltip
 */

import React, { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { cn } from '../utils/cn';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Switch } from './ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useNow } from '../hooks/useNow';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';

export interface LastUpdatedProps {
  /** Timestamp of the most recent successful fetch (Date or ISO string). null = never fetched. */
  lastFetched: Date | string | null;
  /** Called when the user clicks refresh, or when the auto-refresh interval ticks. */
  onRefresh: () => void;
  /** True while a fetch is in flight — disables the refresh button and dims the timestamp. */
  isLoading?: boolean;
  /** Auto-refresh interval in milliseconds. Default 60000 (60s). */
  intervalMs?: number;
  /** Whether auto-refresh starts enabled. Default false (manual only). */
  defaultAutoRefresh?: boolean;
  /** Short label used in the auto-refresh switch tooltip ("Auto-refresh dashboard"). */
  label?: string;
  /** Compact mode hides the auto-refresh toggle and only shows the timestamp + button. */
  compact?: boolean;
}

function formatRelative(value: Date | string | null): string {
  if (!value) return 'never';
  const d = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return 'never';
  const seconds = Math.floor((Date.now() - d.getTime()) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export const LastUpdated: React.FC<LastUpdatedProps> = ({
  lastFetched,
  onRefresh,
  isLoading = false,
  intervalMs = 60000,
  defaultAutoRefresh = false,
  label = 'data',
  compact = false,
}) => {
  const [autoRefresh, setAutoRefresh] = useState(defaultAutoRefresh);
  // Shared 10s "now" tick — every LastUpdated on the page subscribes
  // to the same underlying setInterval registered by useNow, instead
  // of each instance owning its own (audit PRF·L2).
  useNow(10_000);

  // Auto-refresh — visibility-gated so backgrounded tabs stop firing.
  useVisibilityPoll(onRefresh, autoRefresh ? intervalMs : null);

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
              <Switch
                id={switchId}
                checked={autoRefresh}
                onCheckedChange={setAutoRefresh}
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
