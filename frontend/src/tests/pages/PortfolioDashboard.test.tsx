/**
 * Portfolio (v5.275.0) — the Posture layout: a lead sentence, four measures,
 * the projects as a table worst first.  Two severity representations per
 * project (findings and scanner observations nobody has judged yet); review in
 * one vocabulary that adds up; the health reason in words, not on hover.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const dashboardMock = vi.fn();
vi.mock('../../services/api', () => ({
  getPortfolioDashboard: (...a: unknown[]) => dashboardMock(...a),
  getPortfolioTeam: vi.fn().mockResolvedValue({ members: [] }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], selectProject: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true }),
}));

import PortfolioDashboard from '../../pages/PortfolioDashboard';
import type { ProjectCard } from '../../services/api/portfolio';

const sev = (critical = 0, high = 0, medium = 0, low = 0) => ({ critical, high, medium, low });
const LONG = 'engagement-with-a-very-long-name-'.repeat(6);

const card = (over: Partial<ProjectCard>): ProjectCard => ({
  id: 1, name: 'P', slug: 'p', status: 'active', host_count: 10, up_host_count: 10,
  open_port_count: 0, scan_count: 1, days_since_last_scan: 1, is_stale: false,
  unreviewed_hosts: 7, hosts_tested: 5, hosts_in_review: 2, hosts_reviewed: 3,
  findings: sev(), unjudged_observations: sev(), health: 'healthy', attention_reasons: [],
  pending_plan_reviews: 0, open_tasks: 0, active_sessions: 0, blocked_sessions: 0,
  member_count: 1, user_role: 'admin', last_scan_at: '2026-09-20T10:00:00Z',
  ...over,
});

const projects = [
  card({ id: 3, name: 'All judged' }),
  card({ id: 2, name: 'Untriaged critical', unjudged_observations: sev(3, 0, 5), health: 'critical',
    attention_reasons: ['critical_unjudged'] }),
  card({ id: 1, name: LONG, findings: sev(1, 2), health: 'critical', pending_plan_reviews: 1,
    attention_reasons: ['critical_findings', 'pending_review'] }),
];

const summary = {
  total_projects: 3, active_projects: 3, total_hosts: 30, total_open_ports: 0, total_scans: 3,
  total_reviewed: 9, total_in_review: 6,
  findings: sev(1, 2), unjudged_observations: sev(3, 0, 5),
  stale_projects: 0,
  pending_approvals_total: 1, blocked_sessions_total: 0,
};

const renderPage = async () => {
  render(<MemoryRouter><PortfolioDashboard /></MemoryRouter>);
  await screen.findByText('All judged');
};

describe('Portfolio', () => {
  beforeEach(() => {
    dashboardMock.mockReset().mockResolvedValue({ summary, projects });
  });

  it('leads with a sentence and four measures, never a card per number', async () => {
    await renderPage();
    // A finding is already a judgement: only the scanner output is "not yet judged".
    expect(screen.getByText('2 of 3 projects have a critical finding or critical scanner output not yet judged.')).toBeInTheDocument();
    expect(screen.queryByText(/nobody has judged/)).not.toBeInTheDocument();
    expect(screen.getByText('9 of 30 hosts with review concluded and 6 in review, across 3 projects.')).toBeInTheDocument();
    // Named so it cannot be read as Oversight's "Targets tested" (in review + reviewed).
    expect(screen.getByText('Hosts with review concluded')).toBeInTheDocument();
    expect(screen.queryByText('Hosts reviewed')).not.toBeInTheDocument();
    expect(screen.getByText('6 in review · 15 not started')).toBeInTheDocument();
    expect(screen.getByText(/3 critical\/high scanner observations not yet judged/)).toBeInTheDocument();
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
  });

  it('lists projects worst first with the reason in words and review that adds up', async () => {
    await renderPage();
    const table = screen.getByRole('table', { name: /worst first/ });
    const names = within(table).getAllByRole('row').slice(1).map((r) => r.getAttribute('data-project-id'));
    expect(names).toEqual(['2', '1', '3']); // 3 unjudged critical, then 1 critical finding, then neither
    expect(within(table).getByText(/3 critical scanner observations not yet judged/)).toBeInTheDocument();
    expect(within(table).getByText(/— 1 critical finding$/)).toBeInTheDocument();
    expect(within(table).getByText('3 findings: 1 critical · 2 high')).toBeInTheDocument();
    expect(within(table).getAllByText('3 of 10 reviewed')).toHaveLength(3);
    expect(within(table).getAllByText('2 in review · 5 not started')).toHaveLength(3);
    expect(within(table).getByText('1 plan to approve')).toBeInTheDocument();
    // A long name truncates with its full value available.
    expect(within(table).getByTitle(`Open ${LONG}`)).toHaveClass('truncate');
  });

  // At ~1500px the Waiting header and "36 in review · 351 not started" wrapped
  // while Hosts (a single number) held 12% of the width.
  it('sizes the short columns to their content and keeps review and the Waiting header on one line', async () => {
    await renderPage();
    const table = screen.getByRole('table', { name: /worst first/ });
    const col = (name: string) => table.querySelector(`col[data-col="${name}"]`) as HTMLElement;
    expect(col('hosts').style.width).toBe('5rem');
    expect(col('review').style.width).toBe('14rem');
    expect(col('waiting').style.width).toBe('16rem');
    expect(col('found').style.width).toBe(''); // takes the remaining width
    // v5.289.0 — the last rebalance left "What testing found" narrow. At a
    // 1500px content width (16px rem) it must be the widest column.
    const px = (w: string, total: number) => (w.endsWith('%') ? (parseFloat(w) / 100) * total : parseFloat(w) * 16);
    const fixed = ['project', 'review', 'hosts', 'waiting'].map((c) => px(col(c).style.width, 1500));
    const found = 1500 - fixed.reduce((a, b) => a + b, 0);
    expect(found).toBeGreaterThan(Math.max(...fixed));
    expect(found).toBeGreaterThanOrEqual(640); // the longest reason line on one line
    expect(within(table).getByRole('columnheader', { name: /Waiting · last import/ })).toHaveClass('whitespace-nowrap');
    expect(within(table).getAllByText('2 in review · 5 not started')[0]).toHaveClass('whitespace-nowrap');
  });

  it('a measure filters the table to the projects it counts; Reset shows them all', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: /1 plan to approve/ }));
    let table = screen.getByRole('table', { name: /worst first/ });
    expect(within(table).getAllByRole('row')).toHaveLength(2); // header + the one project
    // Reset, then the "With critical" option via the URL-backed filter.
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    table = screen.getByRole('table', { name: /worst first/ });
    expect(within(table).getAllByRole('row')).toHaveLength(4);
  });

  it('says what is NOT in progress instead of repeating the total', async () => {
    await renderPage();
    expect(screen.getByText('None completed')).toBeInTheDocument();
    expect(screen.queryByText(/in total/)).not.toBeInTheDocument();
  });

  it('counts the completed projects and names the quiet rule as project activity', async () => {
    dashboardMock.mockReset().mockResolvedValue({
      summary: { ...summary, total_projects: 5, active_projects: 3, stale_projects: 1 },
      projects,
    });
    await renderPage();
    expect(screen.getByText(/^2 completed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1 active, no import in 14 days' })).toBeInTheDocument();
  });

  it('a project with nothing critical or high says so once', async () => {
    dashboardMock.mockReset().mockResolvedValue({
      summary,
      projects: [card({ id: 3, name: 'All judged' }), card({ id: 4, name: 'Empty', host_count: 0 })],
    });
    await renderPage();
    const table = screen.getByRole('table', { name: /worst first/ });
    expect(within(table).getAllByText('No critical or high')).toHaveLength(2);
    expect(within(table).queryByText(/Nothing critical or high/)).not.toBeInTheDocument();
    // Only the empty project needs the extra line.
    expect(within(table).getAllByText('No hosts imported yet.')).toHaveLength(1);
  });

  it('every waiting item is the same outlined chip, open tasks included', async () => {
    dashboardMock.mockReset().mockResolvedValue({
      summary,
      projects: [card({ id: 5, name: 'Busy', pending_plan_reviews: 1, blocked_sessions: 1, active_sessions: 1, open_tasks: 1 })],
    });
    render(<MemoryRouter><PortfolioDashboard /></MemoryRouter>);
    await screen.findByText('Busy');
    const chips = within(screen.getByTestId('waiting-chips')).getAllByText(/./);
    expect(chips.map((c) => c.textContent)).toEqual(['1 plan to approve', '1 blocked run', '1 active run', '1 open task']);
    for (const chip of chips) {
      expect(chip.className).toMatch(/\bborder-(warning|destructive|info|border)\b/);
      expect(chip.className).not.toMatch(/\bbg-/);
    }
  });
});
