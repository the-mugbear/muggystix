/**
 * What the Reports list and a report's own page both show of a report: its
 * kind, whether its files are still rendering, and the files themselves.
 * Moved out of the Reports PAGE, which the report page imported from
 * (review 2026-10-14, R12).
 */
import React from 'react';
import { Loader2 } from 'lucide-react';

import { downloadClientReportFile, type ClientReport } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import DownloadButton from '../DownloadButton';

/** How often a page showing an issued report whose files are still rendering
 *  reads it again — the report's page and the list alike. */
export const RENDER_POLL_MS = 3000;
export const isRendering = (r: ClientReport | undefined): boolean =>
  r != null && r.status !== 'draft' && r.render_status === 'pending';

export const reportKindLabel = (r: Pick<ClientReport, 'kind' | 'baseline' | 'revision_of'>): string => {
  const parts = [r.kind === 'addendum' ? `Addendum to #${r.baseline?.number ?? '?'}` : 'Full report'];
  if (r.revision_of) parts.push(`revises #${r.revision_of.number ?? '?'}`);
  return parts.join(' · ');
};

/** An issued report's files, each its own download (`DownloadButton`). */
export const FileButtons: React.FC<{ report: ClientReport }> = ({ report }) => {
  const projectId = useProjectId();
  if (report.render_status === 'pending') {
    return (
      <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden /> Rendering files…
      </span>
    );
  }
  if (report.render_status === 'failed') {
    return <span className="text-caption text-destructive">Rendering failed</span>;
  }
  if (report.files.length === 0) return <span className="text-caption text-muted-foreground">No files</span>;
  return (
    <span className="inline-flex flex-wrap gap-xxs">
      {report.files.map((f) => (
        <DownloadButton key={f.format} compact variant="outline" size="sm" className="h-7 px-xs text-caption"
          label={f.format.toUpperCase()} name={`Download ${f.filename}`} filename={f.filename}
          errorText="Could not download the file."
          saveFn={() => downloadClientReportFile(projectId, report.id, f)} />
      ))}
    </span>
  );
};
