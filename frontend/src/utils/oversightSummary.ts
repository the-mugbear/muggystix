/**
 * The Oversight figures as text to paste into an email or a chat (v5.273.0).
 *
 * The set is the manager's metrics notebook (PORTFOLIO.md): projects complete
 * and in progress, targets and targets tested, review counts, findings by
 * severity and the defect rate — overall, per project and per tester — for
 * the filters the page shows.  Built from the loaded dashboard, never
 * recomputed: the numbers are the ones on the screen, under the same
 * definitions (a footer states them, since a pasted figure travels without
 * its (i)).  Pure, so it is tested without the page.
 */
import type {
  OversightProjectRow,
  OversightResponse,
  OversightSeverity,
  OversightSeverityRate,
  OversightTesterRow,
} from '../services/api/oversight';

export type SummaryFormat = 'text' | 'markdown';

export interface SummaryOptions {
  format: SummaryFormat;
  includeProjects: boolean;
  includeTesters: boolean;
  /** "2026-08-24 – 2026-09-23 (UTC)" or "all time", as the page says it. */
  periodLabel: string;
  /** The filters in words, e.g. ["Project: Alpha", "Tester: Ana"]; empty = none. */
  filterLabels: string[];
}

const SEVS = ['critical', 'high', 'medium', 'low'] as const;
const n = (v: number) => v.toLocaleString('en-US');
/** "1 review" / "2 reviews" — every counted noun agrees with its number. */
const pl = (v: number, one: string, many = `${one}s`) => `${n(v)} ${v === 1 ? one : many}`;
const pct = (num: number, den: number) => (den > 0 ? `${Math.round((100 * num) / den)}%` : '—');
const rate = (r: number | null) => (r == null ? '—' : `${r}%`);
const total = (s: OversightSeverity) => s.critical + s.high + s.medium + s.low;
const bySeverity = (s: OversightSeverity) => SEVS.map((k) => `${k} ${n(s[k])}`).join(', ');
const compact = (s: OversightSeverity) => `C ${n(s.critical)} / H ${n(s.high)} / M ${n(s.medium)} / L ${n(s.low)}`;
const rates = (r: OversightSeverityRate) => SEVS.map((k) => `${k} ${rate(r[k])}`).join(', ');
const personName = (t: OversightTesterRow) => t.full_name || t.username;
const inProgress = (status: string) => status === 'active';
const statusWord = (status: string) =>
  (inProgress(status) ? 'in progress' : status === 'completed' ? 'complete' : status.replace(/_/g, ' '));
/** A cell value that cannot break a Markdown table. */
const cell = (v: string) => v.replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();

const orderedProjects = (rows: OversightProjectRow[]) =>
  [...rows].sort((a, b) =>
    Number(inProgress(b.status)) - Number(inProgress(a.status)) || a.name.localeCompare(b.name));

const orderedTesters = (rows: OversightTesterRow[]) =>
  [...rows].sort((a, b) => b.reviewed + b.in_review - (a.reviewed + a.in_review) || personName(a).localeCompare(personName(b)));

