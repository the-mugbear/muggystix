/**
 * ProjectProvider (5.351.0 — on the `['getProjects']` query): the
 * project list, which project is the current one, and the three screens it
 * shows instead of the app (loading, could not load, no projects).  The
 * current project is derived from the list and the reader's choice; the API
 * client's project id is set before any child asks for data.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useQueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => {
  const state = { stored: null as number | null };
  return {
    state,
    getProjects: vi.fn(),
    createProject: vi.fn(),
    setCurrentProjectId: vi.fn((id: number | null) => { state.stored = id; }),
    getCurrentProjectId: vi.fn(() => state.stored),
  };
});
vi.mock('../../services/api', () => api);
// setupTests replaces `useProject` with a fixed project; this file tests the real one.
vi.mock('../../contexts/ProjectContext', async () =>
  vi.importActual<typeof import('../../contexts/ProjectContext')>('../../contexts/ProjectContext'));

import { pickProject, ProjectProvider, useProject } from '../../contexts/ProjectContext';
import type { Project } from '../../services/api';

const project = (id: number, name: string): Project => ({ id, name, slug: name.toLowerCase() } as Project);
const ALPHA = project(1, 'Alpha');
const BRAVO = project(2, 'Bravo');
const CHARLIE = project(3, 'Charlie');

let ctx!: ReturnType<typeof useProject>;
let mounts = 0;
/** The project id the API client had when the page first rendered. */
let idAtFirstRender: number | null = null;
const Page: React.FC = () => {
  ctx = useProject();
  const client = useQueryClient();
  const first = React.useRef(true);
  if (first.current) {
    first.current = false;
    idAtFirstRender = api.getCurrentProjectId();
  }
  React.useEffect(() => { mounts += 1; }, []);
  return (
    <div>
      <p data-testid="current">{ctx.currentProject?.name ?? 'none'}</p>
      <p data-testid="list">{ctx.projects.map((p) => p.name).join(',')}</p>
      <button type="button" onClick={() => void client.invalidateQueries({ queryKey: ['getProjects'] })}>
        another reader re-reads
      </button>
    </div>
  );
};
const mount = () => render(<ProjectProvider><Page /></ProjectProvider>);
const current = () => screen.getByTestId('current').textContent;
const listed = () => screen.getByTestId('list').textContent;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.state.stored = null;
  mounts = 0;
  idAtFirstRender = null;
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('pickProject', () => {
  it('prefers the stored project, then the most recently used still listed, then the first by name', () => {
    const list = [CHARLIE, BRAVO, ALPHA];
    expect(pickProject(list, 2, [3])).toBe(BRAVO);
    expect(pickProject(list, 99, [98, 3, 1])).toBe(CHARLIE);
    expect(pickProject(list, null, [])).toBe(ALPHA);
    expect(pickProject([], 2, [3])).toBeNull();
  });
});

