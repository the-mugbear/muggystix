/**
 * The caller's PROJECT role (`Project.my_role` — 'admin' for a global admin),
 * ordered as the backend's `require_project_role` orders it:
 * admin > analyst > auditor > viewer. Never gate a project feature on the
 * global role; it is binary (admin / member).
 */
import type { Project } from '../services/api';

export type ProjectRoleName = 'admin' | 'analyst' | 'auditor' | 'viewer';

const RANK: Record<ProjectRoleName, number> = { viewer: 1, auditor: 2, analyst: 3, admin: 4 };

/** True when `role` is `min` or above. Null (not a member) is below everything. */
export const projectRoleAtLeast = (
  role: string | null | undefined,
  min: ProjectRoleName,
): boolean => (role != null && (RANK[role as ProjectRoleName] ?? 0) >= RANK[min]);

/** Whether to offer "start an agent session" (`POST /assist/start` needs
 *  auditor). Only a role KNOWN to be below auditor hides it: a project the
 *  context has not loaded, or one without `my_role`, leaves the decision to
 *  the server, which refuses with 403 anyway. */
export const canStartAgentSession = (project: Pick<Project, 'my_role'> | null | undefined): boolean => {
  if (!project || project.my_role === undefined) return true;
  return projectRoleAtLeast(project.my_role, 'auditor');
};