export function buildOversightSummary(data: OversightResponse, opts: SummaryOptions): string {
  const md = opts.format === 'markdown';
  const s = data.summary;
  const sev = s.severity;
  const findingsTotal = total(sev.findings);
  const bold = (t: string) => (md ? `**${t}**` : t);
  const heading = (t: string) => (md ? `**${t}**` : t);
  const bullet = md ? '- ' : '• ';
  const asOf = `${data.generated_at.slice(0, 16).replace('T', ' ')} UTC`;
  const basis = data.severity_basis === 'period'
    ? 'findings and observations first recorded in the period'
    : 'findings and observations as they stand now';

  const lines: string[] = [];
  lines.push(heading(`Security testing update — ${opts.periodLabel}`));
  lines.push(
    `${opts.filterLabels.length ? opts.filterLabels.join(' · ') : 'All projects'} · figures as of ${asOf}; severity: ${basis}.`,
  );
  lines.push('');
  lines.push(`${bullet}${bold('Projects')}: ${n(s.projects_total)} (${n(s.projects_in_progress)} in progress, ${n(s.projects_complete)} complete)`);
  lines.push(
    `${bullet}${bold('Targets')}: ${n(s.targets_current)} recorded; ${n(s.targets_tested)} tested (${pct(s.targets_tested, s.targets_current)})`
    + ` — ${n(s.targets_in_review)} in review, ${n(s.targets_reviewed)} reviewed`,
  );
  lines.push(
    `${bullet}${bold('In the period')}: +${pl(s.targets_added, 'target')} first recorded; ${pl(s.reviews_concluded, 'review')} concluded;`
    + ` ${pl(s.imports, 'scan')} imported; ${pl(s.contributors, 'contributor')}`,
  );
  lines.push(
    `${bullet}${bold('Findings')}: ${n(findingsTotal)} (${bySeverity(sev.findings)}) on ${pl(sev.finding_affected_targets, 'target')}`
    + ` — ${n(sev.finding_states.under_investigation)} under investigation, ${n(sev.finding_states.confirmed)} confirmed,`
    + ` ${n(sev.finding_states.closed)} closed; ${n(sev.findings_false_positive)} false positive${sev.findings_false_positive === 1 ? '' : 's'} not counted`,
  );
  lines.push(
    `${bullet}${bold('Defect rate')} (tested targets with a finding, of ${n(sev.tested_targets)} tested): ${rates(sev.defect_rate)}`,
  );
  lines.push(
    `${bullet}${bold('Scanner observations not yet judged')}: critical ${n(sev.observations_unjudged.critical)},`
    + ` high ${n(sev.observations_unjudged.high)} (of ${n(total(sev.observations))} observations)`,
  );

  if (opts.includeProjects && data.projects.length > 0) {
    lines.push('');
    lines.push(heading(`Per project (${n(data.projects.length)})`));
    const rows = orderedProjects(data.projects);
    if (md) {
      lines.push('| Project | Status | Tested | Findings (C / H / M / L) | Defect rate (C / H) |');
      lines.push('| --- | --- | --- | --- | --- |');
      rows.forEach((r) => lines.push(
        `| ${cell(r.name)} | ${statusWord(r.status)} | ${n(r.hosts_tested)} of ${n(r.host_count)} (${pct(r.hosts_tested, r.host_count)})`
        + ` | ${compact(r.findings)} | ${rate(r.defect_rate.critical)} / ${rate(r.defect_rate.high)} |`,
      ));
    } else {
      rows.forEach((r) => lines.push(
        `${bullet}${r.name} (${statusWord(r.status)}): ${n(r.hosts_tested)} of ${pl(r.host_count, 'target')} tested`
        + ` (${pct(r.hosts_tested, r.host_count)}) · findings ${compact(r.findings)}`
        + ` · defect rate C ${rate(r.defect_rate.critical)} / H ${rate(r.defect_rate.high)}`,
      ));
    }
  }

  if (opts.includeTesters && data.testers.length > 0) {
    lines.push('');
    lines.push(heading(`Per tester (${n(data.testers.length)})`));
    const rows = orderedTesters(data.testers);
    if (md) {
      lines.push('| Tester | Projects | Tested | Reviewed (in period) | Findings (C / H / M / L) |');
      lines.push('| --- | --- | --- | --- | --- |');
      rows.forEach((t) => lines.push(
        `| ${cell(personName(t))} | ${n(t.active_projects)} | ${n(t.tested)} | ${n(t.reviewed)} (${n(t.reviewed_in_period)}) | ${compact(t.findings)} |`,
      ));
    } else {
      rows.forEach((t) => lines.push(
        `${bullet}${personName(t)}: ${pl(t.tested, 'target')} tested, ${n(t.reviewed)} reviewed (${n(t.reviewed_in_period)} in the period)`
        + ` across ${pl(t.active_projects, 'project')} · findings ${compact(t.findings)}`,
      ));
    }
  }

  lines.push('');
  lines.push(
    (md ? '_' : '')
    + 'Findings are judged issues: one finding counts once however many targets it affects, and false positives are left out.'
    + ' A scanner observation is raw scanner output nobody has judged yet. Defect rate = share of tested targets with a finding at that severity.'
    + (md ? '_' : ''),
  );
  return lines.join('\n');
}