describe('ProjectProvider', () => {
  it('shows the loader, then the app in the project stored on this device', async () => {
    api.state.stored = 2;
    api.getProjects.mockResolvedValue([ALPHA, BRAVO]);
    mount();
    expect(screen.getByText('Loading projects…')).toBeInTheDocument();
    await waitFor(() => expect(current()).toBe('Bravo'));
    expect(listed()).toBe('Alpha,Bravo');
    expect(api.getProjects).toHaveBeenCalledTimes(1);
  });

  it('with nothing stored, takes the most recently used — and tells the API client before the page asks for anything', async () => {
    localStorage.setItem('nm.recentProjectIds', JSON.stringify([7, 3]));
    api.getProjects.mockResolvedValue([ALPHA, BRAVO, CHARLIE]);
    mount();
    await waitFor(() => expect(current()).toBe('Charlie'));
    expect(idAtFirstRender).toBe(3);
  });

  it('with nothing stored or recent, takes the first by name', async () => {
    api.getProjects.mockResolvedValue([CHARLIE, BRAVO]);
    mount();
    await waitFor(() => expect(current()).toBe('Bravo'));
    expect(idAtFirstRender).toBe(2);
  });

  it('a failed load is said, with Retry — never "no projects"', async () => {
    api.getProjects.mockRejectedValueOnce({ response: { status: 503 } });
    mount();
    expect(await screen.findByText('Could not load projects')).toBeInTheDocument();
    expect(screen.queryByText('No Projects Yet')).toBeNull();
    expect(api.getProjects).toHaveBeenCalledTimes(1);    // no automatic retry

    api.getProjects.mockResolvedValue([ALPHA]);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(current()).toBe('Alpha'));
  });

  it('an empty list offers an administrator the first project, and opens it once created', async () => {
    api.getProjects.mockResolvedValueOnce([]);
    mount();
    expect(await screen.findByText('No Projects Yet')).toBeInTheDocument();

    api.createProject.mockResolvedValue(ALPHA);
    api.getProjects.mockResolvedValue([ALPHA]);
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '  Alpha ' } });
    fireEvent.click(screen.getByRole('button', { name: /create project/i }));
    await waitFor(() => expect(current()).toBe('Alpha'));
    expect(api.createProject).toHaveBeenCalledWith('Alpha', undefined);
    expect(api.getProjects).toHaveBeenCalledTimes(2);
  });

  it('says why the first project could not be created, and keeps the form', async () => {
    api.getProjects.mockResolvedValue([]);
    mount();
    await screen.findByText('No Projects Yet');
    api.createProject.mockRejectedValue({ response: { status: 409, data: { detail: 'A project with that name exists.' } } });
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Alpha' } });
    fireEvent.click(screen.getByRole('button', { name: /create project/i }));
    expect(await screen.findByText('A project with that name exists.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: /create project/i })).not.toBeDisabled());
    expect(api.getProjects).toHaveBeenCalledTimes(1);
  });

  it('selectProject switches the project and the API client, and remembers it as most recent', async () => {
    api.getProjects.mockResolvedValue([ALPHA, BRAVO]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));
    act(() => ctx.selectProject(BRAVO));
    expect(current()).toBe('Bravo');
    expect(api.state.stored).toBe(2);
    expect(JSON.parse(localStorage.getItem('nm.recentProjectIds') as string)[0]).toBe(2);
  });

  it('refreshProjects re-reads the list behind the loader, keeps the choice, and shows a rename', async () => {
    api.getProjects.mockResolvedValue([ALPHA, BRAVO]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));
    act(() => ctx.selectProject(BRAVO));

    let answer!: (rows: Project[]) => void;
    api.getProjects.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
    let refreshed!: Promise<void>;
    act(() => { refreshed = ctx.refreshProjects(); });
    expect(await screen.findByText('Loading projects…')).toBeInTheDocument();
    await act(async () => { answer([ALPHA, project(2, 'Bravo renamed')]); await refreshed; });
    await waitFor(() => expect(current()).toBe('Bravo renamed'));
    expect(mounts).toBe(2);    // the page was remounted by the loader, as before
  });

  it('a project that a refresh no longer lists gives way to the next most recent', async () => {
    api.getProjects.mockResolvedValue([ALPHA, BRAVO, CHARLIE]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));
    act(() => ctx.selectProject(CHARLIE));
    act(() => ctx.selectProject(BRAVO));

    api.getProjects.mockResolvedValue([ALPHA, CHARLIE]);
    await act(async () => { await ctx.refreshProjects(); });
    await waitFor(() => expect(current()).toBe('Charlie'));
    expect(api.state.stored).toBe(3);
  });

  it('a failed refresh is said on the full screen, and Retry brings the app back', async () => {
    api.getProjects.mockResolvedValueOnce([ALPHA]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));
    api.getProjects.mockRejectedValueOnce({ response: { status: 503 } });
    await act(async () => { await ctx.refreshProjects(); });
    expect(await screen.findByText('Could not load projects')).toBeInTheDocument();

    api.getProjects.mockResolvedValue([ALPHA]);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(current()).toBe('Alpha'));
  });

  it('adoptProject lists and selects a project just created, without re-reading or unmounting the page', async () => {
    api.getProjects.mockResolvedValue([ALPHA, CHARLIE]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));
    act(() => ctx.adoptProject(BRAVO));
    expect(current()).toBe('Bravo');
    await waitFor(() => expect(listed()).toBe('Alpha,Bravo,Charlie'));
    expect(api.getProjects).toHaveBeenCalledTimes(1);
    expect(mounts).toBe(1);
  });

  it('another reader re-reading the list updates it in place: no loader, the page stays mounted — failed or not', async () => {
    api.getProjects.mockResolvedValueOnce([ALPHA]);
    mount();
    await waitFor(() => expect(current()).toBe('Alpha'));

    api.getProjects.mockResolvedValueOnce([ALPHA, BRAVO]);
    fireEvent.click(screen.getByRole('button', { name: 'another reader re-reads' }));
    await waitFor(() => expect(listed()).toBe('Alpha,Bravo'));
    expect(screen.queryByText('Loading projects…')).toBeNull();

    api.getProjects.mockRejectedValueOnce({ response: { status: 503 } });
    fireEvent.click(screen.getByRole('button', { name: 'another reader re-reads' }));
    await waitFor(() => expect(api.getProjects).toHaveBeenCalledTimes(3));
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0); }); });
    expect(screen.queryByText('Could not load projects')).toBeNull();
    expect(listed()).toBe('Alpha,Bravo');
    expect(mounts).toBe(1);
  });
});
