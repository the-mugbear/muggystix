/**
 * Hosts' tool-ready export: a query (`q`) or the search box chooses HOSTS and
 * every open port of those hosts is listed; only the Ports filter narrows
 * ports (the server's rule, pinned by
 * `test_review_2026_10_10_hosts_reads.py::test_a_query_chooses_hosts_and_narrows_no_port`).
 * The dialog says so — once, and only when a query or a search is applied
 * without a Ports filter (owner, 2026-10-22).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getToolReadyOutput: vi.fn() }));
vi.mock('../../services/api', () => api);

import ToolReadyOutput from '../../components/ToolReadyOutput';
import { TooltipProvider } from '../../components/ui/tooltip';

const NOTE = 'A query chooses hosts; every open port of those hosts is listed — use the Ports filter to list only some ports.';

const dialog = (filters: Record<string, string | boolean | undefined>) => render(
  <TooltipProvider>
    <ToolReadyOutput open onClose={() => {}} filters={filters} totalHosts={3} />
  </TooltipProvider>,
);

describe('ToolReadyOutput — what a query does to the ports', () => {
  it('says it when a query chooses the hosts and no Ports filter is applied', () => {
    dialog({ q: 'port:445', sort_by: 'ip_address' });
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it('says it for the search box too', () => {
    dialog({ search: 'web01' });
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it('does not say it when the Ports filter narrows the ports', () => {
    dialog({ q: 'port:445', ports: '445' });
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it('does not say it with no query and no search', () => {
    dialog({ state: 'up', q: '  ' });
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });
});
