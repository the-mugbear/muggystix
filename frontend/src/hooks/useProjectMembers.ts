/**
 * The current project's members — the ONE loader for every owner / assignee
 * picker and for @mention autocomplete and highlighting.
 *
 * One query per project (`['listProjectMembers', projectId]`), shared by
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
import { queryClient, rememberFor } from '../lib/query';
import type { MentionCandidate } from '../utils/mentions';

const TTL_MS = 5 * 60 * 1000;
const EMPTY: ProjectMember[] = [];
const NO_CANDIDATES: MentionCandidate[] = [];

/** Forget every roster the app's client holds.  Tests call it between cases;
 *  each of their renders has a client of its own, so there it changes nothing. */
export function resetProjectMembersCache(): void {
  queryClient.removeQueries({ queryKey: ['listProjectMembers'] });
}

export type ProjectMembersStatus = 'loading' | 'ready' | 'error';

export interface ProjectRoster {
  /** Empty until `status` is `ready` — never another project's rows. */
  members: ProjectMember[];
  status: ProjectMembersStatus;
  retry: () => void;
}

/** `enabled: false` asks for nothing yet (a menu not opened, a role that has
 *  no picker); the status stays `loading`. */
export function useProjectRoster({ enabled = true }: { enabled?: boolean } = {}): ProjectRoster {
  const projectId = useProjectId();
  const on = enabled && projectId !== NO_PROJECT;
  const query = useQuery({
    queryKey: ['listProjectMembers', projectId],
    queryFn: async ({ signal }) => {
      const rows = await listProjectMembers(projectId, signal);
      return Array.isArray(rows) ? rows : EMPTY;
    },
    enabled: on,
    ...rememberFor(TTL_MS),
  });

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
