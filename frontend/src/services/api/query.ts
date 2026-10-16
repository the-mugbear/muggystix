/**
 * The Query page (5.405.0; backend 2.509.0): SPL-style queries over hosts,
 * ports and scanner observations, and saved / shared queries.
 * Backend: app/api/v1/endpoints/queries.py.
 */
import { api, projectPath } from './client';

export interface QueryColumn {
  name: string;
  /** text | int | float | bool | datetime | enum | list */
  type: string;
}

export interface QueryAnswer {
  query: string;
  dataset: string;
  /** rows: a listing; stats: `stats` / `top`. */
  kind: 'rows' | 'stats';
  columns: QueryColumn[];
  rows: unknown[][];
  /** Rows matched (a listing) or groups (stats / top) — the whole answer. */
  total: number;
  /** The most this answer shows. */
  limit: number;
  /** `head` asked for more than the cap. */
  capped: boolean;
  /** stats / top: per row, the query listing exactly that group's rows. */
  drilldowns: (string | null)[] | null;
}

export const runQuery = async (projectId: number, q: string, signal?: AbortSignal): Promise<QueryAnswer> =>
  (await api.get<QueryAnswer>(`${projectPath(projectId)}/query`, { params: { q }, signal })).data;

export interface QuerySchemaColumn {
  name: string;
  type: string;
  description: string;
  values?: string[];
}

export interface QuerySchema {
  datasets: { name: string; description: string; columns: QuerySchemaColumn[]; default_fields: string[]; examples: string[] }[];
  commands: { name: string; syntax: string; description: string }[];
  caps: { rows: number; groups: number; values: number };
}

export const getQuerySchema = async (projectId: number, signal?: AbortSignal): Promise<QuerySchema> =>
  (await api.get<QuerySchema>(`${projectPath(projectId)}/query/schema`, { signal })).data;

export interface SavedQuery {
  id: number;
  name: string;
  description: string | null;
  query: string;
  shared: boolean;
  created_by: string | null;
  mine: boolean;
  can_modify: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export const listSavedQueries = async (projectId: number, signal?: AbortSignal): Promise<SavedQuery[]> =>
  (await api.get<SavedQuery[]>(`${projectPath(projectId)}/queries/saved`, { signal })).data;

export const createSavedQuery = async (
  projectId: number, body: { name: string; query: string; description?: string; shared: boolean },
): Promise<SavedQuery> =>
  (await api.post<SavedQuery>(`${projectPath(projectId)}/queries/saved`, body)).data;

export const updateSavedQuery = async (
  projectId: number, id: number,
  changes: Partial<{ name: string; query: string; description: string | null; shared: boolean }>,
): Promise<SavedQuery> =>
  (await api.patch<SavedQuery>(`${projectPath(projectId)}/queries/saved/${id}`, changes)).data;

export const deleteSavedQuery = async (projectId: number, id: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/queries/saved/${id}`);
};
