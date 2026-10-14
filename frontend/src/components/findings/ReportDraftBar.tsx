/**
 * The bar a finding page shows when a draft report's checklist opened it
 * (`/findings/<id>?edit=report-text&report=<report id>`; walkthrough
 * 2026-10-14): which draft, where this finding stands among those still
 * missing report text, Previous / Next through that list, and the way back.
 *
 * The list is the report's own `summary.missing_text`, read with the report
 * page's key (`['getClientReport', projectId, reportId]`); a report-text save
 * invalidates it (`FindingReportTextCard`), so the count follows.  Once this
 * finding has its text it drops out of the list: Next is then the finding
 * that took its place, Previous the one before — the position it last held
 * is remembered for that (a position, not the list).
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';

import { getClientReport } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';

/** The checklist's anchor on the report page. */
export const REPORT_CHECKLIST_ANCHOR = 'report-checklist';

/** A finding's report text, opened from report `reportId`'s checklist. */
export const findingFromReportPath = (findingId: number, reportId: number): string =>
  `/findings/${findingId}?edit=report-text&report=${reportId}`;

/** Back to the report, at its checklist. */
export const reportChecklistPath = (reportId: number): string =>
  `/reports/${reportId}#${REPORT_CHECKLIST_ANCHOR}`;

/** `?report=` as a report id, or null when it is not one. */
export const reportParam = (value: string | null): number | null => {
  if (!value || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return id > 0 ? id : null;
};

interface Props {
  reportId: number;
  findingId: number;
  /** Leaving goes through the page's unsaved-text guard. */
  onNavigate: (to: string) => void;
}

const ReportDraftBar: React.FC<Props> = ({ reportId, findingId, onNavigate }) => {
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: ['getClientReport', projectId, reportId],
    queryFn: ({ signal }) => getClientReport(projectId, reportId, signal),
  });
  const report = query.data;
  const list = report?.summary?.missing_text ?? [];
  const index = list.findIndex((m) => m.id === findingId);
  // Where this finding last stood in the list (adjusted while rendering:
  // the React pattern for "remember something about an earlier render").
  const [anchor, setAnchor] = useState<number | null>(null);
  if (index >= 0 && anchor !== index) setAnchor(index);

  let prev: (typeof list)[number] | undefined;
  let next: (typeof list)[number] | undefined;
  let where: React.ReactNode;
  const total = list.length;
  const still = `${total.toLocaleString()} finding${total === 1 ? '' : 's'} still missing text`;
  if (index >= 0) {
    prev = list[index - 1];
    next = list[index + 1];
    where = `finding ${(index + 1).toLocaleString()} of ${still}`;
  } else if (anchor !== null) {
    // It had its text written here: its neighbours are where it was.
    prev = list[Math.min(anchor, total) - 1];
    next = list[anchor];
    where = total ? `this finding has its report text now; ${still}` : 'every finding in it has its report text now';
  } else {
    next = list[0];
    where = total ? `this finding is not among the ${still}` : 'every finding in it has its report text';
  }

  const link = (to: string, children: React.ReactNode, label: string) => (
    <Link to={to} aria-label={label} className="inline-flex shrink-0 items-center gap-xxs text-info hover:underline"
      onClick={(e) => { e.preventDefault(); onNavigate(to); }}>
      {children}
    </Link>
  );

  const name = report?.status === 'draft' || !report ? `Report draft #${reportId}`
    : `Report #${report.number ?? reportId}`;

  return (
    <nav aria-label="Report draft" data-testid="report-draft-bar"
      className="mb-sm flex min-w-0 flex-wrap items-center gap-x-md gap-y-xxs border-b border-border pb-xs text-caption">
      <span className="min-w-0 break-words">
        <span className="font-medium text-foreground">{name}</span>
        {report?.title && <span className="text-muted-foreground"> · {report.title}</span>}
        {' — '}
        {query.isPending ? (
          <span className="inline-flex items-center gap-xxs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> reading the report…
          </span>
        ) : query.isError && !report ? (
          <span className="text-destructive">{queryErrorText(query.error, 'Could not read the report.')}</span>
        ) : report?.status !== 'draft' ? (
          <span className="text-muted-foreground">issued: it no longer changes</span>
        ) : (
          <span data-testid="report-draft-position">{where}</span>
        )}
      </span>
      {report?.status === 'draft' && prev && link(
        findingFromReportPath(prev.id, reportId),
        <><ChevronLeft className="size-3.5" aria-hidden /> Previous</>,
        `Previous: ${prev.title}`,
      )}
      {report?.status === 'draft' && next && link(
        findingFromReportPath(next.id, reportId),
        <>Next <ChevronRight className="size-3.5" aria-hidden /></>,
        `Next: ${next.title}`,
      )}
      {link(reportChecklistPath(reportId), <><ArrowLeft className="size-3.5" aria-hidden /> Back to the report</>,
        'Back to the report')}
    </nav>
  );
};

export default ReportDraftBar;
