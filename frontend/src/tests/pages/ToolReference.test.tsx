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
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ToolReference from '../../pages/ToolReference';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { ToolRegistryEntry } from '../../services/api/references';

const getToolRegistry = vi.fn();
const getToolReadiness = vi.fn();
vi.mock('../../services/api/references', () => ({
  getToolRegistry: () => getToolRegistry(),
  getToolReadiness: () => getToolReadiness(),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const tool = (over: Partial<ToolRegistryEntry> = {}): ToolRegistryEntry => ({
  name: 'nmap',
  description: 'Port and service scanner.',
  category: 'Port Scanning',
  ports: '1-65535',
  install: 'apt install nmap',
  url: 'https://nmap.org/',
  kali: true,
  status: 'approved',
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
    <TooltipProvider>
      <ToolReference />
    </TooltipProvider>,
  );

describe('ToolReference', () => {
  beforeEach(() => {
    getToolRegistry.mockReset();
    getToolReadiness.mockReset();
    getToolRegistry.mockResolvedValue({ count: 1, tools: [tool()] });
    // The readiness panel is a separate concern; give it the "never probed"
    // shape so it renders its own empty state and stays out of the way.
    getToolReadiness.mockResolvedValue({
      has_probe: false,
      summary: { installed: 0, missing: 0, warn: 0, unknown: 0, total: 0 },
      tools: [],
    });
  });

  it('renders the registry rather than a built-in list', async () => {
    getToolRegistry.mockResolvedValue({
      count: 2,
      tools: [tool(), tool({ name: 'testssl', category: 'Web Analysis', ports: '443' })],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    expect(screen.getByText('testssl')).toBeInTheDocument();
  });

  it('shows each tool’s agent policy, so documented does not read as runnable', async () => {
    getToolRegistry.mockResolvedValue({
      count: 2,
      tools: [tool(), tool({ name: 'socat', status: 'reference', category: 'Port Scanning' })],
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('nmap')).toBeInTheDocument());
    const approvedRow = document.getElementById('tool-row-nmap')!;
    const referenceRow = document.getElementById('tool-row-socat')!;

    expect(within(approvedRow).getByText('Agent-approved')).toBeInTheDocument();
    expect(within(referenceRow).getByText('Reference only')).toBeInTheDocument();
  });

  it('renders a suggestion with its rationale under a category nobody curated', async () => {
    getToolRegistry.mockResolvedValue({
      count: 1,
      tools: [
        tool({
          name: 'ligolo-ng',
          category: 'Uncategorised',
          status: 'suggested',
          suggested_rationale: 'Needed for pivoting the approved set does not cover.',
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

  it('says so when the catalogue cannot be loaded', async () => {
    getToolRegistry.mockRejectedValue(new Error('boom'));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText('Could not load the tool catalogue.')).toBeInTheDocument(),
    );
  });
});
