/**
 * The current project's members — the ONE loader for every owner / assignee
 * picker and for @mention autocomplete and highlighting.
 *
 * One query per project (`['listProjectMembers', projectId]`,
 * `projectMembersQuery` — also what the member-management screens spread,
 * `tests/projectMembersOneLoader.test.ts`), shared by
 * everything on the page and kept for a few minutes whether or not anything
 * shows it; after that the next reader asks again (a member added mid-session
 * appears without a reload).  A failure is not kept: the next reader, or
 * Retry, asks again.
 * `useProjectRoster` gives the full rows with a status, so a picker whose
 * load failed says so (with `retry`) instead of "No members".
 * `useProjectMembers` is the mention shape of the same query: a failure there
 * yields an empty list — mentions still work when typed in full, they just
 * are not suggested or highlighted.
 */
import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { listProjectMembers, type ProjectMember } from '../services/api';
import { NO_PROJECT, useProjectId } from './useProjectId';
import { rememberFor } from '../lib/query';
import type { MentionCandidate } from '../utils/mentions';

const TTL_MS = 5 * 60 * 1000;
const EMPTY: ProjectMember[] = [];
const NO_CANDIDATES: MentionCandidate[] = [];

export type ProjectMembersStatus = 'loading' | 'ready' | 'error';

export interface ProjectRoster {
  /** Empty until `status` is `ready` — never another project's rows. */
  members: ProjectMember[];
  status: ProjectMembersStatus;
  retry: () => void;
}

/** THE query of a project's members — key, request and lifetime — for every
 *  reader of `['listProjectMembers', projectId]`: the roster below, Project
 *  settings, Portfolio's members sheet (a project it is given, not the
 *  selected one) and the admins' memberships dialog.  One definition, so the
 *  shared cache entry has one shape and one lifetime whoever filled it
 *  (review 2026-10-23, R-FE3: four loaders, one normalising and remembering,
 *  three not).  Spread it into `useQuery` / `fetchQuery` and add `enabled`. */
export const projectMembersQuery = (projectId: number) => ({
  queryKey: ['listProjectMembers', projectId] as const,
  queryFn: async ({ signal }: { signal?: AbortSignal }): Promise<ProjectMember[]> => {
    const rows = await listProjectMembers(projectId, signal);
    return Array.isArray(rows) ? rows : EMPTY;
  },
  ...rememberFor(TTL_MS),
});

/** The reads a membership change (add, role, removal) puts out of date, by
 *  API function — for every member screen (Project settings, Portfolio's
 *  members sheet, the admins' memberships dialog): the roster and its
 *  pickers, the project list (`getProjects` carries the reader's own role,
 *  which `useProjectRole` follows, and lists only their projects), Portfolio's
 *  member counts and one person's memberships.  Hand-kept like
 *  `OPERATIONS_READS`; the screens each kept their own and drifted (review
 *  2026-10-23b, FEC-2: the sheet left `getProjects` out, so demoting yourself
 *  kept your old controls until a reload). */
export const MEMBERSHIP_READS = [
  'listProjectMembers', 'getProjects', 'getPortfolioDashboard', 'getUserMemberships',
] as const;

/** `enabled: false` asks for nothing yet (a menu not opened, a role that has
 *  no picker); the status stays `loading`. */
export function useProjectRoster({ enabled = true }: { enabled?: boolean } = {}): ProjectRoster {
  const projectId = useProjectId();
  const on = enabled && projectId !== NO_PROJECT;
  const query = useQuery({ ...projectMembersQuery(projectId), enabled: on });

  const { refetch } = query;
  const retry = useCallback(() => { void refetch(); }, [refetch]);
  // A reader that has not asked yet is `loading`, whatever another has read.
  const rows = on ? query.data : undefined;
  const members = rows && rows.length > 0 ? rows : EMPTY;
  // Rows that were read stay the roster while a later re-read fails.
  const status: ProjectMembersStatus = rows ? 'ready' : on && query.isError ? 'error' : 'loading';
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
