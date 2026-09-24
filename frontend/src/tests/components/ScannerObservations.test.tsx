/**
 * Scanner observations by issue, with bulk promotion (v5.272.0): an issue
 * common to many hosts is promoted once, on every host or on the hosts ticked.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getObservationIssues: vi.fn(),
  getObservationIssueHosts: vi.fn(),
  promoteObservationIssues: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import * as api from '../../services/api';
import ScannerObservations from '../../components/findings/ScannerObservations';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const issue = (key: string, title: string, hosts: number, over: Record<string, unknown> = {}) => ({
  issue_key: key, title, severity: 'medium', cve_id: null, sources: ['nessus'],
  host_count: hosts, judged_host_count: 0, finding_id: null, finding_status: null, ...over,
});
const SMB = issue('title:smb signing not required', 'SMB Signing not required', 3);
const TLS = issue('title:tls 1.0', 'TLS Version 1.0 Protocol Detection', 2, { finding_id: 12, finding_status: 'confirmed', judged_host_count: 1 });

const renderIt = (canManage = true) =>
  render(<MemoryRouter><ScannerObservations canManage={canManage} /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getObservationIssues.mockResolvedValue({ items: [SMB, TLS], total: 2 });
  mocked.getObservationIssueHosts.mockResolvedValue([
    { host_id: 1, ip_address: '10.9.0.1', hostname: null, severity: 'medium', ports: [445], judged: false, endpoint_status: null },
    { host_id: 2, ip_address: '10.9.0.2', hostname: 'fs2', severity: 'medium', ports: [445], judged: false, endpoint_status: null },
    { host_id: 3, ip_address: '10.9.0.3', hostname: null, severity: 'medium', ports: [], judged: false, endpoint_status: null },
  ]);
  mocked.promoteObservationIssues.mockResolvedValue({
    results: [
      { issue_key: SMB.issue_key, finding_id: 30, created: true, host_count: 2 },
      { issue_key: TLS.issue_key, finding_id: 12, created: false, host_count: 2 },
    ],
  });
});

describe('ScannerObservations', () => {
  it('lists each issue once with its hosts and what is already covered', async () => {
    renderIt();
    expect(await screen.findByText('SMB Signing not required')).toBeInTheDocument();
    expect(screen.getByText('3 hosts')).toBeInTheDocument();
    expect(screen.getByText('1 covered · 1 not yet judged')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Finding #12/ })).toHaveAttribute('href', '/findings/12');
    // Defaults to issues on ANY number of hosts, still waiting (v5.288.0: the
    // old 2+ default hid every single-host issue, criticals included).
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(expect.objectContaining({ minHosts: 1, includeJudged: false }));
    expect(screen.getByTestId('observations-count')).not.toHaveTextContent(/or more hosts/);
  });

  it('draws severity with the shared badge, Info included', async () => {
    mocked.getObservationIssues.mockResolvedValue({
      items: [issue('cve:CVE-1', 'Crit one', 1, { severity: 'critical' }), issue('title:x', 'Info one', 1, { severity: 'info' })],
      total: 2,
    });
    renderIt();
    await screen.findByText('Crit one');
    const crit = screen.getByText('Critical');
    const info = screen.getByText('Info');
    expect(crit.className).toMatch(/uppercase/);
    expect(crit.className).not.toMatch(/capitalize/);
    expect(info.className).toMatch(/uppercase/);
    expect(info.className).toMatch(/shadow-\[inset/);
  });

  it('promotes several issues at once, one on the hosts ticked and one on all of them', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');

    // Narrow SMB to two of its three hosts: that selects the issue.
    fireEvent.click(screen.getByRole('button', { name: /Show the hosts carrying SMB Signing/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Include 10.9.0.3' }));
    expect(screen.getByText('2 of 3 ticked')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select TLS Version 1.0 Protocol Detection' }));

    expect(screen.getByText(/selected · 4 hosts/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Promote to findings' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('2 of 3 hosts')).toBeInTheDocument();
    expect(within(dialog).getByText(/all 2 hosts · joins finding #12/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Promote 2 issues' }));

    await waitFor(() => expect(mocked.promoteObservationIssues).toHaveBeenCalledWith([
      { issue_key: SMB.issue_key, host_ids: [1, 2] },
      { issue_key: TLS.issue_key },
    ]));
    expect(toastMock.success).toHaveBeenCalledWith('Promoted 2 issues: 1 new finding, 1 joined an existing finding');
    await waitFor(() => expect(mocked.getObservationIssues).toHaveBeenCalledTimes(2)); // refreshed
  });

  // Review 2026-09-23 R11: unticking every host left an EMPTY narrowing that
  // re-selecting kept, so promote sent host_ids: [] and the server refused
  // the whole batch.
  it('never sends an empty host list after every host was unticked and the issue re-selected', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    fireEvent.click(screen.getByRole('button', { name: /Show the hosts carrying SMB Signing/ }));
    for (const ip of ['10.9.0.1', '10.9.0.2', '10.9.0.3']) {
      fireEvent.click(await screen.findByRole('checkbox', { name: `Include ${ip}` }));
    }
    expect(screen.queryByRole('button', { name: 'Promote to findings' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SMB Signing not required' }));
    fireEvent.click(screen.getByRole('button', { name: 'Promote to findings' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Promote 1 issue' }));
    await waitFor(() => expect(mocked.promoteObservationIssues).toHaveBeenCalledWith([{ issue_key: SMB.issue_key }]));
  });

  it('a host list longer than it shows cannot be narrowed, and links to all of them', async () => {
    const many = Array.from({ length: 101 }, (_, i) => ({
      host_id: i + 1, ip_address: `10.8.${Math.floor(i / 250)}.${(i % 250) + 1}`, hostname: null,
      severity: 'medium', ports: [], judged: false, endpoint_status: null,
    }));
    mocked.getObservationIssueHosts.mockResolvedValue(many);
    mocked.getObservationIssues.mockResolvedValue({ items: [{ ...SMB, host_count: 4000 }], total: 1 });
    renderIt();
    await screen.findByText('SMB Signing not required');
    fireEvent.click(screen.getByRole('button', { name: /Show the hosts carrying SMB Signing/ }));
    const note = await screen.findByTestId('observation-hosts-cut');
    expect(note).toHaveTextContent('The first 100 of 4,000 hosts');
    expect(mocked.getObservationIssueHosts).toHaveBeenCalledWith(SMB.issue_key, 101);
    expect(screen.queryByRole('checkbox', { name: /^Include / })).not.toBeInTheDocument();
    expect(within(note).getByRole('link')).toHaveAttribute(
      'href', `/hosts?q=${encodeURIComponent('issue:"title:smb signing not required"')}`,
    );
  });

  // Review 2026-09-23 B-UI-3: filters were component state, not shareable.
  it('reads its filters from the URL', async () => {
    render(
      <MemoryRouter initialEntries={['/findings?view=observations&obs_severity=critical&obs_min=5&obs_judged=1&obs_search=ssh']}>
        <ScannerObservations canManage />
      </MemoryRouter>,
    );
    await screen.findByText('SMB Signing not required');
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'critical', minHosts: 5, includeJudged: true, search: 'ssh' }),
    );
  });

  it('a viewer can read the list but not select or promote', async () => {
    renderIt(false);
    await screen.findByText('SMB Signing not required');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Promote to findings' })).not.toBeInTheDocument();
  });
});
