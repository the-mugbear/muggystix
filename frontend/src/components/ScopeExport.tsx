import { useMutation } from '@tanstack/react-query';
import { FolderTree } from 'lucide-react';
import { getScopeExport, type ScopeExportFormat } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import GeneratedTextDialog, { countEntries } from './GeneratedTextDialog';

interface ScopeExportProps {
  open: boolean;
  onClose: () => void;
}

const EXPORT_FORMATS = [
  { value: 'cidrs', label: 'Networks', description: 'One CIDR per line' },
  { value: 'domains', label: 'Domains', description: 'One domain per line; *.example.com includes its subdomains' },
  { value: 'csv', label: 'CSV', description: 'Every entry with its labels, description and site' },
] as const satisfies readonly { value: ScopeExportFormat; label: string; description: string }[];

const NOUN: Record<ScopeExportFormat, [string, string]> = {
  cidrs: ['network', 'networks'],
  domains: ['domain', 'domains'],
  csv: ['entry', 'entries'],
};

const FILE: Record<ScopeExportFormat, string> = {
  cidrs: 'project-scope_cidrs.txt',
  domains: 'project-scope_domains.txt',
  csv: 'project-scope.csv',
};

/** Export what the project's scope declares — its networks and domains, as
 *  a scope file the page's upload reads back.  The hosts found inside the
 *  scope are the Hosts page's export (`scope:` filter); this dialog listed
 *  them until 5.416.0.  The dialog itself — format, generate, copy,
 *  download, and the rule that the text keeps the format it was made in —
 *  is `GeneratedTextDialog`. */
export default function ScopeExport({ open, onClose }: ScopeExportProps) {
  // Asked for by the button, not by opening the dialog: an action, so its
  // answer, busy state and failure are the mutation's.
  const projectId = useProjectId();
  const generate = useMutation({
    mutationFn: (format: ScopeExportFormat) => getScopeExport(projectId, format),
    onError: (err) => console.error('Error exporting the scope:', err),
  });

  return (
    <GeneratedTextDialog
      open={open}
      onClose={onClose}
      icon={<FolderTree className="size-5" aria-hidden />}
      title="Export project scope"
      formats={EXPORT_FORMATS}
      defaultFormat="cidrs"
      formatFieldId="scope-export-format"
      generate={generate}
      generateLabel="Generate list"
      errorFallback="Failed to export the scope"
      textOf={(text) => text}
      heading={({ text, format, formatLabel }) => {
        const entryCount = countEntries(text, format);
        const [one, many] = NOUN[format];
        return <>{entryCount} {entryCount === 1 ? one : many} · {formatLabel}</>;
      }}
      filename={({ format }) => FILE[format]}
      copyLabel="Copy scope export to clipboard"
      downloadLabel="Download scope export as file"
    >
      <p className="text-metadata text-muted-foreground">
        The networks and domains declared below, as a scope file this page’s upload reads back.
      </p>
    </GeneratedTextDialog>
  );
}
