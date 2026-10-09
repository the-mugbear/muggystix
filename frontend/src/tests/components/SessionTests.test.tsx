/**
 * The tests one agent session proposed (5.320.0), on the session's page — it
 * replaced the list of plans and execution runs a session opened.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HostTest } from '../../services/api';

const api = vi.hoisted(() => ({ listHostTests: vi.fn() }));
vi.mock('../../services/api', () => api);
// The project the session's page is shown in: every request names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));

import SessionTests from '../../components/agent-sessions/SessionTests';

const test = (over: Partial<HostTest> = {}): HostTest => ({
  id: 31, host_id: 5, host_ip: '10.0.0.5', tool: 'netexec', description: 'SMB signing',
  command: 'nxc smb {ip}', rationale: 'r', expected_result: null, references: null, target_fqdn: null,
  priority: 'high', label: 'SMB sweep', status: 'in_progress', assigned_to_id: null, assigned_to: null,
  created_by: 'Alice Analyst', source: 'agent', agent_session_id: 72, agent_model: null, agent_client: null,
  tester_summary: null, dismissed_reason: null, revision: 2, evidence_count: 4,
  created_at: '2026-09-30T10:00:00Z',
  ...over,
});

const renderList = (ended = false) =>
  render(<MemoryRouter><SessionTests sessionId={72} ended={ended} /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SessionTests', () => {
  it('lists this session\'s tests, each opening the test on its host\'s page', async () => {
    api.listHostTests.mockResolvedValue({ items: [test()], total: 1, has_more: false });
    renderList();
    const table = await screen.findByTestId('session-tests');
    expect(api.listHostTests).toHaveBeenCalledWith(9, expect.objectContaining({ agent_session_id: 72, offset: 0 }), expect.any(AbortSignal));
    expect(within(table).getByRole('link', { name: '10.0.0.5' })).toHaveAttribute('href', '/hosts/5#host-test-31');
    expect(within(table).getByText('SMB signing')).toBeInTheDocument();
    expect(within(table).getByText('In progress')).toBeInTheDocument();
    expect(within(table).getByText('4')).toBeInTheDocument();
    // A fixed layout: a long description cannot widen the page.
    expect(table).toHaveStyle({ tableLayout: 'fixed' });
  });

  it('says a session proposed nothing — "so far" only while it is still open', async () => {
    api.listHostTests.mockResolvedValue({ items: [], total: 0, has_more: false });
    const view = renderList(false);
    expect(await screen.findByText('None — this session has proposed no tests so far.')).toBeInTheDocument();
    view.unmount();
    renderList(true);
    expect(await screen.findByText('None — this session has proposed no tests.')).toBeInTheDocument();
  });

  it('reports a failed load and retries', async () => {
    api.listHostTests.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'database is down' } } });
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent('database is down');
    expect(screen.queryByTestId('session-tests')).not.toBeInTheDocument();
    api.listHostTests.mockResolvedValue({ items: [test()], total: 1, has_more: false });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('session-tests')).toBeInTheDocument();
  });

  it('offers the rest when there are more than one page, and appends them', async () => {
    api.listHostTests
      .mockResolvedValueOnce({ items: [test({ id: 31 })], total: 3, has_more: true })
      .mockResolvedValueOnce({ items: [test({ id: 32, host_id: 6, host_ip: '10.0.0.6' }), test({ id: 33, host_id: 7, host_ip: '10.0.0.7' })], total: 3, has_more: false });
    renderList();
    await screen.findByTestId('session-tests');
    fireEvent.click(screen.getByRole('button', { name: 'Show more (2 left)' }));
    await waitFor(() => expect(api.listHostTests).toHaveBeenLastCalledWith(9, expect.objectContaining({ offset: 1 }), expect.any(AbortSignal)));
    expect(await screen.findByRole('link', { name: '10.0.0.7' })).toHaveAttribute('href', '/hosts/7#host-test-33');
    expect(screen.getByRole('link', { name: '10.0.0.5' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument();
  });

  it('renders a test with no tool and a 200-character description, truncated', async () => {
    const long = 'y'.repeat(200);
    api.listHostTests.mockResolvedValue({
      items: [test({ tool: null, description: long, target_fqdn: 'portal.example.test' })], total: 1, has_more: false,
    });
    renderList();
    const table = await screen.findByTestId('session-tests');
    expect(within(table).getByTitle(long)).toHaveClass('truncate');
    // The link's tooltip names the target the test was aimed at.
    expect(within(table).getByRole('link', { name: '10.0.0.5' })).toHaveAttribute('title', 'portal.example.test');
    expect(table).not.toHaveTextContent(/null|undefined/);
  });
});
