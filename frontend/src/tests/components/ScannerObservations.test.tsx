/**
 * Scanner observations by issue (v5.272.0), on Inventory with an inspector
 * since 5.404.0: every observation is listed by default; opening one shows
 * what the scanner wrote, every host carrying it and what it printed on each.
 * An issue common to many hosts is promoted once, on every host or on the
 * hosts ticked in its inspector.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getObservationIssues: vi.fn(),
  dismissObservationIssue: vi.fn(),
  getObservationIssueDetail: vi.fn(),
  getObservationIssueHosts: vi.fn(),
  getObservationHostOutput: vi.fn(),
  promoteObservationIssues: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import * as api from '../../services/api';
import ScannerObservations from '../../components/observations/ScannerObservations';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const issue = (key: string, title: string, hosts: number, over: Record<string, unknown> = {}) => ({
  issue_key: key, title, severity: 'medium', cve_id: null, sources: ['nessus'],
  host_count: hosts, judged_host_count: 0, finding_id: null, finding_status: null, ...over,
});
const SMB = issue('title:smb signing not required', 'SMB Signing not required', 3);
const TLS = issue('title:tls 1.0', 'TLS Version 1.0 Protocol Detection', 2, { finding_id: 12, finding_status: 'confirmed', judged_host_count: 1 });

const renderIt = (canManage = true, at = '/observations') =>
  render(<MemoryRouter initialEntries={[at]}><ScannerObservations canManage={canManage} /></MemoryRouter>);

/** Open an issue's inspector from its row. */
const openRow = (title: string) =>
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^Open ${title}`) }));

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getObservationIssues.mockResolvedValue({ items: [SMB, TLS], total: 2 });
  mocked.getObservationIssueDetail.mockResolvedValue({
    issue_key: SMB.issue_key, title: SMB.title, source: 'nessus',
    description: 'Signing is not required on the remote SMB server.', solution: 'Enforce message signing.',
    cvss_score: 5.3, cvss_vector: 'CVSS:3.0/AV:N/AC:L', cve_id: null, plugin_id: '57608',
    plugin_name: 'SMB Signing not required', references: ['https://example.test/smb', 'MS-SMB2'],
  });
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

describe('ScannerObservations — the list', () => {
  it('lists each issue once, every one by default, with its hosts and what is already covered', async () => {
    renderIt();
    expect(await screen.findByText('SMB Signing not required')).toBeInTheDocument();
    expect(screen.getByText('3 hosts')).toBeInTheDocument();
    // 5.418.0 (O3) — the judged fraction is always said, none judged too.
    expect(screen.getByText('1 of 2 judged')).toBeInTheDocument();
    expect(screen.getByText('none judged')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Finding #12/ })).toHaveAttribute('href', '/findings/12');
    // 5.404.0 — every observation by default, judged ones included; any
    // number of hosts (v5.288.0: a 2+ default hid single-host criticals).
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(1, expect.objectContaining({ minHosts: 1, judged: 'any' }), expect.any(AbortSignal));
    expect(screen.getByRole('combobox', { name: 'Judged' })).toHaveTextContent('Judged or not');
    expect(screen.getByTestId('observations-count')).not.toHaveTextContent(/or more hosts|judged/);
  });

  it('says "all judged" for an issue every host of which a finding covers', async () => {
    mocked.getObservationIssues.mockResolvedValue({ items: [{ ...TLS, judged_host_count: 2 }], total: 1 });
    renderIt();
    expect(await screen.findByText('all judged')).toBeInTheDocument();
  });

  // 5.418.0 (O1 / O2 / I11) — the check id, CVSS, ports and last seen tell
  // rows apart; the row and the inspector say them from one component.
  it('a row names its check ids, CVSS, ports, scanners and last seen, as its inspector does', async () => {
    mocked.getObservationIssues.mockResolvedValue({
      items: [{ ...SMB, plugin_ids: ['57608'], cvss_score: 5.3, ports: [139, 445], port_count: 5, last_seen: '2026-10-20T10:00:00' }],
      total: 1,
    });
    renderIt();
    await screen.findByText('SMB Signing not required');
    const line = screen.getByTestId('issue-meta');
    expect(line).toHaveTextContent('check 57608 · CVSS 5.3 · ports 139, 445 +3 · from nessus · last seen');
    openRow('SMB Signing not required');
    const inspector = await screen.findByTestId('observation-inspector');
    expect(within(inspector).getByTestId('issue-meta')).toHaveTextContent('check 57608 · CVSS 5.3 · ports 139, 445 +3 · from nessus');
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

  // Review 2026-09-23 B-UI-3: filters were component state, not shareable.
  it('reads its filters from the URL', async () => {
    renderIt(true, '/observations?obs_severity=critical&obs_min=5&obs_judged=waiting&obs_search=ssh');
    await screen.findByText('SMB Signing not required');
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ severity: 'critical', minHosts: 5, judged: 'waiting', search: 'ssh' }),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('combobox', { name: 'Judged' })).toHaveTextContent('A host not yet judged');
  });

  // Plan A7 — what to test first is asked of the server, over every issue.
  it('asks the server for the exploit filter and the order, from the address and from the controls', async () => {
    renderIt(true, '/observations?obs_exploit=1&obs_sort=hosts&obs_judged=waiting');
    await screen.findByText('SMB Signing not required');
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(
      1, expect.objectContaining({ exploitable: true, sort: 'hosts' }), expect.any(AbortSignal),
    );
    expect(screen.getByTestId('observations-count')).toHaveTextContent('with an exploit reported, with hosts not yet judged · most hosts first');

    fireEvent.click(screen.getByRole('switch', { name: 'Exploit reported' }));
    await waitFor(() => expect(mocked.getObservationIssues).toHaveBeenLastCalledWith(
      1, expect.objectContaining({ exploitable: false, sort: 'hosts' }), expect.any(AbortSignal),
    ));
  });

  it('the Judged filter has four values from the address, and says so when nothing waits', async () => {
    mocked.getObservationIssues.mockResolvedValue({ items: [], total: 0 });
    renderIt(true, '/observations?obs_judged=waiting');
    expect(await screen.findByText(/Nothing waits: every matching observation is already covered/)).toBeInTheDocument();
    for (const [value, label, phrase] of [
      ['none', 'No host judged', 'with no host judged'], ['all', 'Judged on every host', 'judged on every host'],
    ] as const) {
      mocked.getObservationIssues.mockResolvedValue({ items: [SMB], total: 1 });
      const { unmount } = renderIt(true, `/observations?obs_judged=${value}`);
      await waitFor(() => expect(mocked.getObservationIssues).toHaveBeenLastCalledWith(
        1, expect.objectContaining({ judged: value }), expect.any(AbortSignal),
      ));
      expect(await screen.findByTestId('observations-count')).toHaveTextContent(phrase);
      const pickers = screen.getAllByRole('combobox', { name: 'Judged' });
      expect(pickers[pickers.length - 1]).toHaveTextContent(label);
      unmount();
    }
  });

  it('asks for the highest CVSS first', async () => {
    renderIt(true, '/observations?obs_sort=cvss');
    await screen.findByText('SMB Signing not required');
    expect(mocked.getObservationIssues).toHaveBeenCalledWith(1, expect.objectContaining({ sort: 'cvss' }), expect.any(AbortSignal));
    expect(screen.getByTestId('observations-count')).toHaveTextContent('highest CVSS first');
  });

  it('marks an issue with a reported exploit', async () => {
    mocked.getObservationIssues.mockResolvedValue({ items: [{ ...SMB, exploitable: true }, TLS], total: 2 });
    renderIt();
    await screen.findByText('SMB Signing not required');
    expect(screen.getAllByText(/exploit reported/)).toHaveLength(1);
  });

  // v5.290.0 — the action bar used to be inserted above the table on the first
  // tick, pushing every row down so the second click hit the wrong issue.
  it('ticking an issue does not move the table: the action slot is there before and after', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    const slot = screen.getByTestId('observations-bulk-slot');
    const slotClass = slot.className;
    expect(slot).toHaveTextContent('Tick observations to promote them to findings');
    const table = screen.getByRole('table');
    const precedingBefore = table.closest('.overflow-x-auto')!.parentElement!.children.length;

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SMB Signing not required' }));

    expect(screen.getByTestId('observations-bulk-slot')).toBe(slot);
    expect(slot.className).toBe(slotClass);
    expect(slot).toHaveTextContent('1 observation selected');
    expect(within(slot).getByRole('button', { name: 'Promote to findings' })).toBeInTheDocument();
    expect(table.closest('.overflow-x-auto')!.parentElement!.children.length).toBe(precedingBefore);
  });

  it('a list that fits one page has no pager', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    expect(screen.queryByText(/page 1 of/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Load .* more/ })).not.toBeInTheDocument();
  });

  it('a viewer can read the list and open one, but not select, promote or dismiss', async () => {
    renderIt(false);
    await screen.findByText('SMB Signing not required');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Promote to findings' })).not.toBeInTheDocument();
    openRow('SMB Signing not required');
    const inspector = await screen.findByTestId('observation-inspector');
    expect(await within(inspector).findByRole('link', { name: '10.9.0.1' })).toBeInTheDocument();
    expect(within(inspector).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(within(inspector).queryByRole('button', { name: /Promote|Mark false positive/ })).not.toBeInTheDocument();
  });
});

describe('ScannerObservations — the inspector', () => {
  // Analyst walkthrough 2026-10-10 / user request 2026-10-16: what the scanner
  // wrote, and every host carrying it, without opening one of the hosts.
  it('says what the scanner wrote, whose words they are, its identifiers and references, above its hosts', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    expect(mocked.getObservationIssueDetail).not.toHaveBeenCalled();
    openRow('SMB Signing not required');
    const detail = await screen.findByTestId('observation-detail');
    expect(mocked.getObservationIssueDetail).toHaveBeenCalledWith(1, SMB.issue_key, null, expect.any(AbortSignal));
    expect(detail).toHaveTextContent('Signing is not required on the remote SMB server.');
    expect(detail).toHaveTextContent('as nessus reports it');
    expect(detail).toHaveTextContent('Enforce message signing.');
    expect(detail).toHaveTextContent('CVSS 5.3 · CVSS:3.0/AV:N/AC:L');
    expect(detail).toHaveTextContent('nessus · Plugin 57608');
    expect(within(detail).getByRole('link', { name: 'https://example.test/smb' })).toHaveAttribute('target', '_blank');
    expect(detail).toHaveTextContent('MS-SMB2');
    const inspector = screen.getByTestId('observation-inspector');
    expect(within(inspector).getByTestId('observation-inspector-counts')).toHaveTextContent('3 hosts · 0 judged · 3 not yet judged');
    // O4 / O7 — a host opens at THIS issue's weakness row.
    expect(await within(inspector).findByRole('link', { name: '10.9.0.1' }))
      .toHaveAttribute('href', `/hosts/1#${encodeURIComponent('weakness-title:smb signing not required')}`);
  });

  // O4 — the pivots are always there, and a short list's output opens at once.
  it('links its hosts on Hosts and its rows in Query, and shows every host’s output on a short list', async () => {
    mocked.getObservationHostOutput.mockResolvedValue([
      { id: 9, title: SMB.title, port_number: 445, protocol: 'tcp', service_name: null, source: 'nessus',
        plugin_id: '57608', plugin_output: 'signing: false', last_seen: null, references: [] },
    ]);
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    const pivots = await screen.findByTestId('observation-pivots');
    expect(within(pivots).getByRole('link', { name: 'All 3 hosts on Hosts' }))
      .toHaveAttribute('href', `/hosts?q=${encodeURIComponent('issue:"title:smb signing not required"')}`);
    expect(within(pivots).getByRole('link', { name: 'Ask this in Query' }))
      .toHaveAttribute('href', `/query?q=${encodeURIComponent('observations | where issue="title:smb signing not required"')}`);
    fireEvent.click(within(pivots).getByRole('button', { name: 'Show all output' }));
    await waitFor(() => expect(screen.getAllByTestId('observation-host-output')).toHaveLength(3));
    expect(mocked.getObservationHostOutput).toHaveBeenCalledTimes(3);
  });

  // O5 — two scanners' words: a picker, and the server asked for that one's.
  it('offers each scanner’s words when several reported it', async () => {
    mocked.getObservationIssues.mockResolvedValue({ items: [{ ...SMB, sources: ['nessus', 'openvas'] }], total: 1 });
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    const picker = await screen.findByTestId('observation-source-picker');
    expect(within(picker).getByRole('combobox')).toHaveTextContent('nessus');
    fireEvent.click(within(picker).getByRole('combobox'));
    fireEvent.click(await screen.findByRole('option', { name: 'openvas' }));
    await waitFor(() => expect(mocked.getObservationIssueDetail).toHaveBeenLastCalledWith(1, SMB.issue_key, 'openvas', expect.any(AbortSignal)));
  });

  // O9 — j / k step inside the inspector.
  it('j and k step to the next and previous observation', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    await screen.findByTestId('observation-inspector');
    fireEvent.keyDown(window, { key: 'j' });
    expect(await screen.findByText('2 of 2')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'k' });
    expect(await screen.findByText('1 of 2')).toBeInTheDocument();
  });

  it('is opened by the address, and closing it takes the issue out of the address', async () => {
    renderIt(true, `/observations?issue=${encodeURIComponent(SMB.issue_key)}`);
    expect(await screen.findByTestId('observation-inspector')).toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('observation-inspector')).not.toBeInTheDocument());
  });

  it('steps to the next and previous observation', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    await screen.findByTestId('observation-inspector');
    expect(screen.getByRole('button', { name: 'Previous observation' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next observation' }));
    expect(await screen.findByText('2 of 2')).toBeInTheDocument();
    await waitFor(() => expect(mocked.getObservationIssueDetail).toHaveBeenLastCalledWith(1, TLS.issue_key, null, expect.any(AbortSignal)));
    expect(screen.getByRole('button', { name: 'Next observation' })).toBeDisabled();
  });

  it('an issue outside the rows shown still shows its details and hosts, with no actions', async () => {
    renderIt(true, '/observations?issue=cve%3ACVE-2099-1');
    const inspector = await screen.findByTestId('observation-inspector');
    expect(within(inspector).getByTestId('observation-inspector-outside')).toBeInTheDocument();
    expect(await within(inspector).findByTestId('observation-detail')).toBeInTheDocument();
    expect(within(inspector).queryByRole('button', { name: /Promote/ })).not.toBeInTheDocument();
    expect(within(inspector).queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('shows what the scanner printed on one host when its row is opened', async () => {
    mocked.getObservationHostOutput.mockResolvedValue([
      { id: 9, title: SMB.title, port_number: 445, protocol: 'tcp', service_name: 'microsoft-ds', source: 'nessus',
        plugin_id: '57608', plugin_output: 'Message signing required: false', last_seen: null, references: [] },
      { id: 10, title: SMB.title, port_number: null, protocol: null, service_name: null, source: 'openvas',
        plugin_id: null, plugin_output: null, last_seen: null, references: [] },
    ]);
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    fireEvent.click(await screen.findByRole('button', { name: 'Show what the scanner printed on 10.9.0.2' }));
    const output = await screen.findByTestId('observation-host-output');
    expect(mocked.getObservationHostOutput).toHaveBeenCalledWith(1, SMB.issue_key, 2, expect.any(AbortSignal));
    expect(output).toHaveTextContent('Port 445/tcp · microsoft-ds · nessus · plugin 57608');
    expect(output).toHaveTextContent('Message signing required: false');
    expect(output).toHaveTextContent('Host-level · openvas');
    expect(output).toHaveTextContent('The scanner printed nothing for this host.');
  });

  it('says so when no scanner wrote anything, and when it could not be read — the hosts still list', async () => {
    mocked.getObservationIssueDetail.mockResolvedValueOnce({
      issue_key: SMB.issue_key, title: SMB.title, source: 'nmap',
      description: null, solution: null, cvss_score: null, cvss_vector: null, references: [],
    });
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    expect(await screen.findByText('No scanner recorded a description or remediation for this.')).toBeInTheDocument();

    mocked.getObservationIssueDetail.mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: 'Next observation' }));
    expect(await screen.findByText(/Could not load what the scanner wrote/)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: '10.9.0.1' })).toBeInTheDocument();
  });

  it('says where testing of the issue stands on each host, and hands the ticked ones to the agent', async () => {
    mocked.getObservationIssueHosts.mockResolvedValue([
      { host_id: 1, ip_address: '10.9.0.1', hostname: null, severity: 'medium', ports: [445], judged: false, endpoint_status: null, tests_to_do: 2, tests_recorded: 0 },
      { host_id: 2, ip_address: '10.9.0.2', hostname: null, severity: 'medium', ports: [445], judged: false, endpoint_status: null, tests_to_do: 1, tests_recorded: 1 },
      { host_id: 3, ip_address: '10.9.0.3', hostname: null, severity: 'medium', ports: [], judged: false, endpoint_status: null, tests_to_do: 0, tests_recorded: 3 },
      { host_id: 4, ip_address: '10.9.0.4', hostname: null, severity: 'medium', ports: [], judged: true, endpoint_status: 'false_positive' },
    ]);
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    const state = async (ip: string) => (await screen.findByRole('link', { name: ip })).closest('li')!;
    expect(await state('10.9.0.1')).toHaveTextContent('2 tests planned');
    expect(await state('10.9.0.2')).toHaveTextContent('tested · 1 to do');
    expect(await state('10.9.0.3')).toHaveTextContent(/tested(?! ·)/);
    expect(await state('10.9.0.4')).toHaveTextContent('no test');
    expect(await state('10.9.0.4')).toHaveTextContent('On the finding · False positive');

    expect(screen.getByRole('button', { name: /Ask agent to propose tests on the ticked 4 hosts/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include 10.9.0.4' }));
    expect(screen.getByRole('button', { name: /Ask agent to propose tests on the ticked 3 hosts/ })).toBeInTheDocument();
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
    openRow('SMB Signing not required');
    const note = await screen.findByTestId('observation-hosts-cut');
    expect(note).toHaveTextContent('The first 100 of 4,000 hosts');
    expect(mocked.getObservationIssueHosts).toHaveBeenCalledWith(1, SMB.issue_key, 101, expect.any(AbortSignal));
    expect(screen.queryByRole('checkbox', { name: /^Include / })).not.toBeInTheDocument();
    expect(within(screen.getByTestId('observation-pivots')).getByRole('link', { name: 'All 4,000 hosts on Hosts' })).toHaveAttribute(
      'href', `/hosts?q=${encodeURIComponent('issue:"title:smb signing not required"')}`,
    );
    // Past ten hosts their output stays one row at a time.
    expect(screen.queryByRole('button', { name: 'Show all output' })).not.toBeInTheDocument();
  });
});

describe('ScannerObservations — promoting and dismissing', () => {
  it('promotes several issues at once, one on the hosts ticked in its inspector and one on all of them', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');

    // Narrow SMB to two of its three hosts: that selects the issue.
    openRow('SMB Signing not required');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Include 10.9.0.3' }));
    expect(screen.getByText('2 of 3 ticked')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select TLS Version 1.0 Protocol Detection' }));

    expect(screen.getByText(/selected · 4 hosts/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Promote to findings' }));
    const dialog = await screen.findByRole('dialog', { name: /Promote 2 observations/ });
    expect(within(dialog).getByText('2 of 3 hosts')).toBeInTheDocument();
    expect(within(dialog).getByText(/all 2 hosts · joins finding #12/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Promote 2 observations' }));

    await waitFor(() => expect(mocked.promoteObservationIssues).toHaveBeenCalledWith(1, [
      { issue_key: SMB.issue_key, host_ids: [1, 2] },
      { issue_key: TLS.issue_key },
    ]));
    expect(toastMock.success).toHaveBeenCalledWith('Promoted 2 observations: 1 new finding, 1 joined an existing finding');
    await waitFor(() => expect(mocked.getObservationIssues).toHaveBeenCalledTimes(2)); // refreshed
  });

  it('the inspector promotes its own issue only, on the hosts ticked there, and the rest stays selected', async () => {
    mocked.promoteObservationIssues.mockResolvedValue({
      results: [{ issue_key: SMB.issue_key, finding_id: 30, created: true, host_count: 2 }],
    });
    renderIt();
    await screen.findByText('SMB Signing not required');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select TLS Version 1.0 Protocol Detection' }));
    openRow('SMB Signing not required');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Include 10.9.0.3' }));
    fireEvent.click(screen.getByRole('button', { name: 'Promote on 2 ticked hosts…' }));
    const dialog = await screen.findByRole('dialog', { name: /Promote 1 observation/ });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Promote 1 observation' }));
    await waitFor(() => expect(mocked.promoteObservationIssues).toHaveBeenCalledWith(1, [
      { issue_key: SMB.issue_key, host_ids: [1, 2] },
    ]));
    await waitFor(() => expect(screen.getByTestId('observations-bulk-slot')).toHaveTextContent('1 observation selected · 2 hosts'));
  });

  // Review 2026-09-23 R11: unticking every host left an EMPTY narrowing that
  // re-selecting kept, so promote sent host_ids: [] and the server refused
  // the whole batch.
  it('never sends an empty host list after every host was unticked and the issue re-selected', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    openRow('SMB Signing not required');
    for (const ip of ['10.9.0.1', '10.9.0.2', '10.9.0.3']) {
      fireEvent.click(await screen.findByRole('checkbox', { name: `Include ${ip}` }));
    }
    expect(screen.queryByRole('button', { name: 'Promote to findings' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SMB Signing not required' }));
    fireEvent.click(screen.getByRole('button', { name: 'Promote to findings' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: /Promote 1 observation/ })).getByRole('button', { name: 'Promote 1 observation' }));
    await waitFor(() => expect(mocked.promoteObservationIssues).toHaveBeenCalledWith(1, [{ issue_key: SMB.issue_key }]));
  });

  // Visual pass 2026-10-01 — a ticked issue's row is marked, and the header
  // box says "some" until every issue shown is ticked.
  it('marks a ticked issue, shows "some" on the header box, and a click there selects the rest', async () => {
    renderIt();
    await screen.findByText('SMB Signing not required');
    const header = screen.getByRole('checkbox', { name: 'Select every observation shown' });
    const rowOf = (title: string) => screen.getByRole('checkbox', { name: `Select ${title}` }).closest('tr')!;
    expect(header).toHaveAttribute('aria-checked', 'false');

    openRow('SMB Signing not required');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Include 10.9.0.3' }));
    expect(rowOf('SMB Signing not required')).toHaveAttribute('data-state', 'selected');
    expect(rowOf('SMB Signing not required')).toHaveAttribute('aria-selected', 'true');
    expect(rowOf('TLS Version 1.0 Protocol Detection')).not.toHaveAttribute('data-state');
    expect(header).toHaveAttribute('aria-checked', 'mixed');

    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-checked', 'true');
    expect(rowOf('TLS Version 1.0 Protocol Detection')).toHaveAttribute('data-state', 'selected');
    // The issue that was already selected keeps the hosts it was narrowed to.
    expect(screen.getByText('2 of 3 ticked')).toBeInTheDocument();
  });

  // The owner, 2026-10-10: a dismissal that applies to every host carrying
  // the observation is allowed from here — it existed only on a host's page.
  it('marks the one ticked observation a false positive: on every host, or on the hosts ticked under it', async () => {
    mocked.dismissObservationIssue.mockResolvedValue({ issue_key: SMB.issue_key, finding_id: 31, host_count: 3 });
    renderIt();
    await screen.findByText('SMB Signing not required');
    // Nothing ticked: not offered.  Two ticked: shown, disabled, saying why
    // (second review 2026-10-16, O8 — it vanished with no word).
    expect(screen.queryByRole('button', { name: /Mark false positive/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SMB Signing not required' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select TLS Version 1.0 Protocol Detection' }));
    const waiting = screen.getByRole('button', { name: /Mark false positive/ });
    expect(waiting).toBeDisabled();
    expect(waiting).toHaveAttribute('title', expect.stringContaining('One observation at a time'));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select TLS Version 1.0 Protocol Detection' }));

    fireEvent.click(screen.getByRole('button', { name: /Mark false positive/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Mark as a false positive' });
    expect(dialog).toHaveTextContent('On every host carrying it (3)');
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: '  enforced by GPO ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark false positive' }));
    await waitFor(() => expect(mocked.dismissObservationIssue).toHaveBeenCalledWith(1, {
      issue_key: SMB.issue_key, reason: 'enforced by GPO',
    }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Marked a false positive on every host carrying it (3)'));

    // From the inspector, narrowed to two of its three hosts: exactly those are named.
    mocked.dismissObservationIssue.mockResolvedValue({ issue_key: SMB.issue_key, finding_id: 31, host_count: 2 });
    openRow('SMB Signing not required');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Include 10.9.0.3' }));
    const inspector = screen.getByTestId('observation-inspector');
    fireEvent.click(within(inspector).getByRole('button', { name: /Mark false positive/ }));
    const again = await screen.findByRole('dialog', { name: 'Mark as a false positive' });
    expect(again).toHaveTextContent('On the 2 hosts ticked under it, of 3');
    fireEvent.click(within(again).getByRole('button', { name: 'Mark false positive' }));
    await waitFor(() => expect(mocked.dismissObservationIssue).toHaveBeenLastCalledWith(1, {
      issue_key: SMB.issue_key, host_ids: [1, 2],
    }));
  });
});
