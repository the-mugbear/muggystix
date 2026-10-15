/**
 * The images of the project's own report text — what the Executive summary
 * and the System description may place (`IMAGE_DETAILS`) — under the System
 * description: each with its caption (what the report prints
 * under it), where the project's saved text places it, and Delete; plus
 * "Upload image".
 *
 * They are the PROJECT's: the report defaults and every draft show the same
 * list (`useReportTextImages`, which the field's "Insert image" reads too, so
 * a picture is fetched once for the form).  The caption and the delete are
 * the attachment's own routes; the server refuses to delete an image a text
 * still places, and its message — which names where — is shown on the row.
 *
 * Writes are an analyst's (`canWrite`), and on one image its uploader's or a
 * project admin's (`can_modify`); those controls are hidden otherwise.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ImageOff, ImagePlus, Loader2, Trash2 } from 'lucide-react';

import {
  deleteNoteAttachment, setNoteAttachmentCaption, uploadReportTextImage, type ReportTextImage,
} from '../../services/api';
import { useConfirm } from '../../hooks/useConfirm';
import { useProjectId } from '../../hooks/useProjectId';
import { useProjectRole } from '../../hooks/useProjectRole';
import type { ReportTextImagesState } from '../../hooks/useReportTextImages';
import { invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { REPORT_IMAGE_TYPES, reportImageProblem } from '../../utils/reportImages';
import { imagesFromClipboard } from '../../utils/clipboardImages';
import ScreenshotLightbox from '../ScreenshotLightbox';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

const CAPTION_MAX = 2000;

/** "report defaults, draft #12" — the server's `placed_in`, in the page's words. */
export const placedInLabel = (placedIn: readonly string[]): string =>
  placedIn.map((where) => {
    if (where === 'defaults') return 'report defaults';
    const draft = /^report (\d+)$/.exec(where);
    return draft ? `draft #${draft[1]}` : where;
  }).join(', ');

/** One image's caption: typed, then saved with Save (or Enter); Cancel (or
 *  Escape) puts the stored caption back.  The buttons show only while what
 *  is typed differs from what is stored. */
const CaptionEditor: React.FC<{
  caption: string;
  image: ReportTextImage;
  disabled: boolean;
  saving: boolean;
  onSave: (next: string) => void;
}> = ({ caption, image, disabled, saving, onSave }) => {
  const [draft, setDraft] = useState(caption);
  const changed = draft.trim() !== caption;
  const save = () => { if (changed) onSave(draft.trim()); };
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-xs">
      <Input value={draft} maxLength={CAPTION_MAX} className="min-w-0 flex-1"
        aria-label={`Caption for image ${image.id} (${image.filename})`}
        placeholder="Caption — printed under the image; the file name is printed without one"
        disabled={disabled || saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) {
            e.preventDefault();
            save();
          } else if (e.key === 'Escape') {
            setDraft(caption);
          }
        }} />
      {changed && (
        <>
          <Button type="button" size="sm" className="h-8" disabled={disabled || saving} onClick={save}
            aria-label={`Save the caption of image ${image.id}`}>
            {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Save
          </Button>
          <Button type="button" variant="ghost" size="sm" className="h-8" disabled={disabled || saving}
            onClick={() => setDraft(caption)}>
            Cancel
          </Button>
        </>
      )}
    </div>
  );
};

interface Props {
  /** The form's one load of the images (`useReportTextImages`). */
  state: ReportTextImagesState;
  /** The form is saving: every control stays, greyed. */
  disabled?: boolean;
  idPrefix: string;
  /** Where a pasted screenshot is taken as an upload (the project images
   *  panel); without it, nothing is listened for. */
  pasteScope?: React.RefObject<HTMLElement | null>;
}

