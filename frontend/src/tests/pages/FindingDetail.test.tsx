import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// setupTests mocks useNavigate to a throwaway fn; give this file a spy so the
// return target the detail navigates to is observable.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateSpy };
});
vi.mock('../../services/api', () => ({
  // The finding's linked test evidence (5.321.0); none unless a test says so.
  listEvidenceRecords: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  getFinding: vi.fn(),
  getFindingHistory: vi.fn(),
  setFindingStatus: vi.fn(),
  setFindingEndpointStatus: vi.fn(),
  setFindingEndpointsStatus: vi.fn(),
  updateFinding: vi.fn(),
  deleteFinding: vi.fn(),
  removeFindingEndpoint: vi.fn(),
  addFindingHosts: vi.fn(),
  getHosts: vi.fn(),
  getHostNotes: vi.fn(),
  listProjectMembers: vi.fn(),
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
  // The finding's images (caption, "In report", where the text places each).
  getFindingImages: vi.fn().mockResolvedValue({ items: [], caption_max: 2000 }),
  getNoteAttachmentObjectUrl: vi.fn().mockResolvedValue('blob:img'),
  setNoteAttachmentCaption: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  // Pending proposals (5.334.0: one read for the page); none unless a test says so.
  listProposals: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester', role: 'member' }, hasPermission: () => true }),
}));
// The PROJECT role decides who may triage (review 2026-10-01 R32).
const projectRole = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: projectRole.value } }),
}));

import * as api from '../../services/api';
import FindingDetail from '../../pages/FindingDetail';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const finding = (over: Record<string, unknown> = {}) => ({
  id: 7, project_id: 1, title: 'Weak TLS on portal', severity: 'high', status: 'open', source: 'manual',
  owner_id: null, owner_name: null, evidence_annotation_id: null, vuln_id: null,
  host_count: 0, hosts: [], created_at: '2026-08-01T00:00:00Z', updated_at: null, ...over,
});

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/findings/:findingId" element={<FindingDetail />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  projectRole.value = 'analyst';
  mocked.getFinding.mockResolvedValue(finding());
  mocked.getFindingHistory.mockResolvedValue([]);
  mocked.getFindingNotes.mockResolvedValue([]);
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.setFindingStatus.mockResolvedValue(undefined);
});

describe('FindingDetail — C2: metadata edits keep the comment draft', () => {
  it('a status change does not unmount the comment thread', async () => {
    const user = userEvent.setup();
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    const composer = screen.getByLabelText('New comment');
    fireEvent.change(composer, { target: { value: 'repro: openssl s_client …' } });

    // After the change, the refresh returns the new status.
    mocked.getFinding.mockResolvedValue(finding({ status: 'confirmed' }));
    await user.click(screen.getByLabelText('Finding status'));
    await user.click(await screen.findByRole('option', { name: 'Confirmed' }));

    await waitFor(() => expect(mocked.setFindingStatus).toHaveBeenCalledWith(1, 7, 'confirmed', undefined));
    // Refresh happened (finding + history re-fetched) …
    await waitFor(() => expect(mocked.getFinding).toHaveBeenCalledTimes(2));
    // … and the draft the analyst typed is still in the still-mounted composer.
    expect(screen.getByLabelText('New comment')).toHaveValue('repro: openssl s_client …');
    // The whole-page skeleton never replaced the content.
    expect(screen.getByText('Weak TLS on portal')).toBeInTheDocument();
  });
});

describe('FindingDetail — the owner picker when the members could not be loaded', () => {
  it('says so beside the picker, with a retry, instead of offering nobody', async () => {
    mocked.listProjectMembers.mockRejectedValueOnce(new Error('503'));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const alert = await screen.findByText(/members could not be loaded/);
    mocked.listProjectMembers.mockResolvedValue([
      { id: 9, project_id: 1, user_id: 77, username: 'ben', full_name: 'Ben Okafor', role: 'analyst', created_at: '' },
    ]);
    fireEvent.click(within(alert.parentElement as HTMLElement).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/members could not be loaded/)).not.toBeInTheDocument());
    expect(mocked.listProjectMembers).toHaveBeenCalledTimes(2);
  });
});

