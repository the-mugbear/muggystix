/**
 * 5.335.0 — the remediation edit dialog sends only what was changed, so a
 * bulk edit never flattens fields the selected rows differ on.
 */
import { describe, expect, it } from 'vitest';

import type { RemediationRow } from '../services/api';
import {
  applyRowsFor, draftCaution, draftChanges, draftFor, draftProblem, groupTimeline, hasChanges, idParam,
  csvCell, deadlineCell, dueDistance, previewDueOn, remediationCsv, remediationPageSize, remediationSummary,
  timelineSummary, heldRecordText, isRemediationVerification, verificationNote,
  isRemediationFlag, noteRequirement, policyDueLine, searchParam, REMEDIATION_FLAG_LABEL,
  REMEDIATION_FIELD_LABEL, REMEDIATION_STATE_HELP, REMEDIATION_STATE_LABEL, REMEDIATION_STATUS_LABEL,
} from '../utils/remediation';

const row = (over: Partial<RemediationRow> = {}): RemediationRow => ({
  finding_host_id: 1, finding_id: 10, project_id: 1, project_name: 'P', finding_title: 'SMB signing not required',
  state: 'not_assigned', due_on: null, days_left: null, closed_days_late: null, last_follow_up_on: null,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 5,
  ip_address: '10.0.0.5', hostname: null, contact_email: null, contact_name: null, team: null,
  notified_on: null, status: 'open', closed_on: null, updated_at: null, verification: null, ...over,
});

describe('draftFor', () => {
  it('opens one row on its own values', () => {
    const draft = draftFor([row({ contact_email: 'roger@example.com', status: 'deferred', notified_on: '2026-10-03' })]);
    expect(draft).toMatchObject({ contact_email: 'roger@example.com', status: 'deferred', notified_on: '2026-10-03' });
  });

  it('opens several rows on what they agree on and leaves the rest empty', () => {
    const draft = draftFor([
      row({ contact_email: 'roger@example.com', status: 'open', notified_on: '2026-10-03' }),
      row({ finding_host_id: 2, contact_email: 'jane@example.com', status: 'closed', notified_on: '2026-10-03' }),
    ]);
    expect(draft.contact_email).toBe('');
    expect(draft.status).toBe('');
    expect(draft.notified_on).toBe('2026-10-03');
  });
});

describe('draftChanges', () => {
  const rows = [
    row({ contact_email: 'roger@example.com', status: 'open' }),
    row({ finding_host_id: 2, contact_email: 'jane@example.com', status: 'closed', closed_on: '2026-10-06' }),
  ];

  it('sends nothing for a field nobody touched, even when the rows differ on it', () => {
    const opened = draftFor(rows);
    expect(draftChanges(opened, { ...opened, notified_on: '2026-10-04' })).toEqual({ notified_on: '2026-10-04' });
  });

  it('sends null for a field that was emptied', () => {
    const opened = draftFor([row({ contact_email: 'roger@example.com', notified_on: '2026-10-03' })]);
    expect(draftChanges(opened, { ...opened, notified_on: '' })).toEqual({ notified_on: null });
  });

  it('sends a status only when one was chosen and it differs', () => {
    const opened = draftFor(rows);
    expect(draftChanges(opened, opened)).toEqual({});
    expect(draftChanges(opened, { ...opened, status: 'deferred' })).toEqual({ status: 'deferred' });
    const one = draftFor([rows[0]]);
    expect(draftChanges(one, { ...one, status: 'open' })).toEqual({});
  });

  it('trims what was typed', () => {
    const opened = draftFor([row()]);
    expect(draftChanges(opened, { ...opened, contact_email: '  roger@example.com ' }))
      .toEqual({ contact_email: 'roger@example.com' });
  });
});

