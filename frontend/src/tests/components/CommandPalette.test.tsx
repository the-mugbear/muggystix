/**
 * Command palette — finding search (v5.294.0, UX review).
 *
 * "default creds" and "#37" returned "No matches": the palette searched hosts,
 * plans and scans but never findings, and its placeholder named only pages,
 * projects and themes.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

// cmdk measures its list; jsdom has no ResizeObserver.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

const api = vi.hoisted(() => ({
  getHosts: vi.fn(),
  getScans: vi.fn(),
  getTestPlans: vi.fn(),
  listFindings: vi.fn(),
  getFinding: vi.fn(),
}));
vi.mock('../../services/api', () => api);
// The real router hooks: setupTests stubs useNavigate / pins useLocation.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasPermission: () => true, logout: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], currentProject: { id: 1, name: 'Demo' }, selectProject: vi.fn() }),
}));
vi.mock('../../contexts/ThemeContext', () => ({
  useAppTheme: () => ({ themeName: 'light', setThemeName: vi.fn(), availableThemes: [] }),
}));

import CommandPalette from '../../components/CommandPalette';

const finding = (id: number, title: string) => ({ id, title, severity: 'critical', status: 'confirmed' });

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderPalette() {
  return render(
    <MemoryRouter initialEntries={['/operations']}>
      <CommandPalette open onOpenChange={() => undefined} />
      <Routes><Route path="*" element={<Where />} /></Routes>
    </MemoryRouter>,
  );
}

describe('CommandPalette finding search', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getHosts.mockResolvedValue({ items: [] });
    api.getScans.mockResolvedValue([]);
    api.getTestPlans.mockResolvedValue([]);
    api.listFindings.mockResolvedValue({ items: [], total: 0 });
    api.getFinding.mockRejectedValue(new Error('404'));
  });

  it('says it searches hosts and findings', () => {
    renderPalette();
    const input = screen.getByRole('combobox');
    expect(input.getAttribute('placeholder')).toMatch(/hosts/);
    expect(input.getAttribute('placeholder')).toMatch(/findings/);
  });

  it('finds a finding by title and opens it', async () => {
    api.listFindings.mockResolvedValue({ items: [finding(37, 'Critical finding — default creds')], total: 1 });
    renderPalette();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'default creds' } });
    const row = await screen.findByText('Critical finding — default creds', {}, { timeout: 2000 });
    expect(api.listFindings).toHaveBeenCalledWith(expect.objectContaining({ search: 'default creds' }), expect.anything());
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/findings/37'));
  });

  it('finds a finding by its number, "#37" or a single digit', async () => {
    api.getFinding.mockImplementation(async (id: number) => finding(id, `Finding number ${id}`));
    renderPalette();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '#37' } });
    expect(await screen.findByText('Finding number 37', {}, { timeout: 2000 })).toBeTruthy();
    expect(api.getFinding).toHaveBeenCalledWith(37);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '7' } });
    expect(await screen.findByText('Finding number 7', {}, { timeout: 2000 })).toBeTruthy();
    // One digit searches findings only — not every host with a 7 in its IP.
    expect(api.getHosts).not.toHaveBeenCalledWith(expect.objectContaining({ search: '7' }), expect.anything());
  });
});
