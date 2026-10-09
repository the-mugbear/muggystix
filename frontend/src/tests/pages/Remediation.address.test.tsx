/**
 * The remediation work list's two text boxes and the address — under the
 * REAL router (setupTests replaces `useNavigate` / `useLocation` elsewhere).
 *
 * "Contact" (`?contact=`) and "Finding or host" (`?q=`, two characters or
 * more — `utils/remediation.searchParam`) each kept a copy of the address's
 * value and a hand-made timer that wrote it back.  Both are now
 * `hooks/useUrlSearchDraft`: the address owns the filter, the box holds only
 * what is being typed.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

const listRemediation = vi.fn();
const getRemediationPolicy = vi.fn();
vi.mock('../../utils/download', () => ({ saveBlob: vi.fn() }));
vi.mock('../../services/api', () => ({
  listRemediation: (...a: unknown[]) => listRemediation(...a),
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  applyRemediation: vi.fn(),
  listRemediationContacts: vi.fn().mockResolvedValue([]),
  getRemediationFollowUp: vi.fn(),
  recordRemediationFollowUp: vi.fn(),
  recordRemediationFollowUpOverview: vi.fn(),
  getRemediationTrend: vi.fn().mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] }),
  listClientReports: vi.fn().mockResolvedValue([]),
  assignRemediationFromReport: vi.fn(),
  listRemediationOverview: vi.fn(),
  listRemediationTeams: vi.fn().mockResolvedValue([]),
  listRemediationEvents: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  addRemediationNote: vi.fn(),
  deleteRemediationNote: vi.fn(),
  prepareContactReport: vi.fn(),
  getContactReport: vi.fn(),
  downloadContactReport: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: 'admin' } }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Remediation from '../../pages/Remediation';
import { TooltipProvider } from '../../components/ui/tooltip';
import { resetRemediationPolicy } from '../../hooks/useRemediationPolicy';
import type { RemediationPage, RemediationRow } from '../../services/api';

const row = (id: number): RemediationRow => ({
  finding_host_id: id, finding_id: 10, project_id: 1, project_name: 'P', finding_title: `Finding ${id}`,
  state: 'on_track', due_on: '2026-12-01', days_left: 21, closed_days_late: null, last_follow_up_on: null,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 100 + id,
  ip_address: `10.0.0.${id}`, hostname: null, contact_email: 'roger@example.com', contact_name: null, team: null,
  notified_on: '2026-11-01', status: 'open', closed_on: null, updated_at: null, verification: null,
});
// Eighty rows, so a third page exists.
const PAGE: RemediationPage = {
  items: [row(1), row(2)], total: 80, has_more: true, limit: 25, offset: 0,
  status_counts: { open: 2, closed: 0, deferred: 1 },
  verification_counts: { reported_fixed_not_retested: 0, remediated_record_open: 0 },
  flag_counts: { deferral_review_due: 0, deadline_overridden: 0 },
  state_counts: { overdue: 1, due_soon: 0, on_track: 2, not_assigned: 3, no_deadline: 0, deferred: 1, closed: 0 },
  severity_counts: {}, overdue_ages: { '1-7': 0, '8-30': 0, '31-90': 0, '90+': 0 },
  not_followed_up: 0, not_followed_up_days: 7, as_of: '2026-11-10',
} as RemediationPage;
const POLICY = { enabled: true, due_soon_days: 7, time_zone: 'UTC', days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const open = async (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/remediation', element: <TooltipProvider><Remediation /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  await pass(20);
  await pass(20);
  return router;
};
const contactBox = () => screen.getByLabelText('Contact') as HTMLInputElement;
const searchBox = () => screen.getByRole('searchbox', { name: 'Search by finding or host' }) as HTMLInputElement;
const type = (input: HTMLInputElement, text: string) => fireEvent.change(input, { target: { value: text } });
/** The query of each list read — every one asked of the project on screen. */
const asked = () => listRemediation.mock.calls.map(([projectId, query]) => {
  expect(projectId).toBe(1);
  return query as Record<string, unknown>;
});
const last = () => asked()[asked().length - 1];

