/**
 * /scopes in the Posture layout (v5.269.0): one sentence of where the hosts
 * stand, one strip of measures, sections without cards — and no "Correlate
 * Hosts" button (every write path correlates by itself).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
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

describe('Scopes page — screenshot review (v5.288.0)', () => {
  const bare = { id: 12, cidr: '10.77.2.0/24', description: null, site: null, host_count: 0, labels: [], created_at: '2026-09-22T00:00:00Z' };

  it('shows an empty subnet cell as a muted dash with a named, keyboard-reachable edit — not placeholder text', async () => {
    mocked.getDefaultScope.mockResolvedValue({ ...scope, subnets: [bare] });
    renderPage();
    await screen.findByText('10.77.2.0/24');
    expect(screen.queryByText(/Click to add/)).toBeNull();
    expect(screen.queryByText('No labels')).toBeNull();
    expect(screen.getByRole('button', { name: 'Add a description for 10.77.2.0/24' })).toHaveTextContent('—');
    expect(screen.getByRole('button', { name: 'Add a site for 10.77.2.0/24' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit labels for 10.77.2.0/24' })).toBeInTheDocument();
  });

  it('separates a technology\'s host count from its version', async () => {
    mocked.getScopeCoverage.mockResolvedValue({
      ...coverage,
      top_technologies: [{ name: 'Nginx 1.24.0', host_count: 1 }, { name: 'React', host_count: 3 }],
    });
    renderPage();
    const link = await screen.findByRole('link', { name: /Nginx 1\.24\.0/ });
    expect(link).toHaveTextContent('Nginx 1.24.0 · 1 host');
    expect(screen.getByRole('link', { name: /React/ })).toHaveTextContent('React · 3 hosts');
  });

  // v5.289.0 — the edit inputs sat in the ~75px Description column and a
  // 144px Site column: "Zone notes, ass", "DMZ / Internet-f".
  it('edits a subnet in one editor spanning Description, Site and Labels, the description wrapping', async () => {
    const long = { ...scope.subnets[0], description: 'Zone notes, asset class and owner: payments DMZ, owned by the platform team', site: 'DMZ / Internet-facing edge (Frankfurt)' };
    mocked.getDefaultScope.mockResolvedValue({ ...scope, subnets: [long] });
    renderPage();
    await screen.findByText('10.77.1.0/24');
    fireEvent.click(screen.getByRole('button', { name: 'Edit subnet 10.77.1.0/24' }));
    const editor = screen.getByTestId('subnet-editor-11');
    expect(editor.closest('td')).toHaveAttribute('colspan', '3');
    const desc = within(editor).getByLabelText('Description');
    expect(desc.tagName).toBe('TEXTAREA');
    expect(desc).toHaveValue(long.description);
    expect(within(editor).getByLabelText('Site')).toHaveValue(long.site);
    expect(within(editor).getByRole('button', { name: 'Edit labels for 10.77.1.0/24' })).toBeInTheDocument();
    // The row still has one cell per column: 8 columns = 6 cells + one spanning 3.
    const cells = editor.closest('tr')!.querySelectorAll(':scope > td');
    expect(cells).toHaveLength(6);
  });

  // v5.290.0 — a mistyped entry is explained under the field before any
  // request, not in a toast carrying Python's parser text.
  it('explains an invalid CIDR under the field without calling the API', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    const input = screen.getByLabelText('CIDR or IP');
    fireEvent.change(input, { target: { value: '10.0.0.300/24' } });
    // The first Add is the subnet row's; the domains section has its own.
    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[0]);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Not an IP address or CIDR range — e.g. 10.0.0.0/24 or 10.0.0.5',
    );
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAttribute('aria-describedby', 'new-cidr-error');
    expect(mocked.addScopeSubnets).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalled();
    // Typing again clears it.
    fireEvent.change(input, { target: { value: '10.0.0.0/24' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows a server rejection under the field too', async () => {
    mocked.addScopeSubnets.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: "'10.0.0.0/24' is not an IP address or CIDR range" } },
    });
    renderPage();
    await screen.findByText('10.77.1.0/24');
    fireEvent.change(screen.getByLabelText('CIDR or IP'), { target: { value: '10.0.0.0/24' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[0]);
    expect(await screen.findByRole('alert')).toHaveTextContent('is not an IP address or CIDR range');
    expect(mocked.addScopeSubnets).toHaveBeenCalledTimes(1);
  });

  it('checks an edited CIDR the same way', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    fireEvent.click(screen.getByRole('button', { name: 'Edit subnet 10.77.1.0/24' }));
    fireEvent.change(screen.getByLabelText('CIDR or IP for 10.77.1.0/24'), { target: { value: '10.77.1.0/40' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes to 10.77.1.0/24' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Not an IP address or CIDR range');
    expect(mocked.updateSubnet).not.toHaveBeenCalled();
  });

  it('says what the upload button uploads', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByRole('button', { name: 'Upload scope file' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload File' })).toBeNull();
  });

  // UX review 2026-09-24 — at a 1246px window (a ~916px content column) the
  // subnets table was 44px too wide and Description, the column operators
  // type into, got 82px: its header ran into "Site", Actions was cut off.
  it('fits the subnets table in a ~916px column with real room for Description', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    const table = screen.getAllByRole('table')[0];
    const minWidth = Number(table.className.match(/min-w-\[(\d+)px\]/)![1]);
    expect(minWidth).toBeLessThanOrEqual(900);
    const heads = Array.from(table.querySelectorAll('thead th'));
    const fixedPx = heads.reduce((sum, th) => sum + Number(th.className.match(/\bw-(\d+)\b/)?.[1] ?? 0) * 4, 0);
    const pct = heads.reduce((sum, th) => sum + Number(th.className.match(/\bw-\[(\d+)%\]/)?.[1] ?? 0), 0);
    const description = heads.find((th) => th.textContent === 'Description')!;
    expect(description.className).not.toMatch(/\bw-/);
    // Description's share at the table's floor and at a 916px column.
    expect(minWidth - fixedPx - (minWidth * pct) / 100).toBeGreaterThanOrEqual(160);
    expect(916 - fixedPx - (916 * pct) / 100).toBeGreaterThanOrEqual(200);
  });

  it('names the recon action as Operations names its session action', async () => {
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByRole('button', { name: /Start recon session/ })).toBeInTheDocument();
    expect(screen.queryByText(/Agentic Recon/)).toBeNull();
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
