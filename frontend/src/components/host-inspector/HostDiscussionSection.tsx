/**
 * Discussion — the host's notes: one section with the composer (a single line
 * until used) over the thread, the note-details dialog (a thread's type and
 * pin), and the `#note-<id>` deep link.  Notes are talk about the host; work
 * is a test and its result (5.325.0).
 *
 * The notes are the inspector's read (`getHost` carries them): they are read
 * from it, never copied out, and a write puts the server's answer back through
 * `putHost`.  What is being typed here (the note, its screenshots, a reply) is
 * this host's draft; whether there is one goes to the caller (`onDirtyChange`),
 * which confirms before navigation discards it.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { MessageSquare, NotebookPen } from 'lucide-react';
import {
  createAnnotation,
  deleteAnnotation,
  updateAnnotation,
  uploadNoteAttachment,
} from '../../services/api';
import type { Annotation, Host, NoteAttachment, NoteType } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useConfirm } from '../../hooks/useConfirm';
import { useProjectId } from '../../hooks/useProjectId';
import { usePendingAttachments } from '../../hooks/usePendingAttachments';
import { formatApiError } from '../../utils/apiErrors';
import { announceMentionOutcome } from '../../utils/mentions';
import { previewThreads, rootNoteId } from '../../utils/notePreview';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { InspectorSection, openInspectorSection } from './InspectorSection';
import { NoteComposer } from './NoteComposer';
import { NoteThread } from './NoteThread';

const NOTE_THREAD_PREVIEW_LIMIT = 3;
const NOTE_TYPES = ['observation', 'question', 'decision', 'handoff'] as const;


export interface HostDiscussionSectionProps {
  hostId: number;
  /** The host's notes, from the inspector's read. */
  notes: Annotation[];
  /** Put the server's answer onto the inspector's cached host. */
  putHost: (update: (previous: Host) => Host) => void;
  /** Writing a note is a project analyst's (R32). */
  canEdit: boolean;
  isProjectAdmin: boolean;
  currentUserId: number | null;
  /** The note, its screenshots or a reply holds unsaved text. */
  onDirtyChange: (dirty: boolean) => void;
}