beforeEach(() => {
  vi.useFakeTimers();
  listRemediation.mockReset().mockResolvedValue(PAGE);
  getRemediationPolicy.mockReset().mockResolvedValue(POLICY);
  resetRemediationPolicy();
});
afterEach(() => { vi.useRealTimers(); });

describe('Remediation — the Contact box follows the address (real router)', () => {
  it('opens on what the address says, and writes nothing', async () => {
    const router = await open('/remediation?state=overdue&contact=roger&page=3');
    expect(contactBox().value).toBe('roger');
    expect(last()).toMatchObject({ contact: 'roger', state: ['overdue'], offset: 50 });
    await pass(1000);
    expect(router.state.location.search).toBe('?state=overdue&contact=roger&page=3');
    expect(asked()).toHaveLength(1);
  });

  it('typing asks once after the typing stops — trimmed, replacing the entry, from the first page, the other filters kept', async () => {
    const router = await open('/remediation?state=overdue&page=3');
    expect(last()).toMatchObject({ offset: 50 });
    listRemediation.mockClear();

    type(contactBox(), ' rog');
    await pass(200);
    type(contactBox(), ' roger ');
    await pass(200);
    expect(asked()).toHaveLength(0);
    expect(router.state.location.search).toBe('?state=overdue&page=3');

    await pass(150);
    await pass(20);
    expect(router.state.location.search).toBe('?state=overdue&contact=roger');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(asked()).toHaveLength(1);
    expect(last()).toMatchObject({ contact: 'roger', state: ['overdue'], offset: 0 });
  });

  it('keeps what the reader typed in the box once it is committed — the space after a word is not eaten', async () => {
    await open('/remediation');
    type(contactBox(), 'roger ');
    await pass(400);
    expect(last()).toMatchObject({ contact: 'roger' });
    expect(contactBox().value).toBe('roger ');
  });

  it('a link to the same page with another contact, and Back, re-seed the box; the list follows and the address is left alone', async () => {
    const router = await open('/remediation?contact=first');
    expect(contactBox().value).toBe('first');

    await act(async () => { await router.navigate('/remediation?contact=linked&severity=high'); });
    await pass(20);
    expect(contactBox().value).toBe('linked');
    expect(last()).toMatchObject({ contact: 'linked', severity: 'high' });
    await pass(1000);
    expect(router.state.location.search).toBe('?contact=linked&severity=high');
    expect(last()).toMatchObject({ contact: 'linked', severity: 'high' });

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(contactBox().value).toBe('first');
    expect(last()).toMatchObject({ contact: 'first', severity: undefined });
    await pass(1000);
    expect(router.state.location.search).toBe('?contact=first');
    expect(last()).toMatchObject({ contact: 'first', severity: undefined });
  });

  it('"Clear the contact filter" empties the box and the address at once', async () => {
    const router = await open('/remediation?contact=roger&state=overdue');
    fireEvent.click(screen.getByRole('button', { name: 'Clear the contact filter' }));
    await pass(20);
    expect(contactBox().value).toBe('');
    expect(router.state.location.search).toBe('?state=overdue');
    expect(last()).toMatchObject({ contact: undefined, state: ['overdue'] });
    await pass(1000);
    expect(router.state.location.search).toBe('?state=overdue');
  });

  it('"No contact yet" drops a half-typed contact with the committed one: it is never written afterwards', async () => {
    const router = await open('/remediation?contact=roger');
    type(contactBox(), 'roger sm');
    await pass(100);
    fireEvent.click(screen.getByRole('button', { name: 'No contact yet' }));
    await pass(1000);
    expect(contactBox().value).toBe('');
    expect(router.state.location.search).toBe('?unassigned=1');
    expect(last()).toMatchObject({ contact: undefined, unassigned: true });
    expect(asked().some((query) => query.contact === 'roger sm')).toBe(false);
  });

  it('"No contact yet" drops a contact that was typed and never committed', async () => {
    const router = await open('/remediation');
    type(contactBox(), 'ro');
    await pass(100);
    fireEvent.click(screen.getByRole('button', { name: 'No contact yet' }));
    await pass(1000);
    expect(contactBox().value).toBe('');
    expect(router.state.location.search).toBe('?unassigned=1');
    expect(asked().some((query) => query.contact === 'ro')).toBe(false);
  });
});

