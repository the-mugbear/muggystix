/**
 * The scanner observations' search box and the address — under the REAL
 * router (setupTests replaces `useNavigate` / `useLocation` elsewhere).
 *
 * The box kept its own copy of `?obs_search=`, seeded once, and wrote it back
 * from a timer, so Back or a link to the same view had the older text written
 * over the address.  It is now `useUrlSearchDraft('obs_search', { also: [] })`.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  getObservationIssues: vi.fn(),
  getObservationIssueHosts: vi.fn(),
  promoteObservationIssues: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import ScannerObservations from '../../components/findings/ScannerObservations';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const ISSUE = {
  issue_key: 'title:smb signing not required', title: 'SMB Signing not required', severity: 'medium', cve_id: null,
  sources: ['nessus'], host_count: 3, judged_host_count: 0, finding_id: null, finding_status: null,
};

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const open = async (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/findings', element: <ScannerObservations canManage /> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  await pass(20);
  return router;
};
const box = () => screen.getByRole('searchbox', { name: 'Search scanner observations' }) as HTMLInputElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const asked = () => mocked.getObservationIssues.mock.calls.map(([projectId, filters]) => {
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
});
const last = () => asked()[asked().length - 1];

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  mocked.getObservationIssues.mockResolvedValue({ items: [ISSUE], total: 1 });
});
afterEach(() => { vi.useRealTimers(); });

describe('ScannerObservations — the search box follows the address (real router)', () => {
  it('opens on what the address says, and writes nothing', async () => {
    const router = await open('/findings?view=observations&obs_severity=critical&obs_search=ssh');
    expect(box().value).toBe('ssh');
    expect(last()).toMatchObject({ search: 'ssh', severity: 'critical' });
    await pass(1000);
    expect(router.state.location.search).toBe('?view=observations&obs_severity=critical&obs_search=ssh');
    expect(asked()).toHaveLength(1);
  });

  it('typing asks once after the typing stops — trimmed, replacing the entry, every other param kept', async () => {
    // `page` is not this list's (it loads more); the box never removed it.
    const router = await open('/findings?view=observations&obs_severity=critical&page=2');
    mocked.getObservationIssues.mockClear();

    type('  weak');
    await pass(200);
    type('  weak tls ');
    await pass(200);
    expect(asked()).toHaveLength(0);
    expect(router.state.location.search).toBe('?view=observations&obs_severity=critical&page=2');

    await pass(150);
    expect(router.state.location.search).toBe('?view=observations&obs_severity=critical&page=2&obs_search=weak+tls');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(asked()).toHaveLength(1);
    expect(last()).toMatchObject({ search: 'weak tls', severity: 'critical' });

    // Emptied, it is left out of the address.
    type('');
    await pass(350);
    expect(router.state.location.search).toBe('?view=observations&obs_severity=critical&page=2');
    expect(last()).toMatchObject({ search: '' });
  });

  it('a link to the same view with another search, and Back, re-seed the box; the list follows and the address is left alone', async () => {
    const router = await open('/findings?view=observations&obs_search=first');
    expect(box().value).toBe('first');

    await act(async () => { await router.navigate('/findings?view=observations&obs_search=linked&obs_min=5'); });
    await pass(20);
    expect(box().value).toBe('linked');
    expect(last()).toMatchObject({ search: 'linked', minHosts: 5 });
    await pass(1000);
    expect(router.state.location.search).toBe('?view=observations&obs_search=linked&obs_min=5');
    expect(last()).toMatchObject({ search: 'linked', minHosts: 5 });

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(box().value).toBe('first');
    expect(last()).toMatchObject({ search: 'first', minHosts: 1 });
    await pass(1000);
    expect(router.state.location.search).toBe('?view=observations&obs_search=first');
    expect(last()).toMatchObject({ search: 'first', minHosts: 1 });
  });
});
