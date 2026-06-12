/**
 * The finding's own comment / evidence thread — the middle of the
 * notes→findings→reports flow: host notes capture an issue, it's promoted to a
 * finding, then refined HERE with discussion, repro steps, and screenshots
 * before it lands in a report. Threaded (replies indent under their parent);
 * screenshots paste or upload straight onto a comment and ride into the report.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Send, CornerDownRight, X } from 'lucide-react';

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

const FindingCommentThread: React.FC<FindingCommentThreadProps> = ({ findingId, canManage }) => {
  const toast = useToast();
  const [notes, setNotes] = useState<Annotation[]>([]);
  const [loading, setLoading] = useState(true);
  const [body, setBody] = useState('');
  const [pending, setPending] = useState<File[]>([]);
  const [replyTo, setReplyTo] = useState<Annotation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    try {
      setNotes(await getFindingNotes(findingId));
    } catch {
      /* a transient fetch failure shouldn't blank the page — keep prior state */
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
      setPending((p) => [...p, ...images]);
    }
  };

  const submit = async () => {
    if (submitting) return;
    if (!body.trim() && pending.length === 0) return;
    setSubmitting(true);
    try {
      const note = await createFindingNote(findingId, body, replyTo?.id ?? null);
      for (const file of pending) {
        try {
          await uploadFindingNoteAttachment(findingId, note.id, file);
        } catch (err) {
          toast.error(formatApiError(err, `Could not attach ${file.name}.`));
        }
      }
      setBody('');
      setPending([]);
      setReplyTo(null);
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

  const tree = buildTree(notes);

  return (
    <Card className="mb-md">
      <CardHeader>
        <CardTitle>Comments &amp; evidence{notes.length > 0 ? ` (${notes.length})` : ''}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {loading ? (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading comments…
          </div>
        ) : tree.length === 0 ? (
          <p className="text-caption text-muted-foreground">
            No comments yet. Add repro steps, rationale, or screenshots to evidence this finding.
          </p>
        ) : (
          <div className="space-y-md">{tree.map((n) => renderNode(n, 0))}</div>
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
            {pending.length > 0 && (
              <div className="flex flex-wrap gap-xs">
                {pending.map((f, i) => (
                  <span
                    key={`${f.name}-${i}`}
                    className="inline-flex items-center gap-xxs rounded-control border border-border bg-muted px-xs py-0.5 text-caption"
                  >
                    {f.name || 'screenshot.png'}
                    <button
                      type="button"
                      onClick={() => setPending((p) => p.filter((_, j) => j !== i))}
                      aria-label={`Remove ${f.name || 'screenshot'}`}
                      className="hover:text-foreground"
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex justify-end">
              <Button
                size="sm"
                disabled={submitting || (!body.trim() && pending.length === 0)}
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
