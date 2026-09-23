/**
 * The investigation-note composer — one line until it is used (v5.240.0).
 *
 * It was a permanently open form: a title, a subtitle, a label, a three-row
 * textarea, a two-line tip and a status row, ~250px on every host whether or
 * not anyone was writing. Now the field is a single row; focusing it, typing,
 * pasting an image, or an error opens the rest. It stays open while it holds
 * anything, so a draft is never hidden by clicking elsewhere.
 *
 * Presentational: the draft, the pending images and the submit logic stay in
 * HostInspector, which also owns the unsaved-work guard over them.
 */
import React, { useState } from 'react';
import { Loader2, NotebookPen, X } from 'lucide-react';

import type { NoteStatus } from '../../services/api';
import { cn } from '../../utils/cn';
import { Alert, AlertDescription } from '../ui/alert';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import MentionTextarea from '../MentionTextarea';

export interface ComposerImage {
  url: string;
  /** Set once the upload failed (or 'Uploading…' during a retry). */
  error?: string;
}

export interface NoteComposerProps {
  hostId: number;
  body: string;
  onBodyChange: (body: string) => void;
  status: NoteStatus;
  onStatusChange: (status: NoteStatus) => void;
  statusMeta: Record<NoteStatus, { label: string }>;
  submitting: boolean;
  onSubmit: () => void;
  error: string | null;
  onDismissError: () => void;
  onPaste: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  images: ComposerImage[];
  failedAttachmentCount: number;
  onRemoveImage: (index: number) => void;
  onRetryImage: (index: number) => void;
}

export const NoteComposer: React.FC<NoteComposerProps> = ({
  hostId, body, onBodyChange, status, onStatusChange, statusMeta, submitting, onSubmit,
  error, onDismissError, onPaste, images, failedAttachmentCount, onRemoveImage, onRetryImage,
}) => {
  const [focused, setFocused] = useState(false);
  const open = focused || body.length > 0 || images.length > 0 || !!error || failedAttachmentCount > 0;

  return (
    <div
      className="space-y-xs"
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        // Moving between the field, the status select and Save is not leaving —
        // and the select's option list is portalled outside this element.
        const next = event.relatedTarget as HTMLElement | null;
        if (event.currentTarget.contains(next) || next?.closest('[role="listbox"]')) return;
        setFocused(false);
      }}
    >
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center justify-between gap-sm">
            <span className="min-w-0 break-words">{error}</span>
            <Button size="sm" variant="ghost" onClick={onDismissError} aria-label="Dismiss note error">
              <X className="size-3.5" aria-hidden />
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <Label htmlFor={`host-${hostId}-note-body`} className="sr-only">Note</Label>
      <MentionTextarea
        id={`host-${hostId}-note-body`}
        rows={open ? 3 : 1}
        className={cn(!open && 'min-h-0 resize-none')}
        placeholder="Add a note — an observation, a remediation step, handoff context…"
        value={body}
        onChange={(event) => {
          if (error) onDismissError();
          onBodyChange(event.target.value);
        }}
        onPaste={onPaste}
        disabled={submitting}
      />
      {failedAttachmentCount > 0 && (
        <Alert variant="warning">
          <AlertDescription>
            Note saved · {failedAttachmentCount} attachment{failedAttachmentCount === 1 ? '' : 's'} failed.
            Retry or remove each below — the note itself is already recorded.
          </AlertDescription>
        </Alert>
      )}
      {images.length > 0 && (
        <div className="flex flex-wrap gap-xs">
          {images.map((img, idx) => (
            <div key={img.url} className="group relative flex flex-col items-center gap-xxs">
              <img
                src={img.url}
                alt={`Pasted image ${idx + 1}`}
                title={img.error}
                className={cn(
                  'size-16 rounded-control border object-cover',
                  img.error ? 'border-destructive' : 'border-border',
                )}
              />
              <button
                type="button"
                onClick={() => onRemoveImage(idx)}
                aria-label={`Remove pasted image ${idx + 1}`}
                className="absolute -right-1 -top-1 rounded-full bg-destructive p-0.5 text-white shadow"
                disabled={submitting}
              >
                <X className="size-3" aria-hidden />
              </button>
              {img.error && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-xs text-caption"
                  onClick={() => onRetryImage(idx)}
                  disabled={submitting || img.error === 'Uploading…'}
                  aria-label={`Retry uploading pasted image ${idx + 1}`}
                >
                  {img.error === 'Uploading…' ? 'Uploading…' : 'Retry'}
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {open && (
        <div className="flex flex-wrap items-center gap-sm">
          <div className="flex items-center gap-xs">
            <Label htmlFor={`host-${hostId}-note-status`} className="text-caption">Status</Label>
            <Select
              value={status}
              onValueChange={(value) => onStatusChange(value as NoteStatus)}
              disabled={submitting}
            >
              <SelectTrigger id={`host-${hostId}-note-status`} className="h-8 w-[9rem]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(statusMeta).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="min-w-0 flex-1 text-caption text-muted-foreground">
            <strong>@username</strong> notifies a teammate · replies reach everyone in the thread · <strong>paste a screenshot</strong> (Ctrl/Cmd+V) to attach it
          </p>
          <Button size="sm" onClick={onSubmit} disabled={submitting}>
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Saving…
              </>
            ) : (
              <>
                <NotebookPen className="size-4" aria-hidden />
                Save Note
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  );
};

export default NoteComposer;
