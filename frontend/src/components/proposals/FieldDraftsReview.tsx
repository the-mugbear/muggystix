/**
 * The drafts waiting for one report-text field, reviewed where the field is
 * (5.334.0).  Before, they sat in a Proposals section a screen above Report
 * text, each with its own collapsed copy of the current text.  Here the field
 * shows its current text once, beside the selected draft; several drafts are
 * lettered A, B… (two drafts from one session had identical source lines and
 * could only be told apart by reading them).  Accepting one supersedes the
 * others — the server's rule — so the list re-reads after any decision.
 */
import React, { useState } from 'react';

import type { Proposal } from '../../services/api';
import { type OnProposalDecided, useProposalDecision } from '../../hooks/useProposalDecision';
import type { EvidenceResolver } from '../../utils/reportImages';
import { cn } from '../../utils/cn';
import { scrollBelowChrome } from '../../utils/uiStyles';
import { ProposalDecisionControls, ProposalReasons, ProposalSource, staleBase } from './ProposalItem';
import TextComparison from './TextComparison';

export const draftLetter = (index: number): string =>
  index < 26 ? String.fromCharCode(65 + index) : `#${index + 1}`;

const modelOf = (d: Proposal) => d.agent_model ?? (d.source === 'llm_draft' ? 'AI draft' : 'agent');

/** What tells a draft's tab apart: its model when no other draft shares it,
 *  else its opening words (browser pass 5.334.1 — two drafts from one model
 *  both read "Draft A · claude-opus-5-5", "Draft B · claude-opus-5-5"). */
export const draftTabLabel = (d: Proposal, all: Proposal[]): string => {
  const model = modelOf(d);
  if (all.filter((o) => modelOf(o) === model).length === 1) return model;
  // Markdown marks go without leaving a gap ("(synthetic):" must not read "synthetic :").
  const words = String(d.payload?.value ?? '')
    .replace(/^\s*(?:[-*+]|\d+\.)\s+/, '')
    .replace(/[#*_`>[\]()!]/g, '')
    .trim().split(/\s+/).slice(0, 6).join(' ');
  return words ? `“${words}…”` : model;
};

interface Props {
  field: string;
  label: string;
  /** The field's text now (the finding as the page has it). */
  current: string | null | undefined;
  /** This field's pending finding_text proposals, oldest first. */
  drafts: Proposal[];
  canDecide: boolean;
  onDecided?: OnProposalDecided;
  evidence?: EvidenceResolver;
}

const Draft: React.FC<{
  pr: Proposal; label: string; current: string | null | undefined;
  canDecide: boolean; onDecided?: OnProposalDecided; evidence?: EvidenceResolver;
}> = ({ pr, label, current, canDecide, onDecided, evidence }) => {
  const decision = useProposalDecision(pr, onDecided);
  const value = String(pr.payload?.value ?? '');
  return (
    <div className="min-w-0 space-y-xs" data-proposal={pr.id}>
      {/* Kept while "Accept and edit" is open: the reviewer edits with both
          texts in view (browser pass 5.334.1 — they vanished on edit). */}
      <TextComparison
        current={current}
        proposed={value}
        mono={pr.field === 'cvss_vector'}
        staleBase={staleBase(pr)}
        evidence={evidence}
      />
      <div className="space-y-xxs">
        <p className="min-w-0 text-caption text-muted-foreground"><ProposalSource pr={pr} /></p>
        <ProposalReasons pr={pr} />
      </div>
      <ProposalDecisionControls pr={pr} canDecide={canDecide} decision={decision} fieldLabel={label} current={current} />
    </div>
  );
};

const FieldDraftsReview: React.FC<Props> = ({ field, label, current, drafts, canDecide, onDecided, evidence }) => {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = drafts.find((d) => d.id === selectedId) ?? drafts[0];
  if (!selected) return null;
  const many = drafts.length > 1;

  return (
    <div
      id={`review-${field}`}
      className="min-w-0 space-y-xs border-l-2 border-info pl-sm"
      // Room for the section's label printed above the drafts.
      style={scrollBelowChrome('2.25rem')}
      data-testid={`drafts-${field}`}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        <p className="text-caption font-medium text-info">
          {many ? `${drafts.length} drafts to review` : '1 draft to review'}
          <span className="font-normal text-muted-foreground"> · nothing changes until one is accepted{many ? '; accepting one retires the others' : ''}</span>
        </p>
      </div>
      {many && (
        <div role="tablist" aria-label={`${label} drafts`} className="flex min-w-0 flex-wrap gap-xs">
          {drafts.map((d, i) => {
            const active = d.id === selected.id;
            const who = draftTabLabel(d, drafts);
            return (
              <button
                key={d.id}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`review-${field}-panel`}
                onClick={() => setSelectedId(d.id)}
                title={`${modelOf(d)} · ${String(d.payload?.value ?? '').slice(0, 300)}`}
                className={cn(
                  'inline-flex max-w-[16rem] items-center gap-xxs rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active
                    ? 'border-primary bg-primary/10 text-foreground'
                    : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                )}
              >
                <strong className="shrink-0">Draft {draftLetter(i)}</strong>
                <span className="min-w-0 truncate">· {who}</span>
                {d.changed_since_proposed && <span className="shrink-0 text-warning" aria-label="written against older text">⚠</span>}
              </button>
            );
          })}
        </div>
      )}
      <div id={`review-${field}-panel`} role={many ? 'tabpanel' : undefined} className="min-w-0">
        <Draft key={selected.id} pr={selected} label={label} current={current}
          canDecide={canDecide} onDecided={onDecided} evidence={evidence} />
      </div>
    </div>
  );
};

export default FieldDraftsReview;
