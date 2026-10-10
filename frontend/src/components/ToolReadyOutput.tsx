import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useProjectId } from '../hooks/useProjectId';
import { Code } from 'lucide-react';
import { getToolReadyOutput, ToolReadyResult } from '../services/api';
import GeneratedTextDialog from './GeneratedTextDialog';
import { Alert, AlertDescription } from './ui/alert';
import { Label } from './ui/label';
import { Switch } from './ui/switch';

interface ToolReadyOutputProps {
  open: boolean;
  onClose: () => void;
  filters: Record<string, string | boolean | number | string[] | undefined>;
  /** Hosts in the view being exported — stated in the dialog when known. */
  totalHosts?: number;
  /** Rows checked in the table; they do NOT narrow this export, and the dialog says so. */
  selectedCount?: number;
}

const TOOL_FORMATS = [
  { value: 'ip-list', label: 'IP List', description: 'Simple list of IP addresses (one per line)' },
  { value: 'nmap', label: 'Nmap', description: 'Space-separated targets for Nmap' },
  { value: 'metasploit', label: 'Metasploit', description: 'RHOSTS format for Metasploit' },
  { value: 'masscan', label: 'Masscan', description: 'Comma-separated targets for Masscan' },
  { value: 'nuclei', label: 'Nuclei', description: 'URLs by bound name for web ports (IP fallback), IPs for others' },
  { value: 'host-port', label: 'Host:Port', description: 'IP:PORT format for each open port' },
  { value: 'json', label: 'JSON', description: 'Detailed JSON with host information (includes bound names)' },
  { value: 'names', label: 'Names', description: 'In-scope names bound to the selected hosts, one per line' },
  { value: 'web-targets', label: 'Web targets', description: 'URLs by name for web ports, IP fallback' },
];

// Formats whose output uses the names currently bound to each address, so
// the in-scope / all toggle applies.  Mirrors _NAME_AWARE_FORMATS server-side.
const NAME_AWARE_FORMATS = new Set(['nuclei', 'json', 'names', 'web-targets']);

// An IP export of a large project is megabytes; rendering all of it in a
// <pre> stalls the dialog. Copy and Download always use the full output.
const PREVIEW_CHARS = 100_000;

export default function ToolReadyOutput({
  open, onClose, filters, totalHosts, selectedCount,
}: ToolReadyOutputProps) {
  const projectId = useProjectId();
  // The two switches are kept between openings, like the format.
  const [includePorts, setIncludePorts] = useState(false);
  // Default in-scope: a declared domain must cover a name before it becomes
  // a target — the same rule the agent's scope guardrail applies.
  const [inScopeNamesOnly, setInScopeNamesOnly] = useState(true);
  // Asked for by the button, so a mutation: its answer is the output shown,
  // and asking again starts from nothing.  The dialog itself — format,
  // generate, copy, download — is `GeneratedTextDialog`.
  const generate = useMutation({
    mutationFn: (format: string) => getToolReadyOutput(projectId, format, {
      ...filters,
      includePorts,
      ...(NAME_AWARE_FORMATS.has(format)
        ? { namesScope: (inScopeNamesOnly ? 'in_scope' : 'all') as 'in_scope' | 'all' }
        : {}),
    }),
    onError: (err) => console.error('Error generating tool output:', err),
  });

  return (
    <GeneratedTextDialog<string, ToolReadyResult>
      open={open}
      onClose={onClose}
      icon={<Code className="size-5" aria-hidden />}
      title="Export targets"
      formats={TOOL_FORMATS}
      defaultFormat="ip-list"
      formatFieldId="tro-format"
      generate={generate}
      generateLabel="Generate output"
      errorFallback="Failed to generate output"
      textOf={(result) => result.output ?? ''}
      heading={({ formatLabel }) => <>Generated Output ({formatLabel})</>}
      filename={({ format, formatLabel }) => `${formatLabel.toLowerCase()}-targets.${format === 'json' ? 'json' : 'txt'}`}
      copyLabel="Copy output to clipboard"
      downloadLabel="Download output as file"
      previewChars={PREVIEW_CHARS}
      options={(selectedFormat) => (
        <>
          <div className="flex items-center gap-xs">
            <Switch
              id="tro-include-ports"
              checked={includePorts}
              onCheckedChange={setIncludePorts}
            />
            <Label htmlFor="tro-include-ports">Include detailed port information</Label>
          </div>

          {NAME_AWARE_FORMATS.has(selectedFormat) && (
            <div className="flex items-center gap-xs">
              <Switch
                id="tro-in-scope-names"
                checked={inScopeNamesOnly}
                onCheckedChange={setInScopeNamesOnly}
              />
              <Label htmlFor="tro-in-scope-names">
                {inScopeNamesOnly ? 'In-scope names only' : 'All bound names'}
              </Label>
              <span className="min-w-0 truncate text-caption text-muted-foreground">
                {inScopeNamesOnly
                  ? 'Only names a declared domain covers become targets.'
                  : 'Every name currently bound to the address, in scope or not.'}
              </span>
            </div>
          )}
        </>
      )}
      notice={(result) => result.limit != null && (
        <Alert variant="warning">
          <AlertDescription>
            Built from the first {(result.returned ?? result.limit).toLocaleString()} of{' '}
            {result.total != null ? result.total.toLocaleString() : 'more'} matching hosts. This
            format loads port detail per host and stops at {result.limit.toLocaleString()} — narrow
            the filter, or use IP List, Nmap, Metasploit or Masscan, which export every host.
          </AlertDescription>
        </Alert>
      )}
      footnote={({ text, result }, cut) => (
        <>
          {result.returned != null
            ? `Built from ${result.returned.toLocaleString()} host${result.returned === 1 ? '' : 's'}`
            : `${text.split('\n').filter((line) => line.trim()).length} entries generated`}
          {cut && ' · the preview shows the start; Copy and Download include everything'}
        </>
      )}
    >
      {/* Name the population: this exports the VIEW, never the checked rows. */}
      <p className="text-metadata text-muted-foreground break-words">
        Exports the <strong className="text-foreground">current view</strong>
        {totalHosts != null && (
          <> — all {totalHosts.toLocaleString()} host{totalHosts === 1 ? '' : 's'} matching the applied filters, on every page</>
        )}
        , formatted for your own tools.
        {selectedCount ? (
          <> The {selectedCount.toLocaleString()} row{selectedCount === 1 ? '' : 's'} you have checked
            {selectedCount === 1 ? ' does' : ' do'} not narrow it — use <em>Copy IPs</em> in the selection bar for just those.</>
        ) : null}
        {' '}A target list is a hand-off; it authorises no scan.
      </p>
    </GeneratedTextDialog>
  );
}
