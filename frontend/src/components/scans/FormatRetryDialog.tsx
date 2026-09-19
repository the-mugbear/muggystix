import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  getJobDetection,
  reprocessIngestionJob,
  startIngestionJob,
  type DetectionResponse,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
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

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setDetection(null);
    setError(null);
    setChosen('');
    setSourceTool('');
    setLoading(true);
    getJobDetection(jobId)
      .then((d) => {
        if (cancelled) return;
        setDetection(d);
        setChosen(d.primary ?? '');
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Could not inspect the retained file.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, jobId]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const options = { formatOverride: chosen || null, sourceTool: sourceTool.trim() || null };
      if (mode === 'retry' || mode === 'start') {
        await startIngestionJob(jobId, options);
        toast.success(
          `${filename} ${mode === 'start' ? 'queued' : 'queued again'}${chosen ? ` as ${labelFor(detection, chosen)}` : ''}`,
          { autoHideMs: 3000 },
        );
      } else {
        const job = await reprocessIngestionJob(jobId, options);
        toast.success(`Re-processing ${filename} as job #${job.id}${chosen ? ` (${labelFor(detection, chosen)})` : ''}`, { autoHideMs: 4000 });
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
              <AlertDescription className="break-words">{error}</AlertDescription>
            </Alert>
          )}
          {detection && (
            <>
              <p className="text-metadata">
                {primary ? (
                  <>
                    Detected: <span className="font-medium">{primary.label}</span>
                    <span className="text-caption text-muted-foreground"> · by {primary.basis}</span>
                  </>
                ) : (
                  <span className="text-warning">No format was recognised. Choose one.</span>
                )}
                {detection.needs_choice && detection.reason && (
                  <span className="block text-caption text-warning">{detection.reason}</span>
                )}
              </p>
              <div>
                <Label htmlFor="fr-format">Parse as</Label>
                <select
                  id="fr-format"
                  className="flex h-9 w-full rounded-control border border-input bg-background px-sm text-metadata"
                  value={chosen}
                  onChange={(e) => setChosen(e.target.value)}
                >
                  <option value="">Let detection decide</option>
                  {detection.candidates.length > 0 && (
                    <optgroup label="Detected">
                      {detection.candidates.map((c) => (
                        <option key={c.file_type} value={c.file_type}>{c.label} (by {c.basis})</option>
                      ))}
                    </optgroup>
                  )}
                  <optgroup label="All formats">
                    {detection.formats.map((f) => (
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
          <Button onClick={() => void submit()} disabled={submitting || loading || !detection}>
            {submitting && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            {mode === 'retry' ? 'Retry import' : mode === 'start' ? 'Import' : 'Re-process'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const labelFor = (d: DetectionResponse | null, fileType: string): string =>
  d?.formats.find((f) => f.file_type === fileType)?.label ?? fileType;

export default FormatRetryDialog;