describe('FindingDetail — item 7: each endpoint has its own state', () => {
  const twoHosts = () => finding({
    status: 'confirmed',
    host_count: 2,
    endpoint_status_counts: { open: 1, remediated: 1 },
    hosts: [
      { id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' },
      { id: 32, host_id: 6, ip_address: '10.0.0.6', hostname: 'web2', name_id: null, fqdn: null, host_status: 'remediated' },
    ],
  });

  it('says the status is the issue\'s and shows how the endpoints stand', async () => {
    mocked.getFinding.mockResolvedValue(twoHosts());
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText(/The status above is the issue/)).toBeInTheDocument();
    expect(screen.getByText('still present on 1 of 2 · 1 remediated')).toBeInTheDocument();
  });

  it('v5.290.0 — an open endpoint reads "Still present", never "Open", beside a Confirmed finding', async () => {
    mocked.getFinding.mockResolvedValue(twoHosts());
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getAllByText('Still present').length).toBeGreaterThan(0);
    expect(screen.queryByText('Open here')).not.toBeInTheDocument();
  });

  it('says "all still present" when every endpoint is', async () => {
    mocked.getFinding.mockResolvedValue(finding({
      status: 'confirmed', host_count: 1, endpoint_status_counts: { open: 1 },
      hosts: [{ id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' }],
    }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText(/all still present\./)).toBeInTheDocument();
    expect(screen.queryByText(/all open/)).not.toBeInTheDocument();
  });

  it('changing one endpoint\'s state calls the endpoint route, not the finding status', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(twoHosts());
    const before = twoHosts() as unknown as { hosts: Array<Record<string, unknown>> };
    mocked.setFindingEndpointStatus.mockResolvedValue({
      ...before,
      endpoint_status_counts: { retest: 1, remediated: 1 },
      hosts: [{ ...before.hosts[0], host_status: 'retest' }, before.hosts[1]],
    });
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    await user.click(screen.getByLabelText('State of 10.0.0.5'));
    await user.click(await screen.findByRole('option', { name: 'Retest here' }));

    await waitFor(() => expect(mocked.setFindingEndpointStatus).toHaveBeenCalledWith(1, 7, 31, 'retest'));
    expect(mocked.setFindingStatus).not.toHaveBeenCalled();
    // C2 — the row comes from the route's response; the finding (every
    // endpoint of it) is not read a second time.  Only the history is.
    await waitFor(() => expect(screen.getByLabelText('State of 10.0.0.5').textContent).toContain('Retest here'));
    expect(mocked.getFinding).toHaveBeenCalledTimes(1);
    expect(mocked.getFindingHistory).toHaveBeenCalledTimes(2);
  });
});

// Review 2026-10-01 C2 / B13 — a finding with thousands of endpoints.
describe('FindingDetail — many endpoints', () => {
  const many = (n: number, state: (i: number) => string = () => 'open') => {
    const hosts = Array.from({ length: n }, (_, i) => ({
      id: 1000 + i, host_id: 10 + i, ip_address: `10.1.${Math.floor(i / 250)}.${i % 250}`,
      hostname: i === 7 ? 'needle-db' : null, name_id: null, fqdn: null, host_status: state(i),
    }));
    const counts: Record<string, number> = {};
    hosts.forEach((h) => { counts[h.host_status] = (counts[h.host_status] ?? 0) + 1; });
    return finding({ status: 'confirmed', host_count: n, endpoint_status_counts: counts, hosts });
  };
  const rowCount = () => document.querySelectorAll('[data-endpoint-row]').length;

  it('renders a cap of the endpoints, says how many there are, and shows more on request', async () => {
    mocked.getFinding.mockResolvedValue(many(260));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(rowCount()).toBe(100);
    expect(screen.getByTestId('endpoints-cut').textContent).toContain('Showing 100 of 260 endpoints.');

    fireEvent.click(screen.getByRole('button', { name: 'Show 100 more' }));
    expect(rowCount()).toBe(200);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 260' }));
    expect(rowCount()).toBe(260);
    expect(screen.queryByTestId('endpoints-cut')).toBeNull();
  });

  it('filters by state (chips with the counts) and by address or name', async () => {
    mocked.getFinding.mockResolvedValue(many(120, (i) => (i < 5 ? 'remediated' : 'open')));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const chips = screen.getByRole('group', { name: 'Endpoint state filter' });
    expect(chips.textContent).toContain('All120');
    expect(chips.textContent).toContain('Still present115');
    expect(chips.textContent).toContain('Remediated5');
    expect(chips.textContent).not.toContain('Retest');  // a state nobody is in has no chip

    fireEvent.click(screen.getByRole('button', { name: /Remediated/ }));
    expect(rowCount()).toBe(5);
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));
    fireEvent.change(screen.getByLabelText('Filter endpoints by address or name'), { target: { value: 'NEEDLE' } });
    expect(rowCount()).toBe(1);
    expect(screen.getByText('needle-db')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter endpoints by address or name'), { target: { value: 'zzz' } });
    expect(screen.getByText('No endpoint matches this filter.')).toBeInTheDocument();
  });

  it('brings a linked endpoint (?endpoint=) into the rendered rows even past the cap', async () => {
    mocked.getFinding.mockResolvedValue(many(260));
    renderAt('/findings/7?endpoint=1240');
    await screen.findByText('Weak TLS on portal');
    await waitFor(() => expect(document.querySelector('[data-endpoint-row="1240"]')).not.toBeNull());
  });

  it('sets the selected endpoints together, with the note, in one call', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(many(120));
    const after = many(120, (i) => (i < 100 ? 'remediated' : 'open'));
    mocked.setFindingEndpointsStatus.mockResolvedValue(after);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.queryByRole('group', { name: 'Set the selected endpoints' })).toBeNull();

    await user.click(screen.getByLabelText('Select every endpoint shown'));
    const bar = screen.getByRole('group', { name: 'Set the selected endpoints' });
    expect(bar.textContent).toContain('100 selected');
    // Nothing is sent until a state is chosen.
    expect(screen.getByRole('button', { name: 'Set 100 endpoints' })).toBeDisabled();

    await user.click(screen.getByLabelText('Set the selected endpoints to'));
    await user.click(await screen.findByRole('option', { name: 'Remediated here' }));
    fireEvent.change(screen.getByLabelText(/Note recorded with each endpoint/), { target: { value: ' retest 2026-10-01 ' } });
    await user.click(screen.getByRole('button', { name: 'Set 100 endpoints' }));

    await waitFor(() => expect(mocked.setFindingEndpointsStatus).toHaveBeenCalledTimes(1));
    const [projectId, id, body] = mocked.setFindingEndpointsStatus.mock.calls[0];
    expect(projectId).toBe(1);
    expect(id).toBe(7);
    expect(body.host_status).toBe('remediated');
    expect(body.summary).toBe('retest 2026-10-01');
    expect(body.finding_host_ids).toEqual(Array.from({ length: 100 }, (_, i) => 1000 + i));
    // Not 100 single calls, and no second read of the finding.
    expect(mocked.setFindingEndpointStatus).not.toHaveBeenCalled();
    expect(mocked.getFinding).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Set 100 endpoints to remediated here.'));
    expect(screen.queryByRole('group', { name: 'Set the selected endpoints' })).toBeNull();
  });

  it('selects every endpoint MATCHING the filter, sends them 500 to a call, and says what a refused call left unchanged', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(many(1200));
    mocked.setFindingEndpointsStatus
      .mockResolvedValueOnce(many(1200, (i) => (i < 500 ? 'retest' : 'open')))
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'An endpoint changed meanwhile.' } } })
      .mockResolvedValueOnce(many(1200, (i) => (i < 500 || i >= 1000 ? 'retest' : 'open')));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    await user.click(screen.getByLabelText('Select every endpoint shown'));
    await user.click(screen.getByRole('button', { name: 'Select all 1,200' }));
    expect(screen.getByRole('group', { name: 'Set the selected endpoints' }).textContent).toContain('1,200 selected');

    await user.click(screen.getByLabelText('Set the selected endpoints to'));
    await user.click(await screen.findByRole('option', { name: 'Retest here' }));
    await user.click(screen.getByRole('button', { name: 'Set 1,200 endpoints' }));

    await waitFor(() => expect(mocked.setFindingEndpointsStatus).toHaveBeenCalledTimes(3));
    const sizes = mocked.setFindingEndpointsStatus.mock.calls.map((c) => c[2].finding_host_ids.length);
    expect(sizes).toEqual([500, 500, 200]);
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalled());
    const said = toastMock.warning.mock.calls[0][0] as string;
    expect(said).toContain('Set 700 of 1,200 endpoints');
    expect(said).toContain('500 were not changed and are still selected');
    expect(said).toContain('An endpoint changed meanwhile.');
    expect(toastMock.success).not.toHaveBeenCalled();
    // The 500 that were refused are what is still selected.
    expect(screen.getByRole('group', { name: 'Set the selected endpoints' }).textContent).toContain('500 selected');
  }, 20000);

  it('shift-click selects the range from the last row ticked', async () => {
    mocked.getFinding.mockResolvedValue(many(20));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByLabelText('Select 10.1.0.2'));
    fireEvent.click(screen.getByLabelText('Select 10.1.0.6'), { shiftKey: true });
    expect(screen.getByRole('group', { name: 'Set the selected endpoints' }).textContent).toContain('5 selected');
  });

  it('a filter change drops the selection instead of leaving endpoints selected out of sight', async () => {
    mocked.getFinding.mockResolvedValue(many(20, (i) => (i < 5 ? 'remediated' : 'open')));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByLabelText('Select every endpoint shown'));
    expect(screen.getByRole('group', { name: 'Set the selected endpoints' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Remediated/ }));
    expect(screen.queryByRole('group', { name: 'Set the selected endpoints' })).toBeNull();
  });
});

