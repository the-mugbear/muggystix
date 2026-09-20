/**
 * Regression guard for the React #310 crash: a hook (the #note-hash deep-link
 * effect) was placed AFTER HostInspector's loading/!host early returns, so it
 * ran only once the host loaded — a hooks-order violation that crashed the
 * page. This renders the real component through the loading→loaded transition
 * (the exact trigger). The Hosts page test stubs HostInspector, so only a
 * real-render test catches this.
 */
import React from 'react';
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

// v5.243.0 — the conflicts panel ranked sources by detection method and showed
// no dates, so nothing revealed that the selected value might be the OLDER one.
describe('HostInspector — conflicts say when each side was recorded', () => {
  const side = (over: Record<string, unknown>) => ({
    id: 1, field_name: 'os_name', confidence_score: 95, scan_type: 'nmap',
    data_source: 'nmap -O', method: 'os_detection', scan_id: 3,
    updated_at: '2026-06-01T00:00:00Z', ...over,
  });

  it('dates both sides and flags a lower-ranked source that is more recent', async () => {
    (api.getHostConflicts as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      conflict_count: 1,
      confidence: [
        side({ id: 1, confidence_score: 95, updated_at: '2026-06-01T00:00:00Z' }),
        side({ id: 2, confidence_score: 60, data_source: 'nessus', scan_id: 9, updated_at: '2026-09-01T00:00:00Z' }),
      ],
      conflict_history: [],
    });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: /1 conflict/ }));
    expect(await screen.findByText(/A lower-ranked source recorded this field more recently/)).toBeInTheDocument();
    expect(screen.getAllByText(/^recorded /)).toHaveLength(2);
  });

  it('stays quiet when the selected value is also the newest', async () => {
    (api.getHostConflicts as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      conflict_count: 1,
      confidence: [
        side({ id: 1, confidence_score: 95, updated_at: '2026-09-01T00:00:00Z' }),
        side({ id: 2, confidence_score: 60, data_source: 'nessus', updated_at: '2026-06-01T00:00:00Z' }),
      ],
      conflict_history: [],
    });
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /1 conflict/ }));
    await screen.findByText(/Selected value/);
    expect(screen.queryByText(/lower-ranked source recorded/)).not.toBeInTheDocument();
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
