/**
 * A host's remediation timeline (5.335.0): every change to a tracked field
 * and every note, newest first by when it happened.  A project admin adds a
 * note here; a note is its author's to remove, a recorded change is nobody's.
 *
 * The panel stays mounted while the reader moves between hosts, so every
 * completion checks it is still for the host on screen.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  addRemediationNote, deleteRemediationNote, listRemediationEvents, type RemediationEvent,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useConfirm } from '../../hooks/useConfirm';
import { formatApiError } from '../../utils/apiErrors';
import { formatDate, formatTimestamp } from '../../utils/relativeTime';
import {
  REMEDIATION_FIELD_LABEL, REMEDIATION_STATUS_LABEL, groupTimeline, isRemediationStatus, type TimelineGroup,
} from '../../utils/remediation';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  SideSheet, SideSheetBody, SideSheetContent, SideSheetDescription, SideSheetHeader, SideSheetTitle,
} from '../ui/side-sheet';
import { Textarea } from '../ui/textarea';

const PAGE = 50;
/** The route's `limit` ceiling. */
const MAX = 200;

/** A stored value as the page says it: a status by its label, a date as a
 *  date, nothing as "none". */
const shown = (field: string | null, value: string | null): string => {
  if (value == null || value === '') return 'none';
  if (field === 'status' && isRemediationStatus(value)) return REMEDIATION_STATUS_LABEL[value];
  if (field === 'notified_on' || field === 'closed_on') return formatDate(value);
  return value;
};

const Entry: React.FC<{
  group: TimelineGroup<RemediationEvent>;
  canWrite: boolean;
  onDelete: (event: RemediationEvent) => void;
}> = ({ group, canWrite, onDelete }) => {
  const event = group.first;
  const backdated = event.occurred_at.slice(0, 16) !== event.recorded_at.slice(0, 16);
  return (
    <li className="min-w-0 border-b border-border py-xs last:border-b-0">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm text-caption text-muted-foreground">
        <span className="tabular-nums text-foreground">{formatTimestamp(event.occurred_at)}</span>
        <span className="min-w-0 truncate" title={event.author ?? undefined}>
          {event.author ?? 'Someone no longer here'}
          {event.agent_session_id != null && ` · through agent session #${event.agent_session_id}`}
        </span>
        {backdated && <span>recorded {formatTimestamp(event.recorded_at)}</span>}
        {event.edited_at && <span>edited</span>}
      </div>
      {event.finding_title && (
        <p className="truncate text-caption text-muted-foreground" title={event.finding_title}>
          {event.finding_title}{event.finding_host_id == null ? ' (no longer on this host)' : ''}
        </p>
      )}
      {event.kind === 'change' ? (
        <ul className="min-w-0">
          {group.events.map((change) => (
            <li key={change.id} className="break-words text-metadata">
              <span className="font-medium">{REMEDIATION_FIELD_LABEL[change.field ?? ''] ?? change.field}</span>
              {': '}{shown(change.field, change.from)} → {shown(change.field, change.to)}
            </li>
          ))}
        </ul>
      ) : event.kind === 'report' ? (
        <p className="break-words text-metadata">
          <span className="font-medium">{event.body ?? 'Remediation list prepared'}</span> for {event.to ?? 'the contact'}
        </p>
      ) : event.kind === 'follow_up' ? (
        <div className="min-w-0 text-metadata">
          <p className="break-words"><span className="font-medium">Followed up</span> with {event.to ?? 'the contact'}</p>
          {event.body && <p className="whitespace-pre-wrap break-words">{event.body}</p>}
        </div>
      ) : (
        <div className="flex min-w-0 items-start gap-sm">
          <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-metadata">{event.body}</p>
          {canWrite && event.can_modify && (
            <Button size="sm" variant="ghost" className="h-7 shrink-0" onClick={() => onDelete(event)}>Remove</Button>
          )}
        </div>
      )}
    </li>
  );
};

