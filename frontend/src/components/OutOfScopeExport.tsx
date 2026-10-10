import { useMutation } from '@tanstack/react-query';
import { ShieldOff } from 'lucide-react';
import { getOutOfScopeHostList } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import GeneratedTextDialog, { countEntries } from './GeneratedTextDialog';

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

/** Export the hosts no scope covers, as text (the dialog is `GeneratedTextDialog`). */
export default function OutOfScopeExport({ open, onClose }: OutOfScopeExportProps) {
  // Asked for by the button, so a mutation: its answer is the list shown, and
  // asking again starts from nothing.
  const projectId = useProjectId();
  const generate = useMutation({
    mutationFn: (format: ExportFormat) => getOutOfScopeHostList(projectId, format),
    onError: (err) => console.error('Error fetching out-of-scope hosts:', err),
  });

  return (
    <GeneratedTextDialog
      open={open}
      onClose={onClose}
      icon={<ShieldOff className="size-5" aria-hidden />}
      title="Out-of-Scope Hosts"
      formats={EXPORT_FORMATS}
      defaultFormat="txt"
      formatFieldId="oos-format"
      generate={generate}
      generateLabel="Generate list"
      errorFallback="Failed to fetch out-of-scope hosts"
      textOf={(text) => text}
      heading={({ text, format, formatLabel }) => {
        const entryCount = countEntries(text, format);
        return <>{entryCount} host{entryCount === 1 ? '' : 's'}{' · '}{formatLabel}</>;
      }}
      filename={({ format }) => `out_of_scope_hosts.${format}`}
      copyLabel="Copy output to clipboard"
      downloadLabel="Download output as file"
    >
      <p className="text-metadata text-muted-foreground">
        Export hosts that have no subnet/scope mapping. These IPs appeared in scan results but do
        not belong to any defined scope.
      </p>
    </GeneratedTextDialog>
  );
}