const ReportTextImages: React.FC<Props> = ({ state, disabled = false, idPrefix, pasteScope }) => {
  const projectId = useProjectId();
  const { canWrite } = useProjectRole();
  const queryClient = useQueryClient();
  const [confirmEl, confirm] = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [newCaption, setNewCaption] = useState('');
  // What went wrong, kept where it happened until the next change: the
  // server's refusal of a delete names the text to edit first.
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  // The image open in the full-size viewer (its URL is the list's own).
  const [viewing, setViewing] = useState<{ src: string; caption: string } | null>(null);
  const rowError = (id: number, message: string | null) => setRowErrors((m) => {
    const next = { ...m };
    if (message) next[id] = message; else delete next[id];
    return next;
  });
  const changed = () => { void invalidateReads(queryClient, 'listReportTextImages'); };

  const {
    images, listStatus, error, reload, urls, ensure, failed, retry,
  } = state;
  // The thumbnails of what is listed (a few at a time, in the shared cache).
  const idsKey = images.map((i) => i.id).join(',');
  useEffect(() => {
    images.forEach((i) => ensure(i.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, ensure]);

  const upload = useMutation({
    mutationFn: (file: File) => uploadReportTextImage(projectId, file, newCaption),
    onMutate: () => setUploadError(null),
    onSuccess: () => { setNewCaption(''); changed(); },
    onError: (err) => setUploadError(formatApiError(err, 'Could not upload the image.')),
  });
  const uploading = upload.isPending;

  // A screenshot pasted while the panel is in use is uploaded like a picked
  // file (owner, 2026-10-14: "the new Project images pane would host this
  // feature").  Only a paste made inside `pasteScope` (the panel) or with
  // nothing focused, that no field has taken already (the System
  // description takes its own and places it), and that carries an image —
  // text pastes into the caption box as usual.
  const uploadPasted = async (files: File[]) => {
    for (const file of files) {
      const problem = reportImageProblem(file);
      if (problem) { setUploadError(problem); return; }
      try {
        await upload.mutateAsync(file);
      } catch {
        return;         // said by the mutation's onError
      }
    }
  };
  const onPaste = useRef<(event: ClipboardEvent) => void>(() => undefined);
  onPaste.current = (event) => {
    const scope = pasteScope?.current;
    const target = event.target instanceof Node ? event.target : null;
    if (!scope || event.defaultPrevented || disabled) return;
    if (!(target === document.body || (target && scope.contains(target)))) return;
    const files = imagesFromClipboard(event.clipboardData);
    if (!files.length) return;
    event.preventDefault();
    if (uploading) { setUploadError('An image is still uploading — paste again once it has finished.'); return; }
    void uploadPasted(files);
  };
  const takesPaste = !!pasteScope && canWrite;
  useEffect(() => {
    if (!takesPaste) return undefined;
    const listener = (event: ClipboardEvent) => onPaste.current(event);
    document.addEventListener('paste', listener);
    return () => document.removeEventListener('paste', listener);
  }, [takesPaste]);

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = '';
    if (!file || uploading) return;
    const problem = reportImageProblem(file);
    if (problem) { setUploadError(problem); return; }
    upload.mutate(file);
  };

  const captioning = useMutation({
    mutationFn: ({ id, caption }: { id: number; caption: string }) => setNoteAttachmentCaption(projectId, id, caption),
    onMutate: ({ id }) => rowError(id, null),
    onSuccess: changed,
    onError: (err, { id }) => rowError(id, formatApiError(err, 'Could not save the caption.')),
  });
  const removing = useMutation({
    mutationFn: (image: ReportTextImage) => deleteNoteAttachment(projectId, image.id),
    onMutate: (image) => rowError(image.id, null),
    onSuccess: changed,
    // A 409 says which text still places it; that is what to do next.
    onError: (err, image) => rowError(image.id, formatApiError(err, 'Could not delete the image.')),
  });
  const remove = async (image: ReportTextImage) => {
    const ok = await confirm({
      title: 'Delete this image?',
      body: 'It is removed from the project: no report text can place it any more. Issued reports keep their own copy.',
      resourceName: image.caption?.trim() || image.filename,
      severity: 'danger',
      confirmLabel: 'Delete image',
    });
    if (ok) removing.mutate(image);
  };

  return (
    <div className="min-w-0 space-y-xs" data-testid={`${idPrefix}-system-images`}>
      {confirmEl}
      <p className="text-caption text-muted-foreground">
        <span className="font-medium text-foreground">Images</span> — they belong to the project, so its report
        defaults and every draft can place the same ones, in the Executive summary or the System description; an
        image no text places is not printed. A screenshot pasted into either is uploaded here and placed where it
        was pasted.
      </p>
      {listStatus === 'loading' && <p className="text-caption text-muted-foreground">Loading the images…</p>}
      {error && (
        <p role="alert" className="break-words text-caption text-destructive">
          {error} <button type="button" className="text-info hover:underline" onClick={reload}>Retry</button>
        </p>
      )}
      {listStatus === 'ready' && images.length === 0 && (
        <p className="text-caption text-muted-foreground">No image uploaded yet.</p>
      )}
      {images.length > 0 && (
        <ul className="divide-y divide-border" aria-label="Images of the report text">
          {images.map((image) => {
            const src = urls[image.id];
            const loadFailed = !src && failed(image.id);
            const mayChange = canWrite && image.can_modify;
            const caption = image.caption?.trim() ?? '';
            const savingCaption = captioning.isPending && captioning.variables?.id === image.id;
            const deleting = removing.isPending && removing.variables?.id === image.id;
            return (
              <li key={image.id} className="flex min-w-0 items-start gap-sm py-xs">
                <span className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-control border border-border bg-muted">
                  {src ? (
                    // Opens it full size: a 64-pixel thumbnail cannot tell
                    // two network diagrams apart (owner, 2026-10-14).
                    <button type="button" className="size-full cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`View image ${image.id} (${image.filename}) full size`}
                      onClick={() => setViewing({ src, caption: caption || image.filename })}>
                      <img src={src} alt="" className="size-full object-cover" />
                    </button>
                  ) : loadFailed ? (
                    <button type="button" className="flex size-full items-center justify-center"
                      aria-label={`${image.filename} could not be loaded — try again`} onClick={() => retry(image.id)}>
                      <ImageOff className="size-4 text-warning" aria-hidden />
                    </button>
                  ) : (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
                  )}
                </span>
                <div className="min-w-0 flex-1 space-y-xxs">
                  {mayChange ? (
                    // Saved with its own Save (the report workspace, owner
                    // 2026-10-14: a caption is shared by every text that
                    // places the image — it no longer saves silently on
                    // leaving the box).  Keyed by the stored caption, so a
                    // re-read shows the server's.
                    <CaptionEditor key={caption} caption={caption} image={image}
                      disabled={disabled || deleting} saving={savingCaption}
                      onSave={(next) => captioning.mutate({ id: image.id, caption: next })} />
                  ) : (
                    <p className="line-clamp-3 text-body [overflow-wrap:anywhere]" title={caption || undefined}>
                      {caption || <span className="text-muted-foreground">No caption — the report prints the file name</span>}
                    </p>
                  )}
                  <p className="text-caption text-muted-foreground [overflow-wrap:anywhere]" data-testid={`report-image-meta-${image.id}`}>
                    Image {image.id} · {image.filename}
                    {' · '}
                    {image.placed_in.length > 0
                      ? <>Placed in: {placedInLabel(image.placed_in)}</>
                      : 'Not placed in any saved text — not printed'}
                    {savingCaption && <> · Saving the caption…</>}
                  </p>
                  {rowErrors[image.id] && (
                    <p role="alert" className="text-caption text-destructive [overflow-wrap:anywhere]">{rowErrors[image.id]}</p>
                  )}
                </div>
                {mayChange && (
                  <Button type="button" variant="ghost" size="icon" className="size-7 shrink-0"
                    aria-label={`Delete image ${image.id} (${image.filename})`}
                    disabled={disabled || deleting} onClick={() => void remove(image)}>
                    {deleting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Trash2 className="size-4" aria-hidden />}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {canWrite && (
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          <input ref={fileRef} type="file" accept={REPORT_IMAGE_TYPES.join(',')} className="hidden"
            aria-label="Image file to upload" data-testid={`${idPrefix}-system-image-file`} onChange={onPick} />
          <Input className="h-8 min-w-0 max-w-md flex-1 text-caption" maxLength={CAPTION_MAX}
            aria-label="Caption for the next image (optional)" placeholder="Caption for the next image (optional)"
            value={newCaption} disabled={disabled || uploading} onChange={(e) => setNewCaption(e.target.value)} />
          <Button type="button" variant="outline" size="sm" disabled={disabled || uploading}
            onClick={() => fileRef.current?.click()}>
            {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ImagePlus className="size-4" aria-hidden />}
            {uploading ? 'Uploading…' : 'Upload image'}
          </Button>
          <span className="text-caption text-muted-foreground">
            PNG, JPEG or GIF, up to 10 MB.{pasteScope ? ' Or paste a screenshot here (Ctrl+V).' : ''}
          </span>
        </div>
      )}
      {uploadError && (
        <p role="alert" className="text-caption text-destructive [overflow-wrap:anywhere]">{uploadError}</p>
      )}
      <ScreenshotLightbox open={viewing !== null} onClose={() => setViewing(null)}
        src={viewing?.src ?? null} caption={viewing?.caption} />
    </div>
  );
};

export default ReportTextImages;
