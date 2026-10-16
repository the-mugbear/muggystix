/**
 * A button that saves one file — and shows that it is doing so, and that it
 * has.
 *
 * A file save used to give no sign at all: the request ran, the browser put
 * the file in its downloads, and the page looked as it did before the click,
 * so people pressed again and collected copies.  Each button owns its own
 * request (one mutation per button), so the state on it is that file's and a
 * second file is not held up by the first:
 *
 *   - while the file is fetched: a spinner and "Downloading…", and the button
 *     cannot be pressed again;
 *   - when it is saved: a toast naming the file, and a quiet "Downloaded" mark
 *     that stays for as long as the button is on the page;
 *   - when it fails: the caller's words, with the server's reason.
 *
 * A new file is a new button: key it by what it downloads (a preview's job
 * id), and the mark of the file it replaced goes with it.
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Check, Download, Loader2 } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { cn } from '../utils/cn';
import { Button, type ButtonProps } from './ui/button';

const DOWNLOADED_TITLE = 'Downloaded during this visit';

/** A saved file's name, and what it holds when that is not everything. */
export interface SavedFile {
  filename: string;
  note?: string;
}

interface Props extends Pick<ButtonProps, 'variant' | 'size' | 'className' | 'disabled'> {
  /** Fetches and saves the file.  May answer the name it was saved under —
   *  or that name and a note on what the file holds ("the first 50,000 of
   *  80,000 rows"), said in the toast and beside the mark. */
  saveFn: () => Promise<string | SavedFile | void>;
  /** The button's words. */
  label: string;
  /** Its accessible name, when the words alone do not say which file ("HTML"). */
  name?: string;
  /** The file's name when it is known before the request; the request's answer is preferred. */
  filename?: string;
  /** Said, with the server's reason, when the file could not be fetched. */
  errorText: string;
  /** No room for words beside the button (a table cell): the mark is the icon alone. */
  compact?: boolean;
}

/** "Saved acme-report-3.docx to your downloads." */
export const savedMessage = (filename: string | null | undefined): string =>
  (filename ? `Saved ${filename} to your downloads.` : 'Saved the file to your downloads.');

const DownloadButton: React.FC<Props> = ({
  saveFn, label, name, filename, errorText, compact = false, disabled, className, ...button
}) => {
  const toast = useToast();
  // Kept apart from the mutation's own status: a later attempt that fails
  // does not take back that the file was saved once.
  const [downloaded, setDownloaded] = useState(false);
  // What the last saved file holds, when it is not the whole answer.
  const [note, setNote] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: saveFn,
    onSuccess: (saved) => {
      setDownloaded(true);
      const file = typeof saved === 'object' && saved ? saved : null;
      const name = file ? file.filename : typeof saved === 'string' && saved ? saved : filename;
      setNote(file?.note ?? null);
      toast.success(file?.note ? `${savedMessage(name)} ${file.note}` : savedMessage(name));
    },
    onError: (err) => toast.error(formatApiError(err, errorText)),
  });
  const running = save.isPending;
  const icon = compact ? 'size-3.5' : 'size-4';

  return (
    <span className="inline-flex min-w-0 items-center gap-xs">
      <Button
        type="button" {...button} className={className}
        aria-label={name ?? label} aria-busy={running || undefined}
        // The compact mark's words (the button's description, and its tooltip).
        title={downloaded && compact && !running ? DOWNLOADED_TITLE : undefined}
        data-downloaded={downloaded ? '' : undefined}
        disabled={disabled || running}
        // A press while the file is on its way asks for nothing.
        onClick={() => { if (!running) save.mutate(); }}
      >
        {running ? <Loader2 className={cn(icon, 'animate-spin')} aria-hidden />
          : downloaded && compact ? <Check className={cn(icon, 'text-success')} aria-hidden />
            : <Download className={icon} aria-hidden />}
        {running ? 'Downloading…' : label}
      </Button>
      {downloaded && !compact && !running && (
        <span className="inline-flex shrink-0 items-center gap-xxs text-caption text-muted-foreground"
          title={DOWNLOADED_TITLE}>
          <Check className="size-3.5 text-success" aria-hidden /> Downloaded
          {note && <span className="text-warning" data-testid="download-note"> — {note}</span>}
        </span>
      )}
    </span>
  );
};

export default DownloadButton;
