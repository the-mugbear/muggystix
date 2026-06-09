import React from 'react';
import { Reply, Trash2 } from 'lucide-react';

import type { Annotation, NoteStatus } from '../../services/api';
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

/**
 * Recursive note-thread renderer extracted from HostInspector.tsx
 * (v2.43.0 — MONO-2).  Pre-extraction this lived as a 120-line closure
 * inside HostInspector that captured 11 pieces of parent state; reading
 * the file required tracking every one of them mentally.  The
 * extraction makes the props contract explicit and lets the
 * recursive child render via the same `<NoteThread>` element instead
 * of a closure-recursion pattern.
 *
 * Owns no state — every interaction is a callback prop the parent
 * handles.  The parent keeps the source-of-truth (replyTo, replyBody,
 * notes, etc.) so optimistic updates and toasts stay coordinated
 * across the form, list, and create-note panel that share them.
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
}

interface NoteRowProps extends Omit<NoteThreadProps, 'topLevel'> {
  note: Annotation;
  depth: number;
}

const NoteRow: React.FC<NoteRowProps> = ({
  note,
  depth,
  repliesByParent,
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
}) => {
  const isReply = depth > 0;
  const statusMeta = noteStatusMeta[note.status];
  const authorLabel = note.author_name || 'Unknown analyst';
  const children = repliesByParent[note.id] || [];
  return (
    <React.Fragment>
      <div
        // Anchor target for #note-{id} deep-links (P3) — e.g.
        // /hosts/42#note-17 jumps to a specific note/thread.
        id={`note-${note.id}`}
        className={cn(
          'min-w-0 scroll-mt-24 py-sm',
          isReply && 'border-l-2 border-border pl-sm',
          isReply && (depth >= 4 ? 'ml-lg' : depth >= 2 ? 'ml-md' : 'ml-sm'),
        )}
      >
        <div className="mb-xxs flex flex-wrap items-center justify-between gap-xs">
          <div className="flex flex-wrap items-center gap-xs">
            <Badge variant={statusMeta.badgeVariant}>{statusMeta.label}</Badge>
            {!isReply && note.pinned && <Badge variant="warning">Pinned</Badge>}
            {!isReply && note.note_type && (
              <Badge variant="outline" className="capitalize">{note.note_type}</Badge>
            )}
            <span className="text-metadata font-semibold">{authorLabel}</span>
            <span className="text-caption text-muted-foreground">
              {new Date(note.created_at).toLocaleString()}
              {note.updated_at && ' · edited'}
            </span>
          </div>
          <div className="flex items-center gap-xxs">
            {!isReply && (
              <Select
                value={note.status}
                onValueChange={(value) => onUpdateNoteStatus(note.id, value as NoteStatus)}
                disabled={noteActionId === note.id}
              >
                <SelectTrigger
                  className="h-7 w-[9rem] text-caption"
                  aria-label={`Update status for note by ${authorLabel}`}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(noteStatusMeta) as [NoteStatus, NoteStatusMeta][]).map(
                    ([value, meta]) => (
                      <SelectItem key={value} value={value}>
                        {meta.label}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() =>
                    onReplyToChange(
                      replyTo?.id === note.id
                        ? null
                        : { id: note.id, author: authorLabel },
                    )
                  }
                  aria-label="Reply to note"
                >
                  <Reply className="size-4" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Reply</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => onDeleteNote(note.id)}
                  disabled={noteActionId === note.id}
                  aria-label="Delete note"
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <p className="whitespace-pre-wrap text-body">{note.body}</p>
        {/* Thread work-state (P3) — shown on the root note only. */}
        {!isReply && (note.assignee_name || note.due_at || note.resolution_summary) && (
          <div className="mt-xs flex flex-col gap-xxs text-caption text-muted-foreground">
            {note.assignee_name && (
              <span>
                Assigned to{' '}
                <span className="font-medium text-foreground">{note.assignee_name}</span>
              </span>
            )}
            {note.due_at && (
              <span>Due {new Date(note.due_at).toLocaleDateString()}</span>
            )}
            {note.resolution_summary && (
              <div className="rounded-control border border-success/30 bg-success/5 p-xs text-foreground">
                <span className="font-medium">Resolution: </span>
                {note.resolution_summary}
              </div>
            )}
          </div>
        )}
        {replyTo?.id === note.id && (
          <div className="mt-sm border-l-2 border-primary pl-sm">
            <p className="text-caption text-muted-foreground">
              Replying to {replyTo.author}
            </p>
            <Textarea
              rows={2}
              aria-label={`Reply to ${replyTo.author}`}
              placeholder="Write your reply…"
              value={replyBody}
              onChange={(e) => onReplyBodyChange(e.target.value)}
              disabled={noteSubmitting}
              className="mt-xxs"
            />
            <div className="mt-xs flex justify-end gap-xs">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onReplyToChange(null);
                  onReplyBodyChange('');
                }}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={onSubmitReply}
                disabled={noteSubmitting || !replyBody.trim()}
              >
                Reply
              </Button>
            </div>
          </div>
        )}
      </div>
      {children.map((child) => (
        <NoteRow
          key={child.id}
          note={child}
          depth={depth + 1}
          repliesByParent={repliesByParent}
          noteStatusMeta={noteStatusMeta}
          replyTo={replyTo}
          replyBody={replyBody}
          onReplyToChange={onReplyToChange}
          onReplyBodyChange={onReplyBodyChange}
          onSubmitReply={onSubmitReply}
          noteSubmitting={noteSubmitting}
          noteActionId={noteActionId}
          onUpdateNoteStatus={onUpdateNoteStatus}
          onDeleteNote={onDeleteNote}
        />
      ))}
    </React.Fragment>
  );
};

export const NoteThread: React.FC<NoteThreadProps> = ({ topLevel, ...rest }) => {
  if (topLevel.length === 0) return null;
  return (
    <div className="divide-y divide-border">
      {topLevel.map((note) => (
        <div key={`thread-${note.id}`}>
          <NoteRow note={note} depth={0} {...rest} />
        </div>
      ))}
    </div>
  );
};
