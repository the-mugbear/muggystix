/**
 * 5.335.0 — the remediation edit dialog sends only what was changed, so a
 * bulk edit never flattens fields the selected rows differ on.
 */
import { describe, expect, it } from 'vitest';

import type { RemediationRow } from '../services/api';
import {
  applyRowsFor, draftCaution, draftChanges, draftFor, draftProblem, groupTimeline, hasChanges, idParam,
  csvCell, deadlineCell, dueDistance, previewDueOn, remediationCsv, remediationPageSize, remediationSummary,
  timelineSummary,
} from '../utils/remediation';

const row = (over: Partial<RemediationRow> = {}): RemediationRow => ({
  finding_host_id: 1, finding_id: 10, project_id: 1, project_name: 'P', finding_title: 'SMB signing not required',
  state: 'not_assigned', due_on: null, days_left: null, closed_days_late: null, last_follow_up_on: null,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 5,
  ip_address: '10.0.0.5', hostname: null, contact_email: null, contact_name: null, team: null,
  notified_on: null, status: 'open', closed_on: null, updated_at: null, ...over,
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
    expect(draftProblem({ ...opened, status: 'deferred', closed_on: '2026-10-06' })).toMatch(/Closed/);
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
    expect(dueDistance({ state: 'closed', days_left: null, closed_days_late: 0 })).toBe('closed on time');
    expect(dueDistance({ state: 'closed', days_left: null, closed_days_late: 4 })).toBe('closed 4 days late');
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
      .toMatchObject({ primary: 'Closed 4 days late', date: '2026-11-06' });
    expect(deadlineCell({ ...base, state: 'closed', days_left: null, closed_on: '2026-11-01', closed_days_late: null }).primary).toBe('Closed');
  });
});
