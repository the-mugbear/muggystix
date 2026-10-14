/**
 * "Update project defaults…" on a draft (the report workspace, owner
 * 2026-10-14: "if a user modifies a draft they may want that change to
 * become the standard for future drafts").  The ONE way a draft writes the
 * project's report defaults.
 *
 * Lists the details this report has as its own — saved or still pending —
 * each with the project's value now (and where it comes from) → this
 * report's, and how many other drafts follow it (an estimate when read; the
 * push-up re-reads the defaults).  The ticked ones are promoted in one
 * request (`POST …/promote`), after which this draft follows them again.
 * ONLY those keys leave the page's pending edits (`onPromoted`); every other
 * pending edit stays, unsaved.  A failure changes nothing.
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  getClientReportFollowers, promoteClientReportDetails, type ClientReport, type EngagementOverrides,
} from '../../../services/api';
import { useToast } from '../../../contexts/ToastContext';
import { useProjectId } from '../../../hooks/useProjectId';
import { invalidateReads, queryErrorText } from '../../../lib/query';
import { formatApiError } from '../../../utils/apiErrors';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../../ui/dialog';
import {
  DETAIL_LABELS, type DetailContext, type DetailKey, current, sourceCaption, toSave,
} from './detailModel';

/** A value in a line: a text shortened, a list by its names, nothing said. */
export const valueWords = (v: unknown): string => {
  if (Array.isArray(v)) {
    if (!v.length) return 'nobody';
    const names = v.map((r: { name: string }) => r.name);
    return names.slice(0, 4).join(', ') + (names.length > 4 ? ` and ${names.length - 4} more` : '');
  }
  const t = typeof v === 'string' ? v.trim() : '';
  if (!t) return 'not set';
  return t.length > 120 ? `“${t.slice(0, 120)}…”` : `“${t}”`;
};

const UpdateDefaultsDialog: React.FC<{
  open: boolean;
  onClose: () => void;
  report: ClientReport;
  ctx: DetailContext;
  /** The details this report has as its own (saved or pending). */
  keys: DetailKey[];
  onPromoted: (updated: ClientReport, keys: DetailKey[]) => void;
}> = ({ open, onClose, report, ctx, keys, onPromoted }) => {
  const projectId = useProjectId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [unticked, setUnticked] = useState<Set<DetailKey>>(new Set());
  const chosen = keys.filter((k) => !unticked.has(k));

  const followers = useQuery({
    queryKey: ['getClientReportFollowers', projectId, report.id],
    queryFn: ({ signal }) => getClientReportFollowers(projectId, report.id, signal),
    enabled: open,
  });
  const followersError = queryErrorText(followers.error, 'Could not count the drafts that follow them.');

  const values = useMemo(() => {
    const { settings } = toSave(ctx);
    return Object.fromEntries(chosen.map((k) => [k, settings[k]])) as EngagementOverrides;
  }, [ctx, chosen]);

  const promoting = useMutation({
    mutationFn: () => promoteClientReportDetails(projectId, report.id, values),
    onSuccess: (updated) => {
      onPromoted(updated, chosen);
      void invalidateReads(queryClient, 'getReportProfile', 'listClientReports', 'getClientReportFollowers');
      toast.success(`${chosen.length === 1 ? DETAIL_LABELS[chosen[0]] + ' is' : `${chosen.length} details are`} now the project’s default, and this draft follows ${chosen.length === 1 ? 'it' : 'them'} again.`);
      setUnticked(new Set());
      onClose();
    },
    // Nothing changed: every edit stays as it was.
    onError: (err) => toast.error(formatApiError(err, 'Could not update the project’s defaults.')),
  });

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !promoting.isPending) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Update project defaults</DialogTitle>
          <DialogDescription>
            The ticked details become the project’s report defaults. Every draft that follows them prints the new
            value; issued reports do not change. This draft then follows them again. Your other unsaved changes
            stay as they are.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {followersError && <p role="alert" className="mb-xs text-caption text-destructive">{followersError}</p>}
          <ul className="space-y-sm" aria-label="Details to make the project’s defaults">
            {keys.map((key) => {
              const before = ctx.followed?.[key];
              const source = sourceCaption(ctx.sources?.[key]);
              const others = followers.data?.[key];
              return (
                <li key={key} className="flex min-w-0 items-start gap-sm" data-testid={`promote-${key}`}>
                  <Checkbox id={`promote-${key}`} checked={!unticked.has(key)} disabled={promoting.isPending}
                    onCheckedChange={(on) => setUnticked((s) => {
                      const next = new Set(s);
                      if (on) next.delete(key); else next.add(key);
                      return next;
                    })} />
                  <label htmlFor={`promote-${key}`} className="min-w-0 flex-1 space-y-xxs">
                    <span className="block text-body font-medium">{DETAIL_LABELS[key]}</span>
                    <span className="block break-words text-caption text-muted-foreground [overflow-wrap:anywhere]">
                      The project’s now: {valueWords(before)}{source ? ` (${source.toLowerCase()})` : ''}
                    </span>
                    <span className="block break-words text-caption [overflow-wrap:anywhere]">
                      This report’s: {valueWords(current(ctx, key))}
                    </span>
                    <span className="block text-caption text-muted-foreground">
                      {others == null ? 'Counting the drafts that follow it…'
                        : others === 0 ? 'No other draft follows it.'
                          : `${others.toLocaleString()} other draft${others === 1 ? ' follows' : 's follow'} it and will print the new value.`}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={promoting.isPending}>Cancel</Button>
          <Button onClick={() => promoting.mutate()} disabled={promoting.isPending || chosen.length === 0}>
            {promoting.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Update {chosen.length === 1 ? '1 default' : `${chosen.length} defaults`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default UpdateDefaultsDialog;
