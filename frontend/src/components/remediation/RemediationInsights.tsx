/**
 * What a manager is asked first about the deadlines (5.341.0), over the same
 * selection as the list under it: how many of each severity are overdue or
 * due soon, how late the overdue ones are, and how many at-risk rows nobody
 * is chasing.  Every number is a button that opens exactly its rows.
 *
 * Three short lines of text and proportional bars — not a chart: the values
 * are few, exact, and each one is a filter.
 */
import React from 'react';

import type { OverdueBand, RemediationPage, RemediationState } from '../../services/api';
import { OVERDUE_BANDS, OVERDUE_BAND_LABEL, severityWord } from '../../utils/remediation';
import { SeverityBadge } from '../ui/SeverityBadge';

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const BUTTON = 'rounded px-xxs tabular-nums hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export interface RemediationInsightsProps {
  page: RemediationPage;
  onSeverity: (severity: string, state: RemediationState) => void;
  onBand: (band: OverdueBand) => void;
  onNotFollowedUp: () => void;
}

export const RemediationInsights: React.FC<RemediationInsightsProps> = ({ page, onSeverity, onBand, onNotFollowedUp }) => {
  const overdue = page.state_counts.overdue;
  const atRisk = overdue + page.state_counts.due_soon;
  if (atRisk === 0) return null;
  const listed = SEVERITIES.filter((s) => (page.severity_counts[s]?.overdue ?? 0) + (page.severity_counts[s]?.due_soon ?? 0) > 0);
  const widest = Math.max(1, ...OVERDUE_BANDS.map((b) => page.overdue_ages[b]));

  return (
    <div className="mb-md grid min-w-0 gap-x-lg gap-y-sm lg:grid-cols-3" aria-label="Deadlines at risk, by severity and by how late">
      <div className="min-w-0">
        <p className="text-caption font-medium text-foreground">Overdue and due soon, by severity</p>
        <table className="mt-xxs w-full table-fixed text-metadata">
          <caption className="sr-only">Overdue and due-soon findings on hosts per severity</caption>
          <thead className="text-caption text-muted-foreground">
            <tr>
              <th className="py-px text-left font-normal">Severity</th>
              <th className="w-[5.5rem] py-px text-right font-normal">Overdue</th>
              <th className="w-[5.5rem] py-px text-right font-normal">Due soon</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((severity) => {
              const counts = page.severity_counts[severity];
              const cell = (value: number, state: RemediationState, tone: string) => (
                <td className="py-px text-right">
                  {value === 0 ? <span className="px-xxs text-muted-foreground">0</span> : (
                    <button type="button" className={`${BUTTON} font-medium ${tone}`}
                      aria-label={`${value.toLocaleString()} ${severityWord(severity).toLowerCase()} ${state === 'overdue' ? 'overdue' : 'due soon'}: show them`}
                      onClick={() => onSeverity(severity, state)}>
                      {value.toLocaleString()}
                    </button>
                  )}
                </td>
              );
              return (
                <tr key={severity}>
                  <td className="py-px"><SeverityBadge severity={severity} /></td>
                  {cell(counts.overdue, 'overdue', 'text-destructive')}
                  {cell(counts.due_soon, 'due_soon', 'text-warning')}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="min-w-0">
        <p className="text-caption font-medium text-foreground">How late the overdue ones are</p>
        {overdue === 0 ? (
          <p className="mt-xxs text-metadata text-muted-foreground">Nothing is overdue.</p>
        ) : (
          <ul className="mt-xxs space-y-px text-metadata">
            {OVERDUE_BANDS.map((band) => {
              const value = page.overdue_ages[band];
              return (
                <li key={band} className="flex min-w-0 items-center gap-sm">
                  <span className="w-[6.5rem] shrink-0 text-muted-foreground">{OVERDUE_BAND_LABEL[band]}</span>
                  {/* Length is the count; the number beside it is the value. */}
                  <span className="h-2 min-w-0 flex-1" aria-hidden>
                    <span className="block h-2 rounded-sm bg-destructive/70" style={{ width: `${(value / widest) * 100}%` }} />
                  </span>
                  {value === 0 ? <span className="w-10 shrink-0 px-xxs text-right text-muted-foreground">0</span> : (
                    <button type="button" className={`${BUTTON} w-10 shrink-0 text-right font-medium text-foreground`}
                      aria-label={`${value.toLocaleString()} overdue by ${OVERDUE_BAND_LABEL[band].toLowerCase()}: show them`}
                      onClick={() => onBand(band)}>
                      {value.toLocaleString()}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="min-w-0">
        <p className="text-caption font-medium text-foreground">Not being chased</p>
        <p className="mt-xxs text-metadata text-muted-foreground">
          {page.not_followed_up === 0 ? (
            <>Every overdue or due-soon finding on a host has a follow-up recorded in the last {page.not_followed_up_days} days.</>
          ) : (
            <>
              <button type="button" className={`${BUTTON} font-medium text-foreground`}
                aria-label={`${page.not_followed_up.toLocaleString()} overdue or due soon with no recent follow-up: show them`}
                onClick={onNotFollowedUp}>
                {page.not_followed_up.toLocaleString()}
              </button>{' '}
              of the {atRisk.toLocaleString()} overdue or due soon {page.not_followed_up === 1 ? 'has' : 'have'} no
              follow-up recorded in the last {page.not_followed_up_days} days.
            </>
          )}
        </p>
      </div>
    </div>
  );
};

export default RemediationInsights;
