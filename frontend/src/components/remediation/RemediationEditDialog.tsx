/**
 * Edit the tracked fields of one or several findings on hosts (5.335.0).
 *
 * One dialog for a single row and for a selection: it opens on what the rows
 * agree on, and sends only what was changed (`utils/remediation.draftChanges`)
 * through the one write path, `POST /remediation/apply`.
 */
import React, { useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  applyRemediation, type RemediationPolicy, type RemediationRow, type RemediationStatus,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { formatApiError } from '../../utils/apiErrors';
import {
  applyRowsFor, draftCaution, draftChanges, draftFor, draftProblem, hasChanges, invalidateRemediationReads,
  localToday, noteRequirement, policyDueLine, REMEDIATION_STATUSES, REMEDIATION_STATUS_LABEL, type RemediationDraft,
} from '../../utils/remediation';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Textarea } from '../ui/textarea';

const UNCHANGED = '__unchanged__';

const newKey = (): string =>
  (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

/** Across projects: the project whose save was refused, and the ones written before it. */
class RefusedProject extends Error {
  constructor(readonly reason: unknown, readonly project: string, readonly saved: string[]) {
    super(`Could not save for ${project}.`);
  }
}

export const RemediationEditDialog: React.FC<{
  /** The rows being edited; the dialog is open while there are any. */
  rows: RemediationRow[];
  onClose: () => void;
  onSaved: () => void;
  /** The installation's timeline, for the deadline preview. */
  policy?: RemediationPolicy | null;
  /** The cross-project page: each project's rows are written through the
   *  mount that also serves archived projects. */
  acrossProjects?: boolean;
  /** The SERVER's day (the list's `as_of`): the deadlines are decided on it,
   *  so the clock starts on it — not on the reader's own calendar, which may
   *  be a day behind or ahead. */
  today?: string;
}> = ({ rows, onClose, onSaved, policy = null, acrossProjects = false, today }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  // The project page writes to its own project (across projects each row
  // names its own).
  const currentProjectId = useProjectId();
  const opened = useMemo(() => draftFor(rows), [rows]);
  const [draft, setDraft] = useState<RemediationDraft>(opened);
  // One key per opening: a second click on Save is the same note, not another.
  const requestKey = useMemo(newKey, [rows]);

  const several = rows.length > 1;
  const set = <K extends keyof RemediationDraft>(key: K, value: RemediationDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  // Naming a contact is assigning the finding: the clock starts today unless
  // the admin says another day.  Only when no date was there and none was
  // typed — never over a date somebody entered.
  const setContact = (value: string) => setDraft((d) => ({
    ...d,
    contact_email: value,
    notified_on: value.trim() && !opened.contact_email && !opened.notified_on && !d.notified_on && rows.length === 1
      ? (today ?? localToday()) : d.notified_on,
  }));

  // The due date is the policy's unless somebody sets one by hand.  The
  // policy's is a PREVIEW worked out here for one row (a selection may mix
  // severities); with a date set by hand, that date is simply the due date.
  const clockRuns = draft.status === 'open' || draft.status === '';
  const policyLine = several ? null : policyDueLine(policy, rows[0].severity, draft.notified_on);
  // One row: the date input shows once "Set a different date" is chosen (or a
  // date was already set).  A selection always has it — blank = leave as it is.
  const [byHand, setByHand] = useState(!!opened.due_override_on);
  const someByHand = rows.some((r) => !!r.due_override_on);

  const day = today ?? localToday();
  const problem = draftProblem(draft, opened, day);
  const changes = draftChanges(opened, draft);
  const dirty = hasChanges(changes, draft.note);
  // A due date set by hand and a deferral are saved with their reason.
  const noteNeeded = noteRequirement(changes);
  const noteMissing = noteNeeded != null && !draft.note.trim();
  const hosts = new Set(rows.map((r) => r.host_id)).size;

  // A date typed halfway ("10/03/") has no value: the browser reports '' and
  // would let it save as "no date".  Seen in the browser — say so instead.
  const notifiedRef = useRef<HTMLInputElement>(null);
  const closedRef = useRef<HTMLInputElement>(null);
  const overrideRef = useRef<HTMLInputElement>(null);
  const reviewRef = useRef<HTMLInputElement>(null);
  const [halfDate, setHalfDate] = useState(false);
  const checkDates = () => {
    const bad = [notifiedRef, closedRef, overrideRef, reviewRef].some((ref) => !!ref.current?.validity.badInput);
    setHalfDate(bad);
    return bad;
  };

  const saving = useMutation({
    mutationFn: async (): Promise<{ changed: number; notes: number }> => {
      if (!acrossProjects) {
        const result = await applyRemediation(
          currentProjectId, applyRowsFor(rows, changes, draft.note, requestKey), { overwrite: true });
        return { changed: result.summary.changed, notes: result.summary.notes_added };
      }
      // One call per project, one at a time: a refusal stops before the
      // next project is written, and the message says which were saved.
      let changed = 0;
      let notes = 0;
      const byProject = new Map<number, RemediationRow[]>();
      rows.forEach((r) => byProject.set(r.project_id, [...(byProject.get(r.project_id) ?? []), r]));
      const saved: string[] = [];
      for (const [projectId, group] of byProject) {
        try {
          const result = await applyRemediation(
            projectId, applyRowsFor(group, changes, draft.note, requestKey), { overwrite: true }, 'overview');
          changed += result.summary.changed;
          notes += result.summary.notes_added;
          saved.push(group[0].project_name);
        } catch (err) {
          throw new RefusedProject(err, group[0].project_name, saved);
        }
      }
      return { changed, notes };
    },
    onSuccess: ({ changed, notes }) => {
      toast.success(
        changed === 0 && notes > 0
          ? `Note added to ${notes === 1 ? 'the timeline' : `${notes} timelines`}.`
          : rows.length === 1
            ? `Saved for ${rows[0].finding_title} on ${rows[0].ip_address}.`
            : `Saved for ${rows.length.toLocaleString()} findings on hosts.`,
      );
      onSaved();
      onClose();
    },
    onError: (err) => {
      if (!(err instanceof RefusedProject)) {
        toast.error(formatApiError(err, 'Could not save. Nothing was changed.'));
        return;
      }
      if (err.saved.length > 0) onSaved();
      toast.error(formatApiError(err.reason, `Could not save for ${err.project}.`)
        + (err.saved.length > 0 ? ` Already saved: ${err.saved.join(', ')}.` : ' Nothing was changed.'));
    },
    // Whatever was written — all of it, or the projects before a refusal —
    // every remediation read on screen is re-read in place.
    onSettled: () => { void invalidateRemediationReads(queryClient); },
  });
  const busy = saving.isPending;
  const save = () => {
    if (checkDates() || problem || !dirty || noteMissing) return;
    saving.mutate();
  };

  return (
    <Dialog open onOpenChange={(v) => { if (!v && !busy) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="break-words">
            {several
              ? `Edit ${rows.length.toLocaleString()} findings on ${hosts.toLocaleString()} host${hosts === 1 ? '' : 's'}`
              : `${rows[0].finding_title} on ${rows[0].ip_address}`}
          </DialogTitle>
          <DialogDescription>
            {several
              ? 'A field left as it is stays as it is on each row. This records the contact’s progress; it does not change any finding’s own status.'
              : 'This records the contact’s progress; it does not change the finding’s own status.'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-1 gap-sm sm:grid-cols-2">
            <div className="min-w-0">
              <Label htmlFor="rem-email">Contact email</Label>
              <Input id="rem-email" type="email" maxLength={254} value={draft.contact_email}
                placeholder={several && !opened.contact_email ? 'Leave as it is' : 'name@example.com'}
                onChange={(e) => setContact(e.target.value)} />
            </div>
            <div className="min-w-0">
              <Label htmlFor="rem-name">Contact name</Label>
              <Input id="rem-name" maxLength={200} value={draft.contact_name}
                placeholder={several && !opened.contact_name ? 'Leave as it is' : 'Optional'}
                onChange={(e) => set('contact_name', e.target.value)} />
            </div>
            <div className="min-w-0">
              <Label htmlFor="rem-team">Team</Label>
              <Input id="rem-team" maxLength={100} value={draft.team}
                placeholder={several && !opened.team ? 'Leave as it is' : 'Optional — the group that owns the fix'}
                onChange={(e) => set('team', e.target.value)} />
            </div>
            <div className="min-w-0">
              <Label htmlFor="rem-notified">Assigned on</Label>
              <Input id="rem-notified" type="date" ref={notifiedRef} value={draft.notified_on}
                aria-describedby="rem-due"
                onBlur={checkDates} onKeyUp={checkDates}
                onChange={(e) => set('notified_on', e.target.value)} />
              <p id="rem-due" className="mt-xxs text-caption text-muted-foreground">
                {several
                  ? 'The day the contact was given the finding: each deadline counts from it.'
                  : 'The deadline counts from this day.'}
              </p>
            </div>
            <div className="min-w-0">
              <Label htmlFor="rem-status">Status</Label>
              <Select value={draft.status || UNCHANGED}
                onValueChange={(v) => {
                  const status = v === UNCHANGED ? '' : (v as RemediationStatus);
                  // The date belongs to Reported fixed only, the review date to Deferred.
                  setDraft((d) => ({
                    ...d, status,
                    closed_on: status === 'closed' ? d.closed_on : '',
                    deferred_review_on: status === 'deferred' ? d.deferred_review_on : opened.deferred_review_on,
                  }));
                }}>
                <SelectTrigger id="rem-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {!opened.status && <SelectItem value={UNCHANGED}>Leave as it is</SelectItem>}
                  {REMEDIATION_STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>{REMEDIATION_STATUS_LABEL[s]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {draft.status === 'closed' && (
              <div className="min-w-0">
                <Label htmlFor="rem-closed">Reported fixed on</Label>
                <Input id="rem-closed" type="date" ref={closedRef} value={draft.closed_on}
                  onBlur={checkDates} onKeyUp={checkDates}
                  onChange={(e) => set('closed_on', e.target.value)} />
              </div>
            )}
            {draft.status === 'deferred' && (
              <div className="min-w-0">
                <Label htmlFor="rem-review">Review on</Label>
                <Input id="rem-review" type="date" ref={reviewRef} value={draft.deferred_review_on} min={day}
                  aria-required aria-describedby="rem-review-hint"
                  onBlur={checkDates} onKeyUp={checkDates}
                  onChange={(e) => set('deferred_review_on', e.target.value)} />
                <p id="rem-review-hint" className="mt-xxs text-caption text-muted-foreground">
                  The clock is stopped until then; the deferral is listed for review from that day.
                </p>
              </div>
            )}
            {clockRuns && (
              <div className="min-w-0 sm:col-span-2">
                {several || byHand ? (
                  <>
                    <Label htmlFor="rem-override">Due date</Label>
                    <div className="flex min-w-0 flex-wrap items-center gap-sm">
                      <Input id="rem-override" type="date" ref={overrideRef} className="w-48 max-w-full"
                        value={draft.due_override_on} aria-describedby="rem-override-hint"
                        onBlur={checkDates} onKeyUp={checkDates}
                        onChange={(e) => setDraft((d) => ({ ...d, due_override_on: e.target.value, due_override_cleared: false }))} />
                      {!several && (
                        <Button type="button" size="sm" variant="ghost" className="h-8"
                          onClick={() => { set('due_override_on', ''); setByHand(false); }}>
                          Use the policy’s date
                        </Button>
                      )}
                      {several && someByHand && !draft.due_override_cleared && (
                        <Button type="button" size="sm" variant="ghost" className="h-8"
                          onClick={() => setDraft((d) => ({ ...d, due_override_on: '', due_override_cleared: true }))}>
                          Back to the policy’s date
                        </Button>
                      )}
                      {several && draft.due_override_cleared && (
                        <Button type="button" size="sm" variant="ghost" className="h-8"
                          onClick={() => setDraft((d) => ({ ...d, due_override_on: opened.due_override_on, due_override_cleared: false }))}>
                          Undo
                        </Button>
                      )}
                    </div>
                    <p id="rem-override-hint" className="mt-xxs break-words text-caption text-muted-foreground">
                      {several
                        ? draft.due_override_cleared
                          ? 'Every selected row goes back to the policy’s date.'
                          : draft.due_override_on
                            ? 'Set by hand on every selected row, in place of the policy’s date.'
                            : 'Left as it is on each row. A date here replaces the policy’s on every selected row.'
                        : `Set by hand. The policy’s: ${policyLine}`}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-caption font-medium leading-none text-foreground">Due date</p>
                    <div className="mt-xxs flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs">
                      <p className="min-w-0 break-words text-metadata" data-testid="rem-due-in-force">{policyLine}</p>
                      <Button type="button" size="sm" variant="ghost" className="h-7" onClick={() => setByHand(true)}>
                        Set a different date
                      </Button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          <div className="mt-sm">
            <Label htmlFor="rem-note">
              {noteNeeded ? 'Note for the timeline (required)' : 'Note for the timeline (optional)'}
            </Label>
            <Textarea id="rem-note" rows={3} maxLength={10000} value={draft.note}
              aria-required={noteNeeded != null} aria-describedby={noteMissing ? 'rem-note-needed' : undefined}
              placeholder={noteNeeded ? 'Why, and who agreed' : 'Contacted the owner, will respond on Friday'}
              onChange={(e) => set('note', e.target.value)} />
            {noteMissing && (
              <p id="rem-note-needed" role="status" className="mt-xxs text-caption text-warning">{noteNeeded}</p>
            )}
            {several && draft.note.trim() && (
              <p className="mt-xxs text-caption text-muted-foreground">
                The note is added to each of the {hosts.toLocaleString()} host{hosts === 1 ? '' : 's'}’ timelines.
              </p>
            )}
          </div>
          {!halfDate && !problem && draftCaution(draft) && (
            <p role="status" className="mt-xs text-caption text-warning">{draftCaution(draft)}</p>
          )}
          {(halfDate || problem) && (
            <p role="alert" className="mt-xs text-caption text-destructive">
              {halfDate ? 'A date is only partly filled in. Finish it or clear it.' : problem}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy || halfDate || !dirty || !!problem || noteMissing}>
            {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationEditDialog;
