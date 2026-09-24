/**
 * The catch-all route (v5.290.0): an unknown URL used to render an empty
 * layout.  It now names the path and offers the ways back; /projects goes
 * where the projects are.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// The real location hook: setupTests pins useLocation to "/".
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

let isAdmin = false;
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasPermission: (role: string) => (role === 'admin' ? isAdmin : true) }),
}));

import NotFound, { ProjectsRedirect } from '../../pages/NotFound';

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/operations" element={<p>Operations page</p>} />
        <Route path="/settings/projects" element={<p>All projects page</p>} />
        <Route path="/portfolio" element={<p>Portfolio page</p>} />
        <Route path="/projects" element={<ProjectsRedirect />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => { isAdmin = false; });

describe('Not found', () => {
  it('names the unknown path and links back to Operations and the projects', () => {
    renderAt('/network-topology?x=1');
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByText('/network-topology?x=1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to Operations' })).toHaveAttribute('href', '/operations');
    expect(screen.getByRole('link', { name: 'Your projects (Portfolio)' })).toHaveAttribute('href', '/portfolio');
  });

  it('sends a global administrator to All projects', () => {
    isAdmin = true;
    renderAt('/nope');
    expect(screen.getByRole('link', { name: 'All projects' })).toHaveAttribute('href', '/settings/projects');
  });

  it('redirects /projects to All projects for an administrator, Portfolio otherwise', () => {
    isAdmin = true;
    const { unmount } = renderAt('/projects');
    expect(screen.getByText('All projects page')).toBeInTheDocument();
    unmount();
    isAdmin = false;
    renderAt('/projects');
    expect(screen.getByText('Portfolio page')).toBeInTheDocument();
  });

  it('is wired as the last route of the protected layout', () => {
    const src = readFileSync(join(__dirname, '..', '..', 'App.tsx'), 'utf8');
    const paths = [...src.matchAll(/path="([^"]+)"/g)].map((m) => m[1]);
    expect(paths.slice(-2)).toEqual(['/projects', '*']);
  });
});
