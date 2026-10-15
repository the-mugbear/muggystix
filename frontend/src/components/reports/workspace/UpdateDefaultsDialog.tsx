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
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  getClientReportFollowers, promoteClientReportDetails, type ClientReport, type EngagementOverrides,
} from '../../../services/api';
import { useToast } from '../../../contexts/ToastContext';
import { useProjectId } from '../../../hooks/useProjectId';
import { invalidateReads, queryErrorText } from '../../../lib/query';
import { asAxiosError, formatApiError } from '../../../utils/apiErrors';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../../ui/dialog';
import {
  DETAIL_LABELS, type DetailContext, type DetailKey, baseline, shown, sourceCaption, withPending,
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
  const settings = withPending(ctx);
  // An empty team cannot be the project's: with no team saved, a draft
  // lists the project's analysts and admins (review 2026-10-14, H3 — the
  // server refuses it too).  It stays this report's own.
  const cannot = (key: DetailKey) => key === 'testers' && Array.isArray(settings.testers) && !settings.testers.length;
  const chosen = keys.filter((k) => !unticked.has(k) && !cannot(k));

  const followers = useQuery({
    queryKey: ['getClientReportFollowers', projectId, report.id],
    queryFn: ({ signal }) => getClientReportFollowers(projectId, report.id, signal),
    enabled: open,
  });
  const followersError = queryErrorText(followers.error, 'Could not count the drafts that follow them.');

  const promoting = useMutation({
    // With each value, what the draft printed when this page read it (its
    // saved override, or what it followed): the server refuses the push-up
    // when someone has saved another value since (second review 2026-10-14,
    // H1 — a stale page reverted their override and spread the old value).
    mutationFn: () => promoteClientReportDetails(
      projectId, report.id,
      Object.fromEntries(chosen.map((k) => [k, settings[k]])) as EngagementOverrides,
      Object.fromEntries(chosen.map((k) => [k, baseline(ctx, k) ?? null])) as EngagementOverrides,
      // And the project's value it showed ("The project's now"): one set on
      // the defaults page since is not silently replaced (third review
      // 2026-10-15, R9).
      Object.fromEntries(chosen.map((k) => [k, ctx.followed?.[k] ?? null])) as EngagementOverrides,
    ),
    onSuccess: (updated) => {
      onPromoted(updated, chosen);
      void invalidateReads(queryClient, 'getReportProfile', 'listClientReports', 'getClientReportFollowers');
      toast.success(`${chosen.length === 1 ? DETAIL_LABELS[chosen[0]] + ' is' : `${chosen.length} details are`} now the project’s default, and this draft follows ${chosen.length === 1 ? 'it' : 'them'} again.`);
      onClose();
    },
    // Nothing changed: every edit stays as it was.  A draft that prints
    // something else now (409) is read again, so the dialog shows its saved
    // values rather than the stale ones it was refused for (walkthrough
    // 2026-10-15: it kept them until the reader reloaded the page).
    onError: (err) => {
      toast.error(formatApiError(err, 'Could not update the project’s defaults.'));
      if (asAxiosError(err).response?.status === 409) {
        void invalidateReads(queryClient, 'getClientReport', 'getReportProfile', 'getClientReportFollowers');
      }
    },
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
                  <Checkbox id={`promote-${key}`} checked={!unticked.has(key) && !cannot(key)}
                    disabled={promoting.isPending || cannot(key)}
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
                      This report’s: {valueWords(shown(ctx, key))}
                    </span>
                    <span className="block text-caption text-muted-foreground">
                      {cannot(key) ? 'The project’s team cannot be empty: with none saved, drafts list the project’s analysts and admins. It stays this report’s own.'
                        : others == null ? 'Counting the drafts that follow it…'
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
