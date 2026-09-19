import React, { useEffect, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Loader2, Trash2, Upload } from 'lucide-react';

import { ACCEPTED_EXTENSIONS, ACCEPTED_EXTENSION_LIST, SUPPORTED_FORMATS } from '../../data/uploadFormats';
import { useUploadReview, isImportable, type ReviewRow, type StartedUpload } from '../../hooks/useUploadReview';
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

  const preview = previewKey ? rows.find((r) => r.key === previewKey) : null;

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
                      onChoose={(ft) => review.setChoice(row.key, ft)}
                      onSourceTool={(v) => review.setSourceTool(row.key, v)}
                      onImport={() => void review.importOne(row)}
                      onImportAgain={() => review.importAgain(row.key)}
                      onRemove={() => review.remove(row.key)}
                      onViewScan={onViewScan}
                    />
                  ))}
                </TableBody>
              </Table>
              {preview?.detection && (
                <div className="border-t border-border p-sm">
                  <p className="mb-xxs text-caption font-semibold">
                    {preview.filename} — what the reader saw
                  </p>
                  {preview.detection.preview.sample.length > 0 && (
                    <ul className="mb-xs flex flex-col gap-xxs font-mono text-caption text-foreground">
                      {preview.detection.preview.sample.map((line, i) => (
                        <li key={i} className="break-words">{line}</li>
                      ))}
                    </ul>
                  )}
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-control bg-muted/40 p-xs font-mono text-caption text-muted-foreground">
                    {preview.detection.preview.raw || '(empty file)'}
                  </pre>
                </div>
              )}
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
  onChoose: (fileType: string | null) => void;
  onSourceTool: (value: string) => void;
  onImport: () => void;
  onImportAgain: () => void;
  onRemove: () => void;
  onViewScan: (scanId: number) => void;
}> = ({ row, previewOpen, onTogglePreview, onChoose, onSourceTool, onImport, onImportAgain, onRemove, onViewScan }) => {
  const d = row.detection;
  const primary = d?.candidates[0];
  return (
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
        ) : d ? (
          <div className="flex min-w-0 flex-col gap-xxs">
            {!d.needs_choice && primary ? (
              <p className="text-metadata">
                <span className="font-medium">{primary.label}</span>
                <span className="text-caption text-muted-foreground"> · recognised by structure</span>
              </p>
            ) : (
              <p className="break-words text-caption text-warning">{d.reason ?? 'Choose a format.'}</p>
            )}
            {row.phase !== 'started' && row.phase !== 'starting' && (
              <select
                aria-label={`Format for ${row.filename}`}
                className="flex h-8 w-full rounded-control border border-input bg-background px-xs text-caption"
                value={row.chosen ?? ''}
                onChange={(e) => onChoose(e.target.value || null)}
              >
                <option value="">{d.needs_choice ? 'Select a format…' : 'As detected'}</option>
                {d.candidates.length > 0 && (
                  <optgroup label="Detected">
                    {d.candidates.map((c) => (
                      <option key={c.file_type} value={c.file_type}>
                        {c.label} ({c.basis === 'structure' ? 'by structure' : 'by filename'})
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="All formats">
                  {d.formats.map((f) => (
                    <option key={f.file_type} value={f.file_type}>{f.label}</option>
                  ))}
                </optgroup>
              </select>
            )}
            {row.phase !== 'started' && row.phase !== 'starting' && (
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
          <span className="text-caption text-muted-foreground">Choose a format.</span>
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
              <Button size="icon" variant="ghost" className="size-7" aria-label={`Remove ${row.filename} from this review`} onClick={onRemove}>
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
  );
};

export default UploadReviewDialog;
