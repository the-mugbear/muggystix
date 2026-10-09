/**
 * "Scanner observations and scope" on Posture (5.330.0).
 *
 * It was Operations' "Exposure" block; these are the guards its describe in
 * `tests/pages/Operations.test.tsx` carried — the three scope states add up
 * and each opens its list, scanner observations by severity are shown, a
 * failed count says so — plus what the move added: it loads for itself, each
 * half fails on its own, and it no longer points at Posture.
 */
import React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({ getDashboardStats: vi.fn(), getProjectCoverage: vi.fn() }));

import * as api from '../../services/api';
import ExposureSection from '../../components/posture/ExposureSection';
import { TooltipProvider } from '../../components/ui/tooltip';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const emptyStats = {
  total_scans: 0, total_hosts: 0, total_ports: 0, up_hosts: 0, open_ports: 0, total_subnets: 0,
  recent_scans: [], subnet_stats: [],
};
const stats = {
  ...emptyStats,
  vulnerability_stats: {
    critical: 9, high: 154, medium: 20, low: 4, info: 900, hosts_with_vulnerabilities: 130,
    hosts_by_severity: { critical: 7, high: 122, medium: 15, low: 4 },
  },
};
const coverage = {
  project_id: 1, total_hosts: 142, total_scopes: 1,
  scopes: [{ scope_id: 10, scope_name: 'Internal /24', subnet_count: 1, total_scoped_ips: 256, discovered_in_scope: 42, coverage_percent: 16.4 }],
  hosts_in_subnet_scope: 128, hosts_name_scope_only: 2, hosts_outside_scope: 12,
};

/** The page's Refresh, as the page does it (5.351.0): it names the reads that
 *  are out of date — there is no `refreshKey` prop to bump. */
const PageRefresh: React.FC = () => {
  const queryClient = useQueryClient();
  return (
    <button type="button" onClick={() => {
      for (const name of ['getDashboardStats', 'getProjectCoverage']) {
        void queryClient.invalidateQueries({ queryKey: [name] });
      }
    }}>
      Refresh the page
    </button>
  );
};

const renderIt = () => render(
  <MemoryRouter><TooltipProvider><PageRefresh /><ExposureSection /></TooltipProvider></MemoryRouter>,
);
const q = (el: HTMLElement) => new URL(el.getAttribute('href') ?? '', 'https://x').searchParams.get('q');

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getDashboardStats.mockResolvedValue(stats);
  mocked.getProjectCoverage.mockResolvedValue(coverage);
});

describe('Posture — scanner observations and scope', () => {
  it('the three scope states add up to every host, each opening its own list', async () => {
    renderIt();
    // 128 + 2 + 12 = 142; no scope names.
    const scopeLine = (await screen.findByText('Scope')).parentElement as HTMLElement;
    expect(q(within(scopeLine).getByRole('link', { name: /128 in scope subnets/ }))).toBe('scope:subnet');
    expect(q(within(scopeLine).getByRole('link', { name: /2 reached only through an in-scope name/ }))).toBe('scope:name');
    expect(q(within(scopeLine).getByRole('link', { name: /12 outside scope/ }))).toBe('scope:none');
    expect(screen.queryByText('Internal /24')).not.toBeInTheDocument();
    // On Posture it does not point at Posture.
    expect(screen.queryByRole('link', { name: /Posture/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Findings' })).toHaveAttribute('href', '/findings');
  });

  it('a state of zero is said as zero, without a link to an empty list', async () => {
    mocked.getProjectCoverage.mockResolvedValue({ ...coverage, hosts_name_scope_only: 0, hosts_outside_scope: undefined });
    renderIt();
    const scopeLine = (await screen.findByText('Scope')).parentElement as HTMLElement;
    expect(within(scopeLine).getAllByRole('link')).toHaveLength(1);
    expect(scopeLine).toHaveTextContent('0 reached only through an in-scope name');
    expect(scopeLine).toHaveTextContent('0 outside scope');
  });

  it('with no scope declared it says so and links to Scope', async () => {
    mocked.getProjectCoverage.mockResolvedValue({ ...coverage, total_scopes: 0, scopes: [] });
    renderIt();
    expect(await screen.findByText(/No scope is declared, so every host is outside scope/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Register a scope' })).toHaveAttribute('href', '/scopes');
  });

  it('shows scanner observations by severity — they are counted nowhere else', async () => {
    renderIt();
    expect(await screen.findByRole('heading', { name: /Scanner observations by severity/, level: 3 })).toBeInTheDocument();
    const total = screen.getByRole('link', { name: /187 observations, informational excluded/ });
    expect(total).toHaveTextContent('130 hosts carry one');
    expect(q(total)).toBe('kind:vulnerability,misconfiguration,informational');
  });

  it('with no observation recorded it says what would populate it', async () => {
    mocked.getDashboardStats.mockResolvedValue(emptyStats);
    renderIt();
    expect(await screen.findByText(/No critical, high, medium or low scanner observation is recorded/)).toBeInTheDocument();
  });

  it('a failed count says so — never an empty bar — and the scope half still shows; Retry reloads', async () => {
    mocked.getDashboardStats.mockRejectedValue(new Error('stats down'));
    renderIt();
    expect(await screen.findByText(/Scanner observations could not be counted — this is not a clean project/)).toBeInTheDocument();
    expect(screen.queryByText(/No critical, high, medium or low/)).not.toBeInTheDocument();
    expect(await screen.findByText('Scope')).toBeInTheDocument();

    mocked.getDashboardStats.mockResolvedValue(stats);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/187 observations, informational excluded/)).toBeInTheDocument();
  });

  it('a failed scope count says so — never "every host is outside scope"', async () => {
    mocked.getProjectCoverage.mockRejectedValue(new Error('coverage down'));
    renderIt();
    expect(await screen.findByText(/Scope coverage could not be counted/)).toBeInTheDocument();
    expect(screen.queryByText(/No scope is declared/)).not.toBeInTheDocument();
    expect(await screen.findByText(/187 observations/)).toBeInTheDocument();
  });

  it('reloads when the page refreshes', async () => {
    renderIt();
    await screen.findByText('Scope');
    await screen.findByText(/187 observations/);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh the page' }));
    await waitFor(() => expect(mocked.getDashboardStats).toHaveBeenCalledTimes(2));
    expect(mocked.getProjectCoverage).toHaveBeenCalledTimes(2);
  });
});
