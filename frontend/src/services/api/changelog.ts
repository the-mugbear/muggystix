/**
 * The project's changelog: what its members — and the agents working for
 * them — did to the project's record.  One read, for project auditors and
 * above; nothing writes it from the browser (a line is written by the change
 * it describes).
 */
import { api, projectPath } from './client';

export interface ChangelogPerson {
  id: number;
  username: string;
  full_name: string | null;
}

export interface ChangelogLine {
  id: number;
  created_at: string;
  /** The server's action name; `group` is the kind filter it belongs to and
   *  `words` what is printed between the person and the subject. */
  action: string;
  group: string;
  words: string;
  /** Whose authority the action used; null once that account is deleted. */
  actor: ChangelogPerson | null;
  /** An agent did it for `actor`.  The session is null once it is gone. */
  by_agent: boolean;
  agent_session_id: number | null;
  /** What it was done to — a type and an id (the subject may be gone: a
   *  deletion is a line too) and what it was called at the time. */
  subject_type: string | null;
  subject_id: number | null;
  subject_label: string | null;
  summary: string | null;
  /** How many things the one action touched (a bulk action is one line). */
  count: number;
}

export interface ChangelogPage {
  items: ChangelogLine[];
  total: number;
  limit: number;
  offset: number;
  returned: number;
  has_more: boolean;
  /** Everyone with a line in the project: the person filter's choices. */
  people: ChangelogPerson[];
  /** The kinds the list can be narrowed to. */
  groups: string[];
  /** The project holds as many lines as the installation keeps: older ones
   *  were removed. */
  trimmed: boolean;
}

export type ChangelogBy = 'people' | 'agents';

export interface ChangelogFilters {
  actorId?: number | null;
  by?: ChangelogBy | null;
  group?: string | null;
  limit?: number;
  offset?: number;
}

export const getChangelog = async (
  projectId: number,
  filters: ChangelogFilters = {},
  signal?: AbortSignal,
): Promise<ChangelogPage> => {
  const params = new URLSearchParams();
  if (filters.actorId != null) params.set('actor_id', String(filters.actorId));
  if (filters.by) params.set('by_agent', filters.by === 'agents' ? 'true' : 'false');
  if (filters.group) params.set('group', filters.group);
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  if (filters.offset !== undefined) params.set('offset', String(filters.offset));
  const qs = params.toString();
  const r = await api.get<ChangelogPage>(`${projectPath(projectId)}/changelog${qs ? `?${qs}` : ''}`, { signal });
  return r.data;
};
