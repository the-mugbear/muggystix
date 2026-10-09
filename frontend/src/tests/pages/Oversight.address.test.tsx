/**
 * Oversight — the project table's search, sort and page live in the address
 * (`?q=`, `?sort=`, `?page=`), beside the filters that already did — under
 * the REAL router (setupTests replaces `useNavigate` / `useLocation` for
 * every other file).
 *
 * They were component state: a reload, a shared link and Back from a
 * project opened the table unsearched, in the default order, on page 1.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

const dashboardMock = vi.fn();
vi.mock('../../services/api', () => ({
  getOversightDashboard: (...a: unknown[]) => dashboardMock(...a),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], selectProject: vi.fn() }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Oversight from '../../pages/Oversight';
import { TooltipProvider } from '../../components/ui/tooltip';

const sev = () => ({ critical: 0, high: 0, medium: 0, low: 0 });
const pad = (i: number) => String(i).padStart(2, '0');
// Sixty projects — three pages of 25.  By name: 01 … 60.  By hosts: 60 … 01.
const project = (i: number) => ({
  id: i, name: `Project ${pad(i)}`, status: 'active', start_date: null, end_date: null, admins: ['Ada'],
  host_count: i, hosts_tested: 0, hosts_in_review: 0, hosts_reviewed: 0,
  findings: sev(), finding_states: { under_investigation: 0, confirmed: 0, closed: 0 },
  findings_false_positive: 0, finding_affected_targets: 0,
  observations: sev(), observations_judged: sev(), observations_unjudged: sev(),
  defect_rate: sev(), last_scan_at: null, targets_added: 0, reviews_concluded: 0,
  imports: 0, contributors: 0, attention_reasons: [],
});
const response = {
  window: { start: null, end: null }, generated_at: new Date().toISOString(), severity_basis: 'current',
  growth: { unit: 'day', points: [] },
  summary: {
    projects_total: 60, projects_in_progress: 60, projects_complete: 0,
    targets_current: 10, targets_through_end: 10, targets_added: 0, targets_tested: 4,
    targets_in_review: 1, targets_reviewed: 3, reviews_concluded: 0, imports: 0, contributors: 0,
    unattributed_events: 0,
    severity: {
      findings: sev(), finding_states: { under_investigation: 0, confirmed: 0, closed: 0 },
      findings_false_positive: 0, finding_affected_targets: 0, observations: sev(),
      observations_judged: sev(), observations_unjudged: sev(), tested_targets: 4,
      defect_targets: sev(), defect_rate: sev(),
    },
  },
  attention: { critical_projects: 0, no_admin_projects: 0, quiet_projects: 0, no_inventory_projects: 0 },
  accounts: { total: 1, enabled: 1, disabled: 0, without_membership: 0 },
  projects: Array.from({ length: 60 }, (_, k) => project(k + 1)),
  testers: [], project_options: [], tester_options: [],
};

const settle = async (ms = 450) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const open = async (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/oversight', element: <TooltipProvider><Oversight /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  await screen.findByRole('table', { name: 'All projects in the cohort' });
  return router;
};
const box = () => screen.getByRole('searchbox', { name: 'Search projects in this table' }) as HTMLInputElement;
const sortBox = () => screen.getByRole('combobox', { name: 'Sort projects' });
/** The projects listed, in order. */
const names = () => within(screen.getByRole('table', { name: 'All projects in the cohort' }))
  .getAllByRole('row').map((r) => r.textContent?.match(/Project \d{2}/)?.[0]).filter(Boolean);
const range = (from: number, to: number) => {
  const step = from <= to ? 1 : -1;
  return Array.from({ length: Math.abs(to - from) + 1 }, (_, k) => `Project ${pad(from + step * k)}`);
};

beforeEach(() => { dashboardMock.mockReset().mockResolvedValue(response); });

// The custom dates were state seeded once from the address (through a
// memoised `range`, so the lint entry of the time did not see it): after Back
// or a link to another period the boxes showed the dates the reader had left,
// over figures for the period in the address.
describe('Oversight — the custom period boxes follow the address (real router)', () => {
  const start = () => screen.getByLabelText('Start (UTC)') as HTMLInputElement;
  const end = () => screen.getByLabelText('End (UTC)') as HTMLInputElement;

  it('a link to another custom period, and Back, change the boxes and what is asked', async () => {
    const router = await open('/oversight?tab=projects&range=custom&start=2026-01-01&end=2026-01-31');
    expect(start().value).toBe('2026-01-01');
    expect(end().value).toBe('2026-01-31');

    await act(async () => { await router.navigate('/oversight?tab=projects&range=custom&start=2026-03-01&end=2026-03-31'); });
    await waitFor(() => expect(start().value).toBe('2026-03-01'));
    expect(end().value).toBe('2026-03-31');
    await waitFor(() => expect(dashboardMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ start: '2026-03-01', end: '2026-03-31' }), expect.anything(),
    ));

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(start().value).toBe('2026-01-01'));
    expect(end().value).toBe('2026-01-31');
  });

  it('a date being composed is the reader’s until Apply, and Apply writes both', async () => {
    const router = await open('/oversight?tab=projects&range=custom&start=2026-01-01&end=2026-01-31');
    fireEvent.change(end(), { target: { value: '2026-02-15' } });
    expect(end().value).toBe('2026-02-15');
    expect(router.state.location.search).toContain('end=2026-01-31');
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(router.state.location.search).toContain('end=2026-02-15'));
    expect(start().value).toBe('2026-01-01');
    expect(end().value).toBe('2026-02-15');
  });
});