describe('draftProblem', () => {
  const opened = draftFor([row()]);
  it('refuses a contact that is not an address', () => {
    expect(draftProblem({ ...opened, contact_email: 'Roger Smith' })).toMatch(/email address/);
    expect(draftProblem({ ...opened, contact_email: 'roger@example.com' })).toBeNull();
  });
  it('refuses a closed date on a row that is not closed', () => {
    expect(draftProblem({ ...opened, status: 'deferred', closed_on: '2026-10-06' })).toMatch(/Reported fixed/);
    expect(draftProblem({ ...opened, status: 'closed', closed_on: '2026-10-06' })).toBeNull();
  });
});

describe('applyRowsFor', () => {
  it('is one row per selected finding on a host; the note goes once per HOST, on its first row', () => {
    // Two findings on host 5, one on host 6: the note is a line on a host's
    // timeline, so three selected rows are two notes — not three.
    const rows = [row(), row({ finding_host_id: 2 }), row({ finding_host_id: 3, host_id: 6 })];
    expect(applyRowsFor(rows, { status: 'closed' }, ' Patched ', 'k1')).toEqual([
      { finding_host_id: 1, status: 'closed', notes: [{ body: 'Patched', request_key: 'k1' }] },
      { finding_host_id: 2, status: 'closed' },
      { finding_host_id: 3, status: 'closed', notes: [{ body: 'Patched', request_key: 'k1' }] },
    ]);
    expect(applyRowsFor(rows, { status: 'closed' }, '  ', 'k1')[0]).toEqual({ finding_host_id: 1, status: 'closed' });
  });

  it('a note with no field change sends only the rows that carry it', () => {
    const rows = [row(), row({ finding_host_id: 2 }), row({ finding_host_id: 3, host_id: 6 })];
    expect(applyRowsFor(rows, {}, 'Called', 'k1')).toEqual([
      { finding_host_id: 1, notes: [{ body: 'Called', request_key: 'k1' }] },
      { finding_host_id: 3, notes: [{ body: 'Called', request_key: 'k1' }] },
    ]);
  });

  it('reads the page size and the narrowing ids from the address, and nothing else', () => {
    expect([null, '25', '100', '200', '50', 'x', '-1'].map(remediationPageSize)).toEqual([25, 25, 100, 200, 25, 25, 25]);
    expect([null, '7', '0', '-3', '1.5', 'abc', ''].map(idParam)).toEqual([null, 7, null, null, null, null, null]);
  });

  it('a note alone is a change worth saving; nothing at all is not', () => {
    expect(hasChanges({}, 'Called')).toBe(true);
    expect(hasChanges({ status: 'closed' }, '')).toBe(true);
    expect(hasChanges({}, '   ')).toBe(false);
  });
});

describe('groupTimeline', () => {
  const ev = (id: number, over: Record<string, unknown> = {}) => ({
    id, kind: 'change' as 'change' | 'note', finding_host_id: 1 as number | null, finding_title: 'A' as string | null,
    author: 'Ana' as string | null, agent_session_id: null as number | null,
    recorded_at: '2026-10-06T10:00:00Z', ...over,
  });

  it('puts one save’s changes to one finding in one entry', () => {
    const groups = groupTimeline([ev(1), ev(2), ev(3)]);
    expect(groups.map((g) => g.events.map((e) => e.id))).toEqual([[1, 2, 3]]);
  });

  it('keeps another finding, another save, another author and every note apart', () => {
    const groups = groupTimeline([
      ev(1), ev(2, { finding_host_id: 2, finding_title: 'B' }),
      ev(3, { recorded_at: '2026-10-06T11:00:00Z' }), ev(4, { recorded_at: '2026-10-06T11:00:00Z', author: 'Bo' }),
      ev(5, { kind: 'note' }), ev(6, { kind: 'note' }),
    ]);
    expect(groups.map((g) => g.events.map((e) => e.id))).toEqual([[1], [2], [3], [4], [5], [6]]);
  });
});


