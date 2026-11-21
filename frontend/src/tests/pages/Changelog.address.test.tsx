/**
 * Changelog — who did what to the project, under the REAL router: the
 * filters and the page are the address's (UI_STYLE_GUIDE §39), an agent's
 * action is always said to be an agent's, and a failed read is an error,
 * never an empty changelog.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({ getChangelog: vi.fn() }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: 'auditor' } }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import Changelog, { actorText, subjectPath } from '../../pages/Changelog';
import type { ChangelogLine } from '../../services/api';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const line = (over: Partial<ChangelogLine>): ChangelogLine => ({
  id: 1, created_at: '2026-11-18T10:00:00Z', action: 'finding_status_changed', group: 'findings',
  words: 'changed the status of finding', actor: { id: 5, username: 'ana', full_name: 'Ana Ruiz' },
  by_agent: false, agent_session_id: null, subject_type: 'finding', subject_id: 7,
  subject_label: 'Weak TLS', summary: 'open → confirmed', count: 1, ...over,
});

const page = (items: ChangelogLine[], over: Record<string, unknown> = {}) => ({
  items, total: items.length, limit: 50, offset: 0, returned: items.length, has_more: false,
  people: [{ id: 5, username: 'ana', full_name: 'Ana Ruiz' }, { id: 6, username: 'bo', full_name: null }],
  groups: ['findings', 'hosts', 'agents'], trimmed: false, ...over,
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/changelog', element: <Changelog /> }, { path: '/findings/:id', element: <p>the finding</p> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const asked = () => mocked.getChangelog.mock.calls.map(([projectId, filters]) => {
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
});
const last = () => asked()[asked().length - 1];

describe('Changelog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getChangelog.mockResolvedValue(page([
      line({ id: 3, by_agent: true, agent_session_id: 9, action: 'host_note_added', group: 'hosts',
        words: 'added a note on host', subject_type: 'host', subject_id: 4, subject_label: '10.0.0.4', summary: null }),
      line({ id: 2 }),
      line({ id: 1, action: 'finding_deleted', words: 'deleted finding', subject_label: 'Old one', summary: null,
        actor: null }),
    ]));
  });

  it('lists who did what to what, and always says when it was an agent', async () => {
    open('/changelog');
    const agents = within(await screen.findByTestId('changelog-line-3'));
    // The operator's AGENT did it — never shown as the person's own action.
    expect(agents.getByText('Ana Ruiz’s agent')).toBeInTheDocument();
    expect(agents.getByRole('link', { name: '10.0.0.4' })).toHaveAttribute('href', '/hosts/4');

    const person = within(screen.getByTestId('changelog-line-2'));
    expect(person.getByText('Ana Ruiz')).toBeInTheDocument();
    expect(person.getByText(/open → confirmed/)).toBeInTheDocument();
    expect(person.getByRole('link', { name: 'Weak TLS' })).toHaveAttribute('href', '/findings/7');

    // A deleted subject is named and not linked; a deleted account is "a former member".
    const gone = within(screen.getByTestId('changelog-line-1'));
    expect(gone.getByText('Old one')).toBeInTheDocument();
    expect(gone.queryByRole('link')).toBeNull();
    expect(gone.getByText('a former member')).toBeInTheDocument();
  });

  it('opens on what the address says and asks for exactly that', async () => {
    open('/changelog?actor=5&by=agents&kind=hosts&page=2');
    await screen.findByTestId('changelog-line-3');
    expect(asked()).toEqual([{ actorId: 5, by: 'agents', group: 'hosts', offset: 50, limit: 50 }]);
    expect((screen.getByLabelText('Whose actions') as HTMLSelectElement).value).toBe('5');
    expect(screen.getByRole('button', { name: 'Agents' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Hosts' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('a filter writes the address once, drops the page, and asks with it', async () => {
    const router = open('/changelog?page=3');
    await screen.findByTestId('changelog-line-3');

    fireEvent.click(screen.getByRole('button', { name: 'Agents' }));
    await waitFor(() => expect(last()).toMatchObject({ by: 'agents', offset: 0 }));
    expect(router.state.location.search).toBe('?by=agents');

    fireEvent.change(screen.getByLabelText('Whose actions'), { target: { value: '6' } });
    await waitFor(() => expect(last()).toMatchObject({ actorId: 6, by: 'agents' }));
    expect(router.state.location.search).toBe('?by=agents&actor=6');

    fireEvent.click(screen.getByRole('button', { name: 'Findings' }));
    await waitFor(() => expect(last()).toMatchObject({ group: 'findings' }));
    fireEvent.click(screen.getByRole('button', { name: 'All kinds' }));
    fireEvent.click(screen.getByRole('button', { name: 'Everyone' }));
    fireEvent.change(screen.getByLabelText('Whose actions'), { target: { value: '' } });
    await waitFor(() => expect(router.state.location.search).toBe(''));
    expect(last()).toMatchObject({ actorId: null, by: null, group: null });
  });

  it('says so when nothing is recorded, when nothing matches, and when older lines were trimmed', async () => {
    mocked.getChangelog.mockResolvedValue(page([]));
    open('/changelog');
    expect(await screen.findByTestId('changelog-empty')).toHaveTextContent('Nothing has been recorded yet');
  });

  it('a filter with no match says that, not that the changelog is empty', async () => {
    mocked.getChangelog.mockResolvedValue(page([]));
    open('/changelog?kind=hosts');
    expect(await screen.findByTestId('changelog-empty')).toHaveTextContent('No action matches the current filter.');
  });

  it('says when the oldest lines have been removed', async () => {
    mocked.getChangelog.mockResolvedValue(page([line({ id: 2 })], { trimmed: true }));
    open('/changelog');
    await screen.findByTestId('changelog-line-2');
    expect(screen.getByText(/the oldest have been removed/)).toBeInTheDocument();
  });

  it('a failed read is an error, never an empty changelog', async () => {
    mocked.getChangelog.mockRejectedValue(new Error('boom'));
    open('/changelog');
    expect(await screen.findByText('Failed to load the changelog.')).toBeInTheDocument();
    expect(screen.queryByTestId('changelog-empty')).toBeNull();
  });
});

describe('a changelog line', () => {
  it('links only a subject that has a page and still exists', () => {
    expect(subjectPath(line({ subject_type: 'report', subject_id: 3 }))).toBe('/reports/3');
    expect(subjectPath(line({ subject_type: 'agent_session', subject_id: 8 }))).toBe('/agent-sessions/8');
    expect(subjectPath(line({ subject_type: 'proposal', subject_id: 8 }))).toBeNull();
    expect(subjectPath(line({ subject_id: null }))).toBeNull();
    expect(subjectPath(line({ action: 'report_deleted', subject_type: 'report', subject_id: 3 }))).toBeNull();
    // A deleted note or comment is about a host or a finding that is still there.
    expect(subjectPath(line({ action: 'host_note_deleted', subject_type: 'host', subject_id: 4 }))).toBe('/hosts/4');
    expect(subjectPath(line({ action: 'finding_comment_deleted', subject_type: 'finding', subject_id: 6 }))).toBe('/findings/6');
    for (const action of ['finding_deleted', 'name_deleted', 'scan_deleted']) {
      expect(subjectPath(line({ action, subject_type: 'finding', subject_id: 6 }))).toBeNull();
    }
  });

  it('names the person, or their agent, or a former member', () => {
    expect(actorText(line({}))).toBe('Ana Ruiz');
    expect(actorText(line({ actor: { id: 6, username: 'bo', full_name: null }, by_agent: true }))).toBe('bo’s agent');
    expect(actorText(line({ actor: null }))).toBe('a former member');
    expect(actorText(line({ actor: null, by_agent: true }))).toBe('a former member’s agent');
  });
});
