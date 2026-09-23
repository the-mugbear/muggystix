import React, { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Flag, ImagePlus, Loader2, Reply, SlidersHorizontal, Trash2 } from 'lucide-react';

import type { Annotation, NoteStatus } from '../../services/api';
import MessageBubble from '../MessageBubble';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { Textarea } from '../ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { cn } from '../../utils/cn';
import NoteAttachments, { type NoteAttachmentsHandle } from './NoteAttachments';

// v5.241.0 — a note's actions are 28px, not 36px: five full-size icon buttons
// and a select made every note's header the tallest thing in it.
const ACTION_BUTTON = 'size-7';
const ACTION_ICON = 'size-3.5';
// A body past either bound is clamped to four lines until opened.
const LONG_NOTE_CHARS = 400;
const LONG_NOTE_LINES = 5;

/**
 * A host's notes as conversations (v5.264.0; extracted from HostInspector in
 * v2.43.0).  Each top-level note starts a thread — it carries the thread's
 * status, type, pin and promotion — and its replies follow it in the order
 * they were written, as message bubbles: the viewer's on the right, everyone
 * else's on the left (MessageBubble).  A reply to something other than the
 * thread's first note quotes what it answers, instead of indenting.
 *
 * Owns no state beyond per-message view state — every interaction is a
 * callback prop the parent handles.  The parent keeps the source-of-truth
 * (replyTo, replyBody, notes, etc.) so optimistic updates and toasts stay
 * coordinated across the form, list, and create-note panel that share them.
 */

export interface NoteStatusMeta {
  label: string;
  // Mirror the Badge variants HostInspector actually uses for note status
  // chips.  Widened to include "info" (open notes) + the structural
  // variants ("default", "destructive", "outline", "secondary") so the
  // primitive doesn't constrain HostInspector's local map.
  badgeVariant:
    | 'default'
    | 'destructive'
    | 'outline'
    | 'secondary'
    | 'success'
    | 'warning'
    | 'info'
    | 'muted';
}

export interface NoteThreadProps {
  /** Top-level notes (depth=0).  Each one starts its own thread. */
  topLevel: Annotation[];
  /** Map of parent_id → reply array, sorted oldest-first by the parent. */
  repliesByParent: Record<number, Annotation[]>;
  /** Display metadata per note status — owned by HostInspector. */
  noteStatusMeta: Record<NoteStatus, NoteStatusMeta>;
  /** Active reply target (which note is being replied to) + composed body. */
  replyTo: { id: number; author: string } | null;
  replyBody: string;
  onReplyToChange: (target: { id: number; author: string } | null) => void;
  onReplyBodyChange: (body: string) => void;
  onSubmitReply: () => void;
  noteSubmitting: boolean;
  /** Per-note disabled flag while a status update / delete is in flight. */
  noteActionId: number | null;
  onUpdateNoteStatus: (noteId: number, status: NoteStatus) => void;
  onDeleteNote: (noteId: number) => void;
  /** Promote a root note into a finding (omit to hide the affordance). */
  onPromoteNote?: (noteId: number) => void;
  /** Edit a root note's work fields (type/assignee/due/pin). Omit to hide. */
  onEditDetails?: (note: Annotation) => void;
  /** Host id — needed to attach/serve note image evidence. */
  hostId: number;
  /** Analyst+ — gates attach/delete of evidence images (display always on). */
  canManageNotes: boolean;
  /** Reload the notes thread after an attachment upload/delete. */
  onAttachmentsChanged: () => void;
  /** v5.264.0 — the viewer: their notes sit on the right. */
  currentUserId?: number | null;
}

interface NoteMessageProps extends Omit<NoteThreadProps, 'topLevel' | 'repliesByParent'> {
  note: Annotation;
  isRoot: boolean;
  /** The note this one answers, when it is not the thread's first note. */
  quoted?: Annotation;
}

