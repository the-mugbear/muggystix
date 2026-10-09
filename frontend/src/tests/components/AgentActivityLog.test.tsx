/**
 * The API-call feed on an agent session's page (components/AgentActivityLog).
 *
 * Defect 1.6 (2026-10-09): the feed asked for the first 100 calls and its
 * bottom button was "Refresh" — a session with more calls could not show the
 * rest.  It is a "Show more" list now (`useListQuery`), it says how many of
 * how many it shows, and a failed load is said with Retry, never "no calls".
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getAgentSessionApiActivity: vi.fn() }));
vi.mock('../../services/api', () => api);
// The project the log is shown in: every request names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));

import AgentActivityLog from '../../components/AgentActivityLog';
import { TooltipProvider } from '../../components/ui/tooltip';

const PAGE = 100;
const TOTAL = 230;

const call = (id: number) => ({
  id,
  created_at: new Date(Date.UTC(2026, 9, 9, 12, 0, 0) - id * 1000).toISOString(),
  agent_id: 7,
  agent_name: "alice's-agent",
  owner_id: 3,
  owner_username: 'alice',
  method: 'GET',
  path: `/api/v1/agent/hosts/${id}`,
  path_template: `/api/v1/agent/hosts/{host_id}#${id}`,
  status_code: 200,
  duration_ms: 12,
  referenced_target_ips: [],
  referenced_host_ids: [],
});

/** The server: `TOTAL` calls, paged by the request's own offset and limit. */
const serve = (total = TOTAL) => {
  api.getAgentSessionApiActivity.mockImplementation(
    async (_projectId: number, _sessionId: number, filters: { limit?: number; offset?: number } = {}) => {
      const offset = filters.offset ?? 0;
      const limit = filters.limit ?? PAGE;
      const count = Math.max(0, Math.min(limit, total - offset));
      return { total, items: Array.from({ length: count }, (_, i) => call(offset + i + 1)) };
    },
  );
};

const renderLog = () => render(
  <TooltipProvider>
    <AgentActivityLog sessionId={72} title="API activity" defaultMineOnly={false} />
  </TooltipProvider>,
);

const endpoint = (id: number) => `/api/v1/agent/hosts/{host_id}#${id}`;

describe('AgentActivityLog', () => {
  beforeEach(() => {
    api.getAgentSessionApiActivity.mockReset();
  });

  it('lists the session’s calls a page at a time and says how many of how many', async () => {
    serve();
    renderLog();

    expect(await screen.findByText(endpoint(1))).toBeInTheDocument();
    expect(screen.getByText(endpoint(100))).toBeInTheDocument();
    expect(screen.queryByText(endpoint(101))).not.toBeInTheDocument();
    // The page is not the total.
    expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('Showing 100 of 230');
    expect(api.getAgentSessionApiActivity).toHaveBeenCalledWith(9, 72, { limit: PAGE, offset: 0 }, expect.any(AbortSignal));
    // A section on the session's page, not a card.
    expect(screen.getByRole('heading', { level: 2, name: 'API activity' })).toBeInTheDocument();
  });

  it('"Show more" appends the next page until every call is reachable', async () => {
    const user = userEvent.setup();
    serve();
    renderLog();
    await screen.findByText(endpoint(1));

    await user.click(screen.getByRole('button', { name: 'Show more (130 left)' }));
    expect(await screen.findByText(endpoint(200))).toBeInTheDocument();
    expect(api.getAgentSessionApiActivity).toHaveBeenLastCalledWith(9, 72, { limit: PAGE, offset: 100 }, expect.any(AbortSignal));
    // Appended: the first page is still there.
    expect(screen.getByText(endpoint(1))).toBeInTheDocument();
    expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('Showing 200 of 230');

    await user.click(screen.getByRole('button', { name: 'Show more (30 left)' }));
    expect(await screen.findByText(endpoint(230))).toBeInTheDocument();
    expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('Showing 230 of 230');
    expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument();
  });

  it('says a failed load with Retry — never "no calls", never a count', async () => {
    const user = userEvent.setup();
    api.getAgentSessionApiActivity.mockRejectedValue(new Error('upstream timed out'));
    renderLog();

    const failure = await screen.findByRole('alert');
    expect(failure).toHaveTextContent('Failed to load activity log.');
    expect(screen.queryByText(/No matching API calls/)).not.toBeInTheDocument();
    expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('—');
    expect(screen.getByTestId('agent-activity-count')).not.toHaveTextContent(/\d/);

    serve(3);
    await user.click(within(failure).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(endpoint(3))).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('Showing 3 of 3');
  });

  it('says so when the session made no call that matches', async () => {
    serve(0);
    renderLog();
    expect(await screen.findByText(/No matching API calls/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument();
  });

  it('Refresh re-reads what is shown and keeps the place', async () => {
    const user = userEvent.setup();
    serve();
    renderLog();
    await screen.findByText(endpoint(1));
    await user.click(screen.getByRole('button', { name: 'Show more (130 left)' }));
    await screen.findByText(endpoint(200));

    api.getAgentSessionApiActivity.mockClear();
    await user.click(screen.getByRole('button', { name: 'Refresh API activity' }));
    await waitFor(() => expect(api.getAgentSessionApiActivity).toHaveBeenCalledTimes(2));
    expect(api.getAgentSessionApiActivity.mock.calls.map((c) => c[2].offset)).toEqual([0, 100]);
    await waitFor(() => expect(screen.getByTestId('agent-activity-count')).toHaveTextContent('Showing 200 of 230'));
  });
});
