/**
 * Defect metrics on the remediation deadlines page (owner, 2026-10-14): the
 * defects — findings on hosts of the list, false positives left out — by
 * severity, with how many are open, remediated (the ENDPOINT concluded fixed
 * by the retest) and deferred, the remediation rate and two times to
 * remediate.  Scoped by the page's project filter: one project, or every
 * project the reader administers that is not archived.
 *
 * Every count opens the list below with exactly its rows (`onOpen` with the
 * `defect` / `severity` / `active` filters — the server's own
 * `defect_expr`); the rate and the averages are figures, not lists.  The two
 * MTTR headings say how each is derived (owner: a tooltip).
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';

import {
  getRemediationDefectMetrics, type DefectMetricRow, type RemediationDefect,
} from '../../services/api';
import { queryErrorText } from '../../lib/query';
import { InfoTip } from '../ui/info-tip';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const ALL_DEFECTS: RemediationDefect[] = ['open', 'remediated', 'deferred'];

export const MTTR_HELP = 'Mean time to remediate, in days: for each assigned defect, the days from the day it was '
  + 'assigned to the day its endpoint was marked Remediated after the retest, or the day it was deferred — or to '
  + 'today if it is still open — averaged. Defects nobody was assigned have no clock and are left out.';
export const MTTR_CLOSED_HELP = 'The same average over the defects that were remediated or deferred only: how long '
  + 'the finished ones took. A defect whose day was never recorded (remediated before BlueStick kept the day, with '
  + 'no history line to read it from) is left out.';

/** What a count opens: these defect states, these severities (null: all). */
export interface DefectListFilter {
  defect: RemediationDefect[];
  severity: string[] | null;
}

const days = (value: number | null) => (value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 1 }));

const DefectMetricsSection: React.FC<{
  /** The page's project filter; null = all active projects. */
  projectId: number | null;
  onOpen: (filter: DefectListFilter) => void;
}> = ({ projectId, onOpen }) => {
  const query = useQuery({
    queryKey: ['getRemediationDefectMetrics', projectId],
    queryFn: ({ signal }) => getRemediationDefectMetrics(projectId, signal),
  });
  const metrics = query.data ?? null;
  const error = queryErrorText(query.error, 'The defect metrics could not be loaded.');

  if (error) {
    return (
      <p role="alert" className="text-caption text-destructive">
        {error} <button type="button" className="text-info hover:underline" onClick={() => void query.refetch()}>Retry</button>
      </p>
    );
  }
  if (!metrics) return <p className="text-caption text-muted-foreground">Loading…</p>;

  const cell = (row: DefectMetricRow, value: number, defect: RemediationDefect[], what: string) => (
    value === 0 ? <span className="text-muted-foreground">0</span> : (
      <button type="button" onClick={() => onOpen({ defect, severity: row.severities })}
        aria-label={`${row.label}: show ${what} (${value.toLocaleString()})`}
        className="rounded text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {value.toLocaleString()}
      </button>
    )
  );
  const overall = metrics.rows.find((r) => r.key === 'overall');
  const unmeasured = overall ? overall.not_assigned + overall.not_measured : 0;

  return (
    <div className="min-w-0 overflow-x-auto">
      <Table aria-label="Defect metrics" className="min-w-[54rem] table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent hover:shadow-none">
            <TableHead className="w-[10rem]">Severity</TableHead>
            <TableHead className="text-right">Defects</TableHead>
            <TableHead className="text-right">Outstanding</TableHead>
            <TableHead className="text-right">Remediated</TableHead>
            <TableHead className="text-right">Deferred</TableHead>
            <TableHead className="text-right">Remediation rate</TableHead>
            <TableHead className="w-[9rem] text-right">
              <span className="inline-flex items-center justify-end gap-xxs">
                MTTR (days) <InfoTip text={MTTR_HELP} label="How MTTR is derived" />
              </span>
            </TableHead>
            <TableHead className="w-[13rem] text-right">
              <span className="inline-flex items-center justify-end gap-xxs">
                MTTR remediated or deferred (days)
                <InfoTip text={MTTR_CLOSED_HELP} label="How MTTR for remediated or deferred defects is derived" />
              </span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {metrics.rows.map((row) => (
            <TableRow key={row.key}>
              <TableCell className={`align-middle ${row.key === 'overall' || row.key === 'critical_high' ? 'font-medium' : ''}`}>
                {row.label}
              </TableCell>
              <TableCell className="text-right align-middle tabular-nums">{cell(row, row.defects, ALL_DEFECTS, 'defects')}</TableCell>
              <TableCell className="text-right align-middle tabular-nums">{cell(row, row.open, ['open'], 'open defects')}</TableCell>
              <TableCell className="text-right align-middle tabular-nums">{cell(row, row.remediated, ['remediated'], 'remediated defects')}</TableCell>
              <TableCell className="text-right align-middle tabular-nums">{cell(row, row.deferred, ['deferred'], 'deferred defects')}</TableCell>
              <TableCell className="text-right align-middle tabular-nums">
                {row.remediation_rate == null ? '—' : `${row.remediation_rate.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`}
              </TableCell>
              <TableCell className="text-right align-middle tabular-nums">{days(row.mttr_days)}</TableCell>
              <TableCell className="text-right align-middle tabular-nums">{days(row.mttr_closed_days)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {unmeasured > 0 && overall && (
        <p className="mt-xs text-caption text-muted-foreground" data-testid="defect-metrics-unmeasured">
          {/* Only the parts that are not zero (third review 2026-10-15, N7:
              it printed "0 not assigned").  Both kinds the server leaves out:
              no day recorded for the fix or deferral, or one BEFORE the
              assigned date (review 2026-10-14). */}
          Not in the MTTR figures: {[
            overall.not_assigned > 0 && `${overall.not_assigned.toLocaleString()} not assigned`,
            overall.not_measured > 0
              && `${overall.not_measured.toLocaleString()} with no recorded day or one before the assigned date`,
          ].filter(Boolean).join(', ')}.
        </p>
      )}
    </div>
  );
};

export default DefectMetricsSection;