describe('draftCaution', () => {
  const opened = draftFor([row()]);
  it('points out a closed date before the date notified, without refusing it', () => {
    const early = { ...opened, status: 'closed' as const, notified_on: '2026-10-05', closed_on: '2026-10-03' };
    expect(draftCaution(early)).toMatch(/before the assigned date/);
    expect(draftProblem(early)).toBeNull();
  });
  it('says nothing when the dates are in order, equal, or one is missing', () => {
    expect(draftCaution({ ...opened, status: 'closed', notified_on: '2026-10-03', closed_on: '2026-10-05' })).toBeNull();
    expect(draftCaution({ ...opened, status: 'closed', notified_on: '2026-10-03', closed_on: '2026-10-03' })).toBeNull();
    expect(draftCaution({ ...opened, status: 'closed', notified_on: '', closed_on: '2026-10-03' })).toBeNull();
  });
});

describe('deadlines (5.340.0)', () => {
  const policy = { days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };

  it('says the distance to a deadline in words, and how a closed row ended', () => {
    expect(dueDistance({ state: 'overdue', days_left: -1, closed_days_late: null })).toBe('1 day overdue');
    expect(dueDistance({ state: 'due_soon', days_left: 0, closed_days_late: null })).toBe('due today');
    expect(dueDistance({ state: 'on_track', days_left: 12, closed_days_late: null })).toBe('in 12 days');
    expect(dueDistance({ state: 'closed', days_left: null, closed_days_late: 0 })).toBe('reported fixed on time');
    expect(dueDistance({ state: 'closed', days_left: null, closed_days_late: 4 })).toBe('reported fixed 4 days late');
    // No deadline: nothing to say, never "0 days".
    expect(dueDistance({ state: 'closed', days_left: null, closed_days_late: null })).toBeNull();
    expect(dueDistance({ state: 'not_assigned', days_left: null, closed_days_late: null })).toBeNull();
  });

  it('previews a deadline from the assigned date without a time zone moving the day', () => {
    expect(previewDueOn(policy, 'HIGH', '2026-10-05')).toBe('2026-11-04');
    expect(previewDueOn(policy, 'low', '2026-12-31')).toBe('2027-04-30');
    expect(previewDueOn(policy, 'info', '2026-10-05')).toBeNull();      // no timeline
    expect(previewDueOn(policy, 'high', '')).toBeNull();
    expect(previewDueOn(null, 'high', '2026-10-05')).toBeNull();
  });

  it('summarises the timeline, joining severities that share a number', () => {
    expect(timelineSummary(policy)).toBe('Critical and High 30 days · Medium 90 days · Low 120 days · Informational no deadline');
    expect(timelineSummary({ days: { critical: 7, high: 30, medium: 90, low: 120, info: 1 } }))
      .toBe('Critical 7 days · High 30 days · Medium 90 days · Low 120 days · Informational 1 day');
  });
});

