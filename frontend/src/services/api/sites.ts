/**
 * Site management API — the project-scoped Site metadata (criticality tier /
 * owner / expected host count) the attention model weights by.
 */
import { api, p } from './client';

export interface Site {
  id: number;
  name: string;
  criticality_tier: number; // 1 (most critical) … 4
  owner_id: number | null;
  owner_name: string | null;
  expected_host_count: number | null;
  subnet_count: number;
}

export const listSites = async (): Promise<Site[]> => {
  const response = await api.get<Site[]>(`${p()}/sites`);
  return response.data;
};

export const updateSite = async (
  siteId: number,
  payload: { criticality_tier?: number; owner_id?: number | null; expected_host_count?: number | null },
): Promise<Site> => {
  const response = await api.patch<Site>(`${p()}/sites/${siteId}`, payload);
  return response.data;
};
