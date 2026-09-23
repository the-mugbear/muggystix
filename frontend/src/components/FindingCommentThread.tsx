/**
 * The finding's own comment / evidence thread — the middle of the
 * notes→findings→reports flow: host notes capture an issue, it's promoted to a
 * finding, then refined HERE with discussion, repro steps, and screenshots
 * before it lands in a report. Threaded (replies indent under their parent);
 * screenshots paste or upload straight onto a comment and ride into the report.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Send, CornerDownRight, Pencil, RefreshCw, Trash2, X } from 'lucide-react';

import {
  Annotation,
  getFindingNotes,
  createFindingNote,
  updateFindingNote,
  deleteFindingNote,
  uploadFindingNoteAttachment,
} from '../services/api';
import NoteAttachments from './host-inspector/NoteAttachments';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Textarea } from './ui/textarea';
import { useAuth } from '../contexts/AuthContext';
import { useConfirm } from '../hooks/useConfirm';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { AgentAuthorBadge } from './AgentAuthorBadge';

interface FindingCommentThreadProps {
  findingId: number;
  /** Analyst+ — gates the compose/reply/attach affordances. */
  canManage: boolean;
}

interface ThreadNode {
  note: Annotation;
  children: ThreadNode[];
}

/** Build a parent→children tree, each level oldest-first (the list arrives
 *  oldest-first; roots are notes whose parent isn't in this finding). */
