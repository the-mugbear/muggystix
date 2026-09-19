import React, { useEffect, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Loader2, Trash2, Upload } from 'lucide-react';

import { ACCEPTED_EXTENSIONS, ACCEPTED_EXTENSION_LIST, SUPPORTED_FORMATS } from '../../data/uploadFormats';
import { BASIS_LABEL, useUploadReview, isImportable, type ReviewRow, type StartedUpload } from '../../hooks/useUploadReview';
import type { FormatOption } from '../../services/api';
import { cn } from '../../utils/cn';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '../ui/accordion';
import { Alert, AlertDescription } from '../ui/alert';
import { Badge } from '../ui/badge';
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
import { Switch } from '../ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

/**
 * Upload scans (v5.229.0; phase C of the staged-import plan).
 *
 * Choose files → review formats and options → import → results.  Every
 * dropped file is staged, inspected, and shown with what was recognised and
 * on what basis.  One click imports the ready files; a file recognised only
 * by its name, matched by several formats, or not recognised at all needs a
 * choice first and stays staged until it gets one.  Results are the page's
 * banner.  The file's content preview and the reader's sample are one click
 * away, so the choice is recognisable rather than remembered.
 */

const fmtSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export interface UploadReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string | null | undefined;
  skipInformational: boolean;
  savingSkipInformational: boolean;
  onSkipInformationalChange: (next: boolean) => void;
  /** A file the operator started: the page adds it to the results banner. */
  onStarted: (started: StartedUpload) => void;
  onViewScan: (scanId: number) => void;
}