describe('handing it to someone else (5.341.0)', () => {
  it('a CSV cell is never a formula, and a number is left a number', () => {
    expect(csvCell('=HYPERLINK("http://x")')).toBe(`"'=HYPERLINK(""http://x"")"`);
    expect(csvCell('+1 555')).toBe("'+1 555");
    expect(csvCell('@team')).toBe("'@team");
    expect(csvCell('a,b')).toBe('"a,b"');
    expect(csvCell(-8)).toBe('-8');
    expect(csvCell(null)).toBe('');
  });

  it('the CSV has one header and one line per row, with the deadline columns', () => {
    const csv = remediationCsv([row({ state: 'overdue', due_on: '2026-11-02', days_left: -8, team: 'Web, EU' })]).split('\r\n');
    expect(csv).toHaveLength(2);
    expect(csv[0]).toContain('Due on,Days left');
    expect(csv[1]).toContain('Overdue');
    expect(csv[1]).toContain(',2026-11-02,-8,');
    expect(csv[1]).toContain('"Web, EU"');
  });

  it('the summary says what is overdue by severity, how late, and what nobody chases', () => {
    const text = remediationSummary({
      as_of: '2026-11-10',
      state_counts: { overdue: 4, due_soon: 1, on_track: 9, not_assigned: 3, no_deadline: 0, deferred: 2, closed: 5 },
      severity_counts: {
        critical: { overdue: 2, due_soon: 1 }, high: { overdue: 0, due_soon: 0 }, medium: { overdue: 2, due_soon: 0 },
        low: { overdue: 0, due_soon: 0 }, info: { overdue: 0, due_soon: 0 },
      },
      overdue_ages: { '1-7': 1, '8-30': 0, '31-90': 2, '90+': 1 }, not_followed_up: 3, not_followed_up_days: 7,
    }, { where: 'in this project', dueSoonDays: 7 });
    expect(text).toContain('Remediation deadlines in this project, as of 2026-11-10');
    expect(text).toContain('Open findings on hosts: 17');
    expect(text).toContain('- Overdue: 4 (2 critical, 2 medium)');
    expect(text).toContain('- Due within 7 days: 1 (1 critical)');
    expect(text).toContain('- 31–90 days: 2');
    expect(text).not.toContain('8–30 days');                 // an empty band is left out
    expect(text).toContain('no follow-up in 7 days: 3');
  });

  it('one Deadline cell says the state and the distance once', () => {
    const base = { due_on: '2026-11-02', closed_on: null, closed_days_late: null };
    expect(deadlineCell({ ...base, state: 'overdue', days_left: -65 }).primary).toBe('65 days overdue');
    expect(deadlineCell({ ...base, state: 'due_soon', days_left: 0 }).primary).toBe('Due today');
    expect(deadlineCell({ ...base, state: 'on_track', days_left: 10 })).toMatchObject({ primary: 'Due in 10 days', date: '2026-11-02' });
    expect(deadlineCell({ ...base, state: 'not_assigned', days_left: null, due_on: null })).toMatchObject({ primary: 'Not assigned', date: null });
    expect(deadlineCell({ ...base, state: 'closed', days_left: null, closed_on: '2026-11-06', closed_days_late: 4 }))
      .toMatchObject({ primary: 'Reported fixed 4 days late', date: '2026-11-06' });
    expect(deadlineCell({ ...base, state: 'closed', days_left: null, closed_on: '2026-11-06', closed_days_late: 0 }).primary)
      .toBe('Reported fixed on time');
    expect(deadlineCell({ ...base, state: 'closed', days_left: null, closed_on: '2026-11-01', closed_days_late: null }).primary).toBe('Reported fixed');
  });
});

