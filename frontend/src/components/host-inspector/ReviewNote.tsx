/**
 * The reviewer's optional note on their finished review of a host.
 *
 * Finishing a review is one click and asks nothing (owner decision,
 * 2026-10-10); a reviewer who wants to say something about it writes one line
 * here afterwards — or never.  The note is on the READER'S OWN review (the
 * host's `follow` is the reader's row), so there is nobody else's to edit.
 *
 * Shown: the note, if there is one (clamped to two lines, the whole text one
 * click away — a note written before this was a free paragraph).  Writers
 * also get "Add a review note" / "Edit review note" (named for the review:
 * the discussion's own "Add note" sits beside it and is a different thing):
 * one input, Save / Cancel.  Saving it
 * empty removes it.  The server's answer goes back to the caller (`onSaved`),
 * which puts it on the cached host; nothing is remembered here.
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { setReviewNote } from '../../services/api';
import type { HostFollowInfo } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { formatApiError } from '../../utils/apiErrors';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

/** The server's limit (`HostReviewNoteUpdate.review_summary`). */
const NOTE_MAX_LENGTH = 4000;
/** Longer than this and the note is clamped, with "Show all". */
const CLAMP_AFTER = 160;

export const ReviewNote: React.FC<{
  hostId: number;
  note: string | null;
  canEdit: boolean;
  onSaved: (follow: HostFollowInfo) => void;
}> = ({ hostId, note, canEdit, onSaved }) => {
  const projectId = useProjectId();
  const toast = useToast();
  // null = not editing; a string is the reader's draft.
  const [draft, setDraft] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const save = useMutation({
    mutationFn: (text: string) => setReviewNote(projectId, hostId, text.trim() || null),
    onSuccess: (follow) => {
      onSaved(follow);
      setDraft(null);
    },
    // The draft stays: what the reader typed is not lost with the request.
    onError: (err) => toast.error(formatApiError(err, 'The note could not be saved.')),
  });

  if (draft !== null) {
    return (
      <form
        className="flex min-w-0 basis-full flex-wrap items-center gap-xs"
        onSubmit={(e) => { e.preventDefault(); save.mutate(draft); }}
      >
        <Input
          aria-label="Note on your review"
          className="h-8 min-w-0 flex-1 basis-64"
          placeholder="Optional — one line about this review"
          maxLength={NOTE_MAX_LENGTH}
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setDraft(null); } }}
        />
        <Button type="submit" size="sm" disabled={save.isPending}>Save</Button>
        <Button type="button" size="sm" variant="ghost" disabled={save.isPending} onClick={() => setDraft(null)}>
          Cancel
        </Button>
      </form>
    );
  }

  const long = (note?.length ?? 0) > CLAMP_AFTER;
  return (
    <>
      {note && (
        <span className="flex min-w-0 max-w-full items-baseline gap-xs text-caption text-foreground">
          <span
            data-testid="review-note"
            className={expanded || !long ? 'min-w-0 whitespace-pre-wrap break-words' : 'min-w-0 line-clamp-2 break-words'}
          >
            “{note}”
          </span>
          {long && (
            <button
              type="button"
              className="shrink-0 rounded text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? 'Show less' : 'Show all'}
            </button>
          )}
        </span>
      )}
      {canEdit && (
        <Button size="sm" variant="ghost" className="h-7 text-caption" onClick={() => setDraft(note ?? '')}>
          {note ? 'Edit review note' : 'Add a review note'}
        </Button>
      )}
    </>
  );
};

export default ReviewNote;
