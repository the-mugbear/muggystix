/**
 * Oversight's project multi-select: a subset scopes every figure (the
 * request carries exactly those ids), lives in the URL (`?projects=`), and is
 * named in the lead sentence and the copied summary.
 */
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const dashboardMock = vi.fn();
vi.mock('../../services/api', () => ({
  getOversightDashboard: (...a: unknown[]) => dashboardMock(...a),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], selectProject: vi.fn() }),
}));

import Oversight from '../../pages/Oversight';
import { describeProjects, parseProjectIds, serializeProjectIds } from '../../utils/oversightProjects';

const sev = () => ({ critical: 0, high: 0, medium: 0, low: 0 });
const LONG = 'a-project-with-a-very-long-engagement-name-'.repeat(5);
const OPTIONS = [
  { id: 1, name: 'Alpha', status: 'active' },
  { id: 2, name: 'Bravo', status: 'active' },
  { id: 3, name: LONG, status: 'completed' },
  { id: 4, name: 'Delta', status: 'active' },
];

const response = {
  window: { start: null, end: null }, generated_at: new Date().toISOString(), severity_basis: 'current',
  growth: { unit: 'day', points: [] },
  summary: {
    projects_total: 2, projects_in_progress: 2, projects_complete: 0,
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
  attention: { critical_projects: 0, pending_approval_plans: 0, blocked_runs: 0, no_admin_projects: 0, quiet_projects: 0, no_inventory_projects: 0 },
  accounts: { total: 1, enabled: 1, disabled: 0, without_membership: 0 },
  projects: [], testers: [], project_options: OPTIONS, tester_options: [],
};

// setupTests mocks useLocation globally; the search params are real.
const Where = () => <span data-testid="search">{useSearchParams()[0].toString()}</span>;
const search = () => new URLSearchParams(screen.getByTestId('search').textContent ?? '');

const renderAt = async (url: string) => {
  render(<MemoryRouter initialEntries={[url]}><Oversight /><Where /></MemoryRouter>);
  await screen.findByText('Needs attention now');
};

const lastQuery = () => dashboardMock.mock.calls[dashboardMock.mock.calls.length - 1][0];

describe('Oversight — project subset', () => {
  beforeEach(() => { dashboardMock.mockReset().mockResolvedValue(response); });

  it('choosing projects asks for exactly those and puts them in the URL', async () => {
    await renderAt('/oversight');
    expect(lastQuery().project_id).toEqual([]);                       // default: every project
    fireEvent.click(screen.getByRole('button', { name: 'Projects: All projects (4)' }));
    const list = await screen.findByRole('list', { name: 'Projects' });
    fireEvent.click(within(list).getByRole('checkbox', { name: /Delta/ }));
    fireEvent.click(within(list).getByRole('checkbox', { name: /Alpha/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(lastQuery().project_id).toEqual([1, 4]));
    expect(search().get('projects')).toBe('1,4');
    expect(screen.getByRole('button', { name: 'Projects: 2 of 4 projects' })).toBeTruthy();
    expect(await screen.findByText(/These figures cover 2 of 4 projects: Alpha and Delta\./)).toBeTruthy();
  });

  it('a shared URL opens with its subset; search, select-all and clear act on what is shown', async () => {
    await renderAt('/oversight?projects=2,3');
    expect(lastQuery().project_id).toEqual([2, 3]);
    const trigger = screen.getByRole('button', { name: 'Projects: 2 of 4 projects' });
    fireEvent.click(trigger);
    fireEvent.change(await screen.findByLabelText('Search projects'), { target: { value: 'ta' } });
    const list = screen.getByRole('list', { name: 'Projects' });
    expect(within(list).getAllByRole('checkbox')).toHaveLength(1);    // Delta only
    fireEvent.click(screen.getByRole('button', { name: 'Select all shown' }));
    expect(screen.getByText('3 of 4 selected')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Search projects'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByText('None selected = every project')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(lastQuery().project_id).toEqual([]));
    expect(search().has('projects')).toBe(false);
  });

  it('choosing every project is the same as none, and Cancel changes nothing', async () => {
    await renderAt('/oversight?projects=1');
    fireEvent.click(screen.getByRole('button', { name: 'Projects: Alpha' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Select all' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(lastQuery().project_id).toEqual([1]);
    fireEvent.click(screen.getByRole('button', { name: 'Projects: Alpha' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Select all' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(lastQuery().project_id).toEqual([]));
  });

  it('an old single-project link still opens as a subset of one', async () => {
    await renderAt('/oversight?project=3');
    expect(lastQuery().project_id).toEqual([3]);
    const trigger = screen.getByRole('button', { name: `Projects: ${LONG}` });
    expect(trigger.querySelector('.truncate')).toBeTruthy();          // a long name cannot widen the strip
  });
});

describe('oversightProjects', () => {
  it('parses, de-duplicates, sorts and drops junk', () => {
    expect(parseProjectIds(new URLSearchParams('projects=9,1,x,,1,-2,0,4'))).toEqual([1, 4, 9]);
    expect(parseProjectIds(new URLSearchParams(''))).toEqual([]);
  });
  it('serialises a subset; every project or none drops the parameter', () => {
    expect(serializeProjectIds([4, 1], 5)).toBe('1,4');
    expect(serializeProjectIds([], 5)).toBeNull();
    expect(serializeProjectIds([1, 2, 3], 3)).toBeNull();
  });
  it('names a subset briefly', () => {
    const names = new Map([[1, 'A'], [2, 'B'], [3, 'C'], [4, 'D'], [5, 'E']]);
    expect(describeProjects([1], names)).toBe('A');
    expect(describeProjects([1, 2], names)).toBe('A and B');
    expect(describeProjects([1, 2, 3], names)).toBe('A, B and C');
    expect(describeProjects([1, 2, 3, 4, 5], names)).toBe('A, B and 3 more');
    expect(describeProjects([7], names)).toBe('#7');
  });
});