const UploadReviewDialog: React.FC<UploadReviewDialogProps> = ({
  open,
  onOpenChange,
  projectName,
  skipInformational,
  savingSkipInformational,
  onSkipInformationalChange,
  onStarted,
  onViewScan,
}) => {
  const review = useUploadReview({ skipInformational, onStarted });
  const { rows } = review;
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [batchName, setBatchName] = useState('');
  const { nameBatch } = review;
  const saveBatchName = async () => {
    await nameBatch(batchName);
  };
  // A new drop forms a new batch: the field starts empty for it.
  const batchId = review.batch?.id ?? null;
  useEffect(() => {
    setBatchName('');
  }, [batchId]);

  const { getRootProps, getInputProps, isDragActive, fileRejections } = useDropzone({
    onDrop: (accepted) => void review.addFiles(accepted),
    accept: ACCEPTED_EXTENSIONS,
    maxSize: 2 * 1024 * 1024 * 1024,
    multiple: true,
  });

  // Every file started: the banner has them; the dialog's job is done.
  useEffect(() => {
    if (open && review.allStarted) onOpenChange(false);
  }, [open, review.allStarted, onOpenChange]);
  // Closed: started rows go (unresolved staged files stay for the next
  // open).  They used to remain, so `allStarted` was still true and the
  // dialog closed itself the instant "Upload scans" was clicked again.
  const { clearStarted } = review;
  useEffect(() => {
    if (!open) clearStarted();
  }, [open, clearStarted]);

  return (
    <Dialog open={open} onOpenChange={(v) => !review.busy && onOpenChange(v)}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>Upload scans</DialogTitle>
          <DialogDescription>
            Files are stored and inspected first. Import what was recognised; choose a format for the rest.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-sm">
          <div
            {...getRootProps()}
            aria-label="Scan file upload drop zone"
            className={cn(
              'flex flex-col items-center gap-xs rounded-panel border-2 border-dashed p-md text-center transition-colors',
              isDragActive ? 'cursor-pointer border-primary bg-accent' : 'cursor-pointer border-border hover:border-primary hover:bg-accent',
            )}
          >
            <input {...getInputProps()} />
            <Upload className="size-8 text-primary" aria-hidden />
            <p className="text-metadata font-semibold">{isDragActive ? 'Drop the files here…' : 'Drop files here, or click to choose'}</p>
            <p className="break-words text-caption text-muted-foreground">
              Accepted: <span className="font-mono">{ACCEPTED_EXTENSION_LIST.join(' ')}</span>
            </p>
          </div>

          {fileRejections.length > 0 && (
            <Alert variant="destructive">
              <AlertDescription>
                {fileRejections.map(({ file, errors }) => (
                  <div key={file.name} className="break-words">
                    <strong>{file.name}</strong>: {errors.map((e) => e.message).join('; ')}
                  </div>
                ))}
              </AlertDescription>
            </Alert>
          )}

          {/* v5.239.0 — several files dropped together are ONE row of the import
              history; this is the name it is recognised by there. Optional:
              left alone it keeps the generated "N files · time" label. */}
          {review.batch && (
            <div className="flex flex-wrap items-end gap-xs">
              <div className="min-w-0 flex-1">
                <Label htmlFor="upload-batch-name" className="text-caption">
                  Name this upload (optional)
                </Label>
                <Input
                  id="upload-batch-name"
                  className="h-8 text-caption"
                  placeholder={review.batch.label}
                  value={batchName}
                  onChange={(e) => setBatchName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void saveBatchName(); }}
                  maxLength={200}
                />
              </div>
              <Button
                size="sm"
                variant="outline"
                className="h-8"
                disabled={!batchName.trim() || batchName.trim() === review.batch.label}
                onClick={() => void saveBatchName()}
              >
                {review.batch.named && batchName.trim() === review.batch.label ? 'Saved' : 'Save name'}
              </Button>
              {review.batchError && (
                <p role="alert" className="w-full break-words text-caption text-destructive">{review.batchError}</p>
              )}
            </div>
          )}

          {rows.length > 0 && (
            <div className="rounded-panel border border-border">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[28%]">File</TableHead>
                    <TableHead className="w-[40%]">Format</TableHead>
                    <TableHead className="w-[32%]">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <ReviewRowView
                      key={row.key}
                      row={row}
                      previewOpen={previewKey === row.key}
                      onTogglePreview={() => setPreviewKey((k) => (k === row.key ? null : row.key))}
                      formats={review.formats}
                      onChoose={(ft) => review.setChoice(row.key, ft)}
                      onConfirmSuggestion={() => review.confirmSuggestion(row.key)}
                      onRetryDetection={() => review.retryDetection(row.key)}
                      onSourceTool={(v) => review.setSourceTool(row.key, v)}
                      onImport={() => void review.importOne(row)}
                      onImportAgain={() => review.importAgain(row.key)}
                      onRemove={() => void review.remove(row.key)}
                      onViewScan={onViewScan}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <div className="flex items-start justify-between gap-sm rounded-panel border border-border p-sm">
            <div className="min-w-0">
              <Label htmlFor="skip-informational" className="text-metadata font-semibold">
                Skip informational Nessus findings
              </Label>
              <p className="text-caption text-muted-foreground">
                Severity-0 plugins are not stored as findings; open ports are still recorded from them.
                Applies to every upload into <strong>{projectName ?? 'this project'}</strong> until changed, and is
                fixed for a file at the moment it is dropped.
              </p>
            </div>
            <Switch
              id="skip-informational"
              checked={skipInformational}
              onCheckedChange={(v) => onSkipInformationalChange(v === true)}
              disabled={savingSkipInformational}
              aria-label="Skip informational Nessus findings"
            />
          </div>

          <Accordion type="single" collapsible>
            <AccordionItem value="formats">
              <AccordionTrigger>Supported formats ({SUPPORTED_FORMATS.length} tools)</AccordionTrigger>
              <AccordionContent>
                <ul className="flex flex-col divide-y divide-border">
                  {SUPPORTED_FORMATS.map((item) => (
                    <li key={`${item.tool}-${item.formats}`} className="flex min-w-0 flex-col gap-xxs py-xs">
                      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs">
                        <span className="break-words text-metadata font-semibold">{item.tool}</span>
                        <span className="break-words font-mono text-caption text-primary">{item.formats}</span>
                      </div>
                      <p className="break-words text-caption text-muted-foreground">{item.desc}</p>
                      {item.hint && (
                        <p className="break-words text-caption text-muted-foreground">
                          <span className="font-medium">Auto-detect:</span> {item.hint}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </DialogBody>
        <DialogFooter className="items-center">
          {rows.some((r) => r.phase === 'choose' && !r.chosen) && (
            <span className="mr-auto text-caption text-muted-foreground">
              {review.chooseCount} file{review.chooseCount === 1 ? '' : 's'} need a format. Files left here expire after 24 hours.
            </span>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={review.busy}>
            Close
          </Button>
          <Button onClick={() => void review.importReady()} disabled={review.readyCount === 0 || review.busy}>
            {review.busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            Import {review.readyCount} ready file{review.readyCount === 1 ? '' : 's'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const ReviewRowView: React.FC<{
  row: ReviewRow;
  previewOpen: boolean;
  onTogglePreview: () => void;
  /** The chooser's list when this row has no detection of its own. */
  formats: FormatOption[];
  onChoose: (fileType: string | null) => void;
  onConfirmSuggestion: () => void;
  onRetryDetection: () => void;
  onSourceTool: (value: string) => void;
  onImport: () => void;
  onImportAgain: () => void;
  onRemove: () => void;
  onViewScan: (scanId: number) => void;
}> = ({
  row, previewOpen, onTogglePreview, formats, onChoose, onConfirmSuggestion, onRetryDetection,
  onSourceTool, onImport, onImportAgain, onRemove, onViewScan,
}) => {
  const d = row.detection;
  const primary = d?.candidates[0];
  const allFormats = d?.formats ?? formats;
  const labelOf = (ft: string) =>
    d?.candidates.find((c) => c.file_type === ft)?.label ?? allFormats.find((f) => f.file_type === ft)?.label ?? ft;
  const editable = row.phase === 'ready' || row.phase === 'choose';
  // v5.232.1 — the preview opens directly beneath its row.  It used to
  // render after the whole table, which with several files put it below the
  // visible part of the dialog, so clicking Preview looked like nothing
  // happened.  It scrolls into view for the same reason.
  const previewRef = React.useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    if (previewOpen) previewRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [previewOpen]);
  return (
    <>
    <TableRow className="align-top">
      <TableCell className="min-w-0">
        <p className="truncate font-medium" title={row.filename}>{row.filename}</p>
        <p className="text-caption text-muted-foreground">{fmtSize(row.size)}</p>
        {d && (
          <button
            type="button"
            onClick={onTogglePreview}
            className="mt-xxs rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {previewOpen ? 'Hide preview' : 'Preview'}
          </button>
        )}
      </TableCell>
      <TableCell className="min-w-0">
        {row.phase === 'uploading' || row.phase === 'detecting' ? (
          <span className="text-caption text-muted-foreground">{row.phase === 'uploading' ? 'Uploading…' : 'Inspecting…'}</span>
        ) : row.phase === 'duplicate' || row.phase === 'error' ? (
          <span className="text-caption text-muted-foreground">—</span>
        ) : d || editable ? (
          <div className="flex min-w-0 flex-col gap-xxs">
            {d && !d.needs_choice && primary ? (
              <p className="text-metadata">
                <span className="font-medium">{primary.label}</span>
                <span className="text-caption text-muted-foreground"> · recognised by structure</span>
              </p>
            ) : d ? (
              <p className="break-words text-caption text-warning">{d.reason ?? 'Choose a format.'}</p>
            ) : (
              // Inspection failed: say so, offer it again, and keep manual
              // selection available from the independent format list.
              <div className="flex flex-wrap items-center gap-xs">
                <p className="min-w-0 flex-1 break-words text-caption text-warning">
                  The file could not be inspected. Retry, or choose its format yourself.
                </p>
                <Button size="sm" variant="outline" className="h-7" onClick={onRetryDetection}>
                  Retry inspection
                </Button>
              </div>
            )}
            {/* A suggestion is shown, never applied: the row is not ready
                until the operator confirms it or selects a format. */}
            {editable && row.suggested && !row.chosen && (
              <div className="flex flex-wrap items-center gap-xs">
                <p className="min-w-0 flex-1 break-words text-caption text-muted-foreground">
                  Suggested: <span className="font-medium text-foreground">{labelOf(row.suggested)}</span>
                </p>
                <Button size="sm" variant="outline" className="h-7" onClick={onConfirmSuggestion}>
                  Confirm suggested format
                </Button>
              </div>
            )}
            {editable && (
              <select
                aria-label={`Format for ${row.filename}`}
                className="flex h-8 w-full rounded-control border border-input bg-background px-xs text-caption"
                value={row.chosen ?? ''}
                onChange={(e) => onChoose(e.target.value || null)}
              >
                <option value="">{d && !d.needs_choice ? 'As detected' : 'Select a format…'}</option>
                {d && d.candidates.length > 0 && (
                  <optgroup label={d.candidates.some((c) => c.basis !== 'fallback') ? 'Detected' : 'Not recognised — tried for this file type'}>
                    {d.candidates.map((c) => (
                      <option key={c.file_type} value={c.file_type}>
                        {c.label} ({BASIS_LABEL[c.basis] ?? c.basis})
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="All formats">
                  {allFormats.map((f) => (
                    <option key={f.file_type} value={f.file_type}>{f.label}</option>
                  ))}
                </optgroup>
              </select>
            )}
            {editable && (
              <Input
                aria-label={`Source tool for ${row.filename}`}
                className="h-8 text-caption"
                placeholder="Source tool (optional, e.g. subfinder 2.6)"
                value={row.sourceTool}
                onChange={(e) => onSourceTool(e.target.value)}
                maxLength={64}
              />
            )}
          </div>
        ) : (
          <span className="text-caption text-muted-foreground">{row.chosen ? labelOf(row.chosen) : '—'}</span>
        )}
      </TableCell>
      <TableCell className="min-w-0">
        {row.phase === 'uploading' && <span className="text-caption text-muted-foreground">{row.percent}%</span>}
        {row.phase === 'detecting' && (
          <span className="inline-flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> Inspecting
          </span>
        )}
        {(row.phase === 'ready' || row.phase === 'choose') && (
          <div className="flex flex-col gap-xxs">
            <div className="flex flex-wrap items-center gap-xs">
              <Badge variant={row.phase === 'ready' ? 'success' : row.chosen ? 'info' : 'warning'}>
                {row.phase === 'ready' ? 'Ready' : row.chosen ? 'Format chosen' : 'Needs a format'}
              </Badge>
              <Button size="sm" variant="outline" className="h-7" disabled={!isImportable(row)} onClick={onImport}>
                Import
              </Button>
              {/* Discards the staged job, not just this row: a hidden row's
                  file stayed on the server, came back in the queue, and made
                  the next try at the same file a duplicate. */}
              <Button
                size="icon"
                variant="ghost"
                className="size-7"
                aria-label={`Discard staged file ${row.filename}`}
                title="Discard this staged file. Nothing was imported from it."
                onClick={onRemove}
              >
                <Trash2 className="size-3.5" aria-hidden />
              </Button>
            </div>
            {row.error && <p className="break-words text-caption text-destructive">{row.error}</p>}
          </div>
        )}
        {row.phase === 'starting' && <span className="text-caption text-muted-foreground">Starting…</span>}
        {row.phase === 'started' && <Badge variant="success">Started</Badge>}
        {row.phase === 'duplicate' && row.duplicate && (
          <div className="flex flex-col gap-xxs">
            <span className="break-words text-caption">{row.duplicate.message}</span>
            <div className="flex flex-wrap gap-xs">
              {row.duplicate.scanId != null && (
                <Button size="sm" variant="outline" className="h-7" onClick={() => onViewScan(row.duplicate!.scanId!)}>
                  View scan #{row.duplicate.scanId}
                </Button>
              )}
              <Button size="sm" variant="ghost" className="h-7" onClick={onImportAgain} title="Import this file again anyway">
                Import again
              </Button>
            </div>
          </div>
        )}
        {row.phase === 'error' && (
          <div className="flex flex-col gap-xxs">
            <span className="break-words text-caption text-destructive">{row.error}</span>
            <Button size="icon" variant="ghost" className="size-7" aria-label={`Remove ${row.filename}`} onClick={onRemove}>
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </div>
        )}
      </TableCell>
    </TableRow>
    {previewOpen && d && (
      <TableRow ref={previewRef} className="bg-muted/20">
        <TableCell colSpan={3} className="p-sm">
          <p className="mb-xxs text-caption font-semibold">What the reader saw</p>
          {d.preview.sample.length > 0 ? (
            <ul className="mb-xs flex flex-col gap-xxs font-mono text-caption text-foreground">
              {d.preview.sample.map((line, i) => (
                <li key={i} className="break-words">{line}</li>
              ))}
            </ul>
          ) : (
            <p className="mb-xs text-caption text-muted-foreground">
              No interpreted sample for this kind of file; the raw start of the file is below.
            </p>
          )}
          <pre
            aria-label={`Raw start of ${row.filename}`}
            className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-control bg-muted/40 p-xs font-mono text-caption text-muted-foreground"
          >
            {d.preview.raw || '(empty file)'}
          </pre>
        </TableCell>
      </TableRow>
    )}
    </>
  );
};

export default UploadReviewDialog;
