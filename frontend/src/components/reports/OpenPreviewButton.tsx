/**
 * "Open HTML preview": a finished HTML preview shown in a new browser tab
 * instead of saved (walkthrough 2026-10-14).
 *
 * The file is behind the API's authentication, so the tab cannot simply be
 * pointed at its address.  The click opens a BLANK tab at once — a popup
 * blocker allows a tab only inside the click itself, never after an awaited
 * request — then fetches the bytes and points the tab at an object URL of
 * them (`utils/download.showBlobInTab`, which revokes it).  When the browser
 * refused the tab, the file is saved instead and the reader is told so; when
 * the bytes could not be fetched, the blank tab is closed.
 *
 * The report is self-contained (`embed-resources: true`).  A `blob:` page
 * takes this page's Content-Security-Policy, so the report's own scripts do
 * not run there: it reads as a static document (the table of contents is in
 * the body, links between sections work).
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { ExternalLink, Loader2 } from 'lucide-react';

import { fetchReportPreviewBlob } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { saveBlob, showBlobInTab } from '../../utils/download';
import { Button, type ButtonProps } from '../ui/button';

/** What the blank tab says while the preview is fetched. */
const OPENING_WORDS = 'Opening the preview…';

interface Props extends Pick<ButtonProps, 'variant' | 'size' | 'className'> {
  projectId: number;
  /** The draft the preview is of: its job is served through it. */
  reportId: number;
  jobId: number;
  label: string;
}

const OpenPreviewButton: React.FC<Props> = ({ projectId, reportId, jobId, label, ...button }) => {
  const toast = useToast();
  const open = useMutation({
    mutationFn: async (tab: Window | null): Promise<'opened' | string> => {
      let fetched: { blob: Blob; filename: string };
      try {
        fetched = await fetchReportPreviewBlob(projectId, reportId, jobId);
      } catch (err) {
        tab?.close();
        throw err;
      }
      if (tab && !tab.closed) {
        showBlobInTab(tab, fetched.blob, 'text/html');
        return 'opened';
      }
      saveBlob(fetched.blob, fetched.filename);
      return fetched.filename;
    },
    onSuccess: (outcome) => {
      if (outcome !== 'opened') {
        toast.warning(`The browser did not open a new tab, so the preview was saved to your downloads as ${outcome}.`);
      }
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not open the preview.')),
  });
  const running = open.isPending;

  const onClick = () => {
    if (running) return;
    // Synchronously, inside the click: the one moment a tab may be opened.
    const tab = window.open('', '_blank');
    if (tab) {
      try {
        tab.document.title = OPENING_WORDS;
        tab.document.body.textContent = OPENING_WORDS;
      } catch {
        // A blank tab the page cannot write to still opens the preview.
      }
    }
    open.mutate(tab);
  };

  return (
    <Button type="button" {...button} onClick={onClick} disabled={running} aria-busy={running || undefined}>
      {running ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ExternalLink className="size-4" aria-hidden />}
      {running ? 'Opening…' : label}
    </Button>
  );
};

export default OpenPreviewButton;
