/**
 * The composer of a comment or a note, with the screenshots waiting to go with
 * it — the ONE composer of both threads (a host's discussion and a finding's
 * comments; review 2026-10-23, R-FC1).  The queue's rule is
 * `hooks/usePendingAttachments`; this is how it is drawn.
 *
 * Collapsible (the host's, v5.240.0): one line until it is used — focusing it,
 * typing, pasting an image, or an error opens the rest — and it stays open
 * while it holds anything, so a draft is never hidden by clicking elsewhere.
 *
 * Presentational: the draft and the submit logic stay with the thread, which
 * also owns the unsaved-work guard over them.  While a post runs the field,
 * Remove and Retry are disabled: what the post sent is settled first.
 */
import React, { useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { cn } from '../../utils/cn';
import { Alert, AlertDescription } from '../ui/alert';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import MentionTextarea from '../MentionTextarea';

export interface ComposerImage {
  id: string;
  /** The preview (null when none could be made). */
  url: string | null;
  file: File;
  /** Why the upload failed — set once it has, and kept while a retry runs. */
  error?: string;
  /** A retry of the upload is in flight. */
  uploading?: boolean;
}

export interface NoteComposerProps {
  /** The text field's id. */
  id: string;
  /** The text field's (visually hidden) label. */
  label: string;
  placeholder: string;
  /** One line under the field: what a mention does, what a paste does. */
  hint: React.ReactNode;
  /** "Note" / "Comment": what "… saved · 1 attachment failed" names. */
  noun: string;
  body: string;
  onBodyChange: (body: string) => void;
  submitting: boolean;
  onSubmit: () => void;
  /** The submit button's face when not submitting (an icon and words). */
  submitLabel: React.ReactNode;
  /** Nothing to post yet. */
  submitDisabled?: boolean;
  error?: string | null;
  onDismissError?: () => void;
  onPaste: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  images: ComposerImage[];
  failedAttachmentCount: number;
  onRemoveImage: (id: string) => void;
  onRetryImage: (id: string) => void;
  /** One line until used (default); false keeps it open. */
  collapsible?: boolean;
  /** Above the field (a "Replying to …" line). */
  header?: React.ReactNode;
  textareaRef?: React.Ref<HTMLTextAreaElement>;
}

export const NoteComposer: React.FC<NoteComposerProps> = ({
  id, label, placeholder, hint, noun, body, onBodyChange, submitting, onSubmit, submitLabel,
  submitDisabled = false, error = null, onDismissError, onPaste, images, failedAttachmentCount,
  onRemoveImage, onRetryImage, collapsible = true, header, textareaRef,
}) => {
  const [focused, setFocused] = useState(false);
  const open = !collapsible || focused || body.length > 0 || images.length > 0 || !!error || failedAttachmentCount > 0;

  return (
    <div
      className="space-y-xs"
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        // Moving between the field and Save is not leaving.
        const next = event.relatedTarget as HTMLElement | null;
        if (event.currentTarget.contains(next)) return;
        setFocused(false);
      }}
    >
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center justify-between gap-sm">
            <span className="min-w-0 break-words">{error}</span>
            {onDismissError && (
              <Button size="sm" variant="ghost" onClick={onDismissError} aria-label={`Dismiss ${noun.toLowerCase()} error`}>
                <X className="size-3.5" aria-hidden />
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      {header}
      <Label htmlFor={id} className="sr-only">{label}</Label>
      <MentionTextarea
        ref={textareaRef}
        id={id}
        rows={open ? 3 : 1}
        className={cn(!open && 'min-h-0 resize-none')}
        placeholder={placeholder}
        value={body}
        onChange={(event) => {
          if (error) onDismissError?.();
          onBodyChange(event.target.value);
        }}
        onPaste={onPaste}
        disabled={submitting}
      />
      {failedAttachmentCount > 0 && (
        <Alert variant="warning">
          <AlertDescription>
            {noun} saved · {failedAttachmentCount} attachment{failedAttachmentCount === 1 ? '' : 's'} failed.
            Retry or remove each below — the {noun.toLowerCase()} itself is already recorded.
          </AlertDescription>
        </Alert>
      )}
      {images.length > 0 && (
        <div className="flex flex-wrap gap-xs">
          {images.map((img, idx) => (
            <div key={img.id} className="group relative flex w-16 flex-col items-center gap-xxs">
              {img.url ? (
                <img
                  src={img.url}
                  alt={`Pasted image ${idx + 1}`}
                  title={img.uploading ? 'Uploading…' : img.error}
                  className={cn(
                    'size-16 rounded-control border object-cover',
                    img.error && !img.uploading ? 'border-destructive' : 'border-border',
                  )}
                />
              ) : (
                <div
                  role="img"
                  aria-label={`Pasted image ${idx + 1}`}
                  title={img.uploading ? 'Uploading…' : img.error}
                  className={cn(
                    'size-16 rounded-control border bg-muted',
                    img.error && !img.uploading ? 'border-destructive' : 'border-border',
                  )}
                />
              )}
              <span className="w-full truncate text-center text-caption text-muted-foreground" title={img.file.name}>
                {img.file.name || 'screenshot.png'}
              </span>
              <button
                type="button"
                onClick={() => onRemoveImage(img.id)}
                aria-label={`Remove pasted image ${idx + 1}`}
                className="absolute -right-1 -top-1 rounded-full bg-destructive p-0.5 text-white shadow disabled:opacity-50"
                disabled={submitting}
              >
                <X className="size-3" aria-hidden />
              </button>
              {(img.error || img.uploading) && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-xs text-caption"
                  onClick={() => onRetryImage(img.id)}
                  disabled={submitting || img.uploading}
                  aria-label={`Retry uploading pasted image ${idx + 1}`}
                >
                  {img.uploading ? 'Uploading…' : 'Retry'}
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {open && (
        <div className="flex flex-wrap items-center gap-sm">
          <p className="min-w-0 flex-1 text-caption text-muted-foreground">{hint}</p>
          <Button size="sm" onClick={onSubmit} disabled={submitting || submitDisabled}>
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Saving…
              </>
            ) : submitLabel}
          </Button>
        </div>
      )}
    </div>
  );
};

export default NoteComposer;
