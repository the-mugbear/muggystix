/**
 * The current project's members, for @mention autocomplete and highlighting.
 *
 * One request per project, shared by every composer and message on the page
 * and refreshed after a few minutes (a member added mid-session appears
 * without a reload). A failure yields an empty list: mentions still work when
 * typed in full, they just are not suggested or highlighted.
 */
import { useEffect, useState } from 'react';

import { listProjectMembers } from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import type { MentionCandidate } from '../utils/mentions';

const TTL_MS = 5 * 60 * 1000;
const cache = new Map<number, { at: number; members: Promise<MentionCandidate[]> }>();

function load(projectId: number): Promise<MentionCandidate[]> {
  const hit = cache.get(projectId);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.members;
  let request: Promise<MentionCandidate[]>;
  try {
    request = listProjectMembers().then((rows) =>
      rows
        .filter((r) => !!r.username)
        .map((r) => ({ username: r.username as string, full_name: r.full_name ?? null })),
    );
  } catch {
    request = Promise.resolve([]);
  }
  const members = request.catch(() => {
    cache.delete(projectId);
    return [] as MentionCandidate[];
  });
  cache.set(projectId, { at: Date.now(), members });
  return members;
}

/** Test hook: forget every cached roster. */
export function resetProjectMembersCache(): void {
  cache.clear();
}

export function useProjectMembers(): MentionCandidate[] {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? null;
  const [members, setMembers] = useState<MentionCandidate[]>([]);

  useEffect(() => {
    if (projectId == null) {
      setMembers([]);
      return;
    }
    let live = true;
    void load(projectId).then((m) => {
      // An empty roster over an empty roster is no change (no re-render).
      if (live) setMembers((prev) => (prev.length === 0 && m.length === 0 ? prev : m));
    });
    return () => {
      live = false;
    };
  }, [projectId]);

  return members;
}