describe('one name per fact: the contact reports it fixed, the assessment calls it remediated', () => {
  it('never says "Closed" for the record’s closed status, state, date or CSV', () => {
    expect(REMEDIATION_STATUS_LABEL.closed).toBe('Reported fixed');
    expect(REMEDIATION_STATE_LABEL.closed).toBe('Reported fixed');
    expect(REMEDIATION_FIELD_LABEL.closed_on).toBe('Reported fixed on');
    const said = [
      ...Object.values(REMEDIATION_STATUS_LABEL), ...Object.values(REMEDIATION_STATE_LABEL),
      ...Object.values(REMEDIATION_STATE_HELP), ...Object.values(REMEDIATION_FIELD_LABEL),
    ].join(' | ');
    expect(said).not.toMatch(/closed/i);
    // The other statuses keep their names.
    expect(REMEDIATION_STATUS_LABEL).toMatchObject({ open: 'Open', deferred: 'Deferred' });
  });

  it('the CSV says "Reported fixed", the assessment’s status and the gap between them', () => {
    const csv = remediationCsv([
      row({ status: 'closed', state: 'closed', closed_on: '2026-11-06', closed_days_late: 2,
        endpoint_status: 'open', verification: 'reported_fixed_not_retested' }),
      row({ endpoint_status: 'remediated', verification: 'remediated_record_open' }),
      row({ status: 'closed', state: 'closed', endpoint_status: 'remediated' }),
    ]).split('\r\n');
    expect(csv[0]).toContain('Reported fixed on,Days late when reported fixed,Assessment status on this host,Record and assessment');
    expect(csv[0]).not.toMatch(/closed/i);
    expect(csv[1]).toContain('Reported fixed,');
    expect(csv[1]).toContain(',2026-11-06,2,Still present,"Reported fixed, not retested"');
    expect(csv[2]).toContain(',Remediated,"Remediated, record still open"');
    // The two agree: the last cell is empty.
    expect(csv[3]).toMatch(/,Remediated,,/);
    expect(csv.slice(1).join('\n')).not.toMatch(/closed/i);
  });

  it('the copied summary says "Reported fixed" and names the gaps that have rows', () => {
    const counts = {
      as_of: '2026-11-10',
      state_counts: { overdue: 0, due_soon: 0, on_track: 1, not_assigned: 0, no_deadline: 0, deferred: 2, closed: 5 },
      severity_counts: {}, overdue_ages: { '1-7': 0, '8-30': 0, '31-90': 0, '90+': 0 },
      not_followed_up: 0, not_followed_up_days: 7,
    };
    const text = remediationSummary(
      { ...counts, verification_counts: { reported_fixed_not_retested: 5, remediated_record_open: 0 } },
      { where: 'in this project', dueSoonDays: 7 });
    expect(text).toContain('Deferred: 2   Reported fixed: 5');
    expect(text).toContain('Reported fixed, not retested: 5');
    expect(text).not.toContain('Remediated, record still open');      // nothing there: not said
    expect(text).not.toMatch(/closed/i);
    // A page read before the server sent the counts still summarises.
    expect(remediationSummary(counts, { where: 'in this project', dueSoonDays: 7 })).toContain('Reported fixed: 5');
  });

  it('the row’s note shows the server’s gap, and "Remediated" where the two agree', () => {
    expect(verificationNote({ verification: 'reported_fixed_not_retested', endpoint_status: 'open' }))
      .toMatchObject({ text: 'Not retested', tone: 'text-warning' });
    expect(verificationNote({ verification: 'remediated_record_open', endpoint_status: 'remediated' }))
      .toMatchObject({ text: 'Remediated, record still open' });
    expect(verificationNote({ verification: null, endpoint_status: 'remediated' }))
      .toMatchObject({ text: 'Remediated', tone: 'text-muted-foreground' });
    // Nothing to relate: no line at all (the cell does not grow an empty one).
    expect(verificationNote({ verification: null, endpoint_status: 'open' })).toBeNull();
    expect(verificationNote({ verification: null, endpoint_status: 'false_positive' })).toBeNull();
    // The page never derives the gap itself: a closed record on an open
    // endpoint with no `verification` from the server shows none.
    expect(verificationNote({ verification: null, endpoint_status: 'retest' })).toBeNull();
  });

  it('a stored timeline text is said in today’s words, and a filter value is checked', () => {
    expect(heldRecordText('contact Ana, status closed on 2026-09-30')).toBe('contact Ana, status reported fixed on 2026-09-30');
    expect(heldRecordText('status deferred')).toBe('status deferred');
    expect(isRemediationVerification('reported_fixed_not_retested')).toBe(true);
    expect(isRemediationVerification('remediated_record_open')).toBe(true);
    expect(isRemediationVerification('closed')).toBe(false);
    expect(isRemediationVerification(null)).toBe(false);
  });
});

