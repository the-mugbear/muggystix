import { useMutation } from '@tanstack/react-query';
import { FolderTree } from 'lucide-react';
import { getScopeHostList } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import GeneratedTextDialog, { countEntries } from './GeneratedTextDialog';

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
  // The web targets an agent gets as web-targets.txt, from the same builder:
  // a port identified as HTTP, https where it is TLS-wrapped or named so.
  { value: 'web', label: 'Web URLs', description: 'One http/https URL per line, from identified web ports' },
] as const;

type ExportFormat = (typeof EXPORT_FORMATS)[number]['value'];

/** Export a scope's hosts as text.  The dialog itself — format, generate,
 *  copy, download, and the rule that the text keeps the format it was made
 *  in — is `GeneratedTextDialog`. */
export default function ScopeExport({ open, onClose, scopeId, scopeName }: ScopeExportProps) {
  // Asked for by the button, not by opening the dialog: an action, so its
  // answer, busy state and failure are the mutation's.
  const projectId = useProjectId();
  const generate = useMutation({
    mutationFn: (format: ExportFormat) => getScopeHostList(projectId, scopeId, format),
    onError: (err) => console.error('Error fetching scope hosts:', err),
  });

  return (
    <GeneratedTextDialog
      open={open}
      onClose={onClose}
      icon={<FolderTree className="size-5" aria-hidden />}
      title={<>Export scope: {scopeName}</>}
      formats={EXPORT_FORMATS}
      defaultFormat="txt"
      formatFieldId="scope-export-format"
      generate={generate}
      generateLabel="Generate list"
      errorFallback="Failed to fetch scope hosts"
      textOf={(text) => text}
      heading={({ text, format, formatLabel }) => {
        const entryCount = countEntries(text, format);
        return <>{entryCount} {format === 'web' ? 'URL' : 'host'}{entryCount === 1 ? '' : 's'} · {formatLabel}</>;
      }}
      filename={({ format }) => {
        const safeName = scopeName.replace(/\s+/g, '_').replace(/[/\\]/g, '-').slice(0, 40);
        return format === 'web' ? `${safeName}_web-targets.txt` : `${safeName}_hosts.${format}`;
      }}
      copyLabel="Copy scope export to clipboard"
      downloadLabel="Download scope export as file"
    >
      <p className="text-metadata text-muted-foreground">
        Export all hosts mapped to this scope and its subnets.
      </p>
    </GeneratedTextDialog>
  );
}
