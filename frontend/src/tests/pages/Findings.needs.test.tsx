/**
 * Findings — what a finding still needs before the report, on its row, and
 * the filter that lists only those.
 *
 * The server decides the needs and writes each sentence (`finding_needs`);
 * the page shows them under the title — each a link to the section of the
 * finding where it is met — and keeps the filter in the address (`?needs=`).
 *
 * Real router: the filter lives in the address.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  listFindings: vi.fn(),
  setFindingStatus: vi.fn(),
  listProjectMembers: vi.fn(),
  bulkSetFindingStatus: vi.fn(),
  bulkAssignFindings: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester' }, hasPermission: () => true }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import Findings from '../../pages/Findings';
import { TooltipProvider } from '../../components/ui/tooltip';
import { needsFilterOf, rowNeeds } from '../../utils/findingNeeds';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const LONG = 'report text missing: '.concat('recommendation-'.repeat(20));
const finding = (id: number, needs: unknown) => ({
  id, project_id: 1, title: `Finding ${id}`, severity: 'high', status: 'confirmed', source: 'manual',
  owner_id: null, owner_name: null, evidence_annotation_id: null, vuln_id: null, host_count: 0, hosts: [],
  created_at: '2026-08-01T00:00:00Z', updated_at: null, needs,
});
const ROWS = [
  finding(1, [
    { kind: 'missing_text', text: 'report text missing: impact', count: 1 },
    { kind: 'uncaptioned_images', text: '2 report images without a caption', count: 2 },
    { kind: 'proposals', text: '1 proposal to decide', count: 1 },
  ]),
  finding(2, []),
  finding(3, null),                       // an older server: no field at all
  finding(4, [{ kind: 'missing_text', text: LONG, count: 1 }]),
];

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/findings', element: <TooltipProvider><Findings /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const chooser = () => screen.getByRole('combobox', { name: 'Still needed before the report' });
const lastAsked = () => {
  const [projectId, filters] = mocked.listFindings.mock.calls[mocked.listFindings.mock.calls.length - 1];
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listFindings.mockResolvedValue({ items: ROWS, total: ROWS.length, severity_counts: {}, owner_counts: [] });
  mocked.listProjectMembers.mockResolvedValue([]);
});

describe('Findings — what a finding still needs', () => {
  it('says each need under the title, as a link to the section where it is met', async () => {
    open('/findings');
    const line = await screen.findByTestId('finding-needs-1');
    const links = within(line).getAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual([
      'report text missing: impact', '2 report images without a caption', '1 proposal to decide',
    ]);
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/findings/1#section-report-text', '/findings/1#section-comments', '/findings/1#section-proposals',
    ]);
    // Nothing owed, or a server that does not say: no line at all.
    expect(screen.queryByTestId('finding-needs-2')).toBeNull();
    expect(screen.queryByTestId('finding-needs-3')).toBeNull();
    // A long sentence wraps inside its cell.
    expect(screen.getByTestId('finding-needs-4')).toHaveClass('break-words');
  });

  it('a link from a filtered list carries the way back, before the section', async () => {
    open('/findings?severity=high');
    const line = await screen.findByTestId('finding-needs-1');
    expect(within(line).getAllByRole('link')[0].getAttribute('href')).toBe(
      `/findings/1?from=${encodeURIComponent('/findings?severity=high')}#section-report-text`,
    );
  });

  it('the filter is in the address, asks the list for it and starts from the first page', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open('/findings?status=all&page=3');
    await screen.findByText('Finding 1');
    expect(lastAsked().needs).toBeUndefined();
    await user.click(chooser());
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual([
      'Report readiness: any', 'Needs anything', 'Needs report text', 'Needs an image caption', 'Has proposals to decide',
    ]);
    await user.click(screen.getByRole('option', { name: 'Needs an image caption' }));
    await waitFor(() => expect(lastAsked()).toMatchObject({ needs: 'uncaptioned_images', offset: 0 }));
    expect(router.state.location.search).toBe('?status=all&needs=uncaptioned_images');
    expect(chooser()).toHaveTextContent('Needs an image caption');
  });

  // 5.395.0 — the project's pending retests are this list (Operations links
  // here): everyone's, whoever owns the finding.
  it('the host filter is in the address and asks the list for findings with a host in that state', async () => {
    open('/findings?host_state=retest&status=all');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ host_state: 'retest' });
    expect(screen.getByRole('combobox', { name: 'Hosts' })).toHaveTextContent('A host awaiting a retest');
  });

  it('a host state it does not know is no filter', async () => {
    open('/findings?host_state=nonsense');
    await screen.findByText('Finding 1');
    expect(lastAsked().host_state).toBeUndefined();
  });

  it('opens on the filter the address names; a word it does not know is no filter', async () => {
    open('/findings?needs=any');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ needs: 'any' });
    expect(chooser()).toHaveTextContent('Needs anything');
  });

  it('reads the address and a row through one small module', () => {
    expect(needsFilterOf(null)).toBe('all');
    expect(needsFilterOf('captions')).toBe('all');
    expect(needsFilterOf('proposals')).toBe('proposals');
    // Operations' "under investigation" is the row's Status here.
    expect(rowNeeds([
      { kind: 'under_investigation', text: 'under investigation', count: 1 },
      { kind: 'proposals', text: '1 proposal to decide', count: 1 },
    ]).map((n) => n.kind)).toEqual(['proposals']);
    expect(rowNeeds(null)).toEqual([]);
  });
});
