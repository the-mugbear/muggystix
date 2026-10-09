/**
 * Remediation (5.335.0; deadlines 5.340.0) — the project's findings on hosts
 * as work for the people who must fix them: who each was assigned to and
 * when, the deadline that follows from its severity, and where it stands.
 *
 * It exists only on an installation that turned remediation tracking on
 * (System settings).  It is the contact's progress, kept by project admins
 * (auditors read); it is not the finding's own status and never changes it.
 *
 * Sections, not cards (UI_STYLE_GUIDE §7): a lead sentence that says what
 * needs someone, then the work list (`RemediationWorkList`, shared with the
 * cross-project page).
 */
import React, { useState } from 'react';

import type { RemediationPage } from '../services/api';
import { useProjectRole } from '../hooks/useProjectRole';
import { useRemediationPolicy } from '../hooks/useRemediationPolicy';
import { timelineSummary } from '../utils/remediation';
import PostureSection from '../components/posture/PostureSection';
import RemediationAssignFromReportDialog from '../components/remediation/RemediationAssignFromReportDialog';
import RemediationTrend from '../components/remediation/RemediationTrend';
import RemediationWorkList from '../components/remediation/RemediationWorkList';

const n = (value: number) => value.toLocaleString();

/** What needs someone, in one sentence — each number is a state of the list below. */
export const RemediationLead: React.FC<{
  page: RemediationPage | null;
  filtered: boolean;
  dueSoonDays: number;
  /** "in this project" / "across your projects". */
  where: string;
}> = ({ page, filtered, dueSoonDays, where }) => {
  if (page == null) return <>Findings assigned for remediation, by deadline.</>;
  const c = page.state_counts;
  const open = c.overdue + c.due_soon + c.on_track + c.not_assigned + c.no_deadline;
  const total = open + c.deferred + c.closed;
  if (total === 0 && !filtered) {
    return <>Nothing to track yet. A finding shows here, once per host, when it is confirmed, accepted as a risk or remediated.</>;
  }
  const strong = (value: number, tone: string) => (
    <strong className={value > 0 ? tone : 'text-foreground'}>{n(value)}</strong>
  );
  return (
    <>
      {strong(c.overdue, 'text-destructive')} overdue and {strong(c.due_soon, 'text-warning')} due within{' '}
      {dueSoonDays} days, of <strong className="text-foreground">{n(open)}</strong> open{' '}
      {open === 1 ? 'finding on a host' : 'findings on hosts'} {filtered ? 'in this selection' : where}.{' '}
      {c.not_assigned > 0 && (
        <>
          <strong className="text-foreground">{n(c.not_assigned)}</strong>{' '}
          {c.not_assigned === 1 ? 'has' : 'have'} not been assigned, so no deadline is running.
        </>
      )}
    </>
  );
};

const Remediation: React.FC = () => {
  const { isProjectAdmin: canWrite } = useProjectRole();
  const { policy, enabled, loading } = useRemediationPolicy();
  const [page, setPage] = useState<RemediationPage | null>(null);
  const [filtered, setFiltered] = useState(false);
  const [fromReport, setFromReport] = useState(false);
  // Offered to a project admin while something has no assigned date — and
  // only by a server that knows the route (it is the one that sends
  // `flag_counts`), so an older one is never asked for it.
  const canStartClock = canWrite && page != null && page.flag_counts !== undefined
    && page.state_counts.not_assigned > 0;

  if (!enabled) {
    return (
      <div className="flex flex-col gap-lg p-md md:p-lg">
        <h1 className="text-page-title">Remediation</h1>
        <p className="max-w-4xl text-metadata text-muted-foreground">
          {loading ? 'Loading…' : 'Remediation tracking is not turned on for this installation. A global administrator turns it on in System settings.'}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      <div className="min-w-0">
        <h1 className="text-page-title">Remediation</h1>
        <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
          <RemediationLead page={page} filtered={filtered} dueSoonDays={policy?.due_soon_days ?? 7} where="in this project" />
          {canStartClock && (
            <>
              {' '}
              <button type="button" className="text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                title="Give the findings an issued report lists that report’s day as their assigned date"
                onClick={() => setFromReport(true)}>
                Start the clock from a report…
              </button>
            </>
          )}
        </p>
      </div>

      <PostureSection
        title="Findings on hosts"
        description={policy ? `${timelineSummary(policy)}, counted from the day a finding is assigned.` : undefined}
      >
        <RemediationWorkList
          scope="project"
          canWrite={canWrite}
          policy={policy}
          onLoaded={(next, isFiltered) => { setPage(next); setFiltered(isFiltered); }}
        />
      </PostureSection>

      <PostureSection title="Over time">
        <RemediationTrend scope="project" />
      </PostureSection>

      {fromReport && (
        <RemediationAssignFromReportDialog today={page?.as_of} onClose={() => setFromReport(false)} />
      )}
    </div>
  );
};

export default Remediation;