describe('managing due dates: a date set by hand, and a deferral with a review date', () => {
  const byHand = { due_on: '2026-12-20', due_override_on: '2026-12-20', policy_due_on: '2026-12-01', deadline_source: 'override' as const };

  it('opens on the date the rows agree on; rows that differ — or a server that sends none — open blank', () => {
    expect(draftFor([row(byHand)]).due_override_on).toBe('2026-12-20');
    expect(draftFor([row(byHand), row({ ...byHand, finding_host_id: 2 })]).due_override_on).toBe('2026-12-20');
    expect(draftFor([row(byHand), row({ finding_host_id: 2 })]).due_override_on).toBe('');
    // "Not sent" and null are the same nothing.
    expect(draftFor([row(), row({ finding_host_id: 2, due_override_on: null })]).due_override_on).toBe('');
    expect(draftFor([row()])).toMatchObject({ due_override_on: '', due_override_cleared: false, deferred_review_on: '' });
  });

  it('sends the due date only when it was set, changed or cleared', () => {
    const mixed = draftFor([row(byHand), row({ finding_host_id: 2 })]);
    expect(draftChanges(mixed, mixed)).toEqual({});
    expect(draftChanges(mixed, { ...mixed, team: 'Web' })).toEqual({ team: 'Web' });
    expect(draftChanges(mixed, { ...mixed, due_override_on: '2026-12-24' })).toEqual({ due_override_on: '2026-12-24' });
    // Back to the policy's date on rows that differ: sent to every one.
    expect(draftChanges(mixed, { ...mixed, due_override_cleared: true })).toEqual({ due_override_on: null });
    const one = draftFor([row(byHand)]);
    expect(draftChanges(one, { ...one, due_override_on: '' })).toEqual({ due_override_on: null });
    expect(draftChanges(one, { ...one, due_override_on: '2026-12-20' })).toEqual({});
  });

  it('a row that becomes deferred carries its review date; the date goes with Deferred only', () => {
    const open = draftFor([row({ status: 'open' })]);
    expect(draftChanges(open, { ...open, status: 'deferred', deferred_review_on: '2026-12-01' }))
      .toEqual({ status: 'deferred', deferred_review_on: '2026-12-01' });
    // A review date typed and then a different status chosen is not sent.
    expect(draftChanges(open, { ...open, status: 'closed', deferred_review_on: '2026-12-01' })).toEqual({ status: 'closed' });
    const deferred = draftFor([row({ status: 'deferred', state: 'deferred', deferred_review_on: '2026-12-01' })]);
    expect(draftChanges(deferred, { ...deferred, team: 'Web' })).toEqual({ team: 'Web' });
    expect(draftChanges(deferred, { ...deferred, deferred_review_on: '2027-01-15' })).toEqual({ deferred_review_on: '2027-01-15' });
    // A mixed selection made deferred with the date some of them already had: every row carries it.
    const some = draftFor([
      row({ status: 'deferred', deferred_review_on: '2026-12-01' }),
      row({ finding_host_id: 2, status: 'deferred', deferred_review_on: '2026-12-01' }),
    ]);
    const mixedStatus = { ...some, status: '' as const };
    expect(draftChanges(mixedStatus, { ...mixedStatus, status: 'deferred' }))
      .toEqual({ status: 'deferred', deferred_review_on: '2026-12-01' });
  });

  it('a deferral needs its review date, today or later on the server’s day', () => {
    const open = draftFor([row({ status: 'open' })]);
    const today = '2026-11-10';
    expect(draftProblem({ ...open, status: 'deferred' }, open, today)).toBe('A deferral needs a “Review on” date.');
    expect(draftProblem({ ...open, status: 'deferred', deferred_review_on: '2026-11-09' }, open, today))
      .toBe('The “Review on” date must be today or later.');
    expect(draftProblem({ ...open, status: 'deferred', deferred_review_on: '2026-11-10' }, open, today)).toBeNull();
    // Deferred before review dates existed: another field can still be edited…
    const old = draftFor([row({ status: 'deferred', state: 'deferred' })]);
    expect(draftProblem({ ...old, team: 'Web' }, old, today)).toBeNull();
    // …and a review date that has since passed is not a reason to refuse it.
    const past = draftFor([row({ status: 'deferred', state: 'deferred', deferred_review_on: '2026-10-01' })]);
    expect(draftProblem({ ...past, team: 'Web' }, past, today)).toBeNull();
    expect(draftProblem({ ...past, deferred_review_on: '' }, past, today)).toBe('A deferral needs a “Review on” date.');
  });

  it('a hand-set due date and a deferral need a note; nothing else does', () => {
    expect(noteRequirement({})).toBeNull();
    expect(noteRequirement({ contact_email: 'a@example.com', notified_on: '2026-10-01', status: 'closed', closed_on: '2026-10-02' })).toBeNull();
    expect(noteRequirement({ due_override_on: '2026-12-20' })).toMatch(/due date set by hand needs a note/);
    expect(noteRequirement({ due_override_on: null })).toMatch(/policy’s due date needs a note/);
    expect(noteRequirement({ status: 'deferred', deferred_review_on: '2026-12-01' })).toMatch(/deferral needs a note/);
    expect(noteRequirement({ deferred_review_on: '2027-01-15' })).toMatch(/deferral needs a note/);
  });

  it('a change that needs its reason carries the note in EVERY row, each under its own key', () => {
    const rows = [row({ finding_host_id: 1 }), row({ finding_host_id: 2 }), row({ finding_host_id: 3, host_id: 6 })];
    const sent = applyRowsFor(rows, { due_override_on: '2026-12-20' }, ' Agreed ', 'k');
    expect(sent).toEqual([
      { finding_host_id: 1, due_override_on: '2026-12-20', notes: [{ body: 'Agreed', request_key: 'k:1' }] },
      { finding_host_id: 2, due_override_on: '2026-12-20', notes: [{ body: 'Agreed', request_key: 'k:2' }] },
      { finding_host_id: 3, due_override_on: '2026-12-20', notes: [{ body: 'Agreed', request_key: 'k:3' }] },
    ]);
    // Any other change keeps one note per HOST, as before.
    expect(applyRowsFor(rows, { team: 'Web' }, 'Agreed', 'k').map((r) => r.notes?.[0].request_key)).toEqual(['k', undefined, 'k']);
  });

  it('the Deadline cell says a hand-set date in a second phrase, only while the clock runs', () => {
    const cell = deadlineCell({ ...row(byHand), state: 'on_track', days_left: 40 });
    expect(cell).toMatchObject({ primary: 'Due in 40 days', date: '2026-12-20' });
    expect(cell.source?.text).toMatch(/^set by hand · policy /);
    expect(cell.source?.title).toMatch(/^This due date was set by hand\. The policy’s date is /);
    expect(deadlineCell({ ...row(byHand), state: 'overdue', days_left: -2, policy_due_on: null }).source)
      .toEqual({ text: 'set by hand · no policy date', title: 'This due date was set by hand. The policy gives this finding no due date.' });
    expect(deadlineCell({ ...row({ deadline_source: 'policy' }), state: 'on_track', days_left: 40 }).source).toBeUndefined();
    expect(deadlineCell({ ...row(byHand), state: 'closed', closed_on: '2026-12-01' }).source).toBeUndefined();
    // A server that sends none of it: the cell is what it was.
    expect(deadlineCell({ state: 'on_track', due_on: '2026-12-01', days_left: 40, closed_days_late: null, closed_on: null }))
      .toEqual({ primary: 'Due in 40 days', date: '2026-12-01', after: null, tone: '' });
  });

  it('the Deadline cell says when a deferral is reviewed, that its review is due, or that it has none', () => {
    const deferred = { state: 'deferred' as const, due_on: null, days_left: null, closed_days_late: null, closed_on: null };
    expect(deadlineCell({ ...deferred, deferred_review_on: '2026-12-01', deferral_review_due: false }).primary)
      .toMatch(/^Deferred · review .*1/);
    expect(deadlineCell({ ...deferred, deferred_review_on: '2026-11-01', deferral_review_due: true }))
      .toMatchObject({ primary: 'Deferred · review due', date: '2026-11-01', tone: 'font-medium text-warning' });
    expect(deadlineCell({ ...deferred, deferred_review_on: null, deferral_review_due: true }))
      .toMatchObject({ primary: 'Deferred · no review date', date: null, tone: 'font-medium text-warning' });
    // The page never decides a review is due: a past date with no flag from the server is only a date.
    expect(deadlineCell({ ...deferred, deferred_review_on: '2020-01-01' }).tone).toBe('text-muted-foreground');
    // A server that sends neither field: "Deferred", as before.
    expect(deadlineCell(deferred)).toEqual({ primary: 'Deferred', date: null, after: null, tone: 'text-muted-foreground' });
  });

  it('the dialog’s read-only line gives the policy’s date and where it comes from', () => {
    const policy = { days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };
    expect(policyDueLine(policy, 'CRITICAL', '2026-10-05')).toMatch(/ — critical, 30 days from /);
    expect(policyDueLine(policy, 'info', '2026-10-05')).toBe('No due date: informational findings have no remediation timeline.');
    expect(policyDueLine(policy, 'high', '')).toBe('No due date yet: it counts from the assigned date.');
  });

  it('the CSV says where each due date comes from, the policy’s date when set by hand, and the review date', () => {
    const csv = remediationCsv([
      row({ ...byHand, state: 'on_track', days_left: 40 }),
      row({ state: 'on_track', due_on: '2026-12-01', days_left: 21, deadline_source: 'policy', policy_due_on: '2026-12-01' }),
      row({ status: 'deferred', state: 'deferred', deferred_review_on: '2026-12-15' }),
      row(),
    ]).split('\r\n');
    expect(csv[0]).toContain('Due on,Days left,Due date from,Policy due date,Deferral review on,Reported fixed on');
    expect(csv[1]).toContain(',2026-12-20,40,Set by hand,2026-12-01,,');
    // The policy's date is the due date already: not printed twice.
    expect(csv[2]).toContain(',2026-12-01,21,Policy,,,');
    expect(csv[3]).toContain(',Deferred,,,,,,2026-12-15,');
    // A server that sends none of it: three empty cells, nothing else moves.
    expect(csv[4]).toContain(',Not assigned,,,,,,,,,Still present,');
  });

  it('the copied summary names the flags that have rows, and only those', () => {
    const counts = {
      as_of: '2026-11-10',
      state_counts: { overdue: 0, due_soon: 0, on_track: 1, not_assigned: 0, no_deadline: 0, deferred: 2, closed: 5 },
      severity_counts: {}, overdue_ages: { '1-7': 0, '8-30': 0, '31-90': 0, '90+': 0 },
      not_followed_up: 0, not_followed_up_days: 7,
    };
    const text = remediationSummary(
      { ...counts, flag_counts: { deferral_review_due: 2, deadline_overridden: 0 } },
      { where: 'in this project', dueSoonDays: 7 });
    expect(text).toContain('Deferrals to review: 2');
    expect(text).not.toContain('Due date set by hand');
    expect(remediationSummary({ ...counts, flag_counts: { deferral_review_due: 0, deadline_overridden: 4 } },
      { where: 'in this project', dueSoonDays: 7 })).toContain('Due date set by hand: 4');
    // No counts from the server: the summary is what it was.
    expect(remediationSummary(counts, { where: 'in this project', dueSoonDays: 7 })).not.toMatch(/Deferrals to review|set by hand/);
  });

  it('checks a flag and a search from the address, and names the new timeline fields', () => {
    expect(isRemediationFlag('deferral_review_due')).toBe(true);
    expect(isRemediationFlag('deadline_overridden')).toBe(true);
    expect(isRemediationFlag('overdue')).toBe(false);
    expect(isRemediationFlag(null)).toBe(false);
    expect(REMEDIATION_FLAG_LABEL).toEqual({ deferral_review_due: 'Deferrals to review', deadline_overridden: 'Due date set by hand' });
    expect(searchParam(null)).toBe('');
    expect(searchParam(' x ')).toBe('');
    expect(searchParam('  smb ')).toBe('smb');
    expect(searchParam('a'.repeat(300))).toHaveLength(200);
    expect(REMEDIATION_FIELD_LABEL).toMatchObject({ due_override_on: 'Due date', deferred_review_on: 'Review on', severity: 'Severity' });
  });
});
