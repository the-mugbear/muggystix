/**
 * The finding's own comment / evidence thread — the middle of the
 * notes→findings→reports flow: host notes capture an issue, it's promoted to a
 * finding, then refined HERE with discussion, repro steps, and screenshots
 * before it lands in a report. Threaded (replies indent under their parent);
 * screenshots paste or upload straight onto a comment and ride into the report.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Send, CornerDownRight, RefreshCw, X } from 'lucide-react';

import {
  Annotation,
  getFindingNotes,
  createFindingNote,
  uploadFindingNoteAttachment,
} from '../services/api';
import NoteAttachments from './host-inspector/NoteAttachments';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Textarea } from './ui/textarea';
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
  file: File;
  error?: string;
  /** The saved comment this file belongs to once the comment itself posted. */
  noteId?: number;
}

const FindingCommentThread: React.FC<FindingCommentThreadProps> = ({ findingId, canManage }) => {
  const toast = useToast();
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
  const [retrying, setRetrying] = useState<number | null>(null);
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
      setPending((p) => [...p, ...images.map((file) => ({ file }))]);
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

  const retryFailed = async (index: number) => {
    const entry = pending[index];
    if (!entry || entry.noteId == null || retrying !== null) return;
    setRetrying(index);
    try {
      const failed = await attachOne(entry, entry.noteId);
      setPending((p) => p.map((e, i) => (i === index ? failed : e)).filter((e): e is PendingFile => e !== null));
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

  const renderNode = (node: ThreadNode, depth: number): React.ReactNode => {
    const { note } = node;
    return (
      <div key={note.id} className={depth > 0 ? 'border-l-2 border-border pl-sm' : ''}>
        <div className="mb-xxs flex flex-wrap items-center gap-xs">
          <span className="text-metadata font-semibold text-foreground">
            {safeFallback(note.author_name, 'Unknown analyst')}
          </span>
          <AgentAuthorBadge actorType={note.actor_type} />
          <span className="text-caption text-muted-foreground">
            {new Date(note.created_at).toLocaleString()}
          </span>
        </div>
        {note.body && <p className="whitespace-pre-wrap break-words text-body">{note.body}</p>}
        <NoteAttachments
          noteId={note.id}
          attachments={note.attachments ?? []}
          canManage={canManage}
          uploadFn={(file) => uploadFindingNoteAttachment(findingId, note.id, file)}
          onChanged={() => void load()}
        />
        {canManage && (
          <Button
            variant="ghost"
            size="sm"
            className="mt-xxs h-6 text-caption text-muted-foreground"
            onClick={() => startReply(note)}
          >
            <CornerDownRight className="size-3" aria-hidden /> Reply
          </Button>
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
                          onClick={() => void retryFailed(i)}
                          aria-label={`Retry ${name}`}
                          disabled={retrying !== null}
                          className="shrink-0 hover:text-foreground disabled:opacity-50"
                        >
                          {retrying === i ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <RefreshCw className="size-3" aria-hidden />}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => setPending((p) => p.filter((_, j) => j !== i))}
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
    </Card>
  );
};

export default FindingCommentThread;
