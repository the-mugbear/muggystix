import React, { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from 'react';
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { Loader2, ImageOff, ImagePlus, Pencil, Trash2 } from 'lucide-react';
import {
  NoteAttachment,
  uploadNoteAttachment,
  deleteNoteAttachment,
  getNoteAttachmentObjectUrl,
  setNoteAttachmentCaption,
  setNoteAttachmentInReport,
} from '../../services/api';
import type { ImageThumbnails } from '../../utils/evidenceImages';
import { ImagePlacement, placementLine } from '../../utils/reportImages';
import { safeFallback } from '../../utils/uiStyles';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Textarea } from '../ui/textarea';
import ScreenshotLightbox from '../ScreenshotLightbox';
import { useToast } from '../../contexts/ToastContext';
import { invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';

interface NoteAttachmentsProps {
  /** Host that owns the note — used for the default (host-note) upload path. */
  hostId?: number;
  noteId: number;
  attachments: NoteAttachment[];
  /** Analyst+ — gates the attach/delete affordances (display is always on). */
  canManage: boolean;
  /** An image was added, removed or changed.  A host note's thread is read
   *  again here (its attachments come with `getHost`), so only another owner
   *  of the note (a finding's comment thread) needs to be told. */
  onChanged?: () => void;
  /**
   * Override the upload call so the same component serves other annotation
   * targets (e.g. a finding's comment thread). Defaults to the host-note
   * endpoint via hostId. Delete + serve are attachment-id based, so they need
   * no override.
   */
  uploadFn?: (file: File) => Promise<unknown>;
  /**
   * The caller draws the attach control (the host note row puts it with the
   * note's other actions) and opens the picker through the ref. Without it
   * every note spent a row of its own on an "Attach image" button.
   */
  externalTrigger?: boolean;
  /** Tells the owner of an external trigger that an upload is in flight, so
   *  its control can disable itself the way the built-in one does. */
  onBusyChange?: (busy: boolean) => void;
  /**
   * v5.260.0 — show each image's "In report" mark (images are opt-in for the
   * client report). `canMark` says whether THIS viewer may change it for an
   * image (its uploader or a project admin — the server decides the same).
   * Omit on surfaces that are not a finding's evidence.
   *
   * On a finding each image is then a ROW: the thumbnail, its caption (what
   * the report prints under it — the same people who may mark it write it),
   * the mark, and where the finding's text places it.
   */
  reportMarking?: ReportMarking;
}

export interface ReportMarking {
  canMark: (attachment: NoteAttachment) => boolean;
  /** Where the finding's report text places the image; undefined while the
   *  finding's images are loading (no line is shown). */
  placement?: (attachment: NoteAttachment) => ImagePlacement | undefined;
  /** Longest caption the server accepts. */
  captionMax?: number;
  /**
   * The page's one cache of image bytes (`useFindingImages().thumbnails`).
   * With it, an image on the finding's list is shown from that cache — the
   * same object URL the placed images and the editor's picker use — instead
   * of being fetched here a second time.  An image the list does not carry
   * (or every image, when the list could not be read) is still fetched here.
   */
  thumbnails?: ImageThumbnails;
}

export interface NoteAttachmentsHandle {
  openPicker: () => void;
}

const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp';

/**
 * Evidence images on a single note: thumbnails (click → lightbox), an
 * "Attach image" picker, and per-image delete.  Each attachment is fetched as
 * an authenticated blob and rendered from an object URL (the serve endpoint
 * needs the bearer token, so a bare <img src> wouldn't load) — mirrors how the
 * web-interface screenshots load.
 */
const NoteAttachments = forwardRef<NoteAttachmentsHandle, NoteAttachmentsProps>(({
  hostId, noteId, attachments, canManage, onChanged, uploadFn, externalTrigger = false, onBusyChange,
  reportMarking,
}, ref) => {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  // v5.244.0 (code review finding 22) — the busy rule lives HERE, on the handler
  // as well as the control. The built-in button was disabled while uploading;
  // the external trigger added in v5.241.0 called straight through, so a second
  // pick started a second upload over the first. They shared one `uploading`
  // flag (the first to finish cleared the indicator while the other was still
  // running) and the same image could be submitted twice. A ref, not the state:
  // two picks can land before a re-render.
  const busyRef = useRef(false);
  useImperativeHandle(ref, () => ({
    openPicker: () => { if (!busyRef.current) fileRef.current?.click(); },
  }), []);
  const queryClient = useQueryClient();
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null);

  const idsKey = attachments.map((a) => a.id).join(',');

  // Which images the page's shared cache serves.  While the finding's list is
  // still being read nothing is fetched here: it would be fetched again from
  // the cache a moment later.
  const shared = reportMarking?.thumbnails;
  const sharedWaiting = shared?.listStatus === 'loading';
  const fromShared = (id: number) => !!shared && shared.has(id);
  const ensureShared = shared?.ensure;
  useEffect(() => {
    if (!ensureShared) return;
    attachments.forEach((att) => ensureShared(att.id));  // a no-op for an id off the list
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, ensureShared]);

  // The images the shared cache does not serve are read here, each as an
  // object URL made from the authenticated bytes.  A URL is this component's
  // to release: every one made is revoked when it goes, and one that arrives
  // after that is revoked at once.
  const createdUrls = useRef<string[]>([]);
  const gone = useRef(false);
  useEffect(() => {
    gone.current = false;
    const made = createdUrls.current;
    return () => {
      gone.current = true;
      made.forEach(URL.revokeObjectURL);
      made.length = 0;
    };
  }, []);
  // (So the key names this instance: a URL is never handed to another one,
  // which would be left showing it after this one revoked it.)
  const instance = useId();
  const own = useQueries({
    queries: attachments.map((att) => ({
      queryKey: ['getNoteAttachmentObjectUrl', att.id, instance],
      queryFn: async () => {
        const url = await getNoteAttachmentObjectUrl(att.id);
        if (gone.current) URL.revokeObjectURL(url);
        else createdUrls.current.push(url);
        return url;
      },
      enabled: !sharedWaiting && !fromShared(att.id),
      // The bytes of an attachment never change: read once while it is shown.
      staleTime: Infinity,
    })),
  });
  const ownOf = (id: number) => own[attachments.findIndex((a) => a.id === id)];

  const thumbnailUrl = (id: number): string | undefined => (fromShared(id) ? shared?.urls[id] : ownOf(id)?.data);
  // A fetch that failed: the thumbnail says so (and a click tries again)
  // instead of spinning for ever.
  const thumbnailFailed = (id: number): boolean => (fromShared(id) ? !!shared?.failed(id) : !!ownOf(id)?.isError);
  const retryThumbnail = (id: number) => {
    if (fromShared(id)) shared?.retry(id);
    else void ownOf(id)?.refetch();
  };

  // What the server refused, per image, kept beside it until the next change:
  // "this image is placed in the Description of …" says what to do, and a
  // toast is gone before it is read.
  const [refusals, setRefusals] = useState<Record<number, string>>({});
  const refuse = (id: number, message: string | null) => setRefusals((m) => {
    const next = { ...m };
    if (message) next[id] = message; else delete next[id];
    return next;
  });
  // After a change the thread is read again: a host note's attachments come
  // with the host (`getHost`); any other owner says how through `onChanged`.
  // On a finding (`reportMarking`) a caption, a mark or an image is also one
  // of the finding's images: the editor's picker and the placed images follow.
  const changed = () => {
    if (hostId != null) void queryClient.invalidateQueries({ queryKey: ['getHost', hostId] });
    onChanged?.();
    if (reportMarking) void invalidateReads(queryClient, 'getFindingImages');
  };

  const upload = useMutation({
    mutationFn: (file: File): Promise<unknown> => {
      if (uploadFn) return uploadFn(file);
      if (hostId != null) return uploadNoteAttachment(hostId, noteId, file);
      return Promise.reject(new Error('No upload target configured for this attachment.'));
    },
    onSuccess: changed,
    onError: (err) => toast.error(formatApiError(err, 'Could not attach image.')),
    onSettled: () => {
      busyRef.current = false;
      onBusyChange?.(false);
    },
  });
  const uploading = upload.isPending;

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = '';
    if (!file) return;
    // Guard the handler, not only the visible control (the picker can already
    // be open when an upload starts).
    if (busyRef.current) return;
    busyRef.current = true;
    onBusyChange?.(true);
    upload.mutate(file);
  };

  // Optimistic: the mark flips at once and the thread reloads behind it.
  const [reportOverride, setReportOverride] = useState<Record<number, boolean>>({});
  const mark = useMutation({
    mutationFn: ({ id, include }: { id: number; include: boolean }) => setNoteAttachmentInReport(id, include),
    onMutate: ({ id, include }) => setReportOverride((m) => ({ ...m, [id]: include })),
    onSuccess: (_stored, { id }) => {
      refuse(id, null);
      changed();
    },
    onError: (err, { id }) => {
      setReportOverride((m) => {
        const next = { ...m };
        delete next[id];
        return next;
      });
      const message = formatApiError(err, 'Could not change whether the image goes in the report.');
      refuse(id, message);
      toast.error(message);
    },
  });
  const onMark = (att: NoteAttachment, include: boolean) => mark.mutate({ id: att.id, include });

  const remove = useMutation({
    mutationFn: (id: number) => deleteNoteAttachment(id),
    onSuccess: (_none, id) => {
      refuse(id, null);
      changed();
    },
    onError: (err, id) => {
      const message = formatApiError(err, 'Could not delete attachment.');
      refuse(id, message);
      toast.error(message);
    },
  });
  const onDelete = (id: number) => remove.mutate(id);

  // One caption is edited at a time — but a save belongs to ITS image (S6).
  // Saving A and then opening the editor on B used to close B's editor (its
  // text lost) when A's request returned, or replace it with A's on failure.
  // The editor says which image it is on; whether an image's save is in
  // flight, and the text of one that failed, are kept per image.
  const [captionEdit, setCaptionEdit] = useState<{ id: number; text: string } | null>(null);
  const [captionSaving, setCaptionSaving] = useState<ReadonlySet<number>>(new Set());
  const [captionUnsaved, setCaptionUnsaved] = useState<Record<number, string>>({});
  const captionMax = reportMarking?.captionMax ?? 2000;
  // The editor takes the keyboard when it opens (the field used to appear
  // unfocused, so the next keystrokes went to the page), with a caption that
  // is being edited selected; when it closes, the keyboard goes back to the
  // image's caption button.  Keyed on WHICH image is edited, so typing never
  // re-selects the text.
  const captionInputRef = useRef<HTMLTextAreaElement>(null);
  const captionRowsRef = useRef<HTMLDivElement>(null);
  const editingId = captionEdit?.id ?? null;
  const lastEditingId = useRef<number | null>(null);
  useEffect(() => {
    const previous = lastEditingId.current;
    lastEditingId.current = editingId;
    if (editingId != null) {
      captionInputRef.current?.focus();
      captionInputRef.current?.select();
    } else if (previous != null) {
      captionRowsRef.current
        ?.querySelector<HTMLElement>(`[data-caption-button="${previous}"]`)
        ?.focus();
    }
  }, [editingId]);
  const forgetUnsaved = (id: number) => setCaptionUnsaved((m) => {
    if (!(id in m)) return m;
    const next = { ...m };
    delete next[id];
    return next;
  });
  // Several images' captions can be on their way at once, so which are is
  // kept per image here; the mutation itself only knows its latest call.
  const captionSave = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => setNoteAttachmentCaption(id, text.trim()),
    onMutate: ({ id }) => setCaptionSaving((prev) => new Set(prev).add(id)),
    onSuccess: (_stored, { id }) => {
      refuse(id, null);
      forgetUnsaved(id);
      // Only the editor that is still this image's closes.
      setCaptionEdit((current) => (current?.id === id ? null : current));
      changed();
    },
    onError: (err, { id, text }) => {
      refuse(id, formatApiError(err, 'Could not save the caption.'));
      // The words are kept for when this image's editor is opened again; an
      // editor that has moved to another image is left as it is.
      setCaptionUnsaved((m) => ({ ...m, [id]: text }));
    },
    onSettled: (_stored, _err, { id }) => setCaptionSaving((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    }),
  });
  const saveCaption = () => {
    if (!captionEdit || captionSaving.has(captionEdit.id)) return;
    captionSave.mutate(captionEdit);
  };

  if (attachments.length === 0 && !canManage) return null;

  // With the control drawn elsewhere, a note with no images takes no space.
  const takesSpace = attachments.length > 0 || !externalTrigger || uploading;

  return (
    <div className={takesSpace ? 'mt-xs space-y-xs' : undefined}>
      {attachments.length > 0 && (
        <div ref={captionRowsRef} className={reportMarking ? 'space-y-xs' : 'flex flex-wrap gap-xs'}>
          {attachments.map((att) => {
            const url = thumbnailUrl(att.id);
            const loadFailed = !url && thumbnailFailed(att.id);
            const caption = att.caption?.trim() || '';
            const thumbnail = (
              <div className="group relative shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    if (url) setLightbox({ src: url, caption: caption || att.filename });
                    else if (loadFailed) retryThumbnail(att.id);
                  }}
                  className="block size-20 overflow-hidden rounded-control border border-border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={loadFailed ? `${att.filename} could not be loaded — try again` : `View ${att.filename}`}
                  title={loadFailed ? 'Could not load the image — click to try again' : undefined}
                >
                  {url ? (
                    <img src={url} alt={caption || att.filename} className="size-full object-cover" />
                  ) : loadFailed ? (
                    <span className="flex size-full flex-col items-center justify-center gap-xxs text-caption text-warning"
                      data-testid={`thumbnail-failed-${att.id}`}>
                      <ImageOff className="size-4" aria-hidden />
                      Retry
                    </span>
                  ) : (
                    <span className="flex size-full items-center justify-center">
                      <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
                    </span>
                  )}
                </button>
                {canManage && (
                  <button
                    type="button"
                    onClick={() => onDelete(att.id)}
                    aria-label={`Delete ${att.filename}`}
                    className="absolute -right-1 -top-1 hidden rounded-full bg-destructive p-0.5 text-white shadow group-hover:block group-focus-within:block"
                  >
                    <Trash2 className="size-3" aria-hidden />
                  </button>
                )}
              </div>
            );
            if (!reportMarking) return <React.Fragment key={att.id}>{thumbnail}</React.Fragment>;

            const inReport = reportOverride[att.id] ?? !!att.include_in_report;
            const canMark = reportMarking.canMark(att);
            const placement = reportMarking.placement?.(att);
            const line = placement ? placementLine({ ...placement, in_report: inReport }) : null;
            const editing = captionEdit?.id === att.id ? captionEdit : null;
            const saving = captionSaving.has(att.id);
            return (
              <div key={att.id} className="flex min-w-0 items-start gap-sm">
                {thumbnail}
                <div className="min-w-0 flex-1 space-y-xxs">
                  {editing ? (
                    <div className="space-y-xxs">
                      <Textarea
                        ref={captionInputRef}
                        rows={2}
                        maxLength={captionMax}
                        value={editing.text}
                        disabled={saving}
                        aria-label={`Caption for ${att.filename}`}
                        placeholder="What the image shows — printed under it in the report"
                        onChange={(e) => setCaptionEdit({ id: att.id, text: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === 'Escape') {
                            // Cancels the caption only — not a panel around it.
                            e.preventDefault();
                            e.stopPropagation();
                            forgetUnsaved(att.id);
                            setCaptionEdit(null);
                          } else if (
                            e.key === 'Enter' && !e.shiftKey
                            // An Enter that confirms an IME composition is the
                            // input method's, not a save (229 = Safari, which
                            // clears `isComposing` before the keydown).
                            && !e.nativeEvent.isComposing && e.keyCode !== 229
                          ) {
                            // A caption is one line of words: Enter saves it,
                            // Shift+Enter still breaks the line.
                            e.preventDefault();
                            saveCaption();
                          }
                        }}
                      />
                      <div className="flex flex-wrap items-center gap-xs">
                        <Button type="button" size="sm" disabled={saving} onClick={saveCaption}>
                          {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save caption
                        </Button>
                        <Button type="button" variant="ghost" size="sm" disabled={saving}
                          onClick={() => { forgetUnsaved(att.id); setCaptionEdit(null); }}>
                          Cancel
                        </Button>
                        <span className="text-caption text-muted-foreground">{editing.text.length} / {captionMax}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex min-w-0 items-start gap-xs">
                      <p className="line-clamp-3 min-w-0 flex-1 text-body [overflow-wrap:anywhere]" title={caption || undefined}
                        data-testid={`caption-${att.id}`}>
                        {caption || (
                          <span className="text-muted-foreground">
                            No caption — the report prints “{safeFallback(att.filename, 'the file name')}”
                          </span>
                        )}
                      </p>
                      {canMark && saving ? (
                        <span className="flex shrink-0 items-center gap-xxs text-caption text-muted-foreground">
                          <Loader2 className="size-3.5 animate-spin" aria-hidden /> Saving caption…
                        </span>
                      ) : canMark ? (
                        <Button type="button" variant="ghost" size="sm" className="h-6 shrink-0 px-xs text-caption"
                          aria-label={`${caption ? 'Edit' : 'Add'} caption for ${att.filename}`}
                          data-caption-button={att.id}
                          // A caption this image's last save could not store is
                          // offered again, not the stored one.
                          onClick={() => setCaptionEdit({ id: att.id, text: captionUnsaved[att.id] ?? caption })}>
                          <Pencil className="size-3.5" aria-hidden /> {caption ? 'Edit caption' : 'Add caption'}
                        </Button>
                      ) : null}
                    </div>
                  )}
                  <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs text-caption text-muted-foreground">
                    {canMark ? (
                      <label className="flex shrink-0 cursor-pointer items-center gap-xxs">
                        <Checkbox
                          checked={inReport}
                          onCheckedChange={(v) => onMark(att, v === true)}
                          aria-label={`Include ${att.filename} in the report`}
                        />
                        In report
                      </label>
                    ) : inReport ? (
                      <span className="shrink-0">In report</span>
                    ) : null}
                    {line && (inReport || line.tone === 'warning') && (
                      <span className={`min-w-0 [overflow-wrap:anywhere] ${line.tone === 'warning' ? 'text-warning' : ''}`}
                        data-testid={`placement-${att.id}`}>
                        {line.text}
                      </span>
                    )}
                  </div>
                  {refusals[att.id] && (
                    <p role="alert" className="text-caption text-destructive [overflow-wrap:anywhere]">{refusals[att.id]}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {canManage && (
        <>
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={onPick} />
          {!externalTrigger ? (
            <Button variant="ghost" size="sm" disabled={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ImagePlus className="size-4" aria-hidden />}
              Attach image
            </Button>
          ) : uploading ? (
            <p className="flex items-center gap-xs text-caption text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" aria-hidden /> Uploading image…
            </p>
          ) : null}
        </>
      )}

      <ScreenshotLightbox
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
        src={lightbox?.src ?? null}
        caption={lightbox?.caption}
      />
    </div>
  );
});
NoteAttachments.displayName = 'NoteAttachments';

export default NoteAttachments;
