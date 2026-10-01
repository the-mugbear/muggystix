import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Loader2, ImagePlus, Pencil, Trash2 } from 'lucide-react';
import {
  NoteAttachment,
  uploadNoteAttachment,
  deleteNoteAttachment,
  getNoteAttachmentObjectUrl,
  setNoteAttachmentCaption,
  setNoteAttachmentInReport,
} from '../../services/api';
import { ImagePlacement, placementLine } from '../../utils/reportImages';
import { safeFallback } from '../../utils/uiStyles';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Textarea } from '../ui/textarea';
import ScreenshotLightbox from '../ScreenshotLightbox';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';

interface NoteAttachmentsProps {
  /** Host that owns the note — used for the default (host-note) upload path. */
  hostId?: number;
  noteId: number;
  attachments: NoteAttachment[];
  /** Analyst+ — gates the attach/delete affordances (display is always on). */
  canManage: boolean;
  /** Reload the notes thread after an upload/delete so the new state shows. */
  onChanged: () => void;
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
  /** A caption, a mark or an image changed: the page re-reads the finding's
   *  images (the editor's picker and the placed images follow). */
  onImagesChanged?: () => void;
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
  const createdUrls = useRef<string[]>([]);
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [uploading, setUploading] = useState(false);
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null);

  const idsKey = attachments.map((a) => a.id).join(',');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const att of attachments) {
        if (urls[att.id]) continue;
        try {
          const url = await getNoteAttachmentObjectUrl(att.id);
          if (cancelled) {
            URL.revokeObjectURL(url);
          } else {
            createdUrls.current.push(url);
            setUrls((m) => ({ ...m, [att.id]: url }));
          }
        } catch {
          /* leave as a spinner — a transient fetch failure shouldn't break the note */
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  // Revoke every object URL we created when the component unmounts.
  useEffect(() => () => createdUrls.current.forEach(URL.revokeObjectURL), []);

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = '';
    if (!file) return;
    // Guard the handler, not only the visible control (the picker can already
    // be open when an upload starts).
    if (busyRef.current) return;
    busyRef.current = true;
    setUploading(true);
    onBusyChange?.(true);
    try {
      if (uploadFn) {
        await uploadFn(file);
      } else if (hostId != null) {
        await uploadNoteAttachment(hostId, noteId, file);
      } else {
        throw new Error('No upload target configured for this attachment.');
      }
      onChanged();
      reportMarking?.onImagesChanged?.();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not attach image.'));
    } finally {
      busyRef.current = false;
      setUploading(false);
      onBusyChange?.(false);
    }
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
  const changed = () => {
    onChanged();
    reportMarking?.onImagesChanged?.();
  };

  // Optimistic: the mark flips at once and the thread reloads behind it.
  const [reportOverride, setReportOverride] = useState<Record<number, boolean>>({});
  const onMark = async (att: NoteAttachment, include: boolean) => {
    setReportOverride((m) => ({ ...m, [att.id]: include }));
    try {
      await setNoteAttachmentInReport(att.id, include);
      refuse(att.id, null);
      changed();
    } catch (err) {
      setReportOverride((m) => {
        const next = { ...m };
        delete next[att.id];
        return next;
      });
      const message = formatApiError(err, 'Could not change whether the image goes in the report.');
      refuse(att.id, message);
      toast.error(message);
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteNoteAttachment(id);
      refuse(id, null);
      changed();
    } catch (err) {
      const message = formatApiError(err, 'Could not delete attachment.');
      refuse(id, message);
      toast.error(message);
    }
  };

  // One caption is edited at a time.
  const [captionEdit, setCaptionEdit] = useState<{ id: number; text: string; saving: boolean } | null>(null);
  const captionMax = reportMarking?.captionMax ?? 2000;
  const saveCaption = async () => {
    if (!captionEdit || captionEdit.saving) return;
    const { id, text } = captionEdit;
    setCaptionEdit({ id, text, saving: true });
    try {
      await setNoteAttachmentCaption(id, text.trim());
      refuse(id, null);
      setCaptionEdit(null);
      changed();
    } catch (err) {
      refuse(id, formatApiError(err, 'Could not save the caption.'));
      setCaptionEdit({ id, text, saving: false });
    }
  };

  if (attachments.length === 0 && !canManage) return null;

  // With the control drawn elsewhere, a note with no images takes no space.
  const takesSpace = attachments.length > 0 || !externalTrigger || uploading;

  return (
    <div className={takesSpace ? 'mt-xs space-y-xs' : undefined}>
      {attachments.length > 0 && (
        <div className={reportMarking ? 'space-y-xs' : 'flex flex-wrap gap-xs'}>
          {attachments.map((att) => {
            const url = urls[att.id];
            const caption = att.caption?.trim() || '';
            const thumbnail = (
              <div className="group relative shrink-0">
                <button
                  type="button"
                  onClick={() => url && setLightbox({ src: url, caption: caption || att.filename })}
                  className="block size-20 overflow-hidden rounded-control border border-border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`View ${att.filename}`}
                >
                  {url ? (
                    <img src={url} alt={caption || att.filename} className="size-full object-cover" />
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
            return (
              <div key={att.id} className="flex min-w-0 items-start gap-sm">
                {thumbnail}
                <div className="min-w-0 flex-1 space-y-xxs">
                  {editing ? (
                    <div className="space-y-xxs">
                      <Textarea
                        rows={2}
                        maxLength={captionMax}
                        value={editing.text}
                        disabled={editing.saving}
                        aria-label={`Caption for ${att.filename}`}
                        placeholder="What the image shows — printed under it in the report"
                        onChange={(e) => setCaptionEdit({ ...editing, text: e.target.value })}
                      />
                      <div className="flex flex-wrap items-center gap-xs">
                        <Button type="button" size="sm" disabled={editing.saving} onClick={() => void saveCaption()}>
                          {editing.saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save caption
                        </Button>
                        <Button type="button" variant="ghost" size="sm" disabled={editing.saving}
                          onClick={() => setCaptionEdit(null)}>
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
                      {canMark && (
                        <Button type="button" variant="ghost" size="sm" className="h-6 shrink-0 px-xs text-caption"
                          aria-label={`${caption ? 'Edit' : 'Add'} caption for ${att.filename}`}
                          onClick={() => setCaptionEdit({ id: att.id, text: caption, saving: false })}>
                          <Pencil className="size-3.5" aria-hidden /> {caption ? 'Edit caption' : 'Add caption'}
                        </Button>
                      )}
                    </div>
                  )}
                  <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs text-caption text-muted-foreground">
                    {canMark ? (
                      <label className="flex shrink-0 cursor-pointer items-center gap-xxs">
                        <Checkbox
                          checked={inReport}
                          onCheckedChange={(v) => void onMark(att, v === true)}
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
