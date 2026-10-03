/**
 * The finding page's Proposals summary (v5.316.0; a summary since 5.334.0).
 *
 * The walkthrough of 2026-10-02: report-text drafts were listed here, about
 * 800 px above the Report text they change, and endpoint changes under the
 * hosts table rather than on their rows — the reader held three parts of the
 * page in mind to decide one thing.  Each proposal is now reviewed where it
 * applies (report text: `FieldDraftsReview` inside Report text; an endpoint
 * change: on its row in `FindingEndpoints`), and this section, at the top of
 * the page, says what is waiting and goes there.  Only a proposal with no
 * home on the page (an observation to promote or dismiss, an endpoint no
 * longer on the finding) is decided here.
 *
 * Hidden when nothing is pending.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowDownRight } from 'lucide-react';

import type { Proposal } from '../../services/api';
import type { FindingProposals } from '../../hooks/useFindingProposals';
import PostureSection, { SectionCount } from '../posture/PostureSection';
import ProposalItem, { describeProposal, FIELD_LABELS } from './ProposalItem';
import { Button } from '../ui/button';

const FIELD_ORDER = ['description', 'impact', 'recommendation', 'steps_to_reproduce', 'references', 'cvss_vector'];

interface Props {
  proposals: FindingProposals;
  /** The finding's endpoint row ids — an endpoint proposal is reviewed on its row. */
  endpointIds: Set<number>;
  canDecide: boolean;
  onDecided: (updated: Proposal) => void;
}

const goTo = (id: string) => {
  const el = document.getElementById(id);
  el?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  el?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"], [data-proposal-action="accept"]')?.focus?.({ preventScroll: true });
};

const FindingProposalsPanel: React.FC<Props> = ({ proposals, endpointIds, canDecide, onDecided }) => {
  const { items, error, textByField, byEndpoint } = proposals;

  if (error) {
    return (
      <PostureSection className="mb-md" title={<span>Proposals</span>}>
        <p className="text-caption text-destructive">{error}</p>
      </PostureSection>
    );
  }
  if (!items || items.length === 0) return null;

  const fields = [...textByField.keys()].sort((a, b) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
  const onRows = [...byEndpoint.entries()].filter(([id]) => endpointIds.has(id));
  const elsewhere = items.filter((pr) => !(pr.kind === 'finding_text' && pr.field)
    && !(pr.kind === 'endpoint_status' && pr.finding_host_id != null && endpointIds.has(pr.finding_host_id)));

  return (
    <div id="proposals" className="mb-md scroll-mt-24">
      <PostureSection
        title={<><span>Proposals to review</span><SectionCount>{items.length}</SectionCount></>}
        description="Changes an agent or an AI draft proposed for this finding. Nothing has changed yet: each waits where it applies — report text beside the section's current text, endpoint changes on their row."
      >
        <ul className="min-w-0 divide-y divide-border">
          {fields.map((field) => {
            const n = textByField.get(field)?.length ?? 0;
            return (
              <li key={field} className="flex min-w-0 flex-wrap items-center justify-between gap-sm py-xs">
                <span className="min-w-0 text-body">
                  <span className="font-medium">{FIELD_LABELS[field] ?? field}</span>
                  <span className="text-muted-foreground"> · {n === 1 ? '1 draft' : `${n} drafts`}</span>
                </span>
                <Button variant="ghost" size="sm" onClick={() => goTo(`review-${field}`)}>
                  <ArrowDownRight className="size-4" aria-hidden /> Review in Report text
                </Button>
              </li>
            );
          })}
          {onRows.map(([fhId, prs]) => (
            prs.map((pr) => (
              <li key={pr.id} className="flex min-w-0 flex-wrap items-center justify-between gap-sm py-xs">
                <span className="min-w-0 truncate text-body" title={describeProposal(pr)}>
                  {describeProposal(pr).replace(/ on “.*”$/, '')}
                </span>
                <Button asChild variant="ghost" size="sm">
                  <Link
                    to={`?endpoint=${fhId}#endpoints`}
                    replace
                    onClick={() => document.querySelector(`[data-endpoint-row="${fhId}"]`)?.scrollIntoView?.({ block: 'center' })}
                  >
                    <ArrowDownRight className="size-4" aria-hidden /> Review on its row
                  </Link>
                </Button>
              </li>
            ))
          ))}
        </ul>
        {elsewhere.length > 0 && (
          <div className="mt-sm min-w-0">
            <h3 className="text-caption font-semibold text-muted-foreground">Decide here</h3>
            {elsewhere.map((pr) => (
              <ProposalItem key={pr.id} proposal={pr} canDecide={canDecide} onDecided={onDecided} showTarget linkTarget={false} />
            ))}
          </div>
        )}
      </PostureSection>
    </div>
  );
};

export default FindingProposalsPanel;
