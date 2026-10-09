/**
 * The tool reference page, after it stopped carrying its own catalogue.
 *
 * The 61 tools it documents used to be a hardcoded array here — a second list
 * the backend could not see, which had already drifted from the one that
 * actually gates agents. These tests pin the properties that make the migration
 * worth having: the page renders whatever the registry returns (including a
 * category nobody curated, so a vetted-in suggestion can't vanish), it shows
 * each tool's agent policy rather than implying everything documented is
 * runnable, and rows with no install command or URL still render.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ToolReference from '../../pages/ToolReference';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { ToolRegistryEntry } from '../../services/api/references';

const getToolRegistry = vi.fn();
const updateToolRegistryEntry = vi.fn();
vi.mock('../../services/api', () => ({
  getToolRegistry: () => getToolRegistry(),
  updateToolRegistryEntry: (...args: unknown[]) => updateToolRegistryEntry(...args),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const hasRole = vi.fn();
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasRole: (role: string) => hasRole(role) }),
}));

const tool = (over: Partial<ToolRegistryEntry> = {}): ToolRegistryEntry => ({
  name: 'nmap',
  description: 'Port and service scanner.',
  category: 'Port Scanning',
  ports: '1-65535',
  install: 'apt install nmap',
  url: 'https://nmap.org/',
  kali: true,
  status: 'reference',
  phases: ['discovery'],
  intrusive: false,
  requires_privileges: true,
  output_format: 'xml',
  ingestible: true,
  suggested_rationale: null,
  ...over,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <ToolReference />
      </TooltipProvider>
    </MemoryRouter>,
  );

describe('ToolReference', () => {
  beforeEach(() => {
    getToolRegistry.mockReset();
    updateToolRegistryEntry.mockReset();
    hasRole.mockReset();
    hasRole.mockReturnValue(false);
    getToolRegistry.mockResolvedValue({ count: 1, tools: [tool()] });
  });

  it('renders the registry rather than a built-in list', async () => {
    getToolRegistry.mockResolvedValue({
      count: 2,
      tools: [tool(), tool({ name: 'testssl', category: 'Web Analysis', ports: '443' })],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    expect(screen.getByText('testssl')).toBeInTheDocument();
    // Each category is a jump target of the page's picker.
    const bar = await screen.findByRole('navigation', { name: 'Tool categories' });
    expect(within(bar).getByRole('combobox', { name: 'Jump to a section' })).toBeInTheDocument();
    expect(within(document.getElementById('category-port-scanning')!).getByText('nmap')).toBeInTheDocument();
    expect(within(document.getElementById('category-web-analysis')!).getByText('testssl')).toBeInTheDocument();
  });

  // 5.313.0 — a catalogue, not agent policy: no row says an agent may (or may
  // not) run it; only a pending suggestion or a declined one is marked.
  it('is a catalogue: no agent-permission badge, only suggested and declined rows are marked', async () => {
    getToolRegistry.mockResolvedValue({
      count: 2,
      tools: [tool(), tool({ name: 'socat', status: 'rejected', category: 'Port Scanning' })],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    const catalogued = document.getElementById('tool-row-nmap')!;
    const declined = document.getElementById('tool-row-socat')!;

    expect(within(declined).getByText('Declined')).toBeInTheDocument();
    expect(within(catalogued).queryByText(/Agent-approved|Reference only|Declined|Suggested/)).toBeNull();
    expect(screen.queryByText(/Agent-approved/)).toBeNull();
    expect(screen.getByText(/does not decide what an agent\s+may run/)).toBeInTheDocument();
  });

  it('links a tool BlueStick imports to what it reads from it (v5.296.0)', async () => {
    getToolRegistry.mockResolvedValue({
      count: 2,
      tools: [tool(), tool({ name: 'socat', ingestible: false })],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    expect(within(document.getElementById('tool-row-nmap')!).getByRole('link', { name: 'What BlueStick reads' }))
      .toHaveAttribute('href', '/reference/tool-coverage?tool=nmap');
    expect(within(document.getElementById('tool-row-socat')!).queryByRole('link', { name: 'What BlueStick reads' }))
      .toBeNull();
  });

  it('renders a suggestion with its rationale under a category nobody curated', async () => {
    getToolRegistry.mockResolvedValue({
      count: 1,
      tools: [
        tool({
          name: 'ligolo-ng',
          category: 'Uncategorised',
          status: 'suggested',
          suggested_rationale: 'Needed for pivoting nothing in the catalogue covers.',
          install: null,
          url: null,
          ports: null,
        }),
      ],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('ligolo-ng')).toBeInTheDocument());
    // The category is not in the curated order — it must still render, or a
    // vetted-in suggestion would silently disappear from the page.
    expect(screen.getByText('Uncategorised')).toBeInTheDocument();
    const row = document.getElementById('tool-row-ligolo-ng')!;
    expect(within(row).getByText('Suggested')).toBeInTheDocument();
    expect(within(row).getByText(/Needed for pivoting/)).toBeInTheDocument();
    // No install command and no URL are ordinary states for a suggestion.
    expect(within(row).getByText('No install command recorded')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /ligolo-ng/ })).not.toBeInTheDocument();
  });

  it('offers vetting to admins only, and surfaces what is waiting', async () => {
    const suggestion = tool({
      name: 'ligolo-ng',
      status: 'suggested',
      suggested_rationale: 'Pivoting nothing in the catalogue can do.',
    });
    getToolRegistry.mockResolvedValue({ count: 2, tools: [tool(), suggestion] });

    const { unmount } = renderPage();
    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    // A non-admin sees the suggestion, but no way to act on it — the
    // catalogue is shared by every project in the deployment.
    expect(screen.getByText('ligolo-ng')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
    expect(screen.queryByText(/awaiting review/)).not.toBeInTheDocument();
    unmount();

    hasRole.mockReturnValue(true);
    renderPage();
    await waitFor(() => expect(screen.getByText(/1 tool awaiting review/)).toBeInTheDocument());
    // The banner names it, so a pending ask isn't something you find by
    // scrolling the catalogue.
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();
  });

  it('says so when the catalogue cannot be loaded', async () => {
    getToolRegistry.mockRejectedValue(new Error('boom'));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText('Could not load the tool catalogue.')).toBeInTheDocument(),
    );
  });
});
