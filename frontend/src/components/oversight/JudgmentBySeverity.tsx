/**
 * Findings and the judging of scanner output, one row per severity
 * (5.259.0).  Each row: the findings (issues) at that severity, the total
 * scanner observations beside them (5.270.1), a bar of those observations
 * split into judged / not yet judged, and the share of hosts taken into
 * review (in review or reviewed) with a finding at that severity (the API's
 * defect_rate over its `tested_targets`; v5.294.0 renamed the words, not the
 * count — "tested" means a recorded test result everywhere else).
 *
 * Findings are issues and observations are issue × host — the bar is the
 * observations' own part-to-whole and the findings count sits beside it,
 * never inside it.  Emphasis form: "not yet judged" is the accent, "judged"
 * the recessive grey; every number is a direct label, so colour is never
 * the only way to read it.
 */
import React from 'react';

import type { OversightSummary } from '../../services/api/oversight';
import { SEVERITY_HSL } from '../../utils/severity';

const SEVS = ['critical', 'high', 'medium', 'low'] as const;
const LABEL = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' } as const;
const NOT_JUDGED = 'hsl(var(--info))';
const JUDGED = 'hsl(var(--muted-foreground) / 0.35)';

export const JudgmentBySeverity: React.FC<{ severity: OversightSummary['severity'] }> = ({ severity: s }) => (
  <div className="min-w-0">
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] table-fixed text-metadata" aria-label="Findings and scanner observations by severity">
        <colgroup>
          <col style={{ width: '12%' }} /><col style={{ width: '11%' }} /><col style={{ width: '14%' }} /><col /><col style={{ width: '14%' }} />
        </colgroup>
        <thead>
          <tr className="text-caption text-muted-foreground">
            <th className="pb-xs text-left font-medium">Severity</th>
            <th className="pb-xs text-right font-medium" title="Distinct findings; one finding on many hosts counts once; false positives excluded">Findings</th>
            {/* v5.270.1 — the total beside the findings (user request): the
                bar showed only the share still waiting. */}
            <th className="pb-xs text-right font-medium" title="Every scanner observation at that severity — one per issue per host, judged or not">Scanner observations</th>
            <th className="pb-xs pl-lg text-left font-medium">
              Judged —{' '}
              <span className="inline-flex items-center gap-xxs"><span className="inline-block h-2 w-3 rounded-sm" style={{ background: JUDGED }} aria-hidden />judged</span>{' · '}
              <span className="inline-flex items-center gap-xxs"><span className="inline-block h-2 w-3 rounded-sm" style={{ background: NOT_JUDGED }} aria-hidden />not yet judged</span>
            </th>
            <th className="pb-xs text-right font-medium" title={`Share of the ${s.tested_targets.toLocaleString()} hosts taken into review (in review or reviewed) with at least one finding at that severity (not a false positive there)`}>
              Taken into review, with a finding
            </th>
          </tr>
        </thead>
        <tbody>
          {SEVS.map((k) => {
            const judged = s.observations_judged[k];
            const open = s.observations_unjudged[k];
            const total = judged + open;
            const openPct = total ? (open / total) * 100 : 0;
            const rate = s.defect_rate[k];
            return (
              <tr key={k} className="border-t border-border">
                <td className="py-xs">
                  <span className="inline-flex items-center gap-xs">
                    <span className="inline-block size-2.5 rounded-full" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
                    {LABEL[k]}
                  </span>
                </td>
                <td className="py-xs text-right font-semibold tabular-nums">{s.findings[k].toLocaleString()}</td>
                <td className="py-xs text-right font-semibold tabular-nums">{total.toLocaleString()}</td>
                <td className="py-xs pl-lg">
                  {total === 0 ? (
                    <span className="text-caption text-muted-foreground">Nothing to judge</span>
                  ) : (
                    <div className="flex min-w-0 items-center gap-sm">
                      <div className="flex h-3 min-w-0 flex-1 gap-[2px]" role="img"
                        aria-label={`${LABEL[k]}: ${judged} judged, ${open} not yet judged of ${total} scanner observations`}>
                        {judged > 0 && (
                          <div className="h-full rounded-l-sm" style={{ width: `${100 - openPct}%`, background: JUDGED, borderRadius: open ? undefined : 4 }}
                            title={`${judged.toLocaleString()} judged`} />
                        )}
                        {open > 0 && (
                          <div className="h-full rounded-r" style={{ width: `${openPct}%`, background: NOT_JUDGED, minWidth: 2, borderRadius: judged ? undefined : 4 }}
                            title={`${open.toLocaleString()} not yet judged`} />
                        )}
                      </div>
                      <span className="shrink-0 text-caption tabular-nums text-muted-foreground">
                        <span className="font-semibold text-foreground">{open.toLocaleString()}</span> not yet judged
                      </span>
                    </div>
                  )}
                </td>
                <td className="py-xs text-right tabular-nums">{rate == null ? '—' : `${rate}%`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    <p className="mt-xs text-caption text-muted-foreground">
      {s.finding_affected_targets.toLocaleString()} {s.finding_affected_targets === 1 ? 'host carries' : 'hosts carry'} a finding. Findings are issues and observations are issue × host,
      so they are compared, never subtracted. Informational and unknown severities are left out.
    </p>
  </div>
);

export default JudgmentBySeverity;