const NoteMessage: React.FC<NoteMessageProps> = ({
  note,
  isRoot,
  quoted,
  noteStatusMeta,
  replyTo,
  replyBody,
  onReplyToChange,
  onReplyBodyChange,
  onSubmitReply,
  noteSubmitting,
  noteActionId,
  onUpdateNoteStatus,
  onDeleteNote,
  onPromoteNote,
  onEditDetails,
  hostId,
  canManageNotes,
  onAttachmentsChanged,
  currentUserId,
}) => {
  const attachRef = useRef<NoteAttachmentsHandle>(null);
  const [bodyOpen, setBodyOpen] = useState(false);
  const [attachBusy, setAttachBusy] = useState(false);
  const body = note.body ?? '';
  const longBody = body.length > LONG_NOTE_CHARS || body.split('\n').length > LONG_NOTE_LINES;
  const authorLabel = note.author_name || 'Unknown analyst';
  const mine = currentUserId != null && note.author_id === currentUserId;

  // The thread's own state lives on its first note: pinned, type, promoted
  // (its status is the select).  A reply has no select, so it shows its
  // status as a badge.
  const statusMeta = noteStatusMeta[note.status];
  const meta = !isRoot ? (
    statusMeta ? <Badge variant={statusMeta.badgeVariant}>{statusMeta.label}</Badge> : undefined
  ) : (
    <>
      {note.pinned && <Badge variant="warning">Pinned</Badge>}
      {note.note_type && <Badge variant="outline" className="capitalize">{note.note_type}</Badge>}
      {note.finding_id && (
        <Link to={`/findings/${note.finding_id}`} aria-label="View the finding promoted from this note">
          <Badge variant="info" className="hover:underline">Promoted → finding</Badge>
        </Link>
      )}
    </>
  );

  const statusControl = isRoot ? (
    <Select
      value={note.status}
      onValueChange={(value) => onUpdateNoteStatus(note.id, value as NoteStatus)}
      disabled={noteActionId === note.id}
    >
      <SelectTrigger className="h-7 w-[9rem] text-caption" aria-label={`Update status for note by ${authorLabel}`}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.entries(noteStatusMeta) as [NoteStatus, NoteStatusMeta][]).map(([value, m]) => (
          <SelectItem key={value} value={value}>{m.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  ) : undefined;

  const actions = (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className={ACTION_BUTTON}
            onClick={() => onReplyToChange(replyTo?.id === note.id ? null : { id: note.id, author: authorLabel })}
            aria-label="Reply to note">
            <Reply className={ACTION_ICON} aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Reply</TooltipContent>
      </Tooltip>
      {canManageNotes && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className={ACTION_BUTTON}
              onClick={() => attachRef.current?.openPicker()}
              // Disabled while an upload runs, as the built-in button always
              // was (NoteAttachments also refuses a second pick).
              disabled={attachBusy}
              aria-label={attachBusy ? 'Uploading image…' : 'Attach image'}>
              {attachBusy
                ? <Loader2 className={cn(ACTION_ICON, 'animate-spin')} aria-hidden />
                : <ImagePlus className={ACTION_ICON} aria-hidden />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>Attach image</TooltipContent>
        </Tooltip>
      )}
      {isRoot && onEditDetails && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className={ACTION_BUTTON} onClick={() => onEditDetails(note)}
              aria-label="Edit note details (type, assignee, due date, pin)">
              <SlidersHorizontal className={ACTION_ICON} aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Type · assignee · due · pin</TooltipContent>
        </Tooltip>
      )}
      {isRoot && onPromoteNote && !note.finding_id && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className={ACTION_BUTTON} onClick={() => onPromoteNote(note.id)}
              aria-label="Promote note to finding">
              <Flag className={ACTION_ICON} aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Promote to finding</TooltipContent>
        </Tooltip>
      )}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className={ACTION_BUTTON} onClick={() => onDeleteNote(note.id)}
            disabled={noteActionId === note.id} aria-label="Delete note">
            <Trash2 className={ACTION_ICON} aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Delete</TooltipContent>
      </Tooltip>
    </>
  );

  return (
    <div className="min-w-0">
      <MessageBubble
        // Anchor target for #note-{id} deep-links (P3) — e.g. /hosts/42#note-17.
        id={`note-${note.id}`}
        mine={mine}
        author={authorLabel}
        actorType={note.actor_type}
        createdAt={note.created_at}
        edited={!!note.updated_at}
        replyingTo={quoted ? { author: quoted.author_name || 'Unknown analyst', excerpt: quoted.body ?? '' } : null}
        meta={meta}
        metaControls={statusControl}
        actions={actions}
      >
        {/* v5.241.0 — a note body is unbounded (an agent's assessment runs to
            a screen of markdown). Long bodies open on demand; the threshold is
            on the text, not a DOM measurement. */}
        <p className={cn('whitespace-pre-wrap break-words text-body', longBody && !bodyOpen && 'line-clamp-4')}>
          {note.body}
        </p>
        {longBody && (
          <button type="button" onClick={() => setBodyOpen((v) => !v)} aria-expanded={bodyOpen}
            className="rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {bodyOpen ? 'Show less' : 'Show full note'}
          </button>
        )}
        {/* Evidence images attached to this note. */}
        <NoteAttachments
          ref={attachRef}
          externalTrigger
          onBusyChange={setAttachBusy}
          hostId={hostId}
          noteId={note.id}
          attachments={note.attachments ?? []}
          canManage={canManageNotes}
          onChanged={onAttachmentsChanged}
        />
        {/* Thread work-state (P3) — the first note only. */}
        {isRoot && (note.assignee_name || note.due_at || note.resolution_summary) && (
          <div className="mt-xs flex flex-col gap-xxs text-caption text-muted-foreground">
            {note.assignee_name && (
              <span>Assigned to <span className="font-medium text-foreground">{note.assignee_name}</span></span>
            )}
            {note.due_at && <span>Due {new Date(note.due_at).toLocaleDateString()}</span>}
            {note.resolution_summary && (
              <div className="rounded-control border border-success/30 bg-success/5 p-xs text-foreground">
                <span className="font-medium">Resolution: </span>
                {note.resolution_summary}
              </div>
            )}
          </div>
        )}
      </MessageBubble>
      {replyTo?.id === note.id && (
        <div className={cn('mt-sm flex flex-col', mine ? 'items-start' : 'items-end')}>
          <div className="w-full max-w-[85%] border-l-2 border-primary pl-sm">
            <p className="text-caption text-muted-foreground">Replying to {replyTo.author}</p>
            <Textarea rows={2} aria-label={`Reply to ${replyTo.author}`} placeholder="Write your reply…"
              value={replyBody} onChange={(e) => onReplyBodyChange(e.target.value)} disabled={noteSubmitting}
              className="mt-xxs" />
            <div className="mt-xs flex justify-end gap-xs">
              <Button size="sm" variant="ghost" onClick={() => { onReplyToChange(null); onReplyBodyChange(''); }}>
                Cancel
              </Button>
              <Button size="sm" onClick={onSubmitReply} disabled={noteSubmitting || !replyBody.trim()}>
                Reply
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/** Every reply under `rootId`, at any depth, oldest first. */
const flattenReplies = (rootId: number, repliesByParent: Record<number, Annotation[]>): Annotation[] => {
  const out: Annotation[] = [];
  const walk = (id: number) => {
    for (const child of repliesByParent[id] ?? []) {
      out.push(child);
      walk(child.id);
    }
  };
  walk(rootId);
  return out.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime() || a.id - b.id);
};

export const NoteThread: React.FC<NoteThreadProps> = ({ topLevel, repliesByParent, ...rest }) => {
  if (topLevel.length === 0) return null;
  return (
    <div className="divide-y divide-border">
      {topLevel.map((root) => {
        const replies = flattenReplies(root.id, repliesByParent);
        const byId = new Map<number, Annotation>([[root.id, root], ...replies.map((r) => [r.id, r] as const)]);
        return (
          <div key={`thread-${root.id}`} className="space-y-md py-sm" aria-label={`Thread started by ${root.author_name || 'Unknown analyst'}`}>
            <NoteMessage note={root} isRoot {...rest} />
            {replies.map((reply) => (
              <NoteMessage
                key={reply.id}
                note={reply}
                isRoot={false}
                // A reply to the thread's first note needs no quote; a reply
                // to a reply says which one.
                quoted={reply.parent_id != null && reply.parent_id !== root.id ? byId.get(reply.parent_id) : undefined}
                {...rest}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
};
