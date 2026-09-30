/**
 * The finding page's Proposals section (v5.316.0): every pending proposal on
 * this finding — report text grouped by field so several drafts (different
 * agents or models) sit side by side for comparison, then endpoint changes.
 * A finding with any is "needs review".  Hidden when there are none.
 *
 * Accepting report text writes it as you (your name in the history); the
 * field's other drafts are then marked superseded.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { listProposals, Proposal } from '../../services/api';
import { useVisibilityPoll } from '../../hooks/useVisibilityPoll';
import { formatApiError } from '../../utils/apiErrors';
import PostureSection from '../posture/PostureSection';
import ProposalItem, { FIELD_LABELS } from './ProposalItem';

const FIELD_ORDER = ['description', 'impact', 'recommendation', 'steps_to_reproduce', 'references', 'cvss_vector'];

interface Props {
  findingId: number;
  canDecide: boolean;
  /** Bump to re-read (e.g. after "Draft empty sections"). */
  reloadKey?: number;
  /** An accept changed the finding: re-read it. */
  onApplied: () => void;
}

const FindingProposalsPanel: React.FC<Props> = ({ findingId, canDecide, reloadKey = 0, onApplied }) => {
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listProposals({ finding_id: findingId, status: 'pending', limit: 200 });
      setItems(res.items);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Proposals unavailable.'));
    }
  }, [findingId]);

  useEffect(() => { void load(); }, [load, reloadKey]);
  useVisibilityPoll(load, 30_000);

  const groups = useMemo(() => {
    const byField = new Map<string, Proposal[]>();
    const other: Proposal[] = [];
    for (const pr of items ?? []) {
      if (pr.kind === 'finding_text' && pr.field) {
        byField.set(pr.field, [...(byField.get(pr.field) ?? []), pr]);
      } else {
        other.push(pr);
      }
    }
    const fields = [...byField.keys()].sort((a, b) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
    return { fields: fields.map((f) => ({ field: f, items: byField.get(f) ?? [] })), other };
  }, [items]);

  const decided = (updated: Proposal) => {
    if (updated.status === 'accepted') onApplied();
    void load();
  };

  if (error) {
    return (
      <PostureSection className="mb-md" title={<span>Proposals</span>}>
        <p className="text-caption text-destructive">{error}</p>
      </PostureSection>
    );
  }
  if (!items || items.length === 0) return null;

  return (
    <div id="proposals" className="mb-md">
      <PostureSection
        title={<span>Proposals · {items.length} to review</span>}
        description="Changes an agent or an AI draft proposed for this finding. Nothing has changed yet: accept (then edit if needed) or reject each. Several drafts of one section stay side by side to compare; accepting one retires the others."
      >
        <div className="space-y-md">
          {groups.fields.map((g) => (
            <section key={g.field} className="min-w-0">
              <h3 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                {FIELD_LABELS[g.field] ?? g.field}{g.items.length > 1 ? ` · ${g.items.length} drafts` : ''}
              </h3>
              {g.items.map((pr) => (
                <ProposalItem key={pr.id} proposal={pr} canDecide={canDecide} onDecided={decided} />
              ))}
            </section>
          ))}
          {groups.other.length > 0 && (
            <section className="min-w-0">
              <h3 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">Endpoints and triage</h3>
              {groups.other.map((pr) => (
                <ProposalItem key={pr.id} proposal={pr} canDecide={canDecide} onDecided={decided} showTarget linkTarget={false} />
              ))}
            </section>
          )}
        </div>
      </PostureSection>
    </div>
  );
};

export default FindingProposalsPanel;
