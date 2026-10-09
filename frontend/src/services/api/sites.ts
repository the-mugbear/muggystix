/**
 * Site management API — the project-scoped Site metadata (criticality tier /
 * owner / expected host count) the attention model weights by.
 */
import { api, projectPath } from './client';

export interface Site {
  id: number;
  name: string;
  criticality_tier: number; // 1 (most critical) … 4
  owner_id: number | null;
  owner_name: string | null;
  expected_host_count: number | null;
  subnet_count: number;
}

export const listSites = async (projectId: number, signal?: AbortSignal): Promise<Site[]> => {
  const response = await api.get<Site[]>(`${projectPath(projectId)}/sites`, { signal });
  return response.data;
};

export const updateSite = async (
  projectId: number,
  siteId: number,
  payload: { criticality_tier?: number; owner_id?: number | null; expected_host_count?: number | null },
): Promise<Site> => {
  const response = await api.patch<Site>(`${projectPath(projectId)}/sites/${siteId}`, payload);
  return response.data;
};