const buildTree = (notes: Annotation[]): ThreadNode[] => {
  const byId = new Map<number, ThreadNode>();
  notes.forEach((n) => byId.set(n.id, { note: n, children: [] }));
  const roots: ThreadNode[] = [];
  notes.forEach((n) => {
    const node = byId.get(n.id)!;
    const parent = n.parent_id != null ? byId.get(n.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  });
  return roots;
};

/** A file waiting to be attached. `error` is set when its upload against
 *  `noteId` failed — the file is KEPT so it can be retried or removed; a
 *  clipboard-only screenshot has no other copy (UX review C3). */
interface PendingFile {
  /** Stable identity — retry/remove reconcile by id, never by array index,
   *  so a Remove during an in-flight retry can't drop a different file. */
  id: string;
  file: File;
  error?: string;
  /** The saved comment this file belongs to once the comment itself posted. */
  noteId?: number;
}

/** Creation stamps the thread root in a second write, so `updated_at` is set
 *  on every comment; only a later change counts as an edit. */
const wasEdited = (note: Annotation): boolean =>
  !!note.updated_at &&
  new Date(note.updated_at).getTime() - new Date(note.created_at).getTime() > 5000;

const FindingCommentThread: React.FC<FindingCommentThreadProps> = ({ findingId, canManage }) => {
  const toast = useToast();
  const { user } = useAuth();
  const [confirmDialog, confirm] = useConfirm();
  // v5.256.0 — a comment is its author's: only they edit or delete it.
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [noteBusy, setNoteBusy] = useState<number | null>(null);
  // `notes === null` = never loaded successfully; distinct from "loaded, and
  // there are none" so a failed fetch is never presented as an empty record
  // (UX review H1).
  const [notes, setNotes] = useState<Annotation[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [body, setBody] = useState('');
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [replyTo, setReplyTo] = useState<Annotation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState<string | null>(null);
  const nextPendingId = useRef(0);
  const newPendingId = () => `pf-${++nextPendingId.current}`;
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setNotes(await getFindingNotes(findingId));
      setLoadError(null);
    } catch (err) {
      // Keep whatever was on screen; say the refresh failed rather than
      // blanking the thread or pretending it is empty.
      setLoadError(formatApiError(err, "Comments couldn't load."));
    } finally {
      setLoading(false);
    }
  }, [findingId]);

  useEffect(() => { void load(); }, [load]);

  const startReply = (note: Annotation) => {
    setReplyTo(note);
    setTimeout(() => composerRef.current?.focus(), 0);
  };

  // Paste a screenshot straight into the composer — collected as pending files
  // and attached to the comment once it's created on submit.
  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const images = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith('image/'));
    if (images.length) {
      e.preventDefault();
      setPending((p) => [...p, ...images.map((file) => ({ id: newPendingId(), file }))]);
    }
  };

  // Upload one file against an already-saved comment; on failure keep it in
  // the queue with the reason. Never creates a second comment.
  const attachOne = async (entry: PendingFile, noteId: number): Promise<PendingFile | null> => {
    try {
      await uploadFindingNoteAttachment(findingId, noteId, entry.file);
      return null;
    } catch (err) {
      return { ...entry, noteId, error: formatApiError(err, `Could not attach ${entry.file.name || 'screenshot'}.`) };
    }
  };

  const retryFailed = async (id: string) => {
    const entry = pending.find((e) => e.id === id);
    if (!entry || entry.noteId == null || retrying !== null) return;
    setRetrying(id);
    try {
      const failed = await attachOne(entry, entry.noteId);
      // Reconcile by id: if the entry was removed while the upload ran, the
      // rest of the queue is left untouched.
      setPending((p) => (failed ? p.map((e) => (e.id === id ? failed : e)) : p.filter((e) => e.id !== id)));
      if (!failed) await load();
      else toast.error(failed.error ?? 'Could not attach file.');
    } finally {
      setRetrying(null);
    }
  };

  const submit = async () => {
    if (submitting) return;
    if (!body.trim() && pending.length === 0) return;
    // Files left over from an earlier comment's failed uploads belong to THAT
    // comment; they stay in the queue with their Retry/Remove and are not
    // re-homed onto a new one.
    const fresh = pending.filter((e) => e.noteId == null);
    const leftovers = pending.filter((e) => e.noteId != null);
    if (!body.trim() && fresh.length === 0) return;
    setSubmitting(true);
    try {
      const note = await createFindingNote(findingId, body, replyTo?.id ?? null);
      const failed: PendingFile[] = [];
      for (const entry of fresh) {
        const f = await attachOne(entry, note.id);
        if (f) failed.push(f);
      }
      // The comment itself posted: clear the text and reply target. Keep
      // only the files that did not make it, tied to the saved comment.
      setBody('');
      setReplyTo(null);
      setPending([...leftovers, ...failed]);
      if (failed.length) {
        toast.error(`Comment saved · ${failed.length} attachment${failed.length === 1 ? '' : 's'} failed — retry or remove below.`);
      }
      await load();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not post comment.'));
    } finally {
      setSubmitting(false);
    }
  };

  const saveEdit = async () => {
    if (!editing || noteBusy !== null) return;
    const text = editing.text.trim();
    if (!text) return;
    setNoteBusy(editing.id);
    try {
      const updated = await updateFindingNote(findingId, editing.id, text);
      setNotes((prev) => (prev ? prev.map((n) => (n.id === updated.id ? { ...n, ...updated } : n)) : prev));
      setEditing(null);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the comment.'));
    } finally {
      setNoteBusy(null);
    }
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
    setNoteBusy(note.id);
    try {
      await deleteFindingNote(findingId, note.id);
      setNotes((prev) => (prev ? prev.filter((n) => n.id !== note.id) : prev));
      if (replyTo?.id === note.id) setReplyTo(null);
      toast.success('Comment deleted.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not delete the comment.'));
    } finally {
      setNoteBusy(null);
    }
  };

  const renderNode = (node: ThreadNode, depth: number): React.ReactNode => {
    const { note } = node;
    const isMine = canManage && user?.id != null && note.author_id === user.id;
    const isEditing = editing?.id === note.id;
    return (
      <div key={note.id} className={depth > 0 ? 'border-l-2 border-border pl-sm' : ''}>
        <div className="mb-xxs flex flex-wrap items-center gap-xs">
          <span className="text-metadata font-semibold text-foreground">
            {safeFallback(note.author_name, 'Unknown analyst')}
          </span>
          <AgentAuthorBadge actorType={note.actor_type} />
          <span className="text-caption text-muted-foreground">
            {new Date(note.created_at).toLocaleString()}
            {wasEdited(note) && (
              <span title={`Edited ${new Date(note.updated_at as string).toLocaleString()}`}> · edited</span>
            )}
          </span>
        </div>
        {isEditing ? (
          <div className="space-y-xs">
            <Textarea
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
          note.body && <p className="whitespace-pre-wrap break-words text-body">{note.body}</p>
        )}
        <NoteAttachments
          noteId={note.id}
          attachments={note.attachments ?? []}
          canManage={canManage}
          uploadFn={(file) => uploadFindingNoteAttachment(findingId, note.id, file)}
          onChanged={() => void load()}
        />
        {canManage && !isEditing && (
          <div className="mt-xxs flex flex-wrap items-center gap-xxs">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-caption text-muted-foreground"
              onClick={() => startReply(note)}
            >
              <CornerDownRight className="size-3" aria-hidden /> Reply
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
                  onClick={() => void removeNote(note, node.children.length > 0)}
                  disabled={noteBusy !== null}
                  aria-label="Delete comment"
                >
                  {noteBusy === note.id ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <Trash2 className="size-3" aria-hidden />} Delete
                </Button>
              </>
            )}
          </div>
        )}
        {node.children.length > 0 && (
          <div className="mt-sm space-y-md">
            {node.children.map((c) => renderNode(c, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  const tree = buildTree(notes ?? []);
  const failedCount = pending.filter((e) => e.error).length;
  const freshCount = pending.length - failedCount;

  return (
    <Card className="mb-md">
      <CardHeader>
        <CardTitle>Comments &amp; evidence{notes && notes.length > 0 ? ` (${notes.length})` : ''}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
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
            {tree.length === 0 ? (
              <p className="text-caption text-muted-foreground">
                No comments yet. Add repro steps, rationale, or screenshots to evidence this finding.
              </p>
            ) : (
              <div className="space-y-md">{tree.map((n) => renderNode(n, 0))}</div>
            )}
          </>
        )}

        {canManage && (
          <div className="space-y-xs border-t border-border pt-md">
            {replyTo && (
              <div className="flex items-center gap-xs text-caption text-muted-foreground">
                <CornerDownRight className="size-3" aria-hidden />
                Replying to {safeFallback(replyTo.author_name, 'a comment')}
                <button
                  type="button"
                  onClick={() => setReplyTo(null)}
                  className="inline-flex items-center hover:text-foreground"
                  aria-label="Cancel reply"
                >
                  <X className="size-3" aria-hidden />
                </button>
              </div>
            )}
            <Textarea
              ref={composerRef}
              rows={3}
              placeholder="Add a comment — repro steps, rationale, or paste a screenshot…"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onPaste={onPaste}
              aria-label="New comment"
            />
            {failedCount > 0 && (
              <p className="text-caption text-destructive" role="status">
                Comment saved · {failedCount} attachment{failedCount === 1 ? '' : 's'} failed — retry or remove.
              </p>
            )}
            {pending.length > 0 && (
              <div className="flex flex-wrap gap-xs">
                {pending.map((entry, i) => {
                  const name = entry.file.name || 'screenshot.png';
                  return (
                    <span
                      key={`${name}-${i}`}
                      className={
                        'inline-flex max-w-full items-center gap-xxs rounded-control border px-xs py-0.5 text-caption ' +
                        (entry.error ? 'border-destructive/40 bg-destructive/10' : 'border-border bg-muted')
                      }
                      title={entry.error ?? undefined}
                    >
                      <span className="truncate">{name}</span>
                      {entry.error && <span className="shrink-0 text-destructive">failed</span>}
                      {entry.error && entry.noteId != null && (
                        <button
                          type="button"
                          onClick={() => void retryFailed(entry.id)}
                          aria-label={`Retry ${name}`}
                          disabled={retrying !== null}
                          className="shrink-0 hover:text-foreground disabled:opacity-50"
                        >
                          {retrying === entry.id ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <RefreshCw className="size-3" aria-hidden />}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => setPending((p) => p.filter((e) => e.id !== entry.id))}
                        aria-label={`Remove ${name}`}
                        className="shrink-0 hover:text-foreground"
                      >
                        <X className="size-3" aria-hidden />
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
            <div className="flex justify-end">
              <Button
                size="sm"
                disabled={submitting || (!body.trim() && freshCount === 0)}
                onClick={() => void submit()}
              >
                {submitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />}
                {replyTo ? 'Reply' : 'Comment'}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
      {confirmDialog}
    </Card>
  );
};

export default FindingCommentThread;
