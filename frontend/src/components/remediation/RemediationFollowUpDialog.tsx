/**
 * Follow up with one contact (5.340.0): the message to send — their overdue
 * and due-soon findings on hosts as plain text, to paste into mail or chat —
 * and one action to record that it was sent.
 *
 * BlueStick sends nothing: the admin copies the text and sends it their own
 * way, then records the follow-up so the next admin sees the contact was
 * already chased.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy, Loader2 } from 'lucide-react';

import {
  getRemediationFollowUp, recordRemediationFollowUp, type RemediationFollowUp,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { copyToClipboard } from '../../utils/clipboard';
import { localToday } from '../../utils/remediation';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';

export const RemediationFollowUpDialog: React.FC<{
  contactEmail: string;
  /** `all`: across every project the reader administers. */
  scope: 'project' | 'all';
  /** With `all`: only this project. */
  projectId?: number;
  canWrite: boolean;
  onClose: () => void;
  onRecorded: () => void;
}> = ({ contactEmail, scope, projectId, canWrite, onClose, onRecorded }) => {
  const toast = useToast();
  const [data, setData] = useState<RemediationFollowUp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [copied, setCopied] = useState(false);
  const [on, setOn] = useState(localToday());
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    const controller = new AbortController();
    getRemediationFollowUp(contactEmail, scope === 'all' ? 'all' : undefined, projectId, controller.signal)
      // The day is the SERVER's (`as_of`): the message says "as of" it, and
      // the reader's own calendar may be a day behind or ahead.
      .then((result) => { if (live.current) { setData(result); setText(result.text); setOn(result.as_of); } })
      .catch((err) => { if (live.current && !controller.signal.aborted) setError(formatApiError(err, 'The follow-up could not be prepared.')); });
    return () => { live.current = false; controller.abort(); };
  }, [contactEmail, scope, projectId]);

  const copy = async () => {
    const ok = await copyToClipboard(text);
    if (!live.current) return;
    if (ok) { setCopied(true); window.setTimeout(() => { if (live.current) setCopied(false); }, 2000); }
    else toast.error('The message could not be copied. Select the text and copy it by hand.');
  };

  const record = async () => {
    if (!data || !on) return;
    setBusy(true);
    const body = { contact_email: data.contact_email, followed_up_on: on, ...(note.trim() ? { note: note.trim() } : {}) };
    let recorded = 0;
    try {
      if (scope === 'all') {
        // One project at a time, through the mount that serves archived ones.
        for (const id of data.project_ids) recorded += (await recordRemediationFollowUp(body, id)).recorded;
      } else {
        recorded = (await recordRemediationFollowUp(body)).recorded;
      }
      toast.success(recorded === 0
        ? 'Already recorded for that day.'
        : `Follow-up recorded on ${recorded.toLocaleString()} ${recorded === 1 ? 'finding on a host' : 'findings on hosts'}.`);
      onRecorded();
      onClose();
    } catch (err) {
      if (recorded > 0) onRecorded();
      toast.error(formatApiError(err, 'The follow-up was not recorded.'));
    } finally {
      if (live.current) setBusy(false);
    }
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
                ? 'Nothing assigned to this contact is overdue or due soon.'
                : `${data.overdue.toLocaleString()} overdue and ${data.due_soon.toLocaleString()} due soon`
                  + `${scope === 'all' && data.project_ids.length > 1 ? ` across ${data.project_ids.length} projects` : ''}.`
                  + ' Copy the message and send it your own way: BlueStick sends nothing.'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
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
            <Button onClick={() => void record()} disabled={busy || !on}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Record follow-up
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationFollowUpDialog;
