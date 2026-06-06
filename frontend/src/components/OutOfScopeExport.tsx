import React, { useState } from 'react';
import { Copy, Download, Loader2, ShieldOff } from 'lucide-react';
import { getOutOfScopeHostList } from '../services/api';
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

interface OutOfScopeExportProps {
  open: boolean;
  onClose: () => void;
}

const EXPORT_FORMATS = [
  { value: 'txt', label: 'IP List', description: 'One IP address per line' },
  { value: 'csv', label: 'CSV', description: 'IP, hostname, and state columns' },
  { value: 'json', label: 'JSON', description: 'Structured host data' },
] as const;

type ExportFormat = (typeof EXPORT_FORMATS)[number]['value'];

export default function OutOfScopeExport({ open, onClose }: OutOfScopeExportProps) {
  const [selectedFormat, setSelectedFormat] = useState<ExportFormat>('txt');
  const [output, setOutput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  React.useEffect(() => {
    if (open) {
      setOutput('');
      setError(null);
      setCopied(false);
    }
  }, [open]);

  const generateOutput = async () => {
    setLoading(true);
    setError(null);
    setOutput('');
    try {
      const result = await getOutOfScopeHostList(selectedFormat);
      setOutput(result);
    } catch (err) {
      console.error('Error fetching out-of-scope hosts:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch out-of-scope hosts');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(output);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  const downloadOutput = () => {
    const blob = new Blob([output], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `out_of_scope_hosts.${selectedFormat}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const entryCount = output
    ? selectedFormat === 'json'
      ? (() => {
          try {
            return JSON.parse(output).length;
          } catch {
            return 0;
          }
        })()
      : output.split('\n').filter((line) => line.trim()).length - (selectedFormat === 'csv' ? 1 : 0)
    : 0;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <ShieldOff className="size-5" aria-hidden />
            Out-of-Scope Hosts
          </DialogTitle>
        </DialogHeader>
        <p className="text-metadata text-muted-foreground">
          Export hosts that have no subnet/scope mapping. These IPs appeared in scan results but do
          not belong to any defined scope.
        </p>

        <div className="space-y-xxs">
          <Label htmlFor="oos-format">Output format</Label>
          <Select
            value={selectedFormat}
            onValueChange={(v) => setSelectedFormat(v as ExportFormat)}
          >
            <SelectTrigger id="oos-format">
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
                {entryCount} host{entryCount === 1 ? '' : 's'}
              </h3>
              <div className="flex items-center gap-xxs">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={copyToClipboard}
                      aria-label="Copy output to clipboard"
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
                      aria-label="Download output as file"
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
