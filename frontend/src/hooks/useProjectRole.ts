import { useMemo } from 'react';

import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import { ProjectRoleAccess, ProjectRoleName, resolveProjectRole } from '../utils/projectRole';

/**
 * What the caller may do in the CURRENT project (review 2026-10-01 R32 / B1).
 *
 * The account role is binary (admin / member) and every member passed the old
 * `hasPermission('analyst')`, so a project viewer was shown every write
 * control and learned from the 403.  Gate project controls on this hook;
 * keep `hasPermission('admin')` for the account-level surfaces (users, system
 * settings, audit log, Oversight).
 *
 *   const { canWrite, canExport, isProjectAdmin } = useProjectRole();
 *
 * A role that has not loaded leaves the decision to the server (the control
 * is shown) — see `resolveProjectRole`.
 */
export function useProjectRole(): ProjectRoleAccess {
  const { currentProject } = useProject();
  const globalRole = useAuth().user?.role;
  const myRole = currentProject ? currentProject.my_role : undefined;
  const known = !!currentProject;
  return useMemo(
    () => resolveProjectRole(known ? { my_role: myRole } : null, globalRole),
    [known, myRole, globalRole],
  );
}

/**
 * The route / navigation gate: `requiredRole` on a route or a nav entry.
 * `admin` is the ACCOUNT role (those surfaces are instance-wide); `analyst`
 * and `auditor` are the PROJECT role (the pages are project-scoped); `viewer`
 * is any signed-in account.
 */
export function useRoleGate(): (requiredRole: string | undefined) => boolean {
  const { hasPermission } = useAuth();
  const access = useProjectRole();
  return useMemo(() => (requiredRole: string | undefined) => {
    if (!requiredRole) return true;
    if (requiredRole === 'analyst') return access.canWrite;
    if (requiredRole === 'auditor') return access.canExport;
    return hasPermission(requiredRole);
  }, [access, hasPermission]);
}

export type { ProjectRoleAccess, ProjectRoleName };