export const HostDiscussionSection: React.FC<HostDiscussionSectionProps> = ({
  hostId,
  notes,
  putHost,
  canEdit,
  isProjectAdmin,
  currentUserId,
  onDirtyChange,
}) => {
  const toast = useToast();
  const projectId = useProjectId();

  // v2.43.0 — MONO-2: thread grouping for <NoteThread>.
  const noteThreadGroups = useMemo(() => {
    const topLevel = notes.filter((n) => !n.parent_id);
    const repliesByParent: Record<number, Annotation[]> = {};
    notes.filter((n) => n.parent_id).forEach((n) => {
      const pid = n.parent_id!;
      if (!repliesByParent[pid]) repliesByParent[pid] = [];
      repliesByParent[pid].push(n);
    });
    Object.values(repliesByParent).forEach((arr) =>
      arr.sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      ),
    );
    return { topLevel, repliesByParent };
  }, [notes]);

  const [noteBody, setNoteBody] = useState('');
  const [replyTo, setReplyTo] = useState<{ id: number; author: string } | null>(null);
  const [replyBody, setReplyBody] = useState('');
  const [noteError, setNoteError] = useState<string | null>(null);
  // The thread shows its newest threads until asked for the rest (v5.240.0).
  const [showAllNotes, setShowAllNotes] = useState(false);

  // Images pasted into the composer, uploaded to the note on save; a failed
  // one stays, bound to its note, until retried or removed (UX review C3) —
  // the one rule both threads follow (`usePendingAttachments`).  The note is
  // this project's, and so is each screenshot: every request carries the
  // project this inspector was rendered in.
  const attachments = usePendingAttachments<Annotation, NoteAttachment>({
    createNoteFn: (body) => createAnnotation(projectId, hostId, { body }),
    uploadFn: (noteId, file) => uploadNoteAttachment(projectId, hostId, noteId, file),
    onPosted: ({ note, uploaded }) => {
      if (note.mention_warning) toast.warning(note.mention_warning);
      else announceMentionOutcome(toast, note);
      const noteWithImages = uploaded.length ? { ...note, attachments: uploaded } : note;
      putHost((previous) => ({ ...previous, notes: [noteWithImages, ...(previous.notes ?? [])] }));
      setNoteBody('');
      setNoteError(null);
    },
    onPostError: () => setNoteError('Unable to save note right now. Please try again.'),
    // On the note at once, so the thumbnail shows without a re-read.
    onAttached: (attachment, noteId) => putHost((previous) => ({
      ...previous,
      notes: (previous.notes ?? []).map((n) => (
        n.id === noteId ? { ...n, attachments: [...(n.attachments ?? []), attachment] } : n
      )),
    })),
  });

  const dirty = noteBody.trim().length > 0
    || attachments.pending.length > 0
    || replyBody.trim().length > 0;
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // Note-details editor: a thread's type and pin.
  const [detailsNote, setDetailsNote] = useState<Annotation | null>(null);
  const [detailsType, setDetailsType] = useState<string>('none');
  const [detailsPinned, setDetailsPinned] = useState(false);

  const openNoteDetails = (note: Annotation) => {
    setDetailsNote(note);
    setDetailsType(note.note_type || 'none');
    setDetailsPinned(!!note.pinned);
  };

  const noteDetails = useMutation({
    mutationFn: ({ note, type, pinned }: { note: Annotation; type: string; pinned: boolean }) => {
      // The type goes only when it changed: a thread labelled before 5.326.0
      // may carry "finding" or "action", which can be kept but not chosen.
      const typeChanged = type !== (note.note_type || 'none');
      return updateAnnotation(projectId, hostId, note.id, {
        ...(typeChanged ? { note_type: type === 'none' ? null : (type as NoteType) } : {}),
        pinned,
      });
    },
    onSuccess: (updated) => {
      putHost((previous) => ({
        ...previous, notes: (previous.notes ?? []).map((n) => (n.id === updated.id ? updated : n)),
      }));
      toast.success('Note details updated.');
      setDetailsNote(null);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update note details.')),
  });
  const detailsSaving = noteDetails.isPending;
  const handleSaveNoteDetails = () => {
    if (!detailsNote) return;
    noteDetails.mutate({ note: detailsNote, type: detailsType, pinned: detailsPinned });
  };

  const [confirmEl, confirm] = useConfirm();

  // Deep-link to an exact note: when the URL carries #note-<id> (from the
  // Activity feed / mentions / a finding's evidence link), scroll that note
  // into view once the thread has rendered and flash a highlight. Runs once
  // per hash so adding a note later doesn't re-trigger it.
  //
  // v5.244.0 (code review finding 19) — REVEAL, then scroll. This effect used
  // to look for an element that was already mounted and give up otherwise. The
  // density pass then made two things unmount a note: the thread preview (only
  // some roots render) and a collapsed Notes section (its children unmount).
  // A link to evidence that silently lands nowhere is worse than no link, so:
  // resolve the hash against the LOADED notes, keep its root thread in the
  // preview (`linkedRootId`, read by previewThreads below), open the section,
  // and only then scroll — retrying across a few frames while the reveal
  // commits. A hash naming a note this host does not have is left alone.
  const consumedNoteHashRef = React.useRef<string | null>(null);
  const [linkedNoteId, setLinkedNoteId] = useState<number | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const read = () => {
      const match = window.location.hash.match(/^#note-(\d+)$/);
      setLinkedNoteId(match ? Number(match[1]) : null);
    };
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, [hostId]);
  const linkedRootId = useMemo(
    () => (linkedNoteId != null ? rootNoteId(linkedNoteId, notes) : null),
    [linkedNoteId, notes],
  );
  useEffect(() => {
    if (typeof window === 'undefined' || linkedNoteId == null || linkedRootId == null) return undefined;
    const key = `${hostId}#note-${linkedNoteId}`;
    if (consumedNoteHashRef.current === key) return undefined;
    openInspectorSection('host-detail-notes');
    let frame = 0;
    let tries = 0;
    let timer = 0;
    const attempt = () => {
      const el = document.getElementById(`note-${linkedNoteId}`);
      if (!el) {
        // The reveal (section open + preview membership) lands on a later commit.
        if (tries++ < 20) frame = requestAnimationFrame(attempt);
        return;
      }
      consumedNoteHashRef.current = key;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control');
      timer = window.setTimeout(
        () => el.classList.remove('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control'),
        2400,
      );
    };
    frame = requestAnimationFrame(attempt);
    return () => {
      cancelAnimationFrame(frame);
      // Leave the highlight timer running: it only removes classes.
      void timer;
    };
  }, [hostId, linkedNoteId, linkedRootId]);

  // A new thread: the note, then its screenshots one after another (the
  // hook's `post`); images bound to an earlier note stay with that note.
  const handleCreateNote = () => {
    if (!noteBody.trim()) {
      setNoteError('Add a short note before saving.');
      return;
    }
    attachments.post(noteBody.trim());
  };

  const deleteNote = useMutation({
    mutationFn: (noteId: number) => deleteAnnotation(projectId, hostId, noteId),
    onSuccess: (_none, noteId) => {
      putHost((previous) => ({
        ...previous, notes: (previous.notes ?? []).filter((note) => note.id !== noteId),
      }));
      toast.success('Note deleted.');
    },
    // Pre-audit (C8): console.error only — user clicked Trash and
    // the note stayed in the list with no signal whether the click
    // did anything.
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete note.')),
  });
  const noteActionId = deleteNote.isPending ? deleteNote.variables : null;
  const handleDeleteNote = async (noteId: number) => {
    const note = notes.find((n) => n.id === noteId);
    const preview = note?.body ? note.body.slice(0, 140) : 'This note';
    const ok = await confirm({
      title: 'Delete note',
      body: `Delete this note? "${preview}${note?.body && note.body.length > 140 ? '…' : ''}"`,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    deleteNote.mutate(noteId);
  };

  const postReply = useMutation({
    mutationFn: ({ body, parentId }: { body: string; parentId: number }) =>
      createAnnotation(projectId, hostId, { body, parent_id: parentId }),
    onSuccess: (newNote) => {
      if (newNote.mention_warning) toast.warning(newNote.mention_warning);
      else if (!announceMentionOutcome(toast, newNote)) toast.success('Reply posted.');
      putHost((previous) => ({ ...previous, notes: [newNote, ...(previous.notes ?? [])] }));
      setReplyTo(null);
      setReplyBody('');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to post reply.')),
  });
  const handleReply = () => {
    if (!replyTo || !replyBody.trim()) return;
    postReply.mutate({ body: replyBody.trim(), parentId: replyTo.id });
  };
  const noteSubmitting = attachments.posting || postReply.isPending;

  // Newest threads first in line for the space; the API's order is kept, and a
  // thread being replied to is never hidden.
  // v5.244.0 — membership is decided in utils/notePreview: pinned threads, the
  // one being replied to and the one a #note- link points into are always
  // visible; the rest compete on latest ACTIVITY (a reply today keeps an old
  // thread up), not on when the root was written.
  const notePreview = previewThreads(noteThreadGroups.topLevel, noteThreadGroups.repliesByParent, {
    limit: NOTE_THREAD_PREVIEW_LIMIT,
    // A reply target can itself be a reply; what must stay visible is its ROOT.
    keepIds: [replyTo ? rootNoteId(replyTo.id, notes) : null, linkedRootId],
    showAll: showAllNotes,
  });
  const visibleTopLevelNotes = notePreview.visible;
  const hiddenNoteCount = notePreview.hidden;

  return (
    <>
      {confirmEl}
      <InspectorSection
        id="host-detail-notes"
        title="Discussion"
        titleHint="Notes and replies about this host, for the team. Record work as a test and its result; a finding comes from a weakness or a test result."
        icon={<MessageSquare className="size-4 shrink-0 text-primary" aria-hidden />}
        count={notes.length}
      >
        <div className="space-y-sm">
          {/* Writing a note is a project analyst's (R32); a reader sees the
              discussion without a composer whose Post the server refuses. */}
          {!canEdit && notes.length === 0 && (
            <p className="text-caption text-muted-foreground">No notes on this host.</p>
          )}
          {canEdit && (
          <NoteComposer
            id={`host-${hostId}-note-body`}
            label="Note"
            placeholder="Add a note — a question, context for the team, a handoff…"
            hint={<><strong>@username</strong> notifies a teammate · replies reach everyone in the thread · <strong>paste a screenshot</strong> (Ctrl/Cmd+V) to attach it</>}
            noun="Note"
            body={noteBody}
            onBodyChange={setNoteBody}
            submitting={noteSubmitting}
            onSubmit={handleCreateNote}
            submitLabel={<><NotebookPen className="size-4" aria-hidden /> Save Note</>}
            error={noteError}
            onDismissError={() => setNoteError(null)}
            onPaste={attachments.onPaste}
            images={attachments.pending}
            failedAttachmentCount={attachments.failedCount}
            onRemoveImage={attachments.remove}
            onRetryImage={attachments.retry}
          />
          )}
          {notes.length > 0 && (
            // v2.43.0 — MONO-2: notes rendering is <NoteThread> (see
            // ./NoteThread.tsx).
            <NoteThread
              topLevel={visibleTopLevelNotes}
              repliesByParent={noteThreadGroups.repliesByParent}
              replyTo={replyTo}
              replyBody={replyBody}
              onReplyToChange={setReplyTo}
              onReplyBodyChange={setReplyBody}
              onSubmitReply={handleReply}
              noteSubmitting={noteSubmitting}
              noteActionId={noteActionId}
              onDeleteNote={handleDeleteNote}
              onEditDetails={openNoteDetails}
              currentUserId={currentUserId}
              hostId={hostId}
              canManageNotes={canEdit}
              isProjectAdmin={isProjectAdmin}
            />
          )}
          {hiddenNoteCount > 0 && (
            <Button size="sm" variant="ghost" className="text-caption" onClick={() => setShowAllNotes(true)}>
              Show {hiddenNoteCount} earlier thread{hiddenNoteCount === 1 ? '' : 's'}
            </Button>
          )}
        </div>
      </InspectorSection>

      {/* Note-details editor — a thread's type and pin. */}
      <Dialog open={detailsNote !== null} onOpenChange={(v) => { if (!v) setDetailsNote(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Note details</DialogTitle>
            <DialogDescription>
              Label what kind of thread this is, and pin it to keep it at the top of the discussion.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-sm">
            <div className="space-y-xxs">
              <Label htmlFor="note-type">Type</Label>
              <Select value={detailsType} onValueChange={setDetailsType}>
                <SelectTrigger id="note-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— none —</SelectItem>
                  {NOTE_TYPES.map((t) => (
                    <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>
                  ))}
                  {detailsNote?.note_type && !(NOTE_TYPES as readonly string[]).includes(detailsNote.note_type) && (
                    <SelectItem value={detailsNote.note_type} className="capitalize">
                      {detailsNote.note_type} (older label)
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            <Button
              type="button"
              variant={detailsPinned ? 'default' : 'outline'}
              size="sm"
              onClick={() => setDetailsPinned((v) => !v)}
              aria-pressed={detailsPinned}
            >
              {detailsPinned ? 'Pinned' : 'Pin to top'}
            </Button>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetailsNote(null)} disabled={detailsSaving}>
              Cancel
            </Button>
            <Button onClick={handleSaveNoteDetails} disabled={detailsSaving}>
              {detailsSaving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default HostDiscussionSection;
