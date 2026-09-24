/**
 * One agent session's review page.
 *
 * The detail leads with the notes the agent wrote, since those are its only
 * durable output and the thing that carries the operator's name. The list moved
 * to Agent Runs' "Sessions" view in v5.294.0 (its tests are in
 * tests/components/AgentSessionsList.test.tsx); a bare /assist-sessions lands
 * there.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AssistSessions from '../../pages/AssistSessions';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { AssistSessionDetail } from '../../services/api';

// setupTests.ts mocks react-router-dom's useParams to a fixed `{ id: '1' }`
// for every suite, so a page keyed on its own param reads as "no param
// supplied" no matter what route is rendered. Override it here with something
// each test controls.
const params = vi.hoisted(() => ({ current: {} as Record<string, string | undefined> }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => params.current,
  };
});

const getAssistSession = vi.fn();
const getAssistSessionApiActivity = vi.fn();
vi.mock('../../services/api', () => ({
  getAssistSession: (...a: unknown[]) => getAssistSession(...a),
  getAssistSessionApiActivity: (...a: unknown[]) => getAssistSessionApiActivity(...a),
  getPlanApiActivity: vi.fn(),
  getReconSessionApiActivity: vi.fn(),
}));

const detail = (over: Partial<AssistSessionDetail> = {}): AssistSessionDetail => ({
  id: 12,
  project_id: 1,
  purpose: 'Looking for FTP exposure',
  status: 'ended',
  started_by_id: 7,
  started_by_username: 'alice',
  started_at: '2026-08-19T10:00:00Z',
  ended_at: '2026-08-19T11:30:00Z',
  last_activity_at: '2026-08-19T11:20:00Z',
  environment_probed: true,
  key_expires_at: null,
  call_count: 14,
  note_count: 2,
  connection: 'mcp',
  first_call_at: '2026-08-19T10:02:00Z',
  environment: { os_family: 'linux', shell: 'bash' },
  environment_probed_at: '2026-08-19T10:01:00Z',
  agent_model: 'claude-opus-5',
  agent_tool: 'claude-code',
  prompt_version: '1.51.0',
  feedback_count: 1,
  notes: [
    {
      id: 501,
      host_id: 88,
      host_ip: '10.0.0.9',
      hostname: 'ftp01',
      body: 'Anonymous FTP login accepted on 21.',
      status: 'open',
      created_at: '2026-08-19T11:00:00Z',
    },
  ],
  ...over,
});

const Where = () => {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
};

const renderPage = (sessionId?: string) => {
  params.current = sessionId ? { sessionId } : {};
  return render(
    <MemoryRouter initialEntries={[sessionId ? `/assist-sessions/${sessionId}` : '/assist-sessions']}>
      <TooltipProvider>
        <Routes>
          <Route path="/assist-sessions" element={<AssistSessions />} />
          <Route path="/assist-sessions/:sessionId" element={<AssistSessions />} />
          <Route path="/agent-activity" element={<Where />} />
        </Routes>
      </TooltipProvider>
    </MemoryRouter>,
  );
};

describe('AssistSessions', () => {
  beforeEach(() => {
    getAssistSession.mockReset().mockResolvedValue(detail());
    getAssistSessionApiActivity.mockReset().mockResolvedValue({ total: 0, items: [] });
  });

  it('sends the bare list route to the Sessions view of Agent Runs', async () => {
    renderPage();
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-activity?view=sessions');
  });

  it('leads the detail with the notes the agent wrote', async () => {
    renderPage('12');

    await waitFor(() =>
      expect(screen.getByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument(),
    );
    const link = screen.getByRole('link', { name: 'ftp01' });
    expect(link).toHaveAttribute('href', '/hosts/88#note-501');
    expect(screen.getByText(/linux · bash/)).toBeInTheDocument();
    expect(screen.getByText(/claude-opus-5 · claude-code/)).toBeInTheDocument();
  });

  it('says plainly when a session produced nothing', async () => {
    getAssistSession.mockResolvedValue(detail({ notes: [], note_count: 0 }));
    renderPage('12');

    expect(await screen.findByText('This session wrote no notes.')).toBeInTheDocument();
  });

  it('prints the start in the one absolute format', async () => {
    renderPage('12');
    const when = new Date('2026-08-19T10:00:00Z').toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
    expect(await screen.findByText(when)).toBeInTheDocument();
  });
});
