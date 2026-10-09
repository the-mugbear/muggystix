/**
 * Agent Feedback — the search belongs to the address, under the REAL router
 * (setupTests replaces `useNavigate` / `useLocation`; a mocked navigation
 * proves nothing about Back).
 *
 * The defect (code review 2026-10-09, finding 6): `?q=` was copied into state
 * once and written back from an effect.  A link to the same page with another
 * search, or Back, changed the address — and the box kept the old text, the
 * list was asked for the old text, and the old text was written back over the
 * address the reader had gone to.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () =>
  vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: [{ id: 1, name: 'Acme internal' }, { id: 3, name: 'Demo — Insights Eval' }],
    currentProject: { id: 1, name: 'Acme internal' },
    selectProject: vi.fn(),
  }),
}));

const listAgentFeedback = vi.fn();
const getAgentFeedbackStats = vi.fn();
vi.mock('../../services/api', () => ({
  listAgentFeedback: (...a: unknown[]) => listAgentFeedback(...a),
  getAgentFeedbackStats: (...a: unknown[]) => getAgentFeedbackStats(...a),
  updateAgentFeedback: vi.fn(),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Feedback from '../../pages/Feedback';

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/feedback', element: <Feedback /> }, { path: '/elsewhere', element: <p>elsewhere</p> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

const box = () => screen.getByRole('searchbox', { name: 'Search feedback notes' }) as HTMLInputElement;
/** What each list request asked for. */
const asked = () => listAgentFeedback.mock.calls.map(([params]) => params as Record<string, unknown>);
const last = () => asked()[asked().length - 1];
/** Longer than the search's debounce: anything that was going to be written has been. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });

beforeEach(() => {
  listAgentFeedback.mockReset().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50, has_more: false });
  getAgentFeedbackStats.mockReset().mockResolvedValue({
    total: 0, by_status: {}, by_source: {}, by_prompt_version: {}, avg_rating: null,
    top_tool_suggestions: [], with_api_critiques: 0, with_tool_suggestions: 0,
  });
});

describe('Agent Feedback — the search is the address (real router)', () => {
  it('opens on what the address says: the box, the filters and the request', async () => {
    const router = open('/feedback?status=new&q=tls');
    await waitFor(() => expect(listAgentFeedback).toHaveBeenCalled());
    expect(box().value).toBe('tls');
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveTextContent('New');
    expect(last()).toEqual({ limit: 50, status: 'new', search: 'tls' });
    // Opening it asked once and changed nothing in the address.
    await settle();
    expect(asked()).toHaveLength(1);
    expect(router.state.location.search).toBe('?status=new&q=tls');
  });

  it('typing asks once, after the typing stops, and writes the address with replace', async () => {
    const router = open('/feedback?status=new');
    await waitFor(() => expect(listAgentFeedback).toHaveBeenCalledTimes(1));
    fireEvent.change(box(), { target: { value: 'w' } });
    fireEvent.change(box(), { target: { value: 'we' } });
    fireEvent.change(box(), { target: { value: ' weak ' } });
    // Still typing: nothing asked, nothing written.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)); });
    expect(asked()).toHaveLength(1);
    expect(router.state.location.search).toBe('?status=new');

    await waitFor(() => expect(last()).toEqual({ limit: 50, status: 'new', search: 'weak' }));
    await settle();
    // One request for the settled text — not one per letter, and not a second
    // one when the address it wrote comes back.
    expect(asked()).toHaveLength(2);
    expect(router.state.location.search).toBe('?status=new&q=weak');
    expect(router.state.historyAction).toBe('REPLACE');
    // The box keeps what the reader typed.
    expect(box().value).toBe(' weak ');
  });

  it('an emptied search is left out of the address and of the request', async () => {
    const router = open('/feedback?q=tls');
    await waitFor(() => expect(listAgentFeedback).toHaveBeenCalledTimes(1));
    fireEvent.change(box(), { target: { value: '' } });
    await waitFor(() => expect(last()).toEqual({ limit: 50 }));
    expect(router.state.location.search).toBe('');
  });

  it('a link to the same page with another search, and Back, re-seed the box; the list follows; the address is left alone', async () => {
    const router = open('/feedback?q=first');
    await waitFor(() => expect(last()).toMatchObject({ search: 'first' }));

    await act(async () => { await router.navigate('/feedback?status=new&q=linked'); });
    expect(box().value).toBe('linked');
    await waitFor(() => expect(last()).toEqual({ limit: 50, status: 'new', search: 'linked' }));
    // Nothing puts the previous search back over it, now or once a timer fires.
    await settle();
    expect(router.state.location.search).toBe('?status=new&q=linked');
    expect(last()).toEqual({ limit: 50, status: 'new', search: 'linked' });

    await act(async () => { await router.navigate(-1); });
    expect(box().value).toBe('first');
    await waitFor(() => expect(last()).toEqual({ limit: 50, search: 'first' }));
    await settle();
    expect(router.state.location.search).toBe('?q=first');
    expect(last()).toEqual({ limit: 50, search: 'first' });
  });

  it('Back while something half-typed is pending drops the half-typed text, not the address', async () => {
    const router = open('/feedback?q=first');
    await waitFor(() => expect(last()).toMatchObject({ search: 'first' }));
    await act(async () => { await router.navigate('/feedback?q=linked'); });
    await waitFor(() => expect(last()).toMatchObject({ search: 'linked' }));

    fireEvent.change(box(), { target: { value: 'linked and mo' } });
    await act(async () => { await router.navigate(-1); });
    expect(box().value).toBe('first');
    await settle();
    expect(router.state.location.search).toBe('?q=first');
    expect(last()).toEqual({ limit: 50, search: 'first' });
    expect(asked().some((params) => params.search === 'linked and mo')).toBe(false);
  });
});
