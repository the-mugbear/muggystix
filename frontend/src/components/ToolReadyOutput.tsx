import React, { useState } from 'react';
import { Code, Copy, Download, Loader2 } from 'lucide-react';
import { getToolReadyOutput } from '../services/api';
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
import { Switch } from './ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface ToolReadyOutputProps {
  open: boolean;
  onClose: () => void;
  filters: Record<string, string | boolean | number | undefined>;
}

const TOOL_FORMATS = [
  { value: 'ip-list', label: 'IP List', description: 'Simple list of IP addresses (one per line)' },
  { value: 'nmap', label: 'Nmap', description: 'Space-separated targets for Nmap' },
  { value: 'metasploit', label: 'Metasploit', description: 'RHOSTS format for Metasploit' },
  { value: 'masscan', label: 'Masscan', description: 'Comma-separated targets for Masscan' },
  { value: 'nuclei', label: 'Nuclei', description: 'URLs for web services, IPs for others' },
  { value: 'host-port', label: 'Host:Port', description: 'IP:PORT format for each open port' },
  { value: 'json', label: 'JSON', description: 'Detailed JSON with host information' },
];

export default function ToolReadyOutput({ open, onClose, filters }: ToolReadyOutputProps) {
  const [selectedFormat, setSelectedFormat] = useState('ip-list');
  const [includePorts, setIncludePorts] = useState(false);
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
      const apiFilters = { ...filters, includePorts };
      const result = await getToolReadyOutput(selectedFormat, apiFilters);
      setOutput(result);
    } catch (err) {
      console.error('Error generating tool output:', err);
      setError(err instanceof Error ? err.message : 'Failed to generate output');
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
    const selectedFormatInfo = TOOL_FORMATS.find((f) => f.value === selectedFormat);
    const extension = selectedFormat === 'json' ? 'json' : 'txt';
    const filename = `${selectedFormatInfo?.label.toLowerCase() || selectedFormat}-targets.${extension}`;
    const blob = new Blob([output], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const selectedFormatInfo = TOOL_FORMATS.find((f) => f.value === selectedFormat);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <Code className="size-5" aria-hidden />
            Tool-Ready Output Generator
          </DialogTitle>
        </DialogHeader>
        <p className="text-metadata text-muted-foreground">
          Generate tool-ready output from filtered hosts for penetration testing tools.
        </p>

        <div className="space-y-xxs">
          <Label htmlFor="tro-format">Output format</Label>
          <Select value={selectedFormat} onValueChange={setSelectedFormat}>
            <SelectTrigger id="tro-format">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TOOL_FORMATS.map((format) => (
                <SelectItem key={format.value} value={format.value}>
                  {format.label} — {format.description}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-xs">
          <Switch
            id="tro-include-ports"
            checked={includePorts}
            onCheckedChange={setIncludePorts}
          />
          <Label htmlFor="tro-include-ports">Include detailed port information</Label>
        </div>

        <Button onClick={generateOutput} disabled={loading} className="w-full">
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Generating…
            </>
          ) : (
            'Generate output'
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
                Generated Output ({selectedFormatInfo?.label})
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

            <p className="text-caption text-muted-foreground">
              {output.split('\n').filter((line) => line.trim()).length} entries generated
            </p>
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
