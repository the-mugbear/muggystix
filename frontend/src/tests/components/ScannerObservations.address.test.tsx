/**
 * The scanner observations' search box, page and inspector and the address —
 * under the REAL router (setupTests replaces `useNavigate` / `useLocation`
 * elsewhere).
 *
 * The box kept its own copy of `?obs_search=`, seeded once, and wrote it back
 * from a timer, so Back or a link to the same view had the older text written
 * over the address.  It is now `useUrlSearchDraft('obs_search')`.
 *
 * The list's page (5.378.0): `?page=`, as the other paged lists keep it.  The
 * inspector's issue (5.404.0): `?issue=`, a history entry, so Back closes it.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  getObservationIssues: vi.fn(),
  getObservationIssueDetail: vi.fn(),
  getObservationIssueHosts: vi.fn(),
  getObservationHostOutput: vi.fn(),
  promoteObservationIssues: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import ScannerObservations from '../../components/observations/ScannerObservations';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const ISSUE = {
  issue_key: 'title:smb signing not required', title: 'SMB Signing not required', severity: 'medium', cve_id: null,
  sources: ['nessus'], host_count: 3, judged_host_count: 0, finding_id: null, finding_status: null,
};

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const open = async (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/observations', element: <ScannerObservations canManage /> }],
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
  mocked.getObservationIssueDetail.mockImplementation(async (_p: number, key: string) => ({
    issue_key: key, title: key, source: 'nessus', description: null, solution: null,
    cvss_score: null, cvss_vector: null, references: [],
  }));
  mocked.getObservationIssueHosts.mockResolvedValue([]);
});
afterEach(() => { vi.useRealTimers(); });

describe('ScannerObservations — the search box follows the address (real router)', () => {
  it('opens on what the address says, and writes nothing', async () => {
    const router = await open('/observations?obs_severity=critical&obs_search=ssh');
    expect(box().value).toBe('ssh');
    expect(last()).toMatchObject({ search: 'ssh', severity: 'critical' });
    await pass(1000);
    expect(router.state.location.search).toBe('?obs_severity=critical&obs_search=ssh');
    expect(asked()).toHaveLength(1);
  });

  it('typing asks once after the typing stops — trimmed, replacing the entry, every other param kept', async () => {
    const router = await open('/observations?obs_severity=critical');
    mocked.getObservationIssues.mockClear();

    type('  weak');
    await pass(200);
    type('  weak tls ');
    await pass(200);
    expect(asked()).toHaveLength(0);
    expect(router.state.location.search).toBe('?obs_severity=critical');

    await pass(150);
    expect(router.state.location.search).toBe('?obs_severity=critical&obs_search=weak+tls');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(asked()).toHaveLength(1);
    expect(last()).toMatchObject({ search: 'weak tls', severity: 'critical' });

    // Emptied, it is left out of the address.
    type('');
    await pass(350);
    expect(router.state.location.search).toBe('?obs_severity=critical');
    expect(last()).toMatchObject({ search: '' });
  });

  // 5.378.0 — the list is paged (it was "Load 50 more": thirteen clicks for
  // 636 issues, and a reload started again from the top).
  describe('pages', () => {
    const many = (from: number) => ({
      items: Array.from({ length: 50 }, (_, i) => ({ ...ISSUE, issue_key: `title:issue ${from + i}`, title: `Issue ${from + i}` })),
      total: 120,
    });
    beforeEach(() => {
      mocked.getObservationIssues.mockImplementation(async (_p: number, f: { skip?: number }) => many(f.skip ?? 0));
    });

    it('opens on the page the address names, and Next asks for the following one and writes it', async () => {
      const router = await open('/observations?page=2');
      expect(last()).toMatchObject({ skip: 50, limit: 50 });
      expect(screen.getByText('Issue 50')).toBeInTheDocument();
      expect(screen.getByText('51–100 of 120')).toBeInTheDocument();
      expect(screen.getByText('page 2 of 3')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Next 50 observations' }));
      await pass(20);
      expect(router.state.location.search).toBe('?page=3');
      expect(router.state.historyAction).toBe('REPLACE');
      expect(last()).toMatchObject({ skip: 100 });
      expect(screen.getByText('Issue 100')).toBeInTheDocument();
      expect(screen.queryByText('Issue 50')).not.toBeInTheDocument();
    });

    it('a search starts from the first page in the same address write', async () => {
      const router = await open('/observations?page=2');
      mocked.getObservationIssues.mockClear();
      type('ssh');
      await pass(350);
      expect(router.state.location.search).toBe('?obs_search=ssh');
      // One read, of the first page — never page 2 of the new search first.
      expect(asked()).toEqual([expect.objectContaining({ search: 'ssh', limit: 50 })]);
      expect(last()).not.toHaveProperty('skip');
    });

    it('an issue ticked on one page is still selected, and counted, on the next', async () => {
      await open('/observations');
      fireEvent.click(screen.getByRole('checkbox', { name: 'Select Issue 0' }));
      expect(screen.getByTestId('observations-bulk-slot')).toHaveTextContent('1 observation selected');
      fireEvent.click(screen.getByRole('button', { name: 'Next 50 observations' }));
      await pass(20);
      expect(screen.getByText('Issue 50')).toBeInTheDocument();
      expect(screen.getByTestId('observations-bulk-slot')).toHaveTextContent('1 observation selected');
    });

    it('the inspector steps past the page edge: the next page opens on its first issue', async () => {
      const router = await open(`/observations?issue=${encodeURIComponent('title:issue 49')}`);
      expect(screen.getByText('50 of 120')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Next observation' }));
      await pass(50);
      expect(new URLSearchParams(router.state.location.search).get('page')).toBe('2');
      expect(new URLSearchParams(router.state.location.search).get('issue')).toBe('title:issue 50');
      expect(screen.getByText('51 of 120')).toBeInTheDocument();
    });
  });

  it('opening an issue is a history entry: Back closes the inspector and keeps the filters', async () => {
    const router = await open('/observations?obs_severity=critical');
    fireEvent.click(screen.getByRole('button', { name: /^Open SMB Signing not required/ }));
    await pass(20);
    expect(router.state.location.search).toBe(`?obs_severity=critical&issue=${encodeURIComponent(ISSUE.issue_key).replace(/%20/g, '+')}`);
    expect(router.state.historyAction).toBe('PUSH');
    expect(screen.getByTestId('observation-inspector')).toBeInTheDocument();

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(router.state.location.search).toBe('?obs_severity=critical');
    expect(screen.queryByTestId('observation-inspector')).not.toBeInTheDocument();
  });

  it('a link to the same view with another search, and Back, re-seed the box; the list follows and the address is left alone', async () => {
    const router = await open('/observations?obs_search=first');
    expect(box().value).toBe('first');

    await act(async () => { await router.navigate('/observations?obs_search=linked&obs_min=5'); });
    await pass(20);
    expect(box().value).toBe('linked');
    expect(last()).toMatchObject({ search: 'linked', minHosts: 5 });
    await pass(1000);
    expect(router.state.location.search).toBe('?obs_search=linked&obs_min=5');
    expect(last()).toMatchObject({ search: 'linked', minHosts: 5 });

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(box().value).toBe('first');
    expect(last()).toMatchObject({ search: 'first', minHosts: 1 });
    await pass(1000);
    expect(router.state.location.search).toBe('?obs_search=first');
    expect(last()).toMatchObject({ search: 'first', minHosts: 1 });
  });
});
