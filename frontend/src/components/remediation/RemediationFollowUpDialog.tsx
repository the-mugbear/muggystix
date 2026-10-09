/**
 * Follow up with one contact (5.340.0): the message to send — their overdue
 * and due-soon findings on hosts as plain text, to paste into mail or chat —
 * and one action to record that it was sent.
 *
 * BlueStick sends nothing: the admin copies the text and sends it their own
 * way, then records the follow-up so the next admin sees the contact was
 * already chased.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Loader2 } from 'lucide-react';

import {
  getRemediationFollowUp, recordRemediationFollowUp, recordRemediationFollowUpOverview, type RemediationFollowUp,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { GLOBAL, queryErrorText, useLastSettled } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { copyToClipboard } from '../../utils/clipboard';
import { invalidateRemediationReads, localToday } from '../../utils/remediation';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Textarea } from '../ui/textarea';

/** How far ahead a reminder looks (`upcoming_days`). */
export const HORIZONS: Array<{ days: number; label: string }> = [
  { days: 0, label: 'Overdue and due soon' },
  { days: 30, label: 'Overdue, due soon, and due in the next 30 days' },
  { days: 90, label: 'Overdue, due soon, and due in the next 90 days' },
];

/** Why the follow-up was not recorded, and on how many rows it was before that. */
class NotRecorded extends Error {
  constructor(readonly reason: unknown, readonly recorded: number) {
    super('The follow-up was not recorded.');
  }
}

