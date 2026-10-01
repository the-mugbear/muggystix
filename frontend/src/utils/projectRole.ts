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

export interface ProjectRoleAccess {
  /** The caller's role on the current project; `undefined` when it has not
   *  loaded (or the project carries none), `null` for a non-member. */
  role: ProjectRoleName | null | undefined;
  /** analyst+ — uploads, scope, triage, host tests, notes, tags. */
  canWrite: boolean;
  /** auditor+ — exports, reports, starting an agent session. */
  canExport: boolean;
  isProjectAdmin: boolean;
  /** The ACCOUNT role (`User.role === 'admin'`): user management, system
   *  settings, the audit log.  A global admin passes every project check. */
  isGlobalAdmin: boolean;
}

/** One answer to "may this person do that here" (review 2026-10-01 R32).
 *  The rule `canStartAgentSession` set: only a role KNOWN to be too low hides
 *  a control.  A project the context has not loaded, or one without
 *  `my_role`, leaves the decision to the server, which refuses with 403. */
export const resolveProjectRole = (
  project: Pick<Project, 'my_role'> | null | undefined,
  globalRole: string | null | undefined,
): ProjectRoleAccess => {
  const isGlobalAdmin = globalRole === 'admin';
  const unknown = !project || project.my_role === undefined;
  const role = unknown ? undefined : (project.my_role as ProjectRoleName | null);
  const atLeast = (min: ProjectRoleName) => isGlobalAdmin || unknown || projectRoleAtLeast(role, min);
  return {
    role: isGlobalAdmin ? 'admin' : role,
    canWrite: atLeast('analyst'),
    canExport: atLeast('auditor'),
    isProjectAdmin: atLeast('admin'),
    isGlobalAdmin,
  };
};
