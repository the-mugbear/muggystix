/**
 * The finding's own comment / evidence thread — the middle of the
 * notes→findings→reports flow: host notes capture an issue, it's promoted to a
 * finding, then refined HERE with discussion, repro steps, and screenshots
 * before it lands in a report. Threaded (replies indent under their parent);
 * screenshots paste or upload straight onto a comment and ride into the report.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ImagePlus, Loader2, Send, CornerDownRight, Pencil, RefreshCw, Trash2, X } from 'lucide-react';

import {
  Annotation,
  getFindingNotes,
  createFindingNote,
  updateFindingNote,
  deleteFindingNote,
  uploadFindingNoteAttachment,
} from '../services/api';
import NoteAttachments, {
  mayDeleteAttachment, type NoteAttachmentsHandle, type ReportMarking,
} from './host-inspector/NoteAttachments';
import { DiscussionText } from './MentionText';
import MentionTextarea from './MentionTextarea';
import NoteComposer from './host-inspector/NoteComposer';
import { Button } from './ui/button';
import { useAuth } from '../contexts/AuthContext';
import { useConfirm } from '../hooks/useConfirm';
import { useToast } from '../contexts/ToastContext';
import { useListQuery } from '../hooks/useListQuery';
import { usePendingAttachments } from '../hooks/usePendingAttachments';
import { useProjectId } from '../hooks/useProjectId';
import { invalidateReads } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { announceMentionOutcome } from '../utils/mentions';
import { safeFallback } from '../utils/uiStyles';
import MessageBubble, { wasEdited } from './MessageBubble';

interface FindingCommentThreadProps {
  findingId: number;
  /** Analyst+ — gates the compose/reply/attach affordances. */
  canManage: boolean;
  /** The viewer administers the project (the server's word): may delete an
   *  image someone else attached.  Not said = not offered. */
  isProjectAdmin?: boolean;
  /** v5.260.0 — the images' "In report" mark (see NoteAttachments). */
  reportMarking?: ReportMarking;
}

/** Comments per read: the newest to begin with, and per "Show earlier comments". */
export const THREAD_PAGE = 100;

/**
 * The finding's thread, the newest comments first loaded and the earlier ones
 * a "Show earlier comments" at a time (`useListQuery`).  A conversation is
 * read as one run — a reply quotes the comment above it, a notification links
 * to one comment (`#note-<id>`), and what the reader just posted belongs at
 * the end of what is on screen — so it grows in place rather than being cut
 * into numbered pages.  The server's `offset` counts from the newest, so the
 * first read always holds a comment just posted.
 */
function useFindingThread(findingId: number, { enabled = true }: { enabled?: boolean } = {}) {
  const projectId = useProjectId();
  return useListQuery<Annotation>(
    'getFindingNotes',
    ({ offset, limit, signal }) => getFindingNotes(projectId, findingId, { offset, limit }, signal),
    [projectId, findingId],
    // A comment posted while the reader walks back shifts every offset by
    // one: the row handed out twice is dropped.
    { pageSize: THREAD_PAGE, dedupeBy: (note) => note.id, errorMessage: "Comments couldn't load.", enabled },
  );
}

/**
 * How many comments the finding's thread holds — for where the page shows it
 * (its jump bar); null while that is not known (never loaded).
 *
 * It reads the thread's own query and asks for nothing itself
 * (`enabled: false`): the thread, on the same page, is the reader that
 * fetches, so the count is the thread's number at every moment — after a
 * post, a delete, a failed re-read — and costs no request.  It is the
 * server's count of the whole thread, not of the comments loaded.
 */
export function useFindingCommentCount(findingId: number): number | null {
  const thread = useFindingThread(findingId, { enabled: false });
  return thread.responses ? thread.total : null;
}

