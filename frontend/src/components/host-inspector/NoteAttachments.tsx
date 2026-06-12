import React, { useEffect, useRef, useState } from 'react';
import { Loader2, ImagePlus, Trash2 } from 'lucide-react';
import {
  NoteAttachment,
  uploadNoteAttachment,
  deleteNoteAttachment,
  getNoteAttachmentObjectUrl,
} from '../../services/api';
import { Button } from '../ui/button';
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
}

const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp';

/**
 * Evidence images on a single note: thumbnails (click → lightbox), an
 * "Attach image" picker, and per-image delete.  Each attachment is fetched as
 * an authenticated blob and rendered from an object URL (the serve endpoint
 * needs the bearer token, so a bare <img src> wouldn't load) — mirrors how the
 * web-interface screenshots load.
 */
const NoteAttachments: React.FC<NoteAttachmentsProps> = ({ hostId, noteId, attachments, canManage, onChanged, uploadFn }) => {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
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
    setUploading(true);
    try {
      if (uploadFn) {
        await uploadFn(file);
      } else if (hostId != null) {
        await uploadNoteAttachment(hostId, noteId, file);
      } else {
        throw new Error('No upload target configured for this attachment.');
      }
      onChanged();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not attach image.'));
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteNoteAttachment(id);
      onChanged();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not delete attachment.'));
    }
  };

  if (attachments.length === 0 && !canManage) return null;

  return (
    <div className="mt-xs space-y-xs">
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-xs">
          {attachments.map((att) => {
            const url = urls[att.id];
            return (
              <div key={att.id} className="group relative">
                <button
                  type="button"
                  onClick={() => url && setLightbox({ src: url, caption: att.filename })}
                  className="block size-20 overflow-hidden rounded-control border border-border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`View ${att.filename}`}
                >
                  {url ? (
                    <img src={url} alt={att.filename} className="size-full object-cover" />
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
          })}
        </div>
      )}

      {canManage && (
        <>
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={onPick} />
          <Button variant="ghost" size="sm" disabled={uploading} onClick={() => fileRef.current?.click()}>
            {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ImagePlus className="size-4" aria-hidden />}
            Attach image
          </Button>
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
};

export default NoteAttachments;
