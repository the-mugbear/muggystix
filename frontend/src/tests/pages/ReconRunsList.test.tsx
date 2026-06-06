/**
 * Tests for the v3 alpha.12 Recon Runs list page.
 *
 * Pins: rows render, status filter narrows, checkbox multi-select
 * caps at 2 and enables the Compare button which navigates to the
 * compare URL.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({}),
  };
});

vi.mock('../../services/api', () => ({
  listReconSessions: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import ReconRunsList from '../../pages/ReconRunsList';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderPage() {
  return render(
    <MemoryRouter>
      <ReconRunsList />
</MemoryRouter>,
  );
}

const recons = [
  {
    id: 42, project_id: 1, scope_id: 7, scope_name: 'Internal /24',
    status: 'completed', started_at: '2026-05-15T10:00:00Z',
    completed_at: '2026-05-15T11:00:00Z',
    generated_by_model: 'claude-opus-4-7', generated_by_tool: 'claude-code',
    started_by_username: 'alice',
    uploads_submitted: 2, scans_ingested: 2,
    hosts_discovered: 12, ports_discovered: 47,
  },
  {
    id: 43, project_id: 1, scope_id: 8, scope_name: 'DMZ /27',
    status: 'active', started_at: '2026-05-15T12:00:00Z',
    completed_at: null,
    generated_by_model: 'gpt-5-codex', generated_by_tool: 'codex',
    started_by_username: 'bob',
    uploads_submitted: 1, scans_ingested: 0,
    hosts_discovered: 0, ports_discovered: 0,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  // v2.86.10 — wrapper returns {items, total} now.
  mockedApi.listReconSessions.mockResolvedValue({ items: recons, total: recons.length });
});

describe('ReconRunsList page', () => {
  it('renders one row per recon session', async () => {
    renderPage();
    // jsdom renders both desktop table + mobile card list (no viewport
    // media queries) — scope to the desktop table to match the rest of
    // this file (v2.86.10).  ``getByText`` would throw on the duplicate.
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#42')).toBeInTheDocument();
      expect(within(screen.getByRole('table')).getByText('#43')).toBeInTheDocument();
    });
  });

  it('Compare button activates only when exactly 2 are selected', async () => {
    renderPage();
    // v4.39.0: the list now renders a desktop table AND a mobile card
    // list (only one visible per viewport in a browser; jsdom shows
    // both). Scope row/checkbox queries to the desktop <table> so they
    // match a single layout.
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#42')).toBeInTheDocument();
    });
    const checkboxes = within(screen.getByRole('table')).getAllByRole('checkbox');
    // Tick first row only — Compare still disabled.
    fireEvent.click(checkboxes[0]);
    const compareBtn = screen.getByRole('button', { name: /Compare/ });
    expect(compareBtn).toBeDisabled();
    // Tick second row — Compare enables.
    fireEvent.click(checkboxes[1]);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Compare selected \(2\)/ })).toBeEnabled();
    });
  });

  it('Compare button navigates to /recon/compare?a=&b=', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#42')).toBeInTheDocument();
    });
    const checkboxes = within(screen.getByRole('table')).getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.click(screen.getByRole('button', { name: /Compare selected/ }));
    // Active row sorts first; recon #43 (active) before #42 (completed).
    // First click → 43, second click → 42.
    expect(navigateSpy).toHaveBeenCalledWith('/recon/compare?a=43&b=42');
  });

  it('clicking a row opens the recon detail page', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#42')).toBeInTheDocument();
    });
    // The row is a NavigableTableRow that wraps cell content in an
    // `<a href="/recon/runs/42">` link via react-router's <Link>.
    // navigateSpy stubs useNavigate(), but <Link> uses history
    // navigation directly — so asserting on the spy doesn't fire.
    // Assert on the link's href instead (v2.86.10).
    const link = screen.getByRole('link', { name: /Open recon run 42/i });
    expect(link).toHaveAttribute('href', '/recon/runs/42');
  });

  it('status filter chip re-fetches with the chosen status', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#42')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /^Active$/ }));
    await waitFor(() => {
      const calls = mockedApi.listReconSessions.mock.calls.map((c) => c[0]);
      expect(calls.some((c: any) => c?.status === 'active')).toBe(true);
    });
  });
});
