/**
 * "N proposals for these defaults are waiting for review" on the Report
 * defaults page (5.430.0).
 *
 * Text an agent proposes for the executive summary, the system description
 * or another written default is not in the defaults until a person accepts
 * it — so the page whose text it would replace says that it is waiting, and
 * the link opens exactly those proposals, where each is shown beside the
 * current text.
 */
import React from 'react';
import { Link } from 'react-router-dom';

import { usePendingProposalCount } from '../../hooks/usePendingProposalCount';

export const PROPOSED_REPORT_TEXT_PATH = '/proposals?kind=report_detail&scope=all';

const ProposedReportTextLine: React.FC = () => {
  // Nothing waiting, or not known: the page says nothing.
  const waiting = usePendingProposalCount('report_detail');
  if (waiting <= 0) return null;
  return (
    <p className="mt-xs text-metadata text-muted-foreground" data-testid="proposed-report-text-line">
      <Link to={PROPOSED_REPORT_TEXT_PATH} className="font-medium text-primary underline-offset-4 hover:underline">
        {waiting.toLocaleString()} proposal{waiting === 1 ? '' : 's'} for these defaults {waiting === 1 ? 'is' : 'are'} waiting for review
      </Link>
      {' '}— shown under {waiting === 1 ? 'its detail' : 'their details'} below, and not in the text until someone
      accepts {waiting === 1 ? 'it' : 'them'}.
    </p>
  );
};

export default ProposedReportTextLine;