const FindingCommentThread: React.FC<FindingCommentThreadProps> = ({
  findingId, canManage, isProjectAdmin = false, reportMarking,
}) => {
  const toast = useToast();
  const projectId = useProjectId();
  const { user } = useAuth();
  const [confirmDialog, confirm] = useConfirm();
  // v5.256.0 — a comment is its author's: only they edit or delete it.
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [body, setBody] = useState('');
  const [replyTo, setReplyTo] = useState<Annotation | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const queryClient = useQueryClient();
  const thread = useFindingThread(findingId);
  // `notes === null` = never loaded successfully; distinct from "loaded, and
  // there are none" so a failed fetch is never presented as an empty record
  // (UX review H1).  A failed re-read keeps whatever was on screen and says
  // the refresh failed rather than blanking the thread.
  const notes = thread.rows;
  const loading = thread.loading;
  const loadError = thread.error;
  const load = thread.reload;
  // A comment was added to, or one went: the thread is read again — every
  // read the reader has loaded, so the count and the place they had walked
  // back to both hold (the promise is that read) — and with a screenshot so
  // are the finding's images: the report text's "Insert image" and each
  // image's row follow.
  const threadChanged = ({ images }: { images: boolean }) => {
    if (images) void invalidateReads(queryClient, 'getFindingImages');
    return thread.reload();
  };
  const showEarlier = () => {
    // The failure is said beside the button (`loadMoreError`).
    thread.loadMore().catch(() => undefined);
  };

  // A notification links to /findings/:id#note-:noteId — bring that comment
  // into view once the thread has loaded.  A comment older than what is
  // loaded is walked back to, a read at a time, until it is on screen or the
  // thread's first comment has been reached.
  const scrolledTo = useRef<string | null>(null);
  const { hasMore, loadingMore, loadMoreError, loadMore } = thread;
  useEffect(() => {
    const hash = window.location.hash;
    if (!notes || !/^#note-\d+$/.test(hash) || scrolledTo.current === hash) return;
    const el = document.getElementById(hash.slice(1));
    if (el) {
      scrolledTo.current = hash;
      el.scrollIntoView?.({ block: 'center' });
    } else if (hasMore && !loading && !loadingMore && !loadMoreError) {
      loadMore().catch(() => undefined);
    }
  }, [notes, hasMore, loading, loadingMore, loadMoreError, loadMore]);

  const startReply = (note: Annotation) => {
    setReplyTo(note);
    setTimeout(() => composerRef.current?.focus(), 0);
  };

  // A comment and its screenshots are one action: the comment is created,
  // then each screenshot waiting for it is attached in turn; one that fails
  // stays, bound to the saved comment, with Retry and Remove — the one rule
  // both threads follow (`usePendingAttachments`).  Files left over from an
  // earlier comment's failed uploads belong to THAT comment and are not
  // re-homed onto a new one.
  const attachments = usePendingAttachments<Annotation, unknown, number | null>({
    createNoteFn: (text, parentId) => createFindingNote(projectId, findingId, text, parentId),
    uploadFn: (noteId, file) => uploadFindingNoteAttachment(projectId, findingId, noteId, file),
    onPosted: ({ note, uploaded, failed }) => {
      if (note.mention_warning) toast.warning(note.mention_warning);
      else announceMentionOutcome(toast, note);
      // The comment itself posted: clear the text and reply target.
      setBody('');
      setReplyTo(null);
      if (failed.length) {
        toast.error(`Comment saved · ${failed.length} attachment${failed.length === 1 ? '' : 's'} failed — retry or remove below.`);
      }
      // Busy until the thread shows the comment.
      return threadChanged({ images: uploaded.length > 0 });
    },
    onPostError: (err) => toast.error(formatApiError(err, 'Could not post comment.')),
    // Still "retrying" until the thread shows the image.
    onAttached: () => threadChanged({ images: true }),
  });
  const submitting = attachments.posting;

  const submit = () => {
    if (submitting) return;
    if (!body.trim() && attachments.freshCount === 0) return;
    attachments.post(body, replyTo?.id ?? null);
  };

  const edit = useMutation({
    mutationFn: (v: { id: number; text: string }) => updateFindingNote(projectId, findingId, v.id, v.text),
    onSuccess: (updated) => {
      if (updated.mention_warning) toast.warning(updated.mention_warning);
      else announceMentionOutcome(toast, updated);
      thread.mapRows((n) => (n.id === updated.id ? { ...n, ...updated } : n));
      setEditing(null);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the comment.')),
  });

  const remove = useMutation({
    mutationFn: (note: Annotation) => deleteFindingNote(projectId, findingId, note.id),
    onSuccess: (_done, note) => {
      setReplyTo((current) => (current?.id === note.id ? null : current));
      toast.success('Comment deleted.');
      // Busy until the thread no longer shows it.  Its screenshots went with
      // it: they are no longer the finding's images.
      return threadChanged({ images: Boolean(note.attachments?.length) });
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not delete the comment.')),
  });

  // The comment whose own save or delete is in flight.
  const noteBusy = edit.isPending ? edit.variables.id : remove.isPending ? remove.variables.id : null;

  const saveEdit = () => {
    if (!editing || noteBusy !== null) return;
    const text = editing.text.trim();
    if (!text) return;
    edit.mutate({ id: editing.id, text });
  };

  const removeNote = async (note: Annotation, hasReplies: boolean) => {
    if (hasReplies) {
      // The server refuses too (409); say why before asking.
      toast.info('This comment has replies, so it stays to keep them in context. Edit its text instead.');
      return;
    }
    const preview = note.body ? note.body.slice(0, 140) : 'This comment';
    const ok = await confirm({
      title: 'Delete comment?',
      body: `"${preview}${note.body && note.body.length > 140 ? '…' : ''}" and its screenshots will be removed.`,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    remove.mutate(note);
  };

  // v5.264.0 — a conversation, oldest first: the viewer's comments on the
  // left, everyone else's on the right; a reply quotes what it answers.
  const byId = new Map((notes ?? []).map((n) => [n.id, n]));
  const replyCount = new Map<number, number>();
  (notes ?? []).forEach((n) => {
    if (n.parent_id != null) replyCount.set(n.parent_id, (replyCount.get(n.parent_id) ?? 0) + 1);
  });

  // "Attach image" lives in each message's action row (v5.268.1), not inside
  // the bubble: a button in every bubble read as part of the message.
  const attachRefs = useRef(new Map<number, NoteAttachmentsHandle>());
  const [attachBusyId, setAttachBusyId] = useState<number | null>(null);

  const renderMessage = (note: Annotation): React.ReactNode => {
    const authored = user?.id != null && note.author_id === user.id;
    const isMine = canManage && authored;
    const isEditing = editing?.id === note.id;
    const parent = note.parent_id != null ? byId.get(note.parent_id) : undefined;
    const hasReplies = (replyCount.get(note.id) ?? 0) > 0;
    return (
      <MessageBubble
        key={note.id}
        id={`note-${note.id}`}
        mine={authored}
        author={safeFallback(note.author_name, 'Unknown analyst')}
        actorType={note.actor_type}
        createdAt={note.created_at}
        edited={wasEdited(note)}
        editedAt={note.updated_at}
        replyingTo={parent ? {
          // The viewer is "You" on their own bubble, so a quote of it says "you" too.
          author: user?.id != null && parent.author_id === user.id ? 'you' : safeFallback(parent.author_name, 'a comment'),
          excerpt: parent.body ?? '',
        } : null}
        actions={canManage && !isEditing ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-caption text-muted-foreground"
              onClick={() => startReply(note)}
            >
              <CornerDownRight className="size-3" aria-hidden /> Reply
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-caption text-muted-foreground"
              onClick={() => attachRefs.current.get(note.id)?.openPicker()}
              disabled={attachBusyId === note.id}
            >
              {attachBusyId === note.id
                ? <Loader2 className="size-3 animate-spin" aria-hidden />
                : <ImagePlus className="size-3" aria-hidden />} Attach image
            </Button>
            {isMine && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-caption text-muted-foreground"
                  onClick={() => setEditing({ id: note.id, text: note.body ?? '' })}
                  disabled={noteBusy !== null}
                >
                  <Pencil className="size-3" aria-hidden /> Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-caption text-muted-foreground hover:text-destructive"
                  onClick={() => void removeNote(note, hasReplies)}
                  disabled={noteBusy !== null}
                  aria-label="Delete comment"
                >
                  {noteBusy === note.id ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <Trash2 className="size-3" aria-hidden />} Delete
                </Button>
              </>
            )}
          </>
        ) : undefined}
      >
        {isEditing ? (
          <div className="space-y-xs">
            <MentionTextarea
              autoFocus
              rows={3}
              value={editing.text}
              onChange={(e) => setEditing({ id: note.id, text: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Escape') setEditing(null); }}
              aria-label="Edit comment"
              disabled={noteBusy === note.id}
            />
            <div className="flex justify-end gap-xs">
              <Button variant="ghost" size="sm" onClick={() => setEditing(null)} disabled={noteBusy === note.id}>
                Cancel
              </Button>
              <Button size="sm" onClick={() => void saveEdit()} disabled={noteBusy === note.id || !editing.text.trim()}>
                {noteBusy === note.id && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
              </Button>
            </div>
          </div>
        ) : (
          note.body && <DiscussionText text={note.body} className="text-body" />
        )}
        <NoteAttachments
          ref={(h) => { if (h) attachRefs.current.set(note.id, h); else attachRefs.current.delete(note.id); }}
          externalTrigger
          onBusyChange={(busy) => setAttachBusyId(busy ? note.id : null)}
          noteId={note.id}
          attachments={note.attachments ?? []}
          canManage={canManage}
          canDelete={(att) => mayDeleteAttachment(att, { id: user?.id, isProjectAdmin })}
          uploadFn={(file) => uploadFindingNoteAttachment(projectId, findingId, note.id, file)}
          reportMarking={reportMarking}
        />
      </MessageBubble>
    );
  };

  // Oldest first, as the conversation happened (the API returns that order).
  const conversation = [...(notes ?? [])].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime() || a.id - b.id,
  );

  return (
    <section className="mb-md min-w-0" aria-label="Comments and evidence">
      <div className="border-b border-border pb-xs">
        <h2 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
          Comments &amp; evidence{notes && thread.total > 0 ? ` (${thread.total.toLocaleString()})` : ''}
        </h2>
      </div>
      <div className="space-y-md pt-sm">
        {loading && notes === null ? (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading comments…
          </div>
        ) : notes === null ? (
          // Never loaded: unavailable evidence is not the same as no evidence.
          <div className="flex flex-wrap items-center gap-sm">
            <p className="text-caption text-destructive">{loadError ?? "Comments couldn't load."}</p>
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
              <RefreshCw className="size-4" aria-hidden /> Retry
            </Button>
          </div>
        ) : (
          <>
            {loadError && (
              <div className="flex flex-wrap items-center gap-sm rounded-control border border-warning/40 bg-warning/10 px-sm py-xs">
                <p className="text-caption">Showing the last loaded comments — refresh failed: {loadError}</p>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                  <RefreshCw className="size-4" aria-hidden /> Retry
                </Button>
              </div>
            )}
            {conversation.length === 0 ? (
              <p className="text-caption text-muted-foreground">
                No comments yet. Add repro steps, rationale, or screenshots to evidence this finding.
              </p>
            ) : (
              <div className="space-y-md">
                {hasMore && (
                  // Above the oldest comment on screen: the thread reads
                  // downwards, so what came before is asked for at its top.
                  <div className="flex flex-wrap items-center gap-sm">
                    <Button variant="outline" size="sm" onClick={showEarlier} disabled={loadingMore || loading}>
                      {loadingMore && <Loader2 className="size-4 animate-spin" aria-hidden />}
                      Show earlier comments ({Math.max(thread.total - conversation.length, 0).toLocaleString()} earlier)
                    </Button>
                    {loadMoreError && (
                      <p className="text-caption text-destructive" role="status">{loadMoreError}</p>
                    )}
                  </div>
                )}
                {conversation.map(renderMessage)}
              </div>
            )}
          </>
        )}

        {canManage && (
          <div className="border-t border-border pt-md">
            <NoteComposer
              id={`finding-${findingId}-comment`}
              label="New comment"
              placeholder="Add a comment — repro steps, rationale, or paste a screenshot…"
              hint={(
                <>
                  <strong>@username</strong> notifies a teammate · everyone who has commented here, the
                  finding&apos;s author and its owner hear about new comments
                </>
              )}
              noun="Comment"
              collapsible={false}
              textareaRef={composerRef}
              header={replyTo && (
                <div className="flex items-center gap-xs text-caption text-muted-foreground">
                  <CornerDownRight className="size-3" aria-hidden />
                  Replying to {safeFallback(replyTo.author_name, 'a comment')}
                  <button
                    type="button"
                    onClick={() => setReplyTo(null)}
                    className="inline-flex items-center hover:text-foreground disabled:opacity-50"
                    aria-label="Cancel reply"
                    disabled={submitting}
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </div>
              )}
              body={body}
              onBodyChange={setBody}
              submitting={submitting}
              onSubmit={submit}
              submitDisabled={!body.trim() && attachments.freshCount === 0}
              submitLabel={<><Send className="size-4" aria-hidden /> {replyTo ? 'Reply' : 'Comment'}</>}
              onPaste={attachments.onPaste}
              images={attachments.pending}
              failedAttachmentCount={attachments.failedCount}
              onRemoveImage={attachments.remove}
              onRetryImage={attachments.retry}
            />
          </div>
        )}
      </div>
      {confirmDialog}
    </section>
  );
};

export default FindingCommentThread;