export const RemediationFollowUpDialog: React.FC<{
  contactEmail: string;
  /** `all`: across every project the reader administers. */
  scope: 'project' | 'all';
  /** With `all`: only this project. */
  projectId?: number;
  canWrite: boolean;
  /** How far ahead the reminder looks when it opens (one of `HORIZONS`).  A
   *  contact with nothing overdue or due soon is reminded of what is coming. */
  initialAhead?: number;
  onClose: () => void;
  onRecorded: () => void;
}> = ({ contactEmail, scope, projectId, canWrite, initialAhead = 0, onClose, onRecorded }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const across = scope === 'all';
  const [copied, setCopied] = useState(false);
  const [note, setNote] = useState('');
  // How far ahead the reminder looks: 0 = overdue and due soon only.
  const [ahead, setAhead] = useState<number>(initialAhead);

  // Keyed by the horizon: an answer for another one (the choice changed
  // meanwhile) is never the one on screen.
  const query = useQuery({
    queryKey: across
      ? [GLOBAL, 'getRemediationFollowUp', contactEmail, 'all', projectId, ahead]
      : ['getRemediationFollowUp', contactEmail, undefined, projectId, ahead],
    queryFn: ({ signal }) => getRemediationFollowUp(contactEmail, across ? 'all' : undefined, projectId, signal, ahead),
  });
  // The last message prepared stays on screen while another horizon is asked
  // for — and when that fails, so the choice can be changed back.
  const data = useLastSettled(query.data, { global: across }) ?? null;
  const loading = query.isFetching;
  const error = loading ? null : queryErrorText(query.error, 'The follow-up could not be prepared.');

  // What the reader changed, over the message it was changed in: another
  // horizon is another message, with its own text and day.  The day is the
  // SERVER's (`as_of`): the message says "as of" it, and the reader's own
  // calendar may be a day behind or ahead.
  const [edits, setEdits] = useState<{ over: RemediationFollowUp | null; text?: string; on?: string }>({ over: null });
  const mine = edits.over === data ? edits : null;
  const text = mine?.text ?? data?.text ?? '';
  const on = mine?.on ?? data?.as_of ?? localToday();
  const setText = (value: string) => setEdits({ over: data, on: mine?.on, text: value });
  const setOn = (value: string) => setEdits({ over: data, text: mine?.text, on: value });

  const copy = async () => {
    if (await copyToClipboard(text)) { setCopied(true); window.setTimeout(() => setCopied(false), 2000); }
    else toast.error('The message could not be copied. Select the text and copy it by hand.');
  };

  const recording = useMutation({
    mutationFn: async (prepared: RemediationFollowUp): Promise<{ recorded: number; projects: number | null }> => {
      const body = {
        contact_email: prepared.contact_email, followed_up_on: on, ...(note.trim() ? { note: note.trim() } : {}),
        // What the reminder listed is what is recorded.
        ...(ahead > 0 ? { upcoming_days: ahead } : {}),
      };
      let recorded = 0;
      try {
        if (across && projectId == null) {
          // Every project at once, all or nothing.  A server without that route
          // (404 / 405) is asked one project at a time, as before.
          try {
            const result = await recordRemediationFollowUpOverview(body);
            return { recorded: result.recorded, projects: result.projects };
          } catch (err) {
            const status = (err as { response?: { status?: number } })?.response?.status;
            if (status !== 404 && status !== 405) throw err;
            for (const id of prepared.project_ids) recorded += (await recordRemediationFollowUp(body, id)).recorded;
          }
        } else if (across) {
          // One chosen project, through the mount that serves archived ones.
          for (const id of prepared.project_ids) recorded += (await recordRemediationFollowUp(body, id)).recorded;
        } else {
          recorded = (await recordRemediationFollowUp(body)).recorded;
        }
      } catch (err) {
        throw new NotRecorded(err, recorded);
      }
      return { recorded, projects: null };
    },
    onSuccess: ({ recorded, projects }) => {
      toast.success(recorded === 0
        ? 'Already recorded for that day.'
        : projects != null
          ? `Recorded for ${recorded.toLocaleString()} ${recorded === 1 ? 'finding' : 'findings'} in ${projects.toLocaleString()} ${projects === 1 ? 'project' : 'projects'}.`
          : `Follow-up recorded on ${recorded.toLocaleString()} ${recorded === 1 ? 'finding on a host' : 'findings on hosts'}.`);
      onRecorded();
      onClose();
    },
    onError: (err) => {
      const failed = err instanceof NotRecorded ? err : new NotRecorded(err, 0);
      if (failed.recorded > 0) onRecorded();
      toast.error(formatApiError(failed.reason, 'The follow-up was not recorded.'));
    },
    // Recorded in full or in part: the contacts and the rows are re-read.
    onSettled: () => { void invalidateRemediationReads(queryClient); },
  });
  const busy = recording.isPending;
  const record = () => {
    if (!data || !on) return;
    recording.mutate(data);
  };

  const who = data?.contact_name ? `${data.contact_name} (${contactEmail})` : contactEmail;
  const nothing = data != null && data.items.length === 0;

  return (
    <Dialog open onOpenChange={(v) => { if (!v && !busy) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="break-words">Follow up with {who}</DialogTitle>
          <DialogDescription>
            {data == null
              ? 'Preparing the message…'
              : nothing
                ? ahead > 0
                  ? `Nothing assigned to this contact is overdue, due soon or due in the next ${ahead} days.`
                  : 'Nothing assigned to this contact is overdue or due soon.'
                : (ahead > 0 && data.upcoming != null
                  ? `${data.overdue.toLocaleString()} overdue, ${data.due_soon.toLocaleString()} due soon and ${data.upcoming.toLocaleString()} due in the next ${ahead} days`
                  : `${data.overdue.toLocaleString()} overdue and ${data.due_soon.toLocaleString()} due soon`)
                  + `${scope === 'all' && data.project_ids.length > 1 ? ` across ${data.project_ids.length} projects` : ''}.`
                  + ' Copy the message and send it your own way: BlueStick sends nothing.'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {data != null && (
            <div className="mb-sm flex min-w-0 flex-wrap items-center gap-sm">
              <Label htmlFor="rem-fu-ahead" className="shrink-0">Remind about</Label>
              <Select value={String(ahead)} disabled={busy} onValueChange={(v) => setAhead(Number(v))}>
                <SelectTrigger id="rem-fu-ahead" className="h-8 w-80 max-w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {HORIZONS.map((h) => <SelectItem key={h.days} value={String(h.days)}>{h.label}</SelectItem>)}
                </SelectContent>
              </Select>
              {loading && <span className="text-caption text-muted-foreground">Preparing…</span>}
            </div>
          )}
          {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
          {data == null && !error && <p className="text-caption text-muted-foreground">Loading…</p>}
          {data != null && !nothing && (
            <>
              <div className="flex items-center justify-between gap-sm">
                <Label htmlFor="rem-fu-text">Message</Label>
                <Button size="sm" variant="outline" className="h-7" onClick={() => void copy()}>
                  {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
                  {copied ? 'Copied' : 'Copy message'}
                </Button>
              </div>
              <Textarea id="rem-fu-text" rows={12} value={text} className="mt-xxs font-mono text-caption"
                onChange={(e) => setText(e.target.value)} />
              {data.has_more && (
                <p className="mt-xxs text-caption text-warning">
                  The message lists the first {data.items.length.toLocaleString()} rows; this contact has more.
                </p>
              )}
              {canWrite && (
                <div className="mt-md border-t border-border pt-sm">
                  <p className="text-metadata font-medium">After you send it</p>
                  <div className="mt-xs grid grid-cols-1 gap-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
                    <div className="min-w-0">
                      <Label htmlFor="rem-fu-on">Followed up on</Label>
                      <Input id="rem-fu-on" type="date" value={on} max={data.as_of}
                        onChange={(e) => setOn(e.target.value)} />
                    </div>
                    <div className="min-w-0">
                      <Label htmlFor="rem-fu-note">What was said (optional)</Label>
                      <Input id="rem-fu-note" maxLength={500} value={note} placeholder="Mailed; asked for a date"
                        onChange={(e) => setNote(e.target.value)} />
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Close</Button>
          {canWrite && data != null && !nothing && (
            <Button onClick={record} disabled={busy || loading || !!error || !on}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Record follow-up
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationFollowUpDialog;