describe('Remediation — the "Finding or host" box follows the address (real router)', () => {
  it('opens on what the address says, and writes nothing', async () => {
    const router = await open('/remediation?q=smb&page=3');
    expect(searchBox().value).toBe('smb');
    expect(last()).toMatchObject({ q: 'smb', offset: 50 });
    await pass(1000);
    expect(router.state.location.search).toBe('?q=smb&page=3');
    expect(asked()).toHaveLength(1);
  });

  it('one character is neither asked for nor written, and stays in the box; two or more are, once, from the first page', async () => {
    const router = await open('/remediation?state=overdue&page=3');
    listRemediation.mockClear();

    type(searchBox(), 's');
    await pass(1000);
    expect(searchBox().value).toBe('s');
    expect(asked()).toHaveLength(0);
    // The address holds only a search the server takes — and keeps its page.
    expect(router.state.location.search).toBe('?state=overdue&page=3');

    type(searchBox(), ' sm');
    await pass(200);
    type(searchBox(), ' smb ');
    await pass(200);
    expect(asked()).toHaveLength(0);
    await pass(150);
    await pass(20);
    expect(router.state.location.search).toBe('?state=overdue&q=smb');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(asked()).toHaveLength(1);
    expect(last()).toMatchObject({ q: 'smb', state: ['overdue'], offset: 0 });
    expect(searchBox().value).toBe(' smb ');

    // Back under two characters: the search leaves the address, the box keeps the character.
    type(searchBox(), 's');
    await pass(350);
    expect(router.state.location.search).toBe('?state=overdue');
    expect(last()).not.toHaveProperty('q');
    expect(searchBox().value).toBe('s');
  });

  it('a link to the same page with another search, and Back, re-seed the box; the list follows and the address is left alone', async () => {
    const router = await open('/remediation?q=first');
    expect(searchBox().value).toBe('first');

    await act(async () => { await router.navigate('/remediation?q=linked&severity=high'); });
    await pass(20);
    expect(searchBox().value).toBe('linked');
    expect(last()).toMatchObject({ q: 'linked', severity: 'high' });
    await pass(1000);
    expect(router.state.location.search).toBe('?q=linked&severity=high');

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(searchBox().value).toBe('first');
    expect(last()).toMatchObject({ q: 'first', severity: undefined });
    await pass(1000);
    expect(router.state.location.search).toBe('?q=first');
    expect(last()).toMatchObject({ q: 'first' });
  });

  it('a single character typed on a page with no search is dropped when the reader goes to a search and comes Back', async () => {
    const router = await open('/remediation');
    type(searchBox(), 's');
    await pass(400);
    await act(async () => { await router.navigate('/remediation?q=linked'); });
    await pass(20);
    expect(searchBox().value).toBe('linked');
    await act(async () => { await router.navigate(-1); });
    await pass(1000);
    expect(searchBox().value).toBe('');
    expect(router.state.location.search).toBe('');
  });

  it('"Clear the search" empties the box and the address at once', async () => {
    const router = await open('/remediation?q=smb&state=overdue');
    fireEvent.click(screen.getByRole('button', { name: 'Clear the search' }));
    await pass(20);
    expect(searchBox().value).toBe('');
    expect(router.state.location.search).toBe('?state=overdue');
    expect(last()).not.toHaveProperty('q');
  });
});
