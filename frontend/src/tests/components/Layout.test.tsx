/**
 * App shell (v5.294.0, UX review): the browser title, the topbar's project,
 * which sidebar entry says "you are here", and when the tab strip shows.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';

// The real router hooks: setupTests pins useLocation to "/".
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const notifications = vi.hoisted(() => ({
  getNotifications: vi.fn(), markNotificationsRead: vi.fn(), markAllNotificationsRead: vi.fn(),
}));
vi.mock('../../services/api', () => ({
  getUnreadNotificationCount: vi.fn().mockResolvedValue(0),
  getNotifications: (...a: unknown[]) => notifications.getNotifications(...a),
  markNotificationsRead: (...a: unknown[]) => notifications.markNotificationsRead(...a),
  markAllNotificationsRead: (...a: unknown[]) => notifications.markAllNotificationsRead(...a),
}));
vi.mock('../../components/AgentActivityRail', () => ({ default: () => null }));
vi.mock('../../components/ProjectSelector', () => ({ default: () => <div>project selector</div> }));
vi.mock('../../components/CommandPalette', () => ({ default: () => null }));
vi.mock('../../components/UserMenu', () => ({ default: () => null }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasPermission: (role: string) => role !== 'admin' || isAdmin, isAuthenticated: true }),
}));
let project: { id: number; name: string; status: string; my_role?: string } | null = null;
let isAdmin = false;
let otherProjects: { id: number; name: string; status: string; my_role?: string }[] = [];
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project, projects: [...(project ? [project] : []), ...otherProjects] }),
}));
let remediationTracked = false;
vi.mock('../../hooks/useRemediationPolicy', () => ({
  useRemediationPolicy: () => ({ enabled: remediationTracked, error: null, retry: vi.fn() }),
}));
vi.mock('../../contexts/ThemeContext', () => ({
  useAppTheme: () => ({ themeName: 'light', setThemeName: vi.fn(), availableThemes: [] }),
}));

import Layout from '../../components/Layout';

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Layout><p>page</p></Layout>
    </MemoryRouter>,
  );

const sidebar = () => within(screen.getAllByRole('navigation', { name: 'Primary navigation' })[0]);

describe('Layout shell', () => {
  beforeEach(() => {
    project = { id: 1, name: 'Demo — Insights Eval', status: 'active' };
    isAdmin = false;
    otherProjects = [];
    remediationTracked = false;
    document.title = 'BlueStick';
  });

  // Analyst walkthrough 2026-10-10: the entry was offered to everyone, and
  // for someone who administers no project the page's whole content was
  // "You administer no project, so there is nothing to follow up here".
  it('offers Remediation deadlines only to someone who administers a project, where remediation is tracked', () => {
    // Beside Portfolio, above the project's own navigation.
    const offered = () => screen.queryAllByRole('link', { name: 'Remediation deadlines' }).length > 0;
    project = { id: 1, name: 'Demo', status: 'active', my_role: 'analyst' };
    remediationTracked = true;
    const first = renderAt('/hosts');
    expect(offered()).toBe(false);
    first.unmount();

    // An admin of ANOTHER project than the one on screen.
    otherProjects = [{ id: 2, name: 'Theirs', status: 'active', my_role: 'admin' }];
    const second = renderAt('/hosts');
    expect(offered()).toBe(true);
    second.unmount();

    // A global administrator administers every project.
    otherProjects = [];
    isAdmin = true;
    const third = renderAt('/hosts');
    expect(offered()).toBe(true);
    third.unmount();

    // Not tracked: nobody is offered it.
    remediationTracked = false;
    renderAt('/hosts');
    expect(offered()).toBe(false);
  });

  // 5.387.0 (walkthrough 2026-10-12): the bell went to the selected
  // project's Collaboration page, so reading another project's deadline alert
  // took the reader off the cross-project page they were working on.
  it('the bell lists the notifications over the page the reader is on, and one opens its own list', async () => {
    notifications.getNotifications.mockResolvedValue({ notifications: [{
      id: 5, type: 'remediation', title: 'Other project: 2 findings on hosts are due within 7 days', body: null,
      source_type: 'remediation_due_soon', source_id: 9, host_id: null, finding_id: null,
      created_at: '2026-10-11T10:00:00Z',
    }] });
    notifications.markNotificationsRead.mockResolvedValue(undefined);
    isAdmin = true;
    remediationTracked = true;
    const Where = () => { const l = useLocation(); return <p data-testid="where">{l.pathname}{l.search}</p>; };
    render(
      <MemoryRouter initialEntries={['/remediation-deadlines?view=contacts']}>
        <Layout><Where /></Layout>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }));
    const row = await screen.findByRole('button', { name: /Other project: 2 findings on hosts are due within 7 days/ });
    // Nothing moved, and nothing was marked read by looking.
    expect(screen.getByTestId('where')).toHaveTextContent('/remediation-deadlines?view=contacts');
    expect(notifications.markNotificationsRead).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Open Collaboration' })).toBeInTheDocument();
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId('where'))
      .toHaveTextContent('/remediation-deadlines?state=due_soon&project=9'));
    expect(notifications.markNotificationsRead).toHaveBeenCalledWith([5]);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Open Collaboration' })).not.toBeInTheDocument());
  });

  it('names the page and the project in the browser title', () => {
    renderAt('/hosts');
    expect(document.title).toBe('Hosts · Demo — Insights Eval · BlueStick');
  });

  it('shows the project once, without "Active project (active)" or a second switcher', () => {
    renderAt('/hosts');
    expect(screen.getByTestId('topbar-project-name').textContent).toBe('Demo — Insights Eval');
    expect(screen.queryByText(/active project/i)).toBeNull();
    expect(screen.queryByText('Active')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Switch project' })).toBeNull();
  });

  it('shows the status only when it is news', () => {
    project = { id: 1, name: 'Demo', status: 'on_hold' };
    renderAt('/hosts');
    expect(screen.getByText(/on hold/i)).toBeTruthy();
  });

  it('names no project on a cross-project page', () => {
    renderAt('/portfolio');
    expect(screen.queryByTestId('topbar-project-name')).toBeNull();
    expect(document.title).toBe('Portfolio · BlueStick');
  });

  it('a 404 marks no sidebar entry as the current page', () => {
    renderAt('/no-such-page');
    expect(sidebar().queryAllByRole('link', { current: 'page' })).toHaveLength(0);
    expect(document.title).toBe('Page not found · Demo — Insights Eval · BlueStick');
  });

  it('a detail page marks its hub', () => {
    renderAt('/findings/37');
    expect(sidebar().getByRole('link', { current: 'page' }).textContent).toBe('Findings');
  });

  it('a hub with one page shows no tab strip; one with several does', () => {
    const { unmount } = renderAt('/activity');
    expect(screen.queryByRole('navigation', { name: 'Collaboration sections' })).toBeNull();
    unmount();
    renderAt('/hosts');
    const strip = screen.getByRole('navigation', { name: 'Inventory sections' });
    // Ingestion Results is part of the Scans page's import history (5.418.0),
    // not a tab of its own; Scanner observations is (5.404.0), and Query (5.405.0).
    expect(within(strip).getAllByRole('link').map((a) => a.textContent)).toEqual(
      ['Hosts', 'Names', 'Scanner observations', 'Query', 'Scans', 'Scope'],
    );
  });

  // Review 2026-10-01 — the server lets every member READ the scope, so a
  // viewer keeps the Scope tab (the page hides its own write controls).
  it('a project viewer or auditor keeps Scope and Scans', () => {
    for (const my_role of ['viewer', 'auditor']) {
      project = { id: 1, name: 'Demo', status: 'active', my_role };
      const { unmount } = renderAt('/hosts');
      const strip = screen.getByRole('navigation', { name: 'Inventory sections' });
      expect(within(strip).getAllByRole('link').map((a) => a.textContent), my_role).toEqual(
        ['Hosts', 'Names', 'Scanner observations', 'Query', 'Scans', 'Scope'],
      );
      unmount();
    }
  });

  it('Administration is for global administrators only', () => {
    renderAt('/hosts');
    expect(sidebar().queryByRole('link', { name: 'Administration' })).toBeNull();
  });
});
