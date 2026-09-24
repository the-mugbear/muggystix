/**
 * Regression guard for the React #310 crash: a hook (the #note-hash deep-link
 * effect) was placed AFTER HostInspector's loading/!host early returns, so it
 * ran only once the host loaded — a hooks-order violation that crashed the
 * page. This renders the real component through the loading→loaded transition
 * (the exact trigger). The Hosts page test stubs HostInspector, so only a
 * real-render test catches this.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock('../../services/api', () => ({
  getHost: vi.fn().mockResolvedValue({
    id: 1, ip_address: '10.0.0.1', hostname: 'h1', state: 'up',
    ports: [], assignees: [], tags: [], vulnerabilities: [], notes: [],
    discoveries: [], follow: null,
    os_name: null, os_family: null, os_type: null, os_generation: null,
    os_vendor: null, os_accuracy: null, smb_signing: null,
    web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
    first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
  }),
  getHostConflicts: vi.fn().mockResolvedValue([]),
  getHostTestPlanEntries: vi.fn().mockResolvedValue([]),
  getHostFollowers: vi.fn().mockResolvedValue([]),
  recordHostView: vi.fn().mockResolvedValue(undefined),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  // Interaction-only handlers — present so the named imports resolve.
  followHost: vi.fn(), unfollowHost: vi.fn(), assignHost: vi.fn(), unassignHost: vi.fn(),
  createNote: vi.fn(), updateAnnotation: vi.fn(), deleteAnnotation: vi.fn(),
  uploadNoteAttachment: vi.fn(), promoteAnnotation: vi.fn(),
  promoteVulnerability: vi.fn(), previewPromoteVulnerability: vi.fn(),
  updateTestPlanEntry: vi.fn(), getHostNotes: vi.fn().mockResolvedValue([]),
}));

// Stub the heavy child cards (each fetches its own data) so the test exercises
// HostInspector's own hooks, not theirs.
vi.mock('../../components/WebInterfacesCard', () => ({ default: () => null }));
vi.mock('../../components/NseScriptsCard', () => ({ default: () => null }));
vi.mock('../../components/NetExecCard', () => ({ default: () => null }));
vi.mock('../../components/HostFindingsCard', () => ({ default: () => null }));
vi.mock('../../components/HostNamesCard', () => ({ default: () => null }));
vi.mock('../../components/HostLineagePanel', () => ({ default: () => null }));
// PortDetailsCard fetches web interfaces (getHostWebInterfaces) which the api
// mock above doesn't provide; stub it like the other fetching child cards.
vi.mock('../../components/host-inspector/PortDetailsCard', () => ({ default: () => null }));
vi.mock('../../components/EntryResultsPanel', () => ({ default: () => <p>results panel</p> }));

import HostInspector from '../../components/HostInspector';

import * as api from '../../services/api';

// v5.241.0 — a finished entry was dimmed but kept its full height, so done work
// outweighed the work still to do.
describe('HostInspector — proposed tests', () => {
  it('a completed entry is one line until asked for; an open one shows its tests', async () => {
    const entry = (over: Record<string, unknown>) => ({
      id: 1, test_plan_id: 9, plan_title: 'Plan A', plan_status: 'approved', host_id: 1,
      priority: 'high', test_phase: 'enumeration', proposed_tests: ['nmap -sV'], rationale: '',
      status: 'proposed', created_at: '2026-06-14T00:00:00Z', updated_at: '2026-06-14T00:00:00Z', ...over,
    });
    (api.getHostTestPlanEntries as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      entry({ id: 1, status: 'completed', proposed_tests: ['done-test'], findings: 'All clear.' }),
      entry({ id: 2, status: 'proposed', proposed_tests: ['todo-test'] }),
    ]);
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);

    expect(await screen.findByText('todo-test')).toBeInTheDocument();
    expect(screen.queryByText('done-test')).not.toBeInTheDocument();
    expect(screen.queryByText('All clear.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '1 test · summary · show' }));
    expect(screen.getByText('done-test')).toBeInTheDocument();
    expect(screen.getByText('All clear.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'hide detail' })).toHaveAttribute('aria-expanded', 'true');
  });
});

// v5.243.0 — the conflicts panel showed source, method and scan for the selected
// value and no date. The API returns ONE confidence row per host field, so the
// fixture has one: an earlier version of this test fed it two rows for the same
// field — a shape the endpoint cannot produce — to exercise a "lower-ranked
// source is newer" warning that therefore could never fire, and was removed.
//
// v5.246.0 — reported by the user: a host read "1 conflict" and nothing said
// what the conflict was. The panel only rendered history under a confidence
// heading, and dedup-written conflicts have NO confidence record — which is
// this fixture now (`confidence: []`). The panel detail is pinned in
// HostConflictsPanel.test.tsx; this proves the inspector wires it up.
describe('HostInspector — the conflict badge opens the disagreement itself', () => {
  it('names both values with no confidence record present', async () => {
    (api.getHostConflicts as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      conflict_count: 1,
      confidence: [],
      conflict_history: [{
        id: 9, object_type: 'host', object_id: 1, field_name: 'os_name',
        previous_value: 'Linux 4.x', previous_confidence: 60, previous_scan_id: 2, previous_method: 'masscan',
        new_value: 'Windows Server 2022', new_confidence: 95, new_scan_id: 3, new_method: 'nmap -O',
        resolved_at: '2026-06-01T00:00:00Z',
      }],
    });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: /1 conflict/ }));
    expect(await screen.findByText('Scans disagreed about this host')).toBeInTheDocument();
    expect(screen.getByText('Linux 4.x')).toBeInTheDocument();
    expect(screen.getByText('Windows Server 2022')).toBeInTheDocument();
  });
});

// v5.245.0 — reported by the user: "Promote to finding" could only create the
// finding across EVERY host carrying the issue, including hosts nobody had
// verified. The dismiss dialog had a "this host only" choice; promote did not.
describe('HostInspector — promoting a scanner observation', () => {
  const hostWithObservation = {
    id: 1, ip_address: '10.0.0.1', hostname: 'h1', state: 'up',
    ports: [], assignees: [], tags: [], notes: [], discoveries: [], follow: null,
    os_name: null, os_family: null, os_type: null, os_generation: null,
    os_vendor: null, os_accuracy: null, smb_signing: null,
    web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
    first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
    vulnerabilities: [{
      id: 77, plugin_id: '100', title: 'PostgreSQL Weak Password Policy', severity: 'high', source: 'openvas',
      cvss_score: null, cvss_vector: null, cve_id: null, scan_id: 1, port_id: null, port_number: 5432,
      protocol: 'tcp', service_name: 'postgresql', exploitable: null, finding_id: null,
      first_seen: '2026-08-01T00:00:00Z', last_seen: '2026-08-01T00:00:00Z', solution: null,
    }],
    vulnerability_summary: { total_vulnerabilities: 1, critical: 0, high: 1, medium: 0, low: 0, info: 0 },
  };
  const preview = {
    plugin_id: '100', issue_key: 'k', affected_host_count: 2, affected_host_sample: ['10.0.0.1', '10.0.0.9'],
    new_host_count: 2, already_promoted: false, finding_id: null, finding_status: null, host_ip: '10.0.0.1',
  };

  const openPromote = async () => {
    (api.getHost as ReturnType<typeof vi.fn>).mockResolvedValueOnce(hostWithObservation);
    (api.previewPromoteVulnerability as ReturnType<typeof vi.fn>).mockResolvedValue(preview);
    (api.promoteVulnerability as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 5, title: 'x', host_count: 1 });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    // The row's own expand button — the pivot icons carry the title too.
    fireEvent.click(await screen.findByRole('button', { name: /^HIGH\s*PostgreSQL Weak Password Policy$/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^Promote PostgreSQL Weak Password Policy/ }));
    return screen.findByRole('dialog');
  };

  it('defaults to THIS host, says what that means, and sends it', async () => {
    const dialog = await openPromote();
    const thisHost = await screen.findByRole('radio', { name: /This host only/ });
    expect(thisHost).toBeChecked();
    expect(dialog).toHaveTextContent('Creates a finding for 10.0.0.1 only.');
    expect(dialog).toHaveTextContent(/The other 1 host reporting this issue stay untriaged/);

    fireEvent.click(screen.getByRole('button', { name: /^Promote$/ }));
    await waitFor(() => expect(api.promoteVulnerability).toHaveBeenCalledWith(
      77, expect.objectContaining({ status: 'confirmed', scope: 'host' }),
    ));
  });

  // v5.289.0 — "Scanner observations 5 … from 6 scanner observations" read as
  // a contradiction; the header names both units and explains the grouping.
  it('names issues and scanner rows when rows of one issue are grouped', async () => {
    const base = hostWithObservation.vulnerabilities[0];
    (api.getHost as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...hostWithObservation,
      vulnerabilities: [
        base,
        { ...base, id: 78, port_number: 5433 },   // same issue, another port
        { ...base, id: 79, plugin_id: '200', title: 'Telnet Service Enabled', port_number: 23 },
      ],
      vulnerability_summary: { total_vulnerabilities: 3, critical: 0, high: 3, medium: 0, low: 0, info: 0 },
    });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const note = await screen.findByTestId('observation-grouping');
    expect(note).toHaveTextContent('2 issues · from 3 scanner rows');
    expect(screen.getByRole('button', { name: 'About issues and scanner rows' })).toBeInTheDocument();
    expect(screen.queryByText(/from 3 scanner observations/)).not.toBeInTheDocument();
  });

  it('can still be widened to every host carrying the issue, explicitly', async () => {
    await openPromote();
    fireEvent.click(await screen.findByRole('radio', { name: /All 2 hosts carrying this issue/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Promote$/ }));
    await waitFor(() => expect(api.promoteVulnerability).toHaveBeenCalledWith(
      77, expect.objectContaining({ status: 'confirmed', scope: 'issue' }),
    ));
  });
});

describe('HostInspector smoke', () => {
  it('renders through loading→loaded without a hooks-order crash', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    // If a hook sat below the early return, this transition throws React #310.
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
  });

  // v5.215.0 — informational rows are left out of the detail payload; the
  // card says how many are hidden and refetches with them on request.
  it('offers to load hidden informational findings and refetches with include_info', async () => {
    const getHost = api.getHost as unknown as ReturnType<typeof vi.fn>;
    const base = await getHost.getMockImplementation()!();
    getHost.mockResolvedValueOnce({
      ...base, informational_count: 3, informational_included: false,
      vulnerability_summary: { total_vulnerabilities: 3, critical: 0, high: 0, medium: 0, low: 0, info: 3 },
    });
    getHost.mockResolvedValueOnce({
      ...base, informational_count: 3, informational_included: true,
      vulnerability_summary: { total_vulnerabilities: 3, critical: 0, high: 0, medium: 0, low: 0, info: 3 },
    });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const btn = await screen.findByRole('button', { name: 'Show 3 informational findings' });
    expect(btn).toHaveTextContent('3 informational hidden · show');
    fireEvent.click(btn);
    await waitFor(() => expect(getHost).toHaveBeenLastCalledWith(1, { includeInfo: true }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Show 3 informational findings' })).not.toBeInTheDocument(),
    );
  });

  // Review follow-up: a late "show informational" response for host A must not
  // be merged into host B after the operator has navigated on.
  it('drops a late informational response after navigating to another host', async () => {
    const getHost = api.getHost as unknown as ReturnType<typeof vi.fn>;
    const base = await getHost.getMockImplementation()!();
    let resolveA!: (v: unknown) => void;
    const deferredA = new Promise((res) => { resolveA = res; });
    getHost
      // host 1 primary fetch: 3 hidden
      .mockResolvedValueOnce({ ...base, id: 1, ip_address: '10.0.0.1', informational_count: 3, informational_included: false })
      // host 1 "show informational": deferred
      .mockReturnValueOnce(deferredA)
      // host 2 primary fetch: 2 hidden
      .mockResolvedValueOnce({ ...base, id: 2, ip_address: '10.0.0.2', informational_count: 2, informational_included: false });

    const { rerender } = render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Show 3 informational findings' }));
    rerender(<MemoryRouter><HostInspector hostId={2} /></MemoryRouter>);
    await screen.findByRole('button', { name: 'Show 2 informational findings' });

    resolveA({ ...base, id: 1, ip_address: '10.0.0.1', informational_count: 3, informational_included: true });
    await new Promise((r) => setTimeout(r, 0));
    // Had A's response been merged, informational_included would be true and
    // host 2's "show" button would have vanished.
    expect(screen.getByRole('button', { name: 'Show 2 informational findings' })).toBeInTheDocument();
    expect(screen.getByText('10.0.0.2')).toBeInTheDocument();
  });
});
