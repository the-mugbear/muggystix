/**
 * GeneratedTextDialog — the one export dialog (plan B2a): pick a format, ask
 * for the text, read it, copy it, save it.  `ScopeExport`, `OutOfScopeExport`
 * and `ToolReadyOutput` were three copies of it; each is now what differs —
 * its request (a mutation of its own, so the API call stays in a
 * `mutationFn`), its formats, its words and its file name.
 *
 * Two rules live here, once:
 *
 *   - **The text on screen belongs to the request that made it.**  It is
 *     counted, headed and saved by the mutation's own `variables` — the
 *     format it was ASKED in — never by the picker, which the reader may have
 *     moved since (`heading` and `filename` are given that format, not the
 *     picker's).
 *   - **An opening starts with no output and no failure, and with the format
 *     chosen last time** — kept on purpose, so this dialog is NOT keyed by its
 *     opening (`useOpeningKey`): the effect below resets only what was
 *     generated.
 */
import React, { useEffect, useState } from 'react';
import type { UseMutationResult } from '@tanstack/react-query';
import { Copy, Download, Loader2 } from 'lucide-react';

import { queryErrorText } from '../lib/query';
import { copyToClipboard as copyText } from '../utils/clipboard';
import { downloadTextFile } from '../utils/download';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

export interface TextFormat<F extends string = string> {
  value: F;
  label: string;
  description: string;
}

/** What the dialog is told about the text on screen. */
export interface GeneratedText<F extends string, R> {
  /** The whole text (the preview may show less). */
  text: string;
  /** The format it was MADE in — the request's, not the picker's. */
  format: F;
  /** That format's label (the format itself when it is not on the list). */
  formatLabel: string;
  /** The server's whole answer. */
  result: R;
}

export interface GeneratedTextDialogProps<F extends string, R> {
  open: boolean;
  onClose: () => void;
  /** The title's icon and words. */
  icon: React.ReactNode;
  title: React.ReactNode;
  /** The paragraph under the title: what is exported. */
  children: React.ReactNode;
  formats: ReadonlyArray<TextFormat<F>>;
  /** The format the first opening starts on. */
  defaultFormat: F;
  /** The picker's element id (its label points at it). */
  formatFieldId: string;
  /** The caller's own mutation; its variable is the format asked for. */
  generate: Pick<UseMutationResult<R, Error, F>, 'data' | 'variables' | 'isPending' | 'error' | 'mutate' | 'reset'>;
  generateLabel: string;
  /** Said when the request failed and the server gave no reason. */
  errorFallback: string;
  /** The text in the server's answer. */
  textOf: (result: R) => string;
  heading: (made: GeneratedText<F, R>) => React.ReactNode;
  filename: (made: GeneratedText<F, R>) => string;
  copyLabel: string;
  downloadLabel: string;
  /** More to choose before asking, under the picker (given the picker's format). */
  options?: (selected: F) => React.ReactNode;
  /** Something the answer says about itself, above the text — shown whether
   *  or not there is any text. */
  notice?: (result: R) => React.ReactNode;
  /** Show only the start of a text longer than this; Copy and Download take all of it. */
  previewChars?: number;
  /** A line under the text (`cut`: the preview shows only its start). */
  footnote?: (made: GeneratedText<F, R>, cut: boolean) => React.ReactNode;
}

/** How many entries a host list holds, read as the format it was made in:
 *  a JSON array's length, a CSV's lines less its header, else the lines. */
export function countEntries(text: string, format: string): number {
  if (!text) return 0;
  if (format === 'json') {
    try {
      return JSON.parse(text).length;
    } catch {
      return 0;
    }
  }
  return text.split('\n').filter((line) => line.trim()).length - (format === 'csv' ? 1 : 0);
}

export default function GeneratedTextDialog<F extends string, R>({
  open, onClose, icon, title, children, formats, defaultFormat, formatFieldId, generate, generateLabel,
  errorFallback, textOf, heading, filename, copyLabel, downloadLabel, options, notice, previewChars, footnote,
}: GeneratedTextDialogProps<F, R>) {
  // Kept between openings (see above).
  const [selectedFormat, setSelectedFormat] = useState<F>(defaultFormat);
  const [copied, setCopied] = useState(false);

  const result = generate.data;
  const text = result === undefined ? '' : textOf(result);
  // The format the text on screen was MADE in — the request's own argument.
  const format: F = generate.variables ?? selectedFormat;
  const made: GeneratedText<F, R> | null = result !== undefined && text
    ? { text, format, formatLabel: formats.find((f) => f.value === format)?.label ?? format, result }
    : null;
  const loading = generate.isPending;
  const error = queryErrorText(generate.error, errorFallback);
  const preview = previewChars != null && text.length > previewChars ? text.slice(0, previewChars) : text;

  // Each opening starts empty: only what was generated is reset.
  const { reset } = generate;
  useEffect(() => {
    if (open) {
      reset();
      setCopied(false);
    }
  }, [open, reset]);

  const copy = async () => {
    // copyText (utils/clipboard) adds an execCommand fallback for non-secure
    // (http://) contexts where navigator.clipboard is unavailable.
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            {icon}
            {title}
          </DialogTitle>
        </DialogHeader>
        {children}

        <div className="space-y-xxs">
          <Label htmlFor={formatFieldId}>Output format</Label>
          <Select value={selectedFormat} onValueChange={(v) => setSelectedFormat(v as F)}>
            <SelectTrigger id={formatFieldId}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {formats.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.label} — {f.description}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {options?.(selectedFormat)}

        <Button onClick={() => generate.mutate(selectedFormat)} disabled={loading} className="w-full">
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Generating…
            </>
          ) : (
            generateLabel
          )}
        </Button>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {result !== undefined && notice?.(result)}

        {made && (
          <div className="space-y-xs">
            <div className="flex items-center justify-between">
              <h3 className="text-subheading">{heading(made)}</h3>
              <div className="flex items-center gap-xxs">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="icon" onClick={copy} aria-label={copyLabel}>
                      <Copy className="size-4" aria-hidden />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{copied ? 'Copied!' : 'Copy to clipboard'}</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => downloadTextFile(filename(made), made.text)}
                      aria-label={downloadLabel}
                    >
                      <Download className="size-4" aria-hidden />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Download as file</TooltipContent>
                </Tooltip>
              </div>
            </div>

            <pre className="max-h-[24rem] overflow-auto rounded-control border border-border bg-muted/30 p-sm font-mono text-caption text-foreground">
              {preview}
            </pre>

            {footnote && (
              <p className="text-caption text-muted-foreground">{footnote(made, preview.length < text.length)}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
