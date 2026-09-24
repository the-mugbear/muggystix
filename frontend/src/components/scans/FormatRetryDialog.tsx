import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  getJobDetection,
  getUploadFormats,
  reprocessIngestionJob,
  startIngestionJob,
  type DetectionResponse,
  type FormatOption,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { BASIS_LABEL, otherFormats, suggestionOf } from '../../hooks/useUploadReview';
import { formatApiError } from '../../utils/apiErrors';
import { Alert, AlertDescription } from '../ui/alert';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';

/**
 * "Review format and retry" on a failed job, and "Re-process" on a finished
 * one (v5.231.0; phase E of the staged-import plan).  Both read the
 * retained file's detection — the same detection the worker runs — let the
 * operator pick a format and name the source tool, and then either start
 * the failed job again in place or create a new job over the same bytes.
 * Re-process says what it does before it does it.
 */
export interface FormatRetryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: number;
  filename: string;
  /** retry = a failed job restarted in place; start = a staged job imported
   *  (v5.232.0); reprocess = a finished job re-imported as a new job. */
  mode: 'retry' | 'start' | 'reprocess';
  /** For re-process: the scan the prior run produced, if any. */
  priorScanId?: number | null;
  onDone: () => void;
}

const FormatRetryDialog: React.FC<FormatRetryDialogProps> = ({
  open, onOpenChange, jobId, filename, mode, priorScanId, onDone,
}) => {
  const toast = useToast();
  const [detection, setDetection] = useState<DetectionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string>('');
  const [sourceTool, setSourceTool] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // The format list when inspection failed (a detection carries its own).
  const [fallbackFormats, setFallbackFormats] = useState<FormatOption[]>([]);
  const [inspectNonce, setInspectNonce] = useState(0);
  const [fileGone, setFileGone] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setDetection(null);
    setError(null);
    setFileGone(false);
    setFallbackFormats([]);
    // Nothing is preselected.  A confident detection needs no override ("let
    // detection decide"); an uncertain one is a suggestion the operator
    // confirms or replaces — it used to be applied as if they had chosen it.
    setChosen('');
    setLoading(true);
    getJobDetection(jobId)
      .then((d) => {
        if (!cancelled) setDetection(d);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(formatApiError(err, 'Could not inspect the retained file.'));
        // 409 = the retained file is gone: no format choice can help, and
        // offering one would only fail a second time.
        const gone = (err as { response?: { status?: number } })?.response?.status === 409;
        setFileGone(gone);
        if (gone) return;
        // Otherwise manual selection must survive a failed inspection.
        getUploadFormats()
          .then((list) => { if (!cancelled) setFallbackFormats(list); })
          .catch(() => undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, jobId, inspectNonce]);
  useEffect(() => {
    if (open) setSourceTool('');
  }, [open, jobId]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const options = { formatOverride: chosen || null, sourceTool: sourceTool.trim() || null };
      if (mode === 'retry' || mode === 'start') {
        await startIngestionJob(jobId, options);
        toast.success(
          `${filename} ${mode === 'start' ? 'queued' : 'queued again'}${chosen ? ` as ${labelFor(formats, chosen)}` : ''}`,
          { autoHideMs: 3000 },
        );
      } else {
        const job = await reprocessIngestionJob(jobId, options);
        toast.success(`Re-processing ${filename} as job #${job.id}${chosen ? ` (${labelFor(formats, chosen)})` : ''}`, { autoHideMs: 4000 });
      }
      onOpenChange(false);
      onDone();
    } catch (err) {
      setError(formatApiError(err, mode === 'reprocess' ? 'Could not start the re-process.' : 'Could not start the import.'));
    } finally {
      setSubmitting(false);
    }
  };

  const primary = detection?.candidates[0];
  const recognised = !!detection && !detection.needs_choice;
  const suggested = detection ? suggestionOf(detection) : null;
  const formats = detection?.formats ?? fallbackFormats;
  // Without a confident detection there is nothing to "let decide": the
  // operator names the format (or confirms the suggestion) first.
  const canSubmit = !loading && (recognised || chosen !== '') && (!!detection || formats.length > 0);

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>
            {mode === 'retry' ? 'Review format and retry' : mode === 'start' ? 'Review format and import' : 'Re-process this file'}
          </DialogTitle>
          <DialogDescription className="break-words">{filename}</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-sm">
          {mode === 'reprocess' && (
            <Alert variant="warning">
              <AlertDescription>
                This runs the retained file through the pipeline again as a new import.
                A <strong>new scan record</strong> is created when it parses.
                {priorScanId != null
                  ? <> The prior scan (#{priorScanId}) and everything it contributed <strong>stay</strong> until you delete that scan.</>
                  : <> Anything a prior run contributed <strong>stays</strong>.</>}
                {' '}The duplicate guard is bypassed on purpose.
              </AlertDescription>
            </Alert>
          )}
          {loading && (
            <p className="inline-flex items-center gap-xs text-metadata text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Inspecting the retained file…
            </p>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-wrap items-center gap-xs">
                <span className="min-w-0 flex-1 break-words">{error}</span>
                {!detection && !loading && !fileGone && (
                  <Button size="sm" variant="outline" onClick={() => setInspectNonce((n) => n + 1)}>
                    Retry inspection
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          )}
          {!loading && (detection || formats.length > 0) && (
            <>
              {detection && (
                <p className="text-metadata">
                  {recognised && primary ? (
                    <>
                      Detected: <span className="font-medium">{primary.label}</span>
                      <span className="text-caption text-muted-foreground"> · recognised by structure</span>
                    </>
                  ) : (
                    <span className="text-warning">{detection.reason ?? 'No format was recognised. Choose one.'}</span>
                  )}
                </p>
              )}
              {!detection && (
                <p className="text-caption text-warning">
                  The file could not be inspected, so nothing is suggested. Choose its format yourself, or retry.
                </p>
              )}
              {suggested && chosen === '' && (
                <div className="flex flex-wrap items-center gap-xs">
                  <p className="min-w-0 flex-1 break-words text-caption text-muted-foreground">
                    Suggested: <span className="font-medium text-foreground">{labelFor(formats, suggested)}</span>
                  </p>
                  <Button size="sm" variant="outline" onClick={() => setChosen(suggested)}>
                    Confirm suggested format
                  </Button>
                </div>
              )}
              <div>
                <Label htmlFor="fr-format">Parse as</Label>
                <select
                  id="fr-format"
                  className="flex h-9 w-full rounded-control border border-input bg-background px-sm text-metadata"
                  value={chosen}
                  onChange={(e) => setChosen(e.target.value)}
                >
                  <option value="">{recognised ? 'Let detection decide' : 'Select a format…'}</option>
                  {detection && detection.candidates.length > 0 && (
                    <optgroup label={detection.candidates.some((c) => c.basis !== 'fallback') ? 'Detected' : 'Not recognised — tried for this file type'}>
                      {detection.candidates.map((c) => (
                        <option key={c.file_type} value={c.file_type}>{c.label} ({BASIS_LABEL[c.basis] ?? c.basis})</option>
                      ))}
                    </optgroup>
                  )}
                  <optgroup label={detection && detection.candidates.length > 0 ? 'Other formats' : 'All formats'}>
                    {otherFormats(formats, detection?.candidates).map((f) => (
                      <option key={f.file_type} value={f.file_type}>{f.label}</option>
                    ))}
                  </optgroup>
                </select>
                <p className="mt-xxs text-caption text-muted-foreground">
                  A chosen format runs exactly that parser; a wrong choice fails visibly rather than falling back.
                </p>
              </div>
              <div>
                <Label htmlFor="fr-source">Source tool (optional)</Label>
                <Input id="fr-source" value={sourceTool} onChange={(e) => setSourceTool(e.target.value)} maxLength={64} placeholder="e.g. subfinder 2.6" />
              </div>
            </>
          )}
          {detection && (
            <>
              {detection.preview.sample.length > 0 && (
                <div>
                  <p className="text-caption font-semibold">What the reader saw</p>
                  <ul className="font-mono text-caption text-muted-foreground">
                    {detection.preview.sample.map((line, i) => <li key={i} className="break-words">{line}</li>)}
                  </ul>
                </div>
              )}
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-control bg-muted/40 p-xs font-mono text-caption text-muted-foreground">
                {detection.preview.raw || '(empty file)'}
              </pre>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting || !canSubmit}>
            {submitting && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            {mode === 'retry' ? 'Retry import' : mode === 'start' ? 'Import' : 'Re-process'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const labelFor = (formats: FormatOption[], fileType: string): string =>
  formats.find((f) => f.file_type === fileType)?.label ?? fileType;

export default FormatRetryDialog;