export const RemediationTimeline: React.FC<{
  /** The host on screen; null closes the panel. */
  host: { host_id: number; ip_address: string; hostname: string | null } | null;
  canWrite: boolean;
  onClose: () => void;
  /** Set on the cross-project page: the host's project, read and written
   *  through the mount that also serves archived projects. */
  projectId?: number;
}> = ({ host, canWrite, onClose, projectId }) => {
  const toast = useToast();
  const [confirmDialog, confirm] = useConfirm();
  const hostId = host?.host_id ?? null;
  const current = useRef<number | null>(hostId);
  current.current = hostId;

  const [events, setEvents] = useState<RemediationEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [when, setWhen] = useState('');
  const [saving, setSaving] = useState(false);
  const loadGeneration = useRef(0);

  const load = useCallback(async (wanted = PAGE) => {
    if (hostId == null) return;
    // Bounded HERE, for every caller: a refresh after adding a note to 200
    // shown entries asked for 201 and was refused (422), which left the old
    // entries and an error under a cleared note field.
    const limit = Math.min(MAX, Math.max(1, wanted));
    // The newest read wins: "Show older entries" answering after the re-read
    // that follows a new note would put the list back without the note.
    const generation = ++loadGeneration.current;
    const stale = () => current.current !== hostId || generation !== loadGeneration.current;
    try {
      const page = await listRemediationEvents(hostId, { limit }, undefined, projectId);
      if (stale()) return;
      setEvents(page.items);
      setTotal(page.total);
      setError(null);
    } catch (err) {
      if (stale()) return;
      setError(formatApiError(err, 'The timeline could not be loaded.'));
    }
  }, [hostId, projectId]);

  useEffect(() => {
    setEvents(null);
    setTotal(0);
    setError(null);
    setNote('');
    setWhen('');
    // A save still in flight is the previous host's.
    setSaving(false);
    void load();
  }, [load]);

  // A date and time typed halfway has no value; adding the note would record
  // it as "now" while the field still shows the half-typed date.
  const whenRef = useRef<HTMLInputElement>(null);
  const [halfWhen, setHalfWhen] = useState(false);
  const checkWhen = () => {
    const bad = !!whenRef.current?.validity.badInput;
    setHalfWhen(bad);
    return bad;
  };

  const add = async () => {
    const body = note.trim();
    if (checkWhen() || !body || hostId == null) return;
    const submittedFor = hostId;
    setSaving(true);
    try {
      await addRemediationNote({
        host_id: submittedFor, body,
        ...(when ? { occurred_at: new Date(when).toISOString() } : {}),
      }, projectId);
      if (current.current !== submittedFor) return;
      setNote('');
      setWhen('');
      await load(Math.max(PAGE, (events?.length ?? 0) + 1));
    } catch (err) {
      if (current.current === submittedFor) toast.error(formatApiError(err, 'The note was not added.'));
    } finally {
      if (current.current === submittedFor) setSaving(false);
    }
  };

  const remove = async (event: RemediationEvent) => {
    const ok = await confirm({
      title: 'Remove this note?',
      body: 'It is removed from the timeline for everyone. This cannot be undone.',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    const submittedFor = hostId;
    try {
      await deleteRemediationNote(event.id, projectId);
      if (current.current === submittedFor) await load(Math.max(PAGE, events?.length ?? 0));
    } catch (err) {
      if (current.current === submittedFor) toast.error(formatApiError(err, 'The note was not removed.'));
    }
  };

  return (
    <SideSheet open={host != null} onOpenChange={(v) => { if (!v) onClose(); }}>
      {confirmDialog}
      <SideSheetContent>
        <SideSheetHeader>
          <SideSheetTitle className="truncate font-mono" title={host?.hostname ?? undefined}>
            {host?.ip_address}
          </SideSheetTitle>
          <SideSheetDescription className="truncate">
            Remediation timeline{host?.hostname ? ` · ${host.hostname}` : ''}
          </SideSheetDescription>
        </SideSheetHeader>
        <SideSheetBody>
          {canWrite && (
            <div className="mb-md flex min-w-0 flex-col gap-xs">
              <Label htmlFor="rem-timeline-note">Add a note</Label>
              <Textarea id="rem-timeline-note" rows={3} maxLength={10000} value={note}
                placeholder="Contacted the owner, will respond on Friday"
                onChange={(e) => setNote(e.target.value)} />
              <div className="flex min-w-0 flex-wrap items-end gap-sm">
                <div className="min-w-0">
                  <Label htmlFor="rem-timeline-when" className="text-caption text-muted-foreground">
                    When it happened (if not now)
                  </Label>
                  <Input id="rem-timeline-when" type="datetime-local" className="h-8 w-56" ref={whenRef} value={when}
                    onBlur={checkWhen} onKeyUp={checkWhen}
                    onChange={(e) => setWhen(e.target.value)} />
                </div>
                <Button size="sm" onClick={() => void add()} disabled={saving || halfWhen || !note.trim()}>
                  {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Add note
                </Button>
              </div>
              {halfWhen && (
                <p role="alert" className="text-caption text-destructive">
                  The date and time are only partly filled in. Finish them, or clear the field to record the note as now.
                </p>
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="mb-xs break-words text-caption text-destructive">
              {error}{events !== null ? ' The entries below are as last loaded.' : ''}
            </p>
          )}
          {events === null ? (
            !error && <p className="text-caption text-muted-foreground">Loading…</p>
          ) : events.length === 0 ? (
            <p className="text-metadata text-muted-foreground">
              Nothing recorded for this host yet. A change to a contact, date or status shows here, and so does each note.
            </p>
          ) : (
            <>
              <ul className="min-w-0">
                {groupTimeline(events).map((g) => (
                  <Entry key={g.first.id} group={g} canWrite={canWrite} onDelete={(ev) => void remove(ev)} />
                ))}
              </ul>
              {events.length < total && (events.length < MAX ? (
                <Button variant="ghost" size="sm" className="mt-xs" onClick={() => void load(Math.min(MAX, events.length + PAGE))}>
                  Show older entries ({(total - events.length).toLocaleString()} more)
                </Button>
              ) : (
                <p className="mt-xs text-caption text-muted-foreground">
                  The newest {MAX} of {total.toLocaleString()} entries.
                </p>
              ))}
            </>
          )}
        </SideSheetBody>
      </SideSheetContent>
    </SideSheet>
  );
};

export default RemediationTimeline;
