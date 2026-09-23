/**
 * /scopes in the Posture layout (v5.269.0): one sentence of where the hosts
 * stand, one strip of measures, sections without cards — and no "Correlate
 * Hosts" button (every write path correlates by itself).
 */
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';

vi.mock('../../services/api', () => ({
  getDefaultScope: vi.fn(),
  getScopeCoverage: vi.fn(),
  uploadSubnetFile: vi.fn(),
  addScopeSubnets: vi.fn(),
  updateSubnet: vi.fn(),
  deleteSubnet: vi.fn(),
  listSubnetLabels: vi.fn().mockResolvedValue([]),
  bulkApplySubnetLabel: vi.fn(),
  listScopeDomains: vi.fn().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 100, names_in_scope_total: 0 }),
  addScopeDomains: vi.fn(),
  deleteScopeDomain: vi.fn(),
}));
const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
vi.mock('../../hooks/useReconPlan', () => ({ useReconPlan: () => ({ openFor: vi.fn() }) }));
// Dialogs that are closed on load; not under test here.
vi.mock('../../components/StartReconDialog', () => ({ default: () => null }));
vi.mock('../../components/ScopeExport', () => ({ default: () => null }));
vi.mock('../../components/OutOfScopeExport', () => ({ default: () => null }));
vi.mock('../../components/SiteManagerDialog', () => ({ default: () => null }));
vi.mock('../../components/SubnetLabelManager', () => ({
  SubnetLabelManagerDialog: () => null,
  SubnetLabelEditorPopover: ({ children }: { children: React.ReactNode }) => children,
  SubnetLabelChip: () => null,
}));

import * as api from '../../services/api';
import Scopes, { scopeLead } from '../../pages/Scopes';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const coverage = {
  total_scopes: 1,
  total_subnets: 3,
  total_domains: 1,
  name_reachable_hosts: 1,
  total_hosts: 29,
  scoped_hosts: 27,
  out_of_scope_hosts: 1,
  coverage_percentage: 93.1,
  has_scope_configuration: true,
  recent_out_of_scope_hosts: [
    { host_id: 9, ip_address: '198.51.100.9', hostname: 's09-out-of-scope.eval.test', last_seen: '2026-09-21T20:49:37Z', last_scan_id: 3, last_scan_filename: null },
  ],
  top_technologies: [],
};

const scope = {
  id: 1,
  name: 'Eval scope',
  subnets_total: 1,
  subnets: [
    { id: 11, cidr: '10.77.1.0/24', description: 'East segment', site: 'East', host_count: 7, labels: [], created_at: '2026-09-22T00:00:00Z' },
  ],
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <Scopes />
      </TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getDefaultScope.mockResolvedValue(scope);
  mocked.getScopeCoverage.mockResolvedValue(coverage);
  mocked.listSubnetLabels.mockResolvedValue([]);
});

describe('Scopes page — Posture layout (v5.269.0)', () => {
  it('opens with one sentence of where the hosts stand', async () => {
    renderPage();
    expect(await screen.findByText(
      '27 of 29 hosts are inside scoped subnets; 1 reached only through an in-scope name; 1 outside every scope.',
    )).toBeInTheDocument();
  });

  it('has no card and no "Correlate Hosts" button', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    expect(screen.queryByRole('button', { name: /Correlate/ })).toBeNull();
  });

  it('shows one strip of measures, out of scope linking to its hosts', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    const oos = screen.getByRole('link', { name: 'Out-of-scope hosts — view' });
    expect(oos).toHaveTextContent('1');
    expect(oos.getAttribute('href')).toContain('out_of_scope_only=true');
    for (const label of ['In scope', 'Via an in-scope name', 'Out of scope', 'Subnets', 'Domains']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // No badge soup: the counts are not repeated as "N DOMAINS"-style chips.
    expect(screen.queryByText(/% covered/)).toBeNull();
  });

  it('lists hosts outside every scope without telling anyone to scan them', async () => {
    renderPage();
    const heading = await screen.findByText('Hosts outside every scope');
    const section = heading.closest('section')!;
    expect(within(section).getByText('198.51.100.9')).toBeInTheDocument();
    expect(within(section).getByText(/Confirm whether they are in scope/)).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/\bscan (these|them)\b/i);
  });
});

describe('scopeLead', () => {
  it('is clear when nothing is out of scope, neutral before anything is declared', () => {
    expect(scopeLead({ ...coverage, out_of_scope_hosts: 0, name_reachable_hosts: 0 })).toEqual({
      sentence: '27 of 29 hosts are inside scoped subnets.', tone: 'clear',
    });
    expect(scopeLead({ ...coverage, total_subnets: 0, total_domains: 0 }).tone).toBe('neutral');
    expect(scopeLead({ ...coverage, total_hosts: 0 }).sentence)
      .toBe('No hosts discovered yet; 3 subnets and 1 domain declared.');
  });
});
