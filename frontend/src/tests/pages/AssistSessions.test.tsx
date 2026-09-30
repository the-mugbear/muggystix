/**
 * `/assist-sessions/:id` — the old, detail-row-keyed address of a session
 * (v5.312.0): it resolves to the session and redirects to its page, because
 * notes, feedback and bookmarks still link here. A bare `/assist-sessions`
 * goes to Agent Sessions.
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

const getAssistSession = vi.fn();
vi.mock('../../services/api', () => ({
  getAssistSession: (...a: unknown[]) => getAssistSession(...a),
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
    getAssistSession.mockReset();
  });

  it('sends the bare path to Agent Sessions', async () => {
    renderAt();
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-activity');
  });

  it('opens the SESSION the detail row belongs to — detail #52 is session #72', async () => {
    getAssistSession.mockResolvedValue({ id: 52, agent_session_id: 72 });
    renderAt('52');
    expect(await screen.findByTestId('where')).toHaveTextContent('/agent-sessions/72');
    expect(getAssistSession).toHaveBeenCalledWith(52);
  });

  it('says so when the session cannot be found', async () => {
    getAssistSession.mockRejectedValue(new Error('404'));
    renderAt('999');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /All agent sessions/ })).toHaveAttribute('href', '/agent-activity');
  });
});
