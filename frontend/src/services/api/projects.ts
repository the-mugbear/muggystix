/**
 * Project CRUD — list, create, update.  Project membership endpoints are in projects-membership.ts (TBD).
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// --- Project Management ---
export interface Project {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  is_default: boolean;
  is_archived: boolean;
  start_date: string | null;
  end_date: string | null;
  created_by_id: number | null;
  created_at: string;
  updated_at: string | null;
  member_count: number | null;
}

export const getProjects = async (): Promise<Project[]> => {
  const response = await api.get('/projects/');
  return response.data;
};

export const updateProject = async (projectId: number, data: {
  name?: string;
  description?: string;
  status?: string;
  start_date?: string | null;
  end_date?: string | null;
}): Promise<Project> => {
  const response = await api.put(`/projects/${projectId}`, data);
  return response.data;
};

export const createProject = async (name: string, description?: string): Promise<Project> => {
  const response = await api.post('/projects/', { name, description });
  return response.data;
};
