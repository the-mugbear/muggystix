/**
 * Regression guard for the React #310 crash: a hook (the #note-hash deep-link
 * effect) was placed AFTER HostInspector's loading/!host early returns, so it
 * ran only once the host loaded — a hooks-order violation that crashed the
 * page. This renders the real component through the loading→loaded transition
 * (the exact trigger). The Hosts page test stubs HostInspector, so only a
 * real-render test catches this.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
vi.mock('../../components/HostDnsRecordsCard', () => ({ default: () => null }));
vi.mock('../../components/HostLineagePanel', () => ({ default: () => null }));

import HostInspector from '../../components/HostInspector';

describe('HostInspector smoke', () => {
  it('renders through loading→loaded without a hooks-order crash', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    // If a hook sat below the early return, this transition throws React #310.
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
  });
});
