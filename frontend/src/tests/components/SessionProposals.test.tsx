/**
 * What one agent session proposed and what became of it (5.403.0), on the
 * session's page.  A proposed new finding is no finding until someone accepts
 * it: the operator read its absence from the Findings page as a failed call.
 */
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Proposal } from '../../services/api';

const api = vi.hoisted(() => ({ listProposals: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));

import SessionProposals from '../../components/agent-sessions/SessionProposals';

const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  id: 1, project_id: 9, kind: 'finding_create', source: 'agent', status: 'pending',
  finding_id: null, vulnerability_id: null, finding_host_id: null, field: null,
  payload: { title: 'Default credentials on the admin portal', severity: 'high', status: 'open', host_ids: [5] },
  current_value: null,
  target: { finding_title: null, observation_title: null, host_id: null, host_ip: null },
  rationale: null, evidence_ids: [], agent_session_id: 72, proposed_by: 'alice', agent_model: null,
  agent_client: null, prompt_version: null, created_at: '2026-10-16T10:00:00Z',
  decided_by: null, decided_at: null, decision_note: null, result_finding_id: null, error: null,
  ...over,
} as Proposal);

const renderList = (ended = false) =>
  render(<MemoryRouter><SessionProposals sessionId={72} ended={ended} /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SessionProposals', () => {
  it('lists every proposal of the session, whatever was decided, each saying where it stands', async () => {
    api.listProposals.mockResolvedValue({
      total: 3, has_more: false, items: [
        proposal(),
        proposal({ id: 2, status: 'accepted', result_finding_id: 44, payload: { title: 'TLS 1.0 enabled' } }),
        proposal({ id: 3, status: 'rejected', decision_note: 'Duplicate of the TLS finding', decided_by: 'admin', payload: { title: 'Weak TLS' } }),
      ],
    });
    renderList();
    const table = await screen.findByTestId('session-proposals');
    expect(api.listProposals).toHaveBeenCalledWith(
      9, expect.objectContaining({ agent_session_id: 72, status: 'any', offset: 0 }), expect.any(AbortSignal),
    );
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('New finding: Default credentials on the admin portal')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Waiting')).toBeInTheDocument();
    expect(within(rows[0]).getByRole('link', { name: 'Decide on the Proposals page' }))
      .toHaveAttribute('href', '/proposals?agent_session_id=72&scope=all');
    expect(within(rows[1]).getByRole('link', { name: 'Open the new finding' })).toHaveAttribute('href', '/findings/44');
    expect(within(rows[2]).getByText('Duplicate of the TLS finding — admin')).toBeInTheDocument();
    expect(table).toHaveStyle({ tableLayout: 'fixed' });
  });

  it('says a session proposed nothing — "so far" only while it is still open', async () => {
    api.listProposals.mockResolvedValue({ items: [], total: 0, has_more: false });
    const view = renderList(false);
    expect(await screen.findByText('None — this session has proposed nothing so far.')).toBeInTheDocument();
    view.unmount();
    renderList(true);
    expect(await screen.findByText('None — this session has proposed nothing.')).toBeInTheDocument();
  });

  it('reports a failed load instead of an empty list', async () => {
    api.listProposals.mockRejectedValue({ response: { status: 500, data: { detail: 'database is down' } } });
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent(/database is down|Could not load what this session proposed/);
  });
});
