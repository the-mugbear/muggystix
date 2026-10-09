import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { copyToClipboard as copyText } from '../utils/clipboard';
import { downloadTextFile } from '../utils/download';
import { queryErrorText } from '../lib/query';
import { Copy, Download, FolderTree, Loader2 } from 'lucide-react';
import { getScopeHostList } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
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

interface ScopeExportProps {
  open: boolean;
  onClose: () => void;
  scopeId: number;
  scopeName: string;
}

const EXPORT_FORMATS = [
  { value: 'txt', label: 'IP List', description: 'One IP address per line' },
  { value: 'csv', label: 'CSV', description: 'IP, hostname, and state columns' },
  { value: 'json', label: 'JSON', description: 'Structured host data' },
] as const;

type ExportFormat = (typeof EXPORT_FORMATS)[number]['value'];

export default function ScopeExport({ open, onClose, scopeId, scopeName }: ScopeExportProps) {
  const [selectedFormat, setSelectedFormat] = useState<ExportFormat>('txt');
  const [copied, setCopied] = useState(false);

  // Asked for by the button, not by opening the dialog: an action, so its
  // answer, busy state and failure are the mutation's.
  const projectId = useProjectId();
  const generate = useMutation({
    mutationFn: (format: ExportFormat) => getScopeHostList(projectId, scopeId, format),
    onError: (err) => console.error('Error fetching scope hosts:', err),
  });
  const output = generate.data ?? '';
  // The format the text on screen was MADE in — the request's own argument,
  // not the picker, which the reader may have moved since.  The count, the
  // heading and the file's extension follow it.
  const outputFormat: ExportFormat = generate.variables ?? selectedFormat;
  const outputFormatLabel = EXPORT_FORMATS.find((f) => f.value === outputFormat)?.label ?? outputFormat;
  const loading = generate.isPending;
  const error = queryErrorText(generate.error, 'Failed to fetch scope hosts');
  const generateOutput = () => generate.mutate(selectedFormat);

  const { reset } = generate;
  React.useEffect(() => {
    if (open) {
      reset();
      setCopied(false);
    }
  }, [open, reset]);

  const copyToClipboard = async () => {
    // copyText (utils/clipboard) adds an execCommand fallback for non-secure
    // (http://) contexts where navigator.clipboard is unavailable.
    if (await copyText(output)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const downloadOutput = () => {
    const safeName = scopeName.replace(/\s+/g, '_').replace(/[/\\]/g, '-').slice(0, 40);
    downloadTextFile(`${safeName}_hosts.${outputFormat}`, output);
  };

  const entryCount = output
    ? outputFormat === 'json'
      ? (() => {
          try {
            return JSON.parse(output).length;
          } catch {
            return 0;
          }
        })()
      : output.split('\n').filter((line) => line.trim()).length - (outputFormat === 'csv' ? 1 : 0)
    : 0;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <FolderTree className="size-5" aria-hidden />
            Export scope: {scopeName}
          </DialogTitle>
        </DialogHeader>
        <p className="text-metadata text-muted-foreground">
          Export all hosts mapped to this scope and its subnets.
        </p>

        <div className="space-y-xxs">
          <Label htmlFor="scope-export-format">Output format</Label>
          <Select
            value={selectedFormat}
            onValueChange={(v) => setSelectedFormat(v as ExportFormat)}
          >
            <SelectTrigger id="scope-export-format">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EXPORT_FORMATS.map((format) => (
                <SelectItem key={format.value} value={format.value}>
                  {format.label} — {format.description}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button onClick={generateOutput} disabled={loading} className="w-full">
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Generating…
            </>
          ) : (
            'Generate list'
          )}
        </Button>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {output && (
          <div className="space-y-xs">
            <div className="flex items-center justify-between">
              <h3 className="text-subheading">
                {entryCount} host{entryCount === 1 ? '' : 's'} · {outputFormatLabel}
              </h3>
              <div className="flex items-center gap-xxs">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={copyToClipboard}
                      aria-label="Copy scope export to clipboard"
                    >
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
                      onClick={downloadOutput}
                      aria-label="Download scope export as file"
                    >
                      <Download className="size-4" aria-hidden />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Download as file</TooltipContent>
                </Tooltip>
              </div>
            </div>

            <pre className="max-h-[24rem] overflow-auto rounded-control border border-border bg-muted/30 p-sm font-mono text-caption text-foreground">
              {output}
            </pre>
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