// Review 2026-10-01 R32 — a project viewer reads the finding.
describe('FindingDetail — a project viewer', () => {
  it('sees the finding, its endpoints and their states without any write control', async () => {
    projectRole.value = 'viewer';
    mocked.getFinding.mockResolvedValue(finding({
      status: 'confirmed', host_count: 2, endpoint_status_counts: { open: 1, remediated: 1 }, can_modify: false,
      hosts: [
        { id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' },
        { id: 32, host_id: 6, ip_address: '10.0.0.6', hostname: 'web2', name_id: null, fqdn: null, host_status: 'remediated' },
      ],
    }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
    expect(screen.getByText('Remediated here')).toBeInTheDocument();

    expect(screen.queryByLabelText('Finding status')).toBeNull();
    expect(screen.queryByLabelText('State of 10.0.0.5')).toBeNull();
    expect(screen.queryByLabelText('Select every endpoint shown')).toBeNull();
    expect(screen.queryByLabelText('Select 10.0.0.5')).toBeNull();
    expect(screen.queryByRole('button', { name: /Add hosts/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Actions for/ })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
    expect(screen.queryByLabelText('New comment')).toBeNull();
    // The owner roster is an analyst's picker; a reader never asks for it.
    expect(mocked.listProjectMembers).not.toHaveBeenCalled();
  });

  it('an auditor is a reader too (it was the account role, which every member passed)', async () => {
    projectRole.value = 'auditor';
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.queryByLabelText('Finding status')).toBeNull();
  });
});

// UX review 2026-09-24: triage starts from the hosts, and a status with no
// transition must say where it came from.
describe('FindingDetail — layout and the initial status', () => {
  it('lists the affected hosts before the report text and the comments', async () => {
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const hosts = screen.getByRole('heading', { name: /Affected hosts/ });
    const report = screen.getByRole('heading', { name: /Report text/ });
    const composer = screen.getByLabelText('New comment');
    // eslint-disable-next-line no-bitwise
    expect(hosts.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // eslint-disable-next-line no-bitwise
    expect(hosts.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Sections, not cards.
    expect(report.closest('.rounded-panel')).toBeNull();
  });

  // UX review 2026-10-02 U12 — five empty editors sat above the proof.
  it('orders the page: hosts, test evidence, report text, comments, history', async () => {
    mocked.listEvidenceRecords.mockResolvedValueOnce({
      total: 1, has_more: false,
      items: [{
        id: 31, host_test_id: 4, host_id: 5, host_ip: '10.0.0.5', finding_id: 7, finding_host_id: null,
        tool: 'nxc', command: 'nxc smb 10.0.0.5', outcome: 'finding', summary: 'Signing is not required',
        raw_output_preview: 'signing:False', raw_output_bytes: 13, raw_output_truncated_in_preview: false,
        observed_ip: null, executed_at: '2026-10-01T10:00:00Z', agent_session_id: null, recorded_by: 'Ana',
        agent_model: null, agent_client: null, created_at: '2026-10-01T10:00:00Z',
      }],
    });
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const inOrder = [
      screen.getByRole('heading', { name: /Affected hosts/ }),
      await screen.findByRole('heading', { name: /Test evidence/ }),
      screen.getByRole('heading', { name: /Report text/ }),
      screen.getByLabelText('New comment'),
      screen.getByRole('heading', { name: /Disposition history/ }),
    ];
    inOrder.slice(1).forEach((el, i) => {
      // eslint-disable-next-line no-bitwise
      expect(inOrder[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
    // The endpoint table keeps its anchor for `?endpoint=…#endpoints` links.
    expect(document.getElementById('endpoints')).not.toBeNull();
  });

  it('a status with no recorded change says it was set when the finding was created', async () => {
    mocked.getFinding.mockResolvedValue(finding({ status: 'confirmed', created_by_name: 'ana' }));
    renderAt('/findings/7');
    expect(await screen.findByText(/Confirmed since the finding was created by ana/)).toBeInTheDocument();
    expect(screen.queryByText(/No status changes recorded yet/)).toBeNull();
  });
});

// Walkthrough 2026-10-02 — drafts were listed a screen above the Report text
// they change, with the current text folded away under each; endpoint changes
// sat under the table instead of on their row.
describe('FindingDetail — each proposal is reviewed where it applies (5.334.0)', () => {
  const proposal = (over: Record<string, unknown>) => ({
    status: 'pending', source: 'agent', finding_id: 7, vulnerability_id: null, finding_host_id: null,
    field: null, payload: {}, current_value: null, base_value: null, base_recorded: true, changed_since_proposed: false,
    target: { finding_title: 'Weak TLS on portal', observation_title: null, host_id: null, host_ip: null },
    rationale: null, evidence_ids: [], agent_session_id: 81, proposed_by: 'Ana', agent_model: 'model-a',
    agent_client: null, prompt_version: null, created_at: '2026-10-02T10:00:00Z', decided_by: null,
    decided_at: null, decision_note: null, result_finding_id: null, error: null, ...over,
  });

  beforeEach(() => {
    mocked.getFinding.mockResolvedValue(finding({
      host_count: 1,
      hosts: [{ id: 55, host_id: 5, ip_address: '10.10.1.21', hostname: null, host_status: 'open' }],
      report_text: {
        description: 'TLS 1.0 is accepted.', impact: null, recommendation: 'Apply the vendor fix and re-scan.',
        references: null, steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
      },
    }));
    mocked.listProposals.mockResolvedValue({
      total: 3, has_more: false,
      items: [
        proposal({ id: 1, kind: 'finding_text', field: 'recommendation', current_value: 'Apply the vendor fix and re-scan.',
          base_value: 'Apply the vendor fix and re-scan.', payload: { value: 'Upgrade to 2.3.32, then re-scan.' } }),
        proposal({ id: 2, kind: 'finding_text', field: 'recommendation', agent_model: 'model-b',
          created_at: '2026-10-02T11:00:00Z', current_value: 'Apply the vendor fix and re-scan.',
          base_value: 'Apply the vendor fix and re-scan.', payload: { value: 'Restrict access, then patch.' } }),
        proposal({ id: 3, kind: 'endpoint_status', finding_host_id: 55, payload: { host_status: 'retest' },
          target: { finding_title: 'Weak TLS on portal', observation_title: null, host_id: 5, host_ip: '10.10.1.21' } }),
      ],
    });
  });

  it('summarises what waits at the top, and shows each draft beside the section’s current text', async () => {
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const summary = await screen.findByRole('heading', { name: /Proposals to review/ });
    const hosts = screen.getByRole('heading', { name: /Affected hosts/ });
    // eslint-disable-next-line no-bitwise
    expect(summary.compareDocumentPosition(hosts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('button', { name: /Review in Report text/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Review on its row/ })).toBeInTheDocument();

    // The drafts are inside Report text, under Recommendation, lettered.
    const drafts = within(screen.getByTestId('report-text-recommendation')).getByTestId('drafts-recommendation');
    expect(within(drafts).getByRole('tab', { name: /Draft A/ })).toHaveAttribute('aria-selected', 'true');
    expect(within(drafts).getByTestId('compare-current')).toHaveTextContent('Apply the vendor fix and re-scan.');
    expect(within(drafts).getByTestId('compare-proposed')).toHaveTextContent('Upgrade to 2.3.32, then re-scan.');
    fireEvent.click(within(drafts).getByRole('tab', { name: /Draft B/ }));
    expect(within(drafts).getByTestId('compare-proposed')).toHaveTextContent('Restrict access, then patch.');
    // The current text is shown once, not once per draft.
    expect(within(drafts).getAllByTestId('compare-current')).toHaveLength(1);
    // Two models: each tab names its model.
    expect(within(drafts).getByRole('tab', { name: /Draft A · model-a/ })).toBeInTheDocument();
    // The jump target clears the fixed chrome and the section label above it.
    expect(drafts.style.scrollMarginTop).toMatch(/var\(--topbar-h/);

    // The endpoint change is on its row.
    const row = document.querySelector('[data-endpoint-row="55"]') as HTMLElement;
    expect(within(row).getByText(/Proposed: Still present → Retest here/)).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /^Accept$/ })).toBeInTheDocument();
  });

  it('accepting a draft re-reads the finding and the proposals', async () => {
    mocked.acceptProposal.mockResolvedValue({ ...proposal({ id: 1, kind: 'finding_text', field: 'recommendation' }), status: 'accepted' });
    renderAt('/findings/7');
    const drafts = await screen.findByTestId('drafts-recommendation');
    const calls = mocked.listProposals.mock.calls.length;
    fireEvent.click(within(drafts).getByRole('button', { name: /^Accept$/ }));
    await waitFor(() => expect(mocked.acceptProposal).toHaveBeenCalledWith(1, 1, {}));
    await waitFor(() => expect(mocked.getFinding.mock.calls.length).toBeGreaterThan(1));
    await waitFor(() => expect(mocked.listProposals.mock.calls.length).toBeGreaterThan(calls));
  });

  // 5.351.0 — a decision was followed by a callback that re-read, a window
  // event that re-read the proposals again, and an invalidation: the pending
  // proposals were asked for two or three times.  The decision now says once
  // which reads are out of date.
  it('an accepted draft reads the finding, its history, its images and the proposals again — once each', async () => {
    mocked.acceptProposal.mockResolvedValue({ ...proposal({ id: 1, kind: 'finding_text', field: 'recommendation' }), status: 'accepted' });
    renderAt('/findings/7');
    const drafts = await screen.findByTestId('drafts-recommendation');
    await waitFor(() => expect(mocked.getFindingImages).toHaveBeenCalled());
    const before = {
      finding: mocked.getFinding.mock.calls.length, history: mocked.getFindingHistory.mock.calls.length,
      images: mocked.getFindingImages.mock.calls.length, proposals: mocked.listProposals.mock.calls.length,
    };
    fireEvent.click(within(drafts).getByRole('button', { name: /^Accept$/ }));
    await waitFor(() => expect(mocked.listProposals.mock.calls.length).toBe(before.proposals + 1));
    await waitFor(() => expect(mocked.getFinding.mock.calls.length).toBe(before.finding + 1));
    await waitFor(() => expect(mocked.getFindingHistory.mock.calls.length).toBe(before.history + 1));
    await waitFor(() => expect(mocked.getFindingImages.mock.calls.length).toBe(before.images + 1));
    // …and no second round follows.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(mocked.listProposals.mock.calls.length).toBe(before.proposals + 1);
    expect(mocked.getFinding.mock.calls.length).toBe(before.finding + 1);
    expect(toastMock.warning).not.toHaveBeenCalled();
  });

  it('an accepted draft whose finding cannot be read again says so and keeps the page', async () => {
    mocked.acceptProposal.mockResolvedValue({ ...proposal({ id: 1, kind: 'finding_text', field: 'recommendation' }), status: 'accepted' });
    renderAt('/findings/7');
    const drafts = await screen.findByTestId('drafts-recommendation');
    mocked.getFinding.mockRejectedValueOnce(new Error('down'));
    fireEvent.click(within(drafts).getByRole('button', { name: /^Accept$/ }));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledWith(
      'Accepted, but the page could not refresh — reload to see the change.',
    ));
    expect(screen.getByText('Weak TLS on portal')).toBeInTheDocument();
  });

  // The two refreshes share one lane: the status change's slower re-read used
  // to land after the accept's and put the page back to the older finding.
  it('a slow refresh never lands over a newer one', async () => {
    const user = userEvent.setup();
    mocked.acceptProposal.mockResolvedValue({ ...proposal({ id: 1, kind: 'finding_text', field: 'recommendation' }), status: 'accepted' });
    renderAt('/findings/7');
    const drafts = await screen.findByTestId('drafts-recommendation');

    let landOlder!: (f: unknown) => void;
    mocked.getFinding.mockImplementationOnce(() => new Promise((resolve) => { landOlder = resolve; }));
    await user.click(screen.getByLabelText('Finding status'));
    await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
    await waitFor(() => expect(mocked.getFinding).toHaveBeenCalledTimes(2));

    mocked.getFinding.mockResolvedValue(finding({ title: 'After the accept', status: 'confirmed' }));
    fireEvent.click(within(drafts).getByRole('button', { name: /^Accept$/ }));
    expect(await screen.findByText('After the accept')).toBeInTheDocument();

    await act(async () => { landOlder(finding({ title: 'Before the accept', status: 'confirmed' })); });
    expect(screen.getByText('After the accept')).toBeInTheDocument();
    expect(screen.queryByText('Before the accept')).not.toBeInTheDocument();
    // The status control is released by the read that superseded its own.
    expect(screen.queryByLabelText('Saving status')).not.toBeInTheDocument();
  });
});

describe('FindingDetail — M2: history is not a prerequisite', () => {
  it('renders the finding when history fails, with an explicit unavailable message and Retry', async () => {
    mocked.getFindingHistory.mockRejectedValueOnce(new Error('boom'));
    renderAt('/findings/7');

    await screen.findByText('Weak TLS on portal');
    expect(await screen.findByText(/History unavailable/)).toBeInTheDocument();
    // Not presented as "nothing happened".
    expect(screen.queryByText(/No status changes recorded yet/)).toBeNull();

    mocked.getFindingHistory.mockResolvedValueOnce([
      { id: 1, from_status: 'open', to_status: 'confirmed', changed_by_id: 1, changed_by_name: 'ana', summary: null, created_at: '2026-08-02T00:00:00Z' },
    ]);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(screen.getByText('Confirmed')).toBeInTheDocument());
    expect(screen.queryByText(/History unavailable/)).toBeNull();
  });
});

describe('FindingDetail — M1: return to the queue it came from', () => {
  it('the Findings action returns to the carried list URL (filters + page + sort)', async () => {
    const from = '/findings?status=all&severity=high&page=3&sort=severity&dir=desc';
    renderAt(`/findings/7?from=${encodeURIComponent(from)}`);
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith(from);
  });

  it.each([
    'https://evil.example/findings',
    '//evil.example/findings',
    '/hosts?x=1',
    '/findings/9',
  ])('falls back to the bare list for an unsafe or foreign return target: %s', async (bad) => {
    renderAt(`/findings/7?from=${encodeURIComponent(bad)}`);
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/findings');
  });

  it('a direct link with no return state goes to the bare list', async () => {
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/findings');
  });
});

describe('FindingDetail — v5.256.0: the author renames or deletes', () => {
  it('offers neither to someone who may not modify it', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: false, created_by_name: 'Alice' }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Rename/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });

  it('renames in place', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true }));
    mocked.updateFinding.mockResolvedValue(finding({ can_modify: true, title: 'TLS 1.0 on portal' }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Rename/ }));
    fireEvent.change(screen.getByLabelText('Finding title'), { target: { value: '  TLS 1.0 on portal ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateFinding).toHaveBeenCalledWith(1, 7, { title: 'TLS 1.0 on portal' }));
    expect(await screen.findByText('TLS 1.0 on portal')).toBeInTheDocument();
  });

  it('deletes only after confirmation, then returns to the list', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true }));
    mocked.deleteFinding.mockResolvedValue(undefined);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    expect(mocked.deleteFinding).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(mocked.deleteFinding).toHaveBeenCalledWith(1, 7));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/findings'));
  });

  // With remediation tracking on, the server refuses an analyst a removal
  // that would take a filled-in remediation record with it.
  const REFUSAL = 'This finding has a remediation record on this host (a contact, dates or a status), which '
    + 'would be deleted with it. A project admin must remove it. Nothing was changed.';
  const refused = { response: { status: 409, data: { detail: REFUSAL } } };

  it('a refused delete says the server’s reason and stays on the finding', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true }));
    mocked.deleteFinding.mockRejectedValue(refused);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(REFUSAL));
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(screen.getByText('Weak TLS on portal')).toBeInTheDocument();
  });

  it('a refused detach says the server’s reason and keeps the endpoint on the page', async () => {
    mocked.getFinding.mockResolvedValue(finding({
      status: 'confirmed', host_count: 1, endpoint_status_counts: { open: 1 },
      hosts: [{ id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' }],
    }));
    mocked.removeFindingEndpoint.mockRejectedValue(refused);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    confirmMock.mockResolvedValueOnce(true);
    // Removal is in the row's menu, not an icon beside the state control.
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Actions for 10.0.0.5' }));
    await user.click(await screen.findByRole('menuitem', { name: /Remove from finding/ }));
    await waitFor(() => expect(mocked.removeFindingEndpoint).toHaveBeenCalledWith(1, 7, 31));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(REFUSAL));
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Actions for 10.0.0.5' })).toBeInTheDocument();
  });
});

