/**
 * A clock button that opens the finding's disposition trail in a popover.
 * The rows were always recorded on each status transition but had no read
 * path until the GET /findings/:id/history endpoint — this surfaces who
 * changed status, when, and why (the summary captured on terminal moves).
 */
import React from 'react';
import { History, Loader2 } from 'lucide-react';

import {
  getFindingHistory,
  type FindingStatusHistoryEntry,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { safeFallback } from '../utils/uiStyles';

const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  retest: 'Retest',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  remediated: 'Remediated',
};
const label = (s: string | null) => (s ? STATUS_LABEL[s] ?? s : '—');

export const FindingHistoryButton: React.FC<{ findingId: number }> = ({ findingId }) => {
  const [open, setOpen] = React.useState(false);
  const [rows, setRows] = React.useState<FindingStatusHistoryEntry[] | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // Lazy-load on first open; the trail only changes when status changes.
  const load = React.useCallback(() => {
    setLoading(true);
    getFindingHistory(findingId)
      .then((r) => { setRows(r); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Failed to load history.')))
      .finally(() => setLoading(false));
  }, [findingId]);

  return (
    <Popover
      open={open}
      onOpenChange={(v) => { setOpen(v); if (v && rows === null) load(); }}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Status history">
              <History className="size-4" aria-hidden />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Status history</TooltipContent>
      </Tooltip>
      <PopoverContent className="w-80 max-w-[90vw]">
        <p className="mb-xs text-metadata font-semibold">Disposition history</p>
        {loading ? (
          <div className="flex items-center gap-xs text-caption text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading…
          </div>
        ) : error ? (
          <p className="text-caption text-destructive">{error}</p>
        ) : rows && rows.length > 0 ? (
          <ul className="flex flex-col gap-sm">
            {rows.map((r) => (
              <li key={r.id} className="border-l-2 border-border pl-xs">
                <div className="text-caption">
                  <span className="text-muted-foreground">{label(r.from_status)}</span>
                  {' → '}
                  <span className="font-medium text-foreground">{label(r.to_status)}</span>
                </div>
                <div className="text-caption text-muted-foreground">
                  {safeFallback(r.changed_by_name, 'Unknown')} · {new Date(r.created_at).toLocaleString()}
                </div>
                {r.summary && <p className="mt-xxs whitespace-pre-wrap text-caption">{r.summary}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-caption text-muted-foreground">No status changes recorded yet.</p>
        )}
      </PopoverContent>
    </Popover>
  );
};

export default FindingHistoryButton;
