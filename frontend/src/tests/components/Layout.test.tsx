/**
 * App shell (v5.294.0, UX review): the browser title, the topbar's project,
 * which sidebar entry says "you are here", and when the tab strip shows.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// The real router hooks: setupTests pins useLocation to "/".
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
vi.mock('../../services/api', () => ({ getUnreadNotificationCount: vi.fn().mockResolvedValue(0) }));
vi.mock('../../components/AgentActivityRail', () => ({ default: () => null }));
vi.mock('../../components/ProjectSelector', () => ({ default: () => <div>project selector</div> }));
vi.mock('../../components/CommandPalette', () => ({ default: () => null }));
vi.mock('../../components/UserMenu', () => ({ default: () => null }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasPermission: (role: string) => role !== 'admin' || isAdmin, isAuthenticated: true }),
}));
let project: { id: number; name: string; status: string } | null = null;
let isAdmin = false;
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: project }) }));
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
    document.title = 'BlueStick';
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
    expect(within(strip).getAllByRole('link').map((a) => a.textContent)).toEqual(
      ['Hosts', 'Names', 'Scans', 'Ingestion Results', 'Scope'],
    );
  });

  it('Administration is for global administrators only', () => {
    renderAt('/hosts');
    expect(sidebar().queryByRole('link', { name: 'Administration' })).toBeNull();
  });
});