describe('FindingDetail — report text (v5.260.0)', () => {
  const reportText = (over: Record<string, unknown> = {}) => ({
    description: 'TLS 1.0 is enabled.', impact: null, recommendation: null, references: null,
    steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false, ...over,
  });

  it('shows the text as written and names what is still empty', async () => {
    mocked.getFinding.mockResolvedValue(finding({ report_text: reportText() }));
    renderAt('/findings/7');
    expect(await screen.findByText('TLS 1.0 is enabled.')).toBeInTheDocument();
    expect(screen.getByText('impact, recommendation')).toBeInTheDocument();
    // Not the author and not an admin: no editor.
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it('saves only the fields that changed', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true, report_text: reportText() }));
    mocked.updateFinding.mockResolvedValue(
      finding({ can_modify: true, report_text: reportText({ impact: 'Traffic can be read.' }) }),
    );
    renderAt('/findings/7');
    await screen.findByText('TLS 1.0 is enabled.');
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Traffic can be read.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report text' }));
    await waitFor(() => expect(mocked.updateFinding).toHaveBeenCalledWith(1, 7, { impact: 'Traffic can be read.' }));
    expect(await screen.findByText('Traffic can be read.')).toBeInTheDocument();
  });

  const image57 = {
    id: 57, note_id: 3, filename: 'relay.png', caption: 'The relayed session', content_type: 'image/png',
    size_bytes: 10, in_report: true, printable: true, placed_in: ['description'], uploaded_by_id: 1,
    by_agent: false, created_at: null, can_edit: true,
  };
  const comment = {
    id: 3, host_id: null, finding_id: 7, body: 'proof', author_id: 1, author_name: 'tester', actor_type: 'user',
    parent_id: null, thread_root_id: 3, created_at: '2026-09-01T00:00:00Z', updated_at: null,
    attachments: [{
      id: 57, filename: 'relay.png', content_type: 'image/png', size_bytes: 10, created_at: '2026-09-01T00:00:00Z',
      include_in_report: true, caption: 'The relayed session', uploaded_by_id: 1,
    }],
  };

  it('shows an image the text places, where it is placed, and offers it in the editor', async () => {
    URL.revokeObjectURL = vi.fn();
    mocked.getFindingImages.mockResolvedValue({ items: [image57], caption_max: 2000 });
    mocked.getFindingNotes.mockResolvedValue([comment]);
    mocked.getFinding.mockResolvedValue(finding({
      can_modify: true,
      report_text: reportText({ description: 'Relayed.\n\n![](evidence:57)\n\n![x](evidence:999)' }),
    }));
    renderAt('/findings/7');
    await waitFor(() => expect(mocked.getFindingImages).toHaveBeenCalledWith(1, 7, expect.any(AbortSignal)));
    // The section shows the picture (fetched through the attachment route for
    // an id on the finding's list) and says so for a reference that is not one.
    const placed = await screen.findByTestId('evidence-image-57');
    await waitFor(() => expect(placed.querySelector('img')?.getAttribute('src')).toBe('blob:img'));
    expect(placed).toHaveTextContent('Figure: The relayed session');
    expect(screen.getByTestId('evidence-missing-999')).toHaveTextContent('Image not available');
    // The comment's image row says where it prints.
    expect(await screen.findByTestId('placement-57')).toHaveTextContent('In: Description');
    expect(screen.getByTestId('caption-57')).toHaveTextContent('The relayed session');
    // The editor offers it (opened from an empty section; the comment has an
    // Edit button of its own).
    fireEvent.click(screen.getAllByRole('button', { name: 'Not written yet. Write it' })[0]);
    expect(screen.getAllByRole('button', { name: 'Insert image' })[0]).toBeEnabled();
  });

  // Review 2026-10-01 S4 — "not one of this finding's images" may only be
  // said once the finding's image list has been read.
  it('a placed image is a neutral placeholder while the list is being read, then the image — fetched once for the page', async () => {
    URL.revokeObjectURL = vi.fn();
    let release!: (v: unknown) => void;
    mocked.getFindingImages.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    mocked.getFindingNotes.mockResolvedValue([comment]);
    mocked.getFinding.mockResolvedValue(finding({
      can_modify: true, report_text: reportText({ description: 'Relayed.\n\n![](evidence:57)' }),
    }));
    renderAt('/findings/7');
    expect(await screen.findByTestId('evidence-pending-57')).toHaveTextContent('Loading image…');
    expect(screen.queryByText(/Image not available/)).toBeNull();
    expect(screen.queryByTestId('finding-images-error')).toBeNull();
    // Nothing is fetched for the thread's thumbnail either while the list is
    // on its way: it comes from the same cache.
    await screen.findByRole('button', { name: 'View relay.png' });
    expect(mocked.getNoteAttachmentObjectUrl).not.toHaveBeenCalled();

    await act(async () => { release({ items: [image57], caption_max: 2000 }); });
    const placed = await screen.findByTestId('evidence-image-57');
    await waitFor(() => expect(placed.querySelector('img')?.getAttribute('src')).toBe('blob:img'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'View relay.png' }).querySelector('img')).not.toBeNull());
    // M6 — the placed image and the comment's thumbnail are one fetch.
    expect(mocked.getNoteAttachmentObjectUrl).toHaveBeenCalledTimes(1);
  });

  it('says the images could not be loaded when the list fails — on the page and in the text — and retries', async () => {
    URL.revokeObjectURL = vi.fn();
    mocked.getFindingImages.mockRejectedValueOnce(new Error('boom'));
    mocked.getFindingNotes.mockResolvedValue([]);
    mocked.getFinding.mockResolvedValue(finding({
      can_modify: true, report_text: reportText({ description: '![](evidence:57)' }),
    }));
    renderAt('/findings/7');
    const note = await screen.findByTestId('evidence-list-failed-57');
    expect(note).toHaveTextContent('This finding’s images could not be loaded');
    expect(screen.queryByText(/Image not available/)).toBeNull();
    const alert = screen.getByTestId('finding-images-error');
    expect(alert).toHaveTextContent('Images placed in the report text cannot be shown');

    mocked.getFindingImages.mockResolvedValue({ items: [image57], caption_max: 2000 });
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('evidence-image-57')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('finding-images-error')).toBeNull());
  });

  it('a reader sees the placed image and its caption, and no way to change either', async () => {
    URL.revokeObjectURL = vi.fn();
    projectRole.value = 'viewer';
    mocked.getFindingImages.mockResolvedValue({ items: [{ ...image57, can_edit: false }], caption_max: 2000 });
    mocked.getFindingNotes.mockResolvedValue([comment]);
    mocked.getFinding.mockResolvedValue(finding({
      can_modify: false, report_text: reportText({ description: '![](evidence:57)' }),
    }));
    renderAt('/findings/7');
    expect(await screen.findByTestId('evidence-image-57')).toBeInTheDocument();
    expect(await screen.findByTestId('caption-57')).toHaveTextContent('The relayed session');
    expect(screen.queryByRole('button', { name: 'Insert image' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /caption for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /in the report/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete relay.png' })).not.toBeInTheDocument();
  });

  it('refuses an out-of-range score before sending', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true, report_text: reportText() }));
    renderAt('/findings/7');
    await screen.findByText('TLS 1.0 is enabled.');
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Score'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report text' }));
    expect(await screen.findByText('A CVSS score is a number from 0.0 to 10.0.')).toBeInTheDocument();
    expect(mocked.updateFinding).not.toHaveBeenCalled();
  });
});

