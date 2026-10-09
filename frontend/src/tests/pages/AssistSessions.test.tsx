/**
 * `/assist-sessions/:id` — the old address of a session, keyed by a second id
 * it no longer has (5.328.0): the server says which session had that id and
 * the page redirects there, because notes, feedback and bookmarks from before
 * still link here. A bare `/assist-sessions` goes to Agent Sessions.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AssistSessions from '../../pages/AssistSessions';

// setupTests.ts mocks useParams to a fixed `{ id: '1' }` for every suite.
const params = vi.hoisted(() => ({ current: {} as Record<string, string | undefined> }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => params.current };
});

const getAgentSessionByLegacyAssistId = vi.fn();
vi.mock('../../services/api', () => ({
  getAgentSessionByLegacyAssistId: (...a: unknown[]) => getAgentSessionByLegacyAssistId(...a),
}));

const Where = () => {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
};

const renderAt = (sessionId?: string) => {
  params.current = sessionId ? { sessionId } : {};
  return render(
    <MemoryRouter initialEntries={[sessionId ? `/assist-sessions/${sessionId}` : '/assist-sessions']}>
      <Routes>
        <Route path="/assist-sessions" element={<AssistSessions />} />
        <Route path="/assist-sessions/:sessionId" element={<AssistSessions />} />
        <Route path="/agent-activity" element={<Where />} />
        <Route path="/agent-sessions/:sessionId" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
};

describe('AssistSessions (redirect)', () => {
  beforeEach(() => {
    getAgentSessionByLegacyAssistId.mockReset();
  });

  it('sends the bare path to Agent Sessions', async () => {
    renderAt();
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-activity');
  });

  it('opens the session that had the old id — old #52 is session #72', async () => {
    getAgentSessionByLegacyAssistId.mockResolvedValue({ kind: 'project', id: 72, project_id: 1, status: 'ended' });
    renderAt('52');
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-sessions/72');
    expect(getAgentSessionByLegacyAssistId).toHaveBeenCalledWith(52, expect.any(AbortSignal));
  });

  it('opens a legacy assist session too: it has the same page now', async () => {
    getAgentSessionByLegacyAssistId.mockResolvedValue({ kind: 'assist', id: 31, project_id: 1, status: 'ended' });
    renderAt('7');
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-sessions/31');
  });

  it('says so when the session cannot be found', async () => {
    getAgentSessionByLegacyAssistId.mockRejectedValue(new Error('404'));
    renderAt('999');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /All agent sessions/ })).toHaveAttribute('href', '/agent-activity');
  });
});
