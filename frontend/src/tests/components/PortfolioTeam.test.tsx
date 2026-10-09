/**
 * Portfolio's Team view (defect 1.21): one section with a table, a row per
 * member — not a card per member — and the workload figure named for what it
 * counts.  `open_tasks` is the host tests assigned to the member that are
 * still to do (proposed or in progress; `portfolio.py`): work is host tests,
 * there are no "tasks".
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({ getPortfolioTeam: vi.fn() }));

import * as api from '../../services/api';
import PortfolioTeam from '../../components/PortfolioTeam';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const LONG = `Dr ${'Wolfeschlegelstein '.repeat(10)}`.trim();
const project = (id: number, role = 'analyst', name = `Project ${id}`) => ({ project_id: id, project_name: name, role });
const member = (over: Record<string, unknown>) => ({
  user_id: 1, username: 'ana', full_name: 'Ana Ortiz', project_count: 1, projects: [project(1, 'admin', 'Acme')],
  open_tasks: 0, hosts_in_review: 0, ...over,
});

const TEAM = [
  member({ open_tasks: 2, hosts_in_review: 1 }),
  member({
    user_id: 2, username: 'ben', full_name: null, open_tasks: 12, hosts_in_review: 3, project_count: 7,
    projects: [1, 2, 3, 4, 5, 6, 7].map((i) => project(i)),
  }),
  member({ user_id: 3, username: 'cy', full_name: LONG, projects: [project(9, 'viewer', 'P'.repeat(200))] }),
];

const dataRows = () => within(screen.getByRole('table', { name: 'Team workload' })).getAllByRole('row').slice(1);

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getPortfolioTeam.mockResolvedValue({ members: TEAM, total_members: TEAM.length });
});

describe('PortfolioTeam', () => {
  it('is one table with a row per member, busiest first — no card per member', async () => {
    const { container } = render(<PortfolioTeam />);
    await screen.findByRole('table', { name: 'Team workload' });
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(container.querySelector('.bg-card')).toBeNull();
    expect(container.querySelector('.shadow-raised')).toBeNull();

    const rows = dataRows();
    expect(rows).toHaveLength(3);
    // ben: 12 + 3, Ana: 2 + 1, then the member with nothing.
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'ben7 projects', 'Ana Ortiz@ana · 1 project', `${LONG}@cy · 1 project`,
    ]);
  });

  it('names the workload for what it counts: tests assigned, never "tasks"', async () => {
    const { container } = render(<PortfolioTeam />);
    const table = await screen.findByRole('table', { name: 'Team workload' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Member', 'Projects', 'Tests assigned', 'Hosts in review',
    ]);
    expect(container.textContent).not.toMatch(/task/i);
  });

  it('keeps every figure: each member\'s two counts and the team\'s totals', async () => {
    render(<PortfolioTeam />);
    await screen.findByRole('table', { name: 'Team workload' });
    const cells = (r: HTMLElement) => within(r).getAllByRole('cell').slice(2).map((c) => c.textContent);
    expect(dataRows().map(cells)).toEqual([['12', '3'], ['2', '1'], ['0', '0']]);

    const strip = screen.getByTestId('team-measures');
    expect(strip).toHaveTextContent(/Members.*3/);
    expect(strip).toHaveTextContent(/Tests assigned.*14/);
    expect(strip).toHaveTextContent(/Hosts in review.*4/);
  });

  it('lists a member\'s projects with the role, the first five and how many more', async () => {
    render(<PortfolioTeam />);
    await screen.findByRole('table', { name: 'Team workload' });
    const [ben, ana] = dataRows();
    expect(within(ana).getByTitle('Admin on Acme')).toHaveTextContent('Acme· Admin');
    expect(within(ben).getAllByTitle(/^Analyst on Project \d$/)).toHaveLength(5);
    expect(within(ben).getByTitle('Project 6 · Analyst, Project 7 · Analyst')).toHaveTextContent('+2');
  });

  it('truncates a long name and a long project name, with the whole text as the title', async () => {
    render(<PortfolioTeam />);
    await screen.findByRole('table', { name: 'Team workload' });
    const name = screen.getByText(LONG);
    expect(name).toHaveAttribute('title', LONG);
    expect(name.className).toMatch(/\btruncate\b/);
    const projectName = screen.getByText('P'.repeat(200));
    expect(projectName.className).toMatch(/\btruncate\b/);
    expect(projectName.closest('[title]')).toHaveAttribute('title', `Viewer on ${'P'.repeat(200)}`);
    // Fixed layout inside its own scroller: a long value cannot widen the page.
    const table = screen.getByRole('table', { name: 'Team workload' });
    expect(table.style.tableLayout).toBe('fixed');
    expect(table.parentElement?.className).toMatch(/overflow-x-auto/);
  });

  it('says an empty team in words, with no table', async () => {
    mocked.getPortfolioTeam.mockResolvedValue({ members: [], total_members: 0 });
    render(<PortfolioTeam />);
    expect(await screen.findByText('Nobody is a member of any of your projects yet.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('a failed load is said, with Retry — never an empty team', async () => {
    mocked.getPortfolioTeam.mockRejectedValueOnce(new Error('boom'));
    render(<PortfolioTeam />);
    expect(await screen.findByText(/boom|Failed to load the team roster/)).toBeInTheDocument();
    expect(screen.queryByText(/Nobody is a member/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByRole('table', { name: 'Team workload' })).toBeInTheDocument();
  });
});