describe('Oversight — the project table follows the address (real router)', () => {
  it('opens on what the address says: the search box, the sort and the page', async () => {
    await open('/oversight?tab=projects&q=project&sort=targets&page=2');
    expect(box().value).toBe('project');
    expect(sortBox()).toHaveTextContent('Most hosts');
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();
    expect(names()).toEqual(range(35, 11));
  });

  it('a reload on page 3 shows page 3 and writes nothing', async () => {
    const router = await open('/oversight?tab=projects&page=3');
    expect(screen.getByText('Page 3 of 3')).toBeInTheDocument();
    expect(names()).toEqual(range(51, 60));
    await settle();
    expect(router.state.location.search).toBe('?tab=projects&page=3');
    expect(names()).toEqual(range(51, 60));
    // The table is local: one read of the dashboard, whatever the table shows.
    expect(dashboardMock).toHaveBeenCalledTimes(1);
  });

  it('the pager writes the address (replace)', async () => {
    const router = await open('/oversight?tab=projects');
    expect(names()).toEqual(range(1, 25));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(router.state.location.search).toBe('?tab=projects&page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(names()).toEqual(range(26, 50));
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(router.state.location.search).toBe('?tab=projects');
    expect(names()).toEqual(range(1, 25));
  });

  it('typing a search writes it once the typing stops — page dropped, the rest kept — and the table follows', async () => {
    const router = await open('/oversight?tab=projects&sort=name&page=3');
    fireEvent.change(box(), { target: { value: ' Project 1 ' } });
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects&sort=name&q=Project+1'));
    expect(router.state.historyAction).toBe('REPLACE');
    expect(names()).toEqual(range(10, 19));
    expect(screen.getByText('10 of 60 projects · search is local to this table')).toBeInTheDocument();

    fireEvent.change(box(), { target: { value: '' } });
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects&sort=name'));
    expect(names()).toEqual(range(1, 25));
    expect(dashboardMock).toHaveBeenCalledTimes(1);
  });

  it('choosing a sort writes it and drops the page; the default order is left out', async () => {
    const user = userEvent.setup();
    const router = await open('/oversight?tab=projects&page=2');
    await user.click(sortBox());
    await user.click(await screen.findByRole('option', { name: 'Most hosts' }));
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects&sort=targets'));
    expect(router.state.historyAction).toBe('REPLACE');
    expect(names()).toEqual(range(60, 36));

    await user.click(sortBox());
    await user.click(await screen.findByRole('option', { name: 'Most critical, then high' }));
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects'));
    expect(dashboardMock).toHaveBeenCalledTimes(1);
  });

  it('a sort the address cannot mean is the default order', async () => {
    await open('/oversight?tab=projects&sort=nonsense');
    expect(sortBox()).toHaveTextContent('Most critical, then high');
    expect(names()).toEqual(range(1, 25));
  });

  it('a page past the end shows the last page', async () => {
    await open('/oversight?tab=projects&page=40');
    expect(screen.getByText('Page 3 of 3')).toBeInTheDocument();
    expect(names()).toEqual(range(51, 60));
  });

  it('a filter of the whole page drops the table page and keeps its search and sort', async () => {
    const router = await open('/oversight?tab=projects&q=project&sort=name&page=2');
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects&q=project&sort=name&overlap=1'));
  });

  it('a link to the same page with other table state, and Back, change the table; nothing writes the old state back', async () => {
    const router = await open('/oversight?tab=projects&q=Project+0');
    expect(box().value).toBe('Project 0');
    expect(names()).toEqual(range(1, 9));

    await act(async () => { await router.navigate('/oversight?tab=projects&sort=targets&page=3'); });
    expect(box().value).toBe('');
    expect(sortBox()).toHaveTextContent('Most hosts');
    expect(names()).toEqual(range(10, 1));
    await settle();
    expect(router.state.location.search).toBe('?tab=projects&sort=targets&page=3');
    expect(names()).toEqual(range(10, 1));

    await act(async () => { await router.navigate(-1); });
    expect(box().value).toBe('Project 0');
    expect(sortBox()).toHaveTextContent('Most critical, then high');
    expect(names()).toEqual(range(1, 9));
    await settle();
    expect(router.state.location.search).toBe('?tab=projects&q=Project+0');
  });

  it('Back while something half-typed is pending drops the half-typed text, not the address', async () => {
    const router = await open('/oversight?tab=projects&q=Project+0');
    await act(async () => { await router.navigate('/oversight?tab=projects&q=Project+1'); });
    fireEvent.change(box(), { target: { value: 'Project 1 and mo' } });
    await act(async () => { await router.navigate(-1); });
    await settle();
    expect(box().value).toBe('Project 0');
    expect(router.state.location.search).toBe('?tab=projects&q=Project+0');
  });

  it('Reset clears the search, the sort and the page with the filters, in one write that stays', async () => {
    const router = await open('/oversight?tab=projects&status=active&q=Project+1&sort=name&page=2');
    // Something half-typed must not come back after the reset either.
    fireEvent.change(box(), { target: { value: 'Project 12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(router.state.location.search).toBe('');
    await settle();
    expect(router.state.location.search).toBe('');

    await act(async () => { await router.navigate('/oversight?tab=projects'); });
    expect(box().value).toBe('');
    expect(names()).toEqual(range(1, 25));
  });
});
