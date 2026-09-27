/**
 * Agent Feedback (v5.310.0) — the triage queue says who and where, so a
 * reviewer can check a claim against the session's own API calls.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { AgentFeedbackEntry, FeedbackStats } from '../../services/api';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const selectProject = vi.hoisted(() => vi.fn());
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: [{ id: 1, name: 'Acme internal' }, { id: 3, name: 'Demo — Insights Eval' }],
    currentProject: { id: 1, name: 'Acme internal' },
    selectProject,
  }),
}));

const listAgentFeedback = vi.fn();
const getAgentFeedbackStats = vi.fn();
const updateAgentFeedback = vi.fn();
vi.mock('../../services/api', () => ({
  listAgentFeedback: (...a: unknown[]) => listAgentFeedback(...a),
  getAgentFeedbackStats: (...a: unknown[]) => getAgentFeedbackStats(...a),
  updateAgentFeedback: (...a: unknown[]) => updateAgentFeedback(...a),
}));

import Feedback from '../../pages/Feedback';

const entry = (over: Partial<AgentFeedbackEntry> = {}): AgentFeedbackEntry => ({
  id: 9,
  project_id: 3,
  agent_id: 11,
  test_plan_id: null,
  execution_session_id: null,
  recon_session_id: null,
  assist_session_id: null,
  agent_session_id: 57,
  session_page_id: 37,
  session_api_calls: 89,
  project_name: 'Demo — Insights Eval',
  agent_name: 'session-agent',
  source: 'plan_generation',
  prompt_version: '2.11.0',
  overall_rating: null,
  api_critiques: [
    { endpoint: 'plan_validate', issue: 'counts the whole project', suggestion: 'use the plan filter' },
    { endpoint: 'tools/list', issue: 'names submit_test_plan', suggestion: 'plan_submit' },
  ],
  tool_suggestions: [],
  friction_notes: 'Direct HTTP MCP client connected with verified TLS.',
  agent_metrics: { agent_name: 'Codex' },
  status: 'new',
  reviewed_by_id: null,
  reviewed_at: null,
  reviewer_notes: null,
  created_at: '2026-09-27T04:01:06Z',
  ...over,
});

const stats: FeedbackStats = {
  total: 9,
  by_status: { new: 5, reviewed: 3, actioned: 1 },
  by_source: {},
  by_prompt_version: {},
  avg_rating: 3.6,
  top_tool_suggestions: [{ name: 'nuclei', count: 2, categories: [] }],
  with_api_critiques: 4,
  with_tool_suggestions: 2,
};

const page = (items: AgentFeedbackEntry[], total = items.length) => ({
  items, total, skip: 0, limit: 50, has_more: items.length < total,
});

const renderPage = (url = '/feedback') => render(
  <MemoryRouter initialEntries={[url]}><Feedback /></MemoryRouter>,
);

beforeEach(() => {
  navigate.mockReset();
  selectProject.mockReset();
  listAgentFeedback.mockReset();
  getAgentFeedbackStats.mockReset().mockResolvedValue(stats);
  updateAgentFeedback.mockReset();
});

describe('Agent Feedback', () => {
  it('names the project, the session with its call count, the client and the critiques', async () => {
    listAgentFeedback.mockResolvedValue(page([entry()]));
    renderPage();
    const row = await screen.findByTestId('feedback-row-9');
    expect(within(row).getByText('Demo — Insights Eval')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Session #57 · 89 calls/ })).toBeInTheDocument();
    // The client the agent named, over the key's generated agent name.
    expect(within(row).getByText('Codex')).toBeInTheDocument();
    expect(within(row).getByText('2 API critiques')).toBeInTheDocument();
    expect(screen.getByText('1 of 1 shown')).toBeInTheDocument();
  });

  it('names the MCP client over the reused agent record when the agent did not say', async () => {
    listAgentFeedback.mockResolvedValue(page([entry({
      agent_metrics: null, client_name: 'claude-code', agent_name: '[named-assets seed] planner',
    })]));
    renderPage();
    const row = await screen.findByTestId('feedback-row-9');
    expect(within(row).getByText('claude-code')).toBeInTheDocument();
    expect(within(row).queryByText('[named-assets seed] planner')).not.toBeInTheDocument();
  });

  it("opens the session's call log in the session's own project", async () => {
    listAgentFeedback.mockResolvedValue(page([entry()]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /Session #57/ }));
    expect(selectProject).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
    expect(navigate).toHaveBeenCalledWith('/assist-sessions/37');
  });

  it('measures are links to the rows they count', async () => {
    listAgentFeedback.mockResolvedValue(page([entry()]));
    renderPage();
    expect(await screen.findByRole('link', { name: 'Show the feedback waiting for triage' }))
      .toHaveAttribute('href', '/feedback?status=new');
    expect(screen.getByRole('link', { name: 'Show the feedback that names an API problem' }))
      .toHaveAttribute('href', '/feedback?content=critiques');
  });

  it('reads its filters from the URL', async () => {
    listAgentFeedback.mockResolvedValue(page([]));
    renderPage('/feedback?status=new&content=critiques&project=3');
    await waitFor(() => expect(listAgentFeedback).toHaveBeenCalled());
    expect(listAgentFeedback).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'new', has_api_critiques: true, project_id: 3,
    }));
    expect(await screen.findByText('No feedback matches these filters.')).toBeInTheDocument();
  });

  it('searches once the typing stops, not on every keystroke', async () => {
    listAgentFeedback.mockResolvedValue(page([entry()]));
    renderPage();
    await screen.findByTestId('feedback-row-9');
    const calls = listAgentFeedback.mock.calls.length;
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search feedback notes' }), 'tls');
    await waitFor(() => expect(listAgentFeedback).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: 'tls' }),
    ));
    // One more list request for the settled term — not one per letter.
    expect(listAgentFeedback.mock.calls.length).toBe(calls + 1);
  });

  it('pages with "Show more" when there are more rows than the first page', async () => {
    listAgentFeedback
      .mockResolvedValueOnce(page([entry()], 2))
      .mockResolvedValueOnce({ items: [entry({ id: 8 })], total: 2, skip: 1, limit: 50, has_more: false });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Show 1 more' }));
    expect(await screen.findByTestId('feedback-row-8')).toBeInTheDocument();
    expect(listAgentFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 1 }));
  });
});
