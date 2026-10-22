/**
 * The Query page (5.405.0; backend 2.509.0): pipe queries over hosts,
 * ports and scanner observations, and saved / shared queries.
 * Backend: app/api/v1/endpoints/queries.py.
 */
import { saveBlob } from '../../utils/download';
import { exportNote } from '../../utils/queryExport';
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
  /** A listing: per row, its page (the host, the finding, the endpoint). */
  links?: (string | null)[] | null;
  /** stats / top: the group columns — the answer's first columns. */
  by?: string[] | null;
  /** [row, column] of each values() list cut at the cap: the cell holds the first 20. */
  truncated_values?: [number, string][];
  /** An empty answer whose `where` compares text with `=`: that `=` is exact,
   *  and how many rows would match ignoring case. */
  hint?: string | null;
  /** A listing: where these rows begin in the whole answer (0 = its first row). */
  start?: number;
  /** A listing with rows past these (and no `head`): the next page's `after`. */
  next_after?: string | null;
  /** A `hosts` listing with no `where`: the Hosts-page query listing exactly
   *  these hosts ("" = every host); null otherwise. */
  hosts_q?: string | null;
}

/** `after` = a listing's next page (the previous page's `next_after`, 5.418.0). */
export const runQuery = async (
  projectId: number, q: string, after: string | null, signal?: AbortSignal,
): Promise<QueryAnswer> =>
  (await api.get<QueryAnswer>(`${projectPath(projectId)}/query`, {
    params: { q, after: after ?? undefined }, signal,
  })).data;

/** A query as the builder shows it — the server's grammar, read once
 *  (review Q9); `shape` null with a `reason` when the builder cannot show
 *  all of it.  A query that does not read is a 400 with `position`. */
export interface BuilderShape {
  dataset: string;
  terms: string;
  conditions: { column: string; op: string; value: string }[];
  by: string[];
  measure: { fn: string; column: string; as: string | null };
  most_first: boolean;
  /** head N (0: none); for top, its limit. */
  head: number;
}

export const parseQueryForBuilder = async (
  projectId: number, q: string, signal?: AbortSignal,
): Promise<{ shape: BuilderShape | null; reason: string | null }> =>
  (await api.get(`${projectPath(projectId)}/query/parse`, { params: { q }, signal })).data;

/** The answer as CSV (auditor and above; a listing up to 50,000 rows) — saved
 *  as `query-<dataset>.csv`; answers the file name and, when it is not the
 *  whole answer, a note saying how much it holds. */
export const downloadQueryCsv = async (
  projectId: number, q: string, dataset: string,
): Promise<{ filename: string; note?: string }> => {
  const response = await api.get(`${projectPath(projectId)}/query/export.csv`, { params: { q }, responseType: 'blob' });
  // Five exports of one dataset are five files (second review, Q12).
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  const filename = `query-${dataset}-${stamp}.csv`;
  saveBlob(new Blob([response.data], { type: 'text/csv' }), filename);
  return { filename, note: exportNote(response.headers ?? {}) };
};

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

/** One of my queries (v2.512.0): every run is recorded on the server, mine
 *  and my agent's (`by_agent`), a refused one with why. */
export interface QueryHistoryEntry {
  id: number;
  q: string;
  by_agent: boolean;
  /** The answer's total at the last run; null when it was refused. */
  result_count: number | null;
  /** Why the last run was refused. */
  error: string | null;
  error_position: number | null;
  run_count: number;
  first_run_at: string | null;
  last_run_at: string;
}

export interface QueryHistoryPage {
  items: QueryHistoryEntry[];
  /** Entries matching `contains` (the newest 200 are kept). */
  total: number;
}

export const listQueryHistory = async (
  projectId: number, params: { contains?: string; limit: number; offset: number }, signal?: AbortSignal,
): Promise<QueryHistoryPage> =>
  (await api.get<QueryHistoryPage>(`${projectPath(projectId)}/query/history`, {
    params: { ...params, contains: params.contains || undefined }, signal,
  })).data;

export const deleteQueryHistoryEntry = async (projectId: number, id: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/query/history/${id}`);
};

export const clearQueryHistory = async (projectId: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/query/history`);
};

export interface QueryColumnValues {
  /** False for a column that is not text: nothing to offer. */
  supported: boolean;
  values: { value: string; count: number }[];
  timed_out: boolean;
}

/** A text column's values in the project containing `contains`, most rows
 *  first — what the editor offers after `where col = "`. */
export const getQueryColumnValues = async (
  projectId: number, dataset: string, column: string, contains: string, signal?: AbortSignal,
): Promise<QueryColumnValues> =>
  (await api.get<QueryColumnValues>(`${projectPath(projectId)}/query/values`, {
    params: { dataset, column, contains }, signal,
  })).data;
