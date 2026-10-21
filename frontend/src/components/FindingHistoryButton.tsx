/**
 * A clock button that opens the finding's history in a popover: who changed
 * its status, when and why (the summary captured on terminal moves), its
 * endpoint changes and — 5.413.0 — each edit of a field, named by its line.
 * What an edit changed (before and after) is on the finding page.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { History, Loader2 } from 'lucide-react';
import { STATUS_LABEL } from '../utils/findingStatus';

import { getFindingHistory } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { safeFallback } from '../utils/uiStyles';
import { formatTimestamp } from '../utils/relativeTime';

const label = (s: string | null) => (s ? (STATUS_LABEL as Record<string, string>)[s] ?? s : '—');

export const FindingHistoryButton: React.FC<{ findingId: number }> = ({ findingId }) => {
  const projectId = useProjectId();
  const [open, setOpen] = React.useState(false);
  // Read on first open and kept while the button is there: a write that adds
  // a line invalidates this query, so the next open reads it again.
  const query = useQuery({
    queryKey: ['getFindingHistory', projectId, findingId],
    queryFn: ({ signal }) => getFindingHistory(projectId, findingId, signal),
    enabled: open,
    staleTime: Infinity,
  });
  const rows = query.data ?? null;
  const loading = query.isFetching;
  const error = queryErrorText(query.error, 'Failed to load history.');

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Finding history">
              <History className="size-4" aria-hidden />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Finding history</TooltipContent>
      </Tooltip>
      <PopoverContent className="w-80 max-w-[90vw]">
        <p className="mb-xs text-metadata font-semibold">History</p>
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
                {/* An endpoint or edit line leaves the status as it was. */}
                {r.from_status !== r.to_status || !r.summary ? (
                  <div className="text-caption">
                    <span className="text-muted-foreground">{label(r.from_status)}</span>
                    {' → '}
                    <span className="font-medium text-foreground">{label(r.to_status)}</span>
                  </div>
                ) : null}
                <div className="text-caption text-muted-foreground">
                  {safeFallback(r.changed_by_name, 'Unknown')} · {formatTimestamp(r.created_at)}
                </div>
                {r.summary && <p className="mt-xxs whitespace-pre-wrap text-caption">{r.summary}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-caption text-muted-foreground">Nothing recorded yet.</p>
        )}
      </PopoverContent>
    </Popover>
  );
};

export default FindingHistoryButton;
