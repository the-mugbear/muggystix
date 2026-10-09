/**
 * A host's remediation timeline (5.335.0): every change to a tracked field
 * and every note, newest first by when it happened.  A project admin adds a
 * note here; a note is its author's to remove, a recorded change is nobody's.
 *
 * The sheet stays mounted while the reader moves between hosts; what it shows
 * (`TimelineBody`) is keyed by the host, so a read or a save that completes
 * for the previous host has nowhere to land.
 */
import React, { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  addRemediationNote, deleteRemediationNote, listRemediationEvents, type RemediationEvent,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useConfirm } from '../../hooks/useConfirm';
import { queryErrorText, useLastSettled } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { formatDate, formatTimestamp } from '../../utils/relativeTime';
import {
  REMEDIATION_FIELD_LABEL, REMEDIATION_STATUS_LABEL, groupTimeline, heldRecordText, isRemediationStatus,
  type TimelineGroup,
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

const DATE_FIELDS = ['notified_on', 'closed_on', 'due_override_on', 'deferred_review_on'];

/** A stored value as the page says it: a status by its label, a date as a
 *  date, nothing as "none". */
const shown = (field: string | null, value: string | null): string => {
  if (value == null || value === '') return 'none';
  if (field === 'status' && isRemediationStatus(value)) return REMEDIATION_STATUS_LABEL[value];
  if (field != null && DATE_FIELDS.includes(field)) return formatDate(value);
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
          {group.events.map((change) => change.field === 'finding' ? (
            // The record went with its finding: `to` says what happened to
            // the finding, `from` what the record held.
            <li key={change.id} className="break-words text-metadata">
              <span className="font-medium">
                {change.to === 'finding deleted' ? 'Finding deleted' : `Finding ${change.to ?? 'removed'}`}
              </span>
              {change.from ? `. Its remediation record held: ${heldRecordText(change.from)}.` : '.'}
            </li>
          ) : (
            <li key={change.id} className="break-words text-metadata">
              <span className="font-medium">{REMEDIATION_FIELD_LABEL[change.field ?? ''] ?? change.field}</span>
              {': '}{shown(change.field, change.from)} → {shown(change.field, change.to)}
              {/* A severity change says what it did to the deadline. */}
              {change.field === 'severity' && change.body && (
                <span className="block whitespace-pre-wrap text-muted-foreground">{change.body}</span>
              )}
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

/** One host's entries and its note form.  Keyed by the host (and its project)
 *  where it is rendered: the note being typed, how far back the list reaches
 *  and every request in flight belong to that host and go with it. */
const TimelineBody: React.FC<{
  hostId: number;
  canWrite: boolean;
  projectId?: number;
  confirm: ReturnType<typeof useConfirm>[1];
}> = ({ hostId, canWrite, projectId, confirm }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');
  const [when, setWhen] = useState('');
  // How many entries are asked for: "Show older entries" raises it.
  const [limit, setLimit] = useState(PAGE);

  const query = useQuery({
    queryKey: ['listRemediationEvents', hostId, { limit }, projectId],
    queryFn: ({ signal }) => listRemediationEvents(hostId, { limit }, signal, projectId),
  });
  // The entries stay on screen while more are asked for, and when that fails.
  const page = useLastSettled(query.data) ?? null;
  const events = page ? page.items : null;
  const total = page ? page.total : 0;
  const error = queryErrorText(query.error, 'The timeline could not be loaded.');

  // Read the list again reaching back `wanted` entries.  Bounded HERE, for
  // every caller: a refresh after adding a note to 200 shown entries asked
  // for 201 and was refused (422), which left the old entries and an error
  // under a cleared note field.
  const reread = (wanted: number): Promise<unknown> => {
    const next = Math.min(MAX, Math.max(1, wanted));
    if (next === limit) return queryClient.invalidateQueries({ queryKey: ['listRemediationEvents'] });
    setLimit(next);
    return Promise.resolve();
  };

  // A date and time typed halfway has no value; adding the note would record
  // it as "now" while the field still shows the half-typed date.
  const whenRef = useRef<HTMLInputElement>(null);
  const [halfWhen, setHalfWhen] = useState(false);
  const checkWhen = () => {
    const bad = !!whenRef.current?.validity.badInput;
    setHalfWhen(bad);
    return bad;
  };

  const adding = useMutation({
    mutationFn: (body: string) => addRemediationNote({
      host_id: hostId, body,
      ...(when ? { occurred_at: new Date(when).toISOString() } : {}),
    }, projectId),
    // The button stays busy until the list shows the note.
    onSuccess: () => {
      setNote('');
      setWhen('');
      return reread(Math.max(PAGE, (events?.length ?? 0) + 1));
    },
  });
  const saving = adding.isPending;
  const add = () => {
    const body = note.trim();
    if (checkWhen() || !body) return;
    // Said only while this host is still the one on screen.
    adding.mutate(body, { onError: (err) => toast.error(formatApiError(err, 'The note was not added.')) });
  };

  const removing = useMutation({
    mutationFn: (event: RemediationEvent) => deleteRemediationNote(event.id, projectId),
    onSuccess: () => reread(Math.max(PAGE, events?.length ?? 0)),
  });
  const remove = async (event: RemediationEvent) => {
    const ok = await confirm({
      title: 'Remove this note?',
      body: 'It is removed from the timeline for everyone. This cannot be undone.',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    removing.mutate(event, { onError: (err) => toast.error(formatApiError(err, 'The note was not removed.')) });
  };

  return (
    <>
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
            <Button size="sm" onClick={add} disabled={saving || halfWhen || !note.trim()}>
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
            // A failed "Show older" is asked again by the same button.
            <Button variant="ghost" size="sm" className="mt-xs"
              onClick={() => {
                const next = Math.min(MAX, events.length + PAGE);
                if (next === limit) void query.refetch(); else setLimit(next);
              }}>
              Show older entries ({(total - events.length).toLocaleString()} more)
            </Button>
          ) : (
            <p className="mt-xs text-caption text-muted-foreground">
              The newest {MAX} of {total.toLocaleString()} entries.
            </p>
          ))}
        </>
      )}
    </>
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
  const [confirmDialog, confirm] = useConfirm();

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
          {host && (
            <TimelineBody key={`${projectId ?? ''}:${host.host_id}`} hostId={host.host_id} canWrite={canWrite}
              projectId={projectId} confirm={confirm} />
          )}
        </SideSheetBody>
      </SideSheetContent>
    </SideSheet>
  );
};

export default RemediationTimeline;
