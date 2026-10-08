/**
 * Edit the tracked fields of one or several findings on hosts (5.335.0).
 *
 * One dialog for a single row and for a selection: it opens on what the rows
 * agree on, and sends only what was changed (`utils/remediation.draftChanges`)
 * through the one write path, `POST /remediation/apply`.
 */
import React, { useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  applyRemediation, type RemediationPolicy, type RemediationRow, type RemediationStatus,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { formatDate } from '../../utils/relativeTime';
import {
  applyRowsFor, draftCaution, draftChanges, draftFor, draftProblem, hasChanges, localToday, previewDueOn,
  REMEDIATION_STATUSES, REMEDIATION_STATUS_LABEL, severityWord, type RemediationDraft,
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
  const opened = useMemo(() => draftFor(rows), [rows]);
  const [draft, setDraft] = useState<RemediationDraft>(opened);
  const [busy, setBusy] = useState(false);
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

  // What the deadline will be, said before saving.  One row only: a
  // selection may mix severities.
  const dueOn = !several && draft.notified_on && (draft.status === 'open' || draft.status === '')
    ? previewDueOn(policy, rows[0].severity, draft.notified_on) : null;
  const noTimeline = !several && policy != null && policy.days[rows[0].severity.toLowerCase()] == null;

  const problem = draftProblem(draft);
  const changes = draftChanges(opened, draft);
  const dirty = hasChanges(changes, draft.note);
  const hosts = new Set(rows.map((r) => r.host_id)).size;

  // A date typed halfway ("10/03/") has no value: the browser reports '' and
  // would let it save as "no date".  Seen in the browser — say so instead.
  const notifiedRef = useRef<HTMLInputElement>(null);
  const closedRef = useRef<HTMLInputElement>(null);
  const [halfDate, setHalfDate] = useState(false);
  const checkDates = () => {
    const bad = !!notifiedRef.current?.validity.badInput || !!closedRef.current?.validity.badInput;
    setHalfDate(bad);
    return bad;
  };

  const save = async () => {
    if (checkDates() || problem || !dirty) return;
    setBusy(true);
    try {
      let changed = 0;
      let notes = 0;
      if (acrossProjects) {
        // One call per project, one at a time: a refusal stops before the
        // next project is written, and the message says which were saved.
        const byProject = new Map<number, RemediationRow[]>();
        rows.forEach((r) => byProject.set(r.project_id, [...(byProject.get(r.project_id) ?? []), r]));
        const saved: string[] = [];
        for (const [projectId, group] of byProject) {
          try {
            const result = await applyRemediation(
              applyRowsFor(group, changes, draft.note, requestKey), { overwrite: true }, projectId);
            changed += result.summary.changed;
            notes += result.summary.notes_added;
            saved.push(group[0].project_name);
          } catch (err) {
            if (saved.length > 0) onSaved();
            toast.error(formatApiError(err, `Could not save for ${group[0].project_name}.`)
              + (saved.length > 0 ? ` Already saved: ${saved.join(', ')}.` : ' Nothing was changed.'));
            return;
          }
        }
      } else {
        const result = await applyRemediation(applyRowsFor(rows, changes, draft.note, requestKey), { overwrite: true });
        changed = result.summary.changed;
        notes = result.summary.notes_added;
      }
      toast.success(
        changed === 0 && notes > 0
          ? `Note added to ${notes === 1 ? 'the timeline' : `${notes} timelines`}.`
          : rows.length === 1
            ? `Saved for ${rows[0].finding_title} on ${rows[0].ip_address}.`
            : `Saved for ${rows.length.toLocaleString()} findings on hosts.`,
      );
      onSaved();
      onClose();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save. Nothing was changed.'));
    } finally {
      setBusy(false);
    }
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
                  : noTimeline
                    ? `${severityWord(rows[0].severity.toLowerCase())} findings have no remediation deadline.`
                    : dueOn
                      ? `Due ${formatDate(dueOn)} (${severityWord(rows[0].severity.toLowerCase()).toLowerCase()}, ${policy?.days[rows[0].severity.toLowerCase()]} days).`
                      : 'The deadline counts from this day.'}
              </p>
            </div>
            <div className="min-w-0">
              <Label htmlFor="rem-status">Status</Label>
              <Select value={draft.status || UNCHANGED}
                onValueChange={(v) => {
                  const status = v === UNCHANGED ? '' : (v as RemediationStatus);
                  // The date belongs to Reported fixed only.
                  setDraft((d) => ({ ...d, status, closed_on: status === 'closed' ? d.closed_on : '' }));
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
          </div>
          <div className="mt-sm">
            <Label htmlFor="rem-note">Note for the timeline (optional)</Label>
            <Textarea id="rem-note" rows={3} maxLength={10000} value={draft.note}
              placeholder="Contacted the owner, will respond on Friday"
              onChange={(e) => set('note', e.target.value)} />
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
          <Button onClick={() => void save()} disabled={busy || halfDate || !dirty || !!problem}>
            {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationEditDialog;
