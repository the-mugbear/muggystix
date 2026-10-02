/**
 * /scopes in the Posture layout (v5.269.0): one sentence of where the hosts
 * stand, one strip of measures, sections without cards — and no "Correlate
 * Hosts" button (every write path correlates by itself).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
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
// The PROJECT role decides who may change the scope (review 2026-10-01 R32).
const role = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
}));
const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
// The scan action opens the Start Agent Session dialog, which reads the
// operator's live sessions.
const mySessions = vi.hoisted(() => ({ sessions: [] as unknown[] }));
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: mySessions.sessions, loading: false, failed: false, refresh: vi.fn() }),
}));
// Dialogs that are closed on load; not under test here.
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
  role.value = 'analyst';
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

  // 5.313.0 — no per-scope key: the action hands the operator's one agent
  // session a task naming this scope. 5.313.1 — a scan uploaded to the
  // session, not a recon run.
  it('hands scanning this scope to your agent session', async () => {
    mySessions.sessions = [];
    renderPage();
    await screen.findByText('10.77.1.0/24');
    fireEvent.click(screen.getByRole('button', { name: /Scan with your agent/ }));
    expect(await screen.findByText('Start Agent Session')).toBeInTheDocument();
    expect(
      screen.getByText('Read scope 1 in BlueStick, run your scanners on what is in scope, and upload the output to this session.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Start a session, connect your agent, then give it this/)).toBeInTheDocument();
  });

  it('points to a live agent session instead of starting another', async () => {
    mySessions.sessions = [{
      kind: 'project', id: 72, project_id: 1, purpose: null, status: 'active',
      user_id: 1, user_username: 'me', started_at: '2026-09-29T10:00:00Z', completed_at: null,
      last_activity_at: null,
      key_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      call_count: 4, note_count: 0, connection: 'mcp', first_call_at: null,
    }];
    renderPage();
    await screen.findByText('10.77.1.0/24');
    fireEvent.click(screen.getByRole('button', { name: /Scan with your agent/ }));
    expect(await screen.findByText(/is live — paste this to its agent/)).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '#72' })[0]).toHaveAttribute('href', '/agent-sessions/72');
    mySessions.sessions = [];
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

// Review 2026-10-01 R32 — Scopes had no role check: a reader saw the add row,
// the editors, the uploads and the delete buttons, and learned from the 403.
describe('Scopes page — a project viewer reads the scope', () => {
  it('lists the scope without any control that changes it', async () => {
    role.value = 'viewer';
    mocked.listScopeDomains.mockResolvedValue({
      items: [{ id: 5, domain: 'example.com', include_subdomains: true, name_count: 2 }],
      total: 1, skip: 0, limit: 100, names_in_scope_total: 2,
    });
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByText('East segment')).toBeInTheDocument();
    await screen.findByText('*.example.com');

    expect(screen.queryByRole('button', { name: /Upload scope file/ })).toBeNull();
    expect(screen.queryByLabelText('CIDR or IP')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Add$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Manage project subnet labels/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Manage site criticality/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit subnet/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete subnet/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit labels/ })).toBeNull();
    expect(screen.queryByLabelText(/Select .* for bulk label apply/)).toBeNull();
    expect(screen.queryByLabelText(/Domain \(one or more/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove example.com from scope/ })).toBeNull();
    // Exports are an auditor's.
    expect(screen.queryByRole('button', { name: /Export/ })).toBeNull();
    // Reading stays: the search and the links.
    expect(screen.getByLabelText('Search subnets by CIDR or description')).toBeInTheDocument();
  });

  // Visual pass 2026-10-01 — a ticked subnet looks ticked, and the header box
  // says "some" over a partial selection (it was an empty box).
  it('an analyst’s ticked subnet is marked, and the header box shows "some" until all are ticked', async () => {
    role.value = 'analyst';
    const second = { ...scope.subnets[0], id: 12, cidr: '10.77.2.0/24', description: 'West segment' };
    mocked.getDefaultScope.mockResolvedValue({ ...scope, subnets: [scope.subnets[0], second] });
    renderPage();
    const first = await screen.findByLabelText('Select 10.77.1.0/24 for bulk label apply');
    const header = screen.getByLabelText('Select all subnets for bulk label apply');
    expect(header).toHaveAttribute('aria-checked', 'false');
    expect(first.closest('tr')).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(first);
    expect(first.closest('tr')).toHaveAttribute('data-state', 'selected');
    expect(first.closest('tr')).toHaveAttribute('aria-selected', 'true');
    expect(header).toHaveAttribute('aria-checked', 'mixed');

    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('Select 10.77.2.0/24 for bulk label apply').closest('tr')).toHaveAttribute('data-state', 'selected');
  });

  it('an auditor reads and exports; an analyst edits', async () => {
    role.value = 'auditor';
    const { unmount } = renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByRole('button', { name: 'Export scope' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Edit subnet/ })).toBeNull();
    unmount();

    role.value = 'analyst';
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByRole('button', { name: /Upload scope file/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit subnet 10.77.1.0/24' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete subnet 10.77.1.0/24' })).toBeInTheDocument();
  });
});

// R34 — a failed label catalogue looked like "this project has no labels".
describe('Scopes page — the label catalogue could not be loaded', () => {
  it('says so, without blocking the page, and retries', async () => {
    mocked.listSubnetLabels.mockRejectedValueOnce(new Error('down'));
    renderPage();
    await screen.findByText('10.77.1.0/24');
    expect(await screen.findByText(/The subnet labels could not be loaded\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/The subnet labels could not be loaded\./)).toBeNull());
  });
});

// B15 / R33 — the subnet search is in the URL, and the latest request wins.
describe('Scopes page — subnet search', () => {
  // (setupTests replaces useLocation; the search params are the real ones.)
  const Where = () => <output data-testid="where">{useSearchParams()[0].toString()}</output>;
  const renderAt = (url: string) => render(
    <MemoryRouter initialEntries={[url]}>
      <TooltipProvider><Scopes /><Where /></TooltipProvider>
    </MemoryRouter>,
  );

  it('a link with a search restores it and asks the server for it', async () => {
    renderAt('/scopes?subnet_q=dmz');
    await screen.findByText('10.77.1.0/24');
    expect(screen.getByLabelText('Search subnets by CIDR or description')).toHaveValue('dmz');
    expect(mocked.getDefaultScope).toHaveBeenCalledWith(expect.objectContaining({ subnetsSearch: 'dmz' }));
  });

  it('typing writes the search to the URL once it settles', async () => {
    renderAt('/scopes');
    await screen.findByText('10.77.1.0/24');
    fireEvent.change(screen.getByLabelText('Search subnets by CIDR or description'), { target: { value: 'east' } });
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('subnet_q=east'));
    fireEvent.click(screen.getByRole('button', { name: 'Clear subnet search' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe(''));
  });

  it('a slow first load never replaces the result of a later search', async () => {
    let releaseFirst!: (v: unknown) => void;
    const slowFirst = new Promise((resolve) => { releaseFirst = resolve; });
    const found = { ...scope, subnets_total: 1, subnets: [{ ...scope.subnets[0], id: 99, cidr: '172.16.9.0/24', description: 'found by search' }] };
    mocked.getDefaultScope.mockImplementation(({ subnetsSearch }: { subnetsSearch: string }) =>
      (subnetsSearch ? Promise.resolve(found) : slowFirst));
    // The page shows its body only once the first load settles; a reload
    // after a change is the request a search can overtake.
    releaseFirst(scope);
    renderAt('/scopes');
    await screen.findByText('10.77.1.0/24');

    let releaseReload!: (v: unknown) => void;
    const slowReload = new Promise((resolve) => { releaseReload = resolve; });
    mocked.getDefaultScope.mockImplementation(({ subnetsSearch }: { subnetsSearch: string }) =>
      (subnetsSearch ? Promise.resolve(found) : slowReload));
    mocked.listScopeDomains.mockResolvedValue({ items: [], total: 0, skip: 0, limit: 100, names_in_scope_total: 0 });
    mocked.addScopeSubnets.mockResolvedValue({});
    fireEvent.change(screen.getByLabelText('CIDR or IP'), { target: { value: '10.9.9.0/24' } });
    fireEvent.click(screen.getAllByRole('button', { name: /^Add$/ })[0]);
    await waitFor(() => expect(mocked.addScopeSubnets).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('Search subnets by CIDR or description'), { target: { value: '172' } });
    await screen.findByText('172.16.9.0/24');

    // The unfiltered reload answers last.
    await act(async () => { releaseReload(scope); await Promise.resolve(); });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('172.16.9.0/24')).toBeInTheDocument();
    expect(screen.queryByText('10.77.1.0/24')).toBeNull();
  });

  // Review 2026-10-01 M2 — the coverage does not depend on the search: the
  // superseded reload's subnets are dropped, its coverage is not.
  it('a search that supersedes a reload keeps the reload’s coverage', async () => {
    const found = { ...scope, subnets: [{ ...scope.subnets[0], id: 99, cidr: '172.16.9.0/24' }] };
    renderAt('/scopes');
    await screen.findByText('10.77.1.0/24');

    let releaseReload!: (v: unknown) => void;
    const slowReload = new Promise((resolve) => { releaseReload = resolve; });
    let releaseCoverage!: (v: unknown) => void;
    mocked.getDefaultScope.mockImplementation(({ subnetsSearch }: { subnetsSearch: string }) =>
      (subnetsSearch ? Promise.resolve(found) : slowReload));
    mocked.getScopeCoverage.mockReturnValue(new Promise((resolve) => { releaseCoverage = resolve; }));
    mocked.addScopeSubnets.mockResolvedValue({});
    fireEvent.change(screen.getByLabelText('CIDR or IP'), { target: { value: '10.9.9.0/24' } });
    fireEvent.click(screen.getAllByRole('button', { name: /^Add$/ })[0]);
    await waitFor(() => expect(mocked.getScopeCoverage).toHaveBeenCalledTimes(2));

    fireEvent.change(screen.getByLabelText('Search subnets by CIDR or description'), { target: { value: '172' } });
    await screen.findByText('172.16.9.0/24');
    await act(async () => {
      releaseCoverage({ ...coverage, scoped_hosts: 12345, total_hosts: 12347 });
      releaseReload(scope);
      await Promise.resolve();
    });
    expect(await screen.findByText('12,345')).toBeInTheDocument();
    expect(screen.getByText('172.16.9.0/24')).toBeInTheDocument();
  });
});
