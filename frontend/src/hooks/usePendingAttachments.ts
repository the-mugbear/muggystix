/**
 * The screenshots waiting to go with a comment or a note — the ONE rule both
 * composers follow (a finding's comment thread and a host's discussion; review
 * 2026-10-23, R-FC1: each had its own copy, and the finding's had drifted).
 *
 *   - A pasted image waits in the queue, with its preview (an object URL held
 *     by `lib/objectUrls`, released when the image is taken out, has been
 *     uploaded, or the composer goes).
 *   - Posting is one action: the comment is created, then each image that was
 *     waiting for it is uploaded to it in turn.  An image whose upload fails
 *     does not fail the comment: it stays in the queue, bound to the comment
 *     that now exists (`noteId`) — the clipboard is gone, this is the only
 *     copy (UX review C3) — with Retry (to THAT comment, never a second one)
 *     and Remove.
 *   - Images left over from an earlier comment's failed uploads belong to that
 *     comment: a new post leaves them where they are.
 *   - Every change to the queue is made against the queue as it is NOW (a
 *     functional update, by entry id): a post's answer takes out only the
 *     images that post sent and puts back only those that failed, so an image
 *     pasted, removed or retried while the post ran is neither lost nor
 *     brought back (review 2026-10-23, H-FC2).
 *
 * The API is the caller's (`createNoteFn`, `uploadFn` — run inside the hook's
 * own mutations), so the hook is tested
 * with `renderHook`; what the caller does with an answer (put it onto its
 * cached record, read its thread again) is `onPosted` / `onAttached`.  The
 * composer's controls are disabled while a post runs (`posting`).
 */
import { useCallback, useRef, useState } from 'react';
import type React from 'react';
import { useMutation } from '@tanstack/react-query';

import { useObjectUrls } from '../lib/objectUrls';
import { formatApiError } from '../utils/apiErrors';
import { imagesFromClipboard } from '../utils/clipboardImages';

export interface PendingAttachment {
  /** Stable identity: every reconcile is by id, never by position. */
  id: string;
  file: File;
  /** The preview's object URL (null when none could be made). */
  url: string | null;
  /** Why its upload failed — set once it has, and kept while a retry runs. */
  error?: string;
  /** A retry of its upload is in flight. */
  uploading?: boolean;
  /** The saved comment this file belongs to once the comment itself posted. */
  noteId?: number;
}

export interface PostOutcome<N, A, V> {
  note: N;
  /** What the server answered for each image that went up with the comment. */
  uploaded: A[];
  /** The images that did not, now bound to `note`. */
  failed: PendingAttachment[];
  /** What the caller posted with (`post(body, vars)`). */
  vars: V;
}

export interface PendingAttachmentsOptions<N extends { id: number }, A, V> {
  /** Create the comment itself. */
  createNoteFn: (body: string, vars: V) => Promise<N>;
  /** Upload one file to a saved comment. */
  uploadFn: (noteId: number, file: File) => Promise<A>;
  /** The comment posted (its failed images are already back in the queue).
   *  A promise returned here keeps `posting` true until it settles. */
  onPosted?: (outcome: PostOutcome<N, A, V>) => unknown;
  /** The comment could not be created: nothing was uploaded, the queue is as it was. */
  onPostError?: (error: unknown) => void;
  /** A retried image reached its comment (it has left the queue). */
  onAttached?: (attachment: A, noteId: number) => unknown;
}

const failure = (err: unknown, file: File) =>
  formatApiError(err, `Could not attach ${file.name || 'screenshot'}.`);

export function usePendingAttachments<N extends { id: number }, A, V = void>(
  options: PendingAttachmentsOptions<N, A, V>,
) {
  const { createNoteFn, uploadFn, onPosted, onPostError, onAttached } = options;
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const nextId = useRef(0);
  const previews = useObjectUrls<string>();

  const add = useCallback((files: File[]) => {
    const entries = files
      .filter((f) => f.type.startsWith('image/'))
      .map((file) => {
        const id = `pa-${++nextId.current}`;
        return { id, file, url: previews.putBlob(id, file) };
      });
    if (entries.length) setPending((prev) => [...prev, ...entries]);
  }, [previews]);

  /** A paste of images goes to the queue instead of into the text; any other
   *  paste is the text field's. */
  const onPaste = useCallback((e: React.ClipboardEvent<HTMLElement>) => {
    const files = imagesFromClipboard(e.clipboardData);
    if (files.length) {
      e.preventDefault();
      add(files);
    }
  }, [add]);

  const remove = useCallback((id: string) => {
    previews.release(id);
    setPending((prev) => prev.filter((e) => e.id !== id));
  }, [previews]);

  const postMutation = useMutation({
    mutationFn: async ({ body, vars, fresh }: { body: string; vars: V; fresh: PendingAttachment[] }) => {
      const note = await createNoteFn(body, vars);
      const uploaded: A[] = [];
      const failed: PendingAttachment[] = [];
      for (const entry of fresh) {
        try {
          uploaded.push(await uploadFn(note.id, entry.file));
          previews.release(entry.id);
        } catch (err) {
          failed.push({ ...entry, noteId: note.id, error: failure(err, entry.file) });
        }
      }
      return { note, uploaded, failed };
    },
    onSuccess: ({ note, uploaded, failed }, { vars, fresh }) => {
      // Only what this post sent leaves the queue, and only what failed comes
      // back, in its place; everything else is as the queue holds it now.
      const sent = new Set(fresh.map((e) => e.id));
      const back = new Map(failed.map((e) => [e.id, e]));
      setPending((prev) => prev.flatMap((e) => {
        if (!sent.has(e.id)) return [e];
        const f = back.get(e.id);
        return f ? [f] : [];
      }));
      return onPosted?.({ note, uploaded, failed, vars });
    },
    onError: (err) => onPostError?.(err),
  });

  /** Post the comment with every image that is not an earlier comment's. */
  const post = (body: string, vars: V) => {
    if (postMutation.isPending) return;
    postMutation.mutate({ body, vars, fresh: pending.filter((e) => e.noteId == null) });
  };

  const retryMutation = useMutation({
    mutationFn: ({ entry, noteId }: { entry: PendingAttachment; noteId: number }) => uploadFn(noteId, entry.file),
    onMutate: ({ entry }) => setPending(
      (prev) => prev.map((e) => (e.id === entry.id ? { ...e, uploading: true } : e)),
    ),
    onSuccess: (attachment, { entry, noteId }) => {
      previews.release(entry.id);
      setPending((prev) => prev.filter((e) => e.id !== entry.id));
      return onAttached?.(attachment, noteId);
    },
    // Reconciled by id: an image removed while its retry ran stays removed.
    onError: (err, { entry }) => setPending((prev) => prev.map((e) => (
      e.id === entry.id ? { ...e, uploading: false, error: failure(err, entry.file) } : e
    ))),
  });

  /** Upload a failed image again, to the comment it was meant for. */
  const retry = (id: string) => {
    const entry = pending.find((e) => e.id === id);
    if (!entry || entry.noteId == null || entry.uploading || postMutation.isPending) return;
    retryMutation.mutate({ entry, noteId: entry.noteId });
  };

  const failedCount = pending.filter((e) => e.error && !e.uploading).length;
  return {
    pending,
    add,
    onPaste,
    remove,
    retry,
    post,
    posting: postMutation.isPending,
    /** Failed uploads not being tried again right now. */
    failedCount,
    /** Images that would go with the next post. */
    freshCount: pending.filter((e) => e.noteId == null).length,
  };
}
