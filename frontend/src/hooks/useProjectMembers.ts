/**
 * The current project's members — the ONE loader for every owner / assignee
 * picker and for @mention autocomplete and highlighting.
 *
 * One request per project, shared by everything on the page and refreshed
 * after a few minutes (a member added mid-session appears without a reload).
 * `useProjectRoster` gives the full rows with a status, so a picker whose
 * load failed says so (with `retry`) instead of "No members".
 * `useProjectMembers` is the mention shape of the same cache: a failure there
 * yields an empty list — mentions still work when typed in full, they just
 * are not suggested or highlighted.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { listProjectMembers, type ProjectMember } from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import type { MentionCandidate } from '../utils/mentions';

const TTL_MS = 5 * 60 * 1000;
const cache = new Map<number, { at: number; rows: Promise<ProjectMember[]> }>();
const EMPTY: ProjectMember[] = [];
const NO_CANDIDATES: MentionCandidate[] = [];

function load(projectId: number): Promise<ProjectMember[]> {
  const hit = cache.get(projectId);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.rows;
  let rows: Promise<ProjectMember[]>;
  try {
    rows = Promise.resolve(listProjectMembers()).then((r) => (Array.isArray(r) ? r : EMPTY));
  } catch (err) {
    rows = Promise.reject(err);
  }
  const entry = { at: Date.now(), rows };
  cache.set(projectId, entry);
  // A failure is not remembered: the next reader (or Retry) asks again.
  rows.catch(() => { if (cache.get(projectId) === entry) cache.delete(projectId); });
  return rows;
}

/** Test hook: forget every cached roster. */
export function resetProjectMembersCache(): void {
  cache.clear();
}

export type ProjectMembersStatus = 'loading' | 'ready' | 'error';

export interface ProjectRoster {
  /** Empty until `status` is `ready` — never another project's rows. */
  members: ProjectMember[];
  status: ProjectMembersStatus;
  retry: () => void;
}

interface RosterState {
  projectId: number | null;
  members: ProjectMember[];
  status: ProjectMembersStatus;
}

/** `enabled: false` asks for nothing yet (a menu not opened, a role that has
 *  no picker); the status stays `loading`. */
export function useProjectRoster({ enabled = true }: { enabled?: boolean } = {}): ProjectRoster {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? null;
  const [state, setState] = useState<RosterState>({ projectId, members: EMPTY, status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled || projectId == null) return undefined;
    let live = true;
    setState((prev) => (
      prev.projectId === projectId && prev.status !== 'error' ? prev : { projectId, members: EMPTY, status: 'loading' }
    ));
    load(projectId).then(
      (rows) => {
        if (!live) return;
        setState((prev) => (
          prev.projectId === projectId && prev.status === 'ready' && prev.members === rows
            ? prev
            : { projectId, members: rows.length ? rows : EMPTY, status: 'ready' }
        ));
      },
      () => { if (live) setState({ projectId, members: EMPTY, status: 'error' }); },
    );
    return () => { live = false; };
  }, [projectId, enabled, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const mine = state.projectId === projectId;
  const members = mine ? state.members : EMPTY;
  const status = mine ? state.status : 'loading';
  return useMemo(() => ({ members, status, retry }), [members, status, retry]);
}

export function useProjectMembers(): MentionCandidate[] {
  const { members } = useProjectRoster();
  return useMemo(
    () => (members.length === 0
      ? NO_CANDIDATES
      : members
        .filter((r) => !!r.username)
        .map((r) => ({ username: r.username as string, full_name: r.full_name ?? null }))),
    [members],
  );
}