describe('FindingDetail — add affected hosts', () => {
  const attached = {
    id: 90, host_id: 11, ip_address: '10.0.0.11', hostname: 'dc01', host_status: 'open', name_id: null, fqdn: null,
  };
  const hostRow = (id: number, ip: string, hostname: string | null = null) => ({ id, ip_address: ip, hostname });

  it('searches, picks several hosts across searches, and adds them in one request', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(finding({ host_count: 1, hosts: [attached] }));
    mocked.getHosts.mockImplementation(async (_projectId: number, { search }: { search?: string }) => ({
      items: search === 'web'
        ? [hostRow(13, '10.0.0.13', 'web01.' + 'x'.repeat(200))]
        : [hostRow(11, '10.0.0.11', 'dc01'), hostRow(12, '10.0.0.12', 'dc02')],
      total: null,
    }));
    mocked.addFindingHosts.mockResolvedValue(finding({
      host_count: 3,
      hosts: [
        attached,
        { ...attached, id: 91, host_id: 12, ip_address: '10.0.0.12' },
        { ...attached, id: 92, host_id: 13, ip_address: '10.0.0.13' },
      ],
    }));

    renderAt('/findings/7');
    await user.click(await screen.findByRole('button', { name: /Add hosts/ }));

    // The host already on the finding is shown but cannot be picked.
    const dc01 = await screen.findByRole('checkbox', { name: 'Add 10.0.0.11' });
    expect(dc01).toBeDisabled();
    expect(screen.getByText('already affected')).toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: 'Add 10.0.0.12' }));
    // A second search keeps the first pick.
    await user.type(screen.getByLabelText('Search hosts'), 'web');
    await user.click(await screen.findByRole('checkbox', { name: 'Add 10.0.0.13' }));
    expect(screen.getByLabelText('Selected hosts')).toHaveTextContent('10.0.0.12');

    await user.click(screen.getByRole('button', { name: 'Add 2 hosts' }));
    await waitFor(() => expect(mocked.addFindingHosts).toHaveBeenCalledWith(1, 7, [12, 13]));
    const heading = await screen.findByRole('heading', { name: /Affected hosts/ });
    await waitFor(() => expect(heading).toHaveTextContent('Affected hosts3'));
    expect(toastMock.success).toHaveBeenCalledWith('Added 2 hosts.');
    expect(mocked.getFindingHistory).toHaveBeenCalledTimes(2); // refreshed after the add
  });

  it('keeps the dialog open with the reason when the add fails', async () => {
    const user = userEvent.setup();
    mocked.getHosts.mockResolvedValue({ items: [hostRow(12, '10.0.0.12')], total: null });
    mocked.addFindingHosts.mockRejectedValue(new Error('Hosts [12] are not in this project.'));
    renderAt('/findings/7');
    await user.click(await screen.findByRole('button', { name: /Add hosts/ }));
    await user.click(await screen.findByRole('checkbox', { name: 'Add 10.0.0.12' }));
    await user.click(screen.getByRole('button', { name: 'Add 1 host' }));
    expect(await screen.findByText(/not in this project|could not be added/)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

// The hosts are a bounded panel, so the page says where its sections are.
describe('FindingDetail — the jump bar', () => {
  const scrollIntoView = vi.fn();
  beforeEach(() => {
    scrollIntoView.mockClear();
    Element.prototype.scrollIntoView = scrollIntoView;
    // Nothing waiting and no test evidence, whatever an earlier block left set.
    mocked.listProposals.mockResolvedValue({ items: [], total: 0, has_more: false });
    mocked.listEvidenceRecords.mockResolvedValue({ items: [], total: 0, has_more: false });
  });
  const hosts = (n: number) => Array.from({ length: n }, (_, i) => ({
    id: 100 + i, host_id: 10 + i, ip_address: `10.0.0.${i + 1}`, hostname: null, name_id: null, fqdn: null, host_status: 'open',
  }));
  const entries = async () => {
    const bar = await screen.findByRole('navigation', { name: 'Sections of this finding' });
    return within(bar).getAllByRole('button').map((b) => b.textContent);
  };

  it('names the sections the page shows, in page order, with what the page knows about each', async () => {
    mocked.getFinding.mockResolvedValue(finding({
      host_count: 3, hosts: hosts(3),
      report_text: {
        description: 'TLS 1.0 is accepted.', impact: null, recommendation: null, references: null,
        steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
      },
    }));
    mocked.getFindingNotes.mockResolvedValue([
      { id: 1, parent_id: null, body: 'one', author_id: 1, author_name: 'tester', created_at: '2026-10-01T10:00:00Z', attachments: [] },
      { id: 2, parent_id: null, body: 'two', author_id: 1, author_name: 'tester', created_at: '2026-10-01T11:00:00Z', attachments: [] },
    ]);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    // No proposals waiting and no test evidence: neither is offered.
    await waitFor(async () => expect(await entries()).toEqual([
      'Affected hosts3', 'Report text2 empty', 'Comments & evidence2', 'Disposition history',
    ]));
  });

  it('offers Proposals and Test evidence when the page shows them', async () => {
    mocked.listProposals.mockResolvedValue({
      total: 1, has_more: false,
      items: [{
        id: 1, kind: 'finding_text', field: 'impact', status: 'pending', source: 'agent', finding_id: 7,
        vulnerability_id: null, finding_host_id: null, payload: { value: 'An attacker reads the session.' },
        current_value: null, base_value: null, base_recorded: true, changed_since_proposed: false,
        target: { finding_title: 'Weak TLS on portal', observation_title: null, host_id: null, host_ip: null },
        rationale: null, evidence_ids: [], agent_session_id: 81, proposed_by: 'Ana', agent_model: 'model-a',
        agent_client: null, prompt_version: null, created_at: '2026-10-02T10:00:00Z', decided_by: null,
        decided_at: null, decision_note: null, result_finding_id: null, error: null,
      }],
    });
    mocked.listEvidenceRecords.mockResolvedValue({
      total: 1, has_more: false,
      items: [{
        id: 31, host_test_id: 4, host_id: 5, host_ip: '10.0.0.5', finding_id: 7, finding_host_id: null,
        tool: 'nxc', command: 'nxc smb 10.0.0.5', outcome: 'finding', summary: 'Signing is not required',
        raw_output_preview: 'signing:False', raw_output_bytes: 13, raw_output_truncated_in_preview: false,
        observed_ip: null, executed_at: '2026-10-01T10:00:00Z', agent_session_id: null, recorded_by: 'Ana',
        agent_model: null, agent_client: null, created_at: '2026-10-01T10:00:00Z',
      }],
    });
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    await waitFor(async () => expect((await entries()).slice(0, 3)).toEqual(['Proposals1', 'Affected hosts0', 'Test evidence']));
  });

  it('a click scrolls to that section', async () => {
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    const bar = await screen.findByRole('navigation', { name: 'Sections of this finding' });
    scrollIntoView.mockClear();
    fireEvent.click(within(bar).getByRole('button', { name: /Disposition history/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    const target = scrollIntoView.mock.instances[0] as unknown as HTMLElement;
    expect(target.id).toBe('section-history');
    expect(within(target).getByRole('heading', { name: /Disposition history/ })).toBeInTheDocument();
    expect(target.style.scrollMarginTop).toMatch(/var\(--topbar-h/);
  });

  it('`?endpoint=` brings the hosts panel into view with the row in it', async () => {
    mocked.getFinding.mockResolvedValue(finding({ host_count: 40, hosts: hosts(40) }));
    renderAt('/findings/7?endpoint=135');
    await screen.findByText('Weak TLS on portal');
    await waitFor(() => expect(document.querySelector('[data-endpoint-row="135"]')).not.toBeNull());
    expect(scrollIntoView.mock.instances.map((el) => (el as unknown as HTMLElement).id)).toContain('endpoints');
  });
});

describe('FindingDetail — removing an endpoint from its row menu', () => {
  it('removes after confirmation and offers Undo, which puts the same row back', async () => {
    const user = userEvent.setup();
    const row = { id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: 9, fqdn: 'a.example.com', host_status: 'retest' };
    const other = { id: 32, host_id: 6, ip_address: '10.0.0.6', hostname: null, name_id: null, fqdn: null, host_status: 'open' };
    const before = finding({ status: 'confirmed', host_count: 2, hosts: [row, other] });
    mocked.getFinding.mockResolvedValue(before);
    mocked.removeFindingEndpoint.mockResolvedValue({ ...before, host_count: 1, hosts: [other] });
    mocked.addFindingHosts.mockResolvedValue(before);
    confirmMock.mockResolvedValueOnce(true);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    await user.click(screen.getByRole('button', { name: 'Actions for a.example.com on 10.0.0.5' }));
    await user.click(await screen.findByRole('menuitem', { name: /Remove from finding/ }));
    await waitFor(() => expect(mocked.removeFindingEndpoint).toHaveBeenCalledWith(1, 7, 31));
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Remove endpoint from finding?', severity: 'danger' }));
    await waitFor(() => expect(document.querySelector('[data-endpoint-row="31"]')).toBeNull());

    const [message, options] = toastMock.success.mock.calls[0];
    expect(message).toBe('Removed a.example.com (10.0.0.5) from the finding.');
    expect(options.action.label).toBe('Undo');
    await act(async () => { options.action.onClick(); });
    expect(mocked.addFindingHosts).toHaveBeenCalledWith(1, 7, [], [{ host_id: 5, name_id: 9, host_status: 'retest' }]);
    await waitFor(() => expect(document.querySelector('[data-endpoint-row="31"]')).not.toBeNull());
  });
});
