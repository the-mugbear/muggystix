/**
 * Project membership: the roles, and what is asked or refused before a
 * member's role is changed or a member is removed.
 *
 * ONE implementation for the three screens that manage members — Project
 * settings (Members), Portfolio's members sheet and the administrator's
 * memberships dialog.  They had three role lists and three confirm rules;
 * only Project settings asked before the last project admin was demoted or
 * before the reader changed their own role.
 *
 * What the SERVER does (`backend/app/api/v1/endpoints/projects.py`), for a
 * project admin and a global administrator alike:
 *   - removing the only project admin is refused (400 "Cannot remove the last
 *     project admin") — said here BEFORE the request, in words;
 *   - changing a role is never refused: the only admin can be demoted and the
 *     reader can demote themselves — so those two are confirmed here;
 *   - a member can remove themselves (unless they are the only admin).
 *
 * No client, no React: a decision is data, and `allowMemberChange` runs it
 * with the screen's own confirm dialog and toast.
 */

export interface ProjectRoleOption {
  value: string;
  label: string;
  /** What the role may do, for the reader choosing it. */
  can: string;
}

/** The per-project roles, highest first (admin > analyst > auditor > viewer). */
export const PROJECT_ROLES: ProjectRoleOption[] = [
  { value: 'admin', label: 'Admin', can: 'project settings and members, plus everything below' },
  { value: 'analyst', label: 'Analyst', can: 'uploads, scopes, triage, host tests, report drafts' },
  { value: 'auditor', label: 'Auditor', can: 'read everything, exports and reports' },
  { value: 'viewer', label: 'Viewer', can: 'read the inventory' },
];

/** The role an add-member form starts on, on every screen that has one
 *  (owner decision 2026-10-10): the lowest.  A higher role is a choice the
 *  person adding makes, never what an unread form sends. */
export const DEFAULT_MEMBER_ROLE = 'viewer';

export const projectRoleLabel = (role: string): string =>
  PROJECT_ROLES.find((r) => r.value === role)?.label ?? role;

/** A person's name as the member screens print it. */
export const memberName = (m: { full_name?: string | null; username?: string | null }): string =>
  m.full_name || m.username || 'this member';

/** How many project admins a roster has. */
export const countProjectAdmins = (members: ReadonlyArray<{ role: string }>): number =>
  members.filter((m) => m.role === 'admin').length;

export interface MemberConfirmation {
  title: string;
  body: string;
  severity: 'danger';
  confirmLabel: string;
}

export type MemberDecision =
  /** Nothing to ask: send it. */
  | { kind: 'proceed' }
  /** Ask first. */
  | { kind: 'confirm'; confirm: MemberConfirmation }
  /** The server refuses this: say why and send nothing. */
  | { kind: 'refuse'; reason: string };

export interface MemberChange {
  /** The member's printed name (`memberName`). */
  name: string;
  /** Their role in the project now. */
  role: string;
  projectName: string;
  /** The member is the signed-in reader. */
  isSelf: boolean;
  /** Project admins on the roster now, this member included. */
  adminCount: number;
}

const isOnlyAdmin = (c: MemberChange) => c.role === 'admin' && c.adminCount <= 1;

/** Before `PUT /projects/{id}/members/{user}`. */
export function roleChangeDecision(change: MemberChange, newRole: string): MemberDecision {
  if (newRole === change.role) return { kind: 'proceed' };
  const label = projectRoleLabel(newRole);
  const aLabel = `${/^[aeiou]/i.test(label) ? 'an' : 'a'} ${label}`;
  if (change.isSelf) {
    return {
      kind: 'confirm',
      confirm: {
        title: 'Change your own role?',
        body: `You will be ${aLabel} on ${change.projectName}.`
          + (newRole === 'admin' ? '' : ' Unless you are a global administrator, you will no longer be able to change settings or members there.')
          + (isOnlyAdmin(change) ? ' You are its only project admin: only a global administrator could then manage its members.' : ''),
        severity: 'danger',
        confirmLabel: `Make me ${label}`,
      },
    };
  }
  if (isOnlyAdmin(change)) {
    return {
      kind: 'confirm',
      confirm: {
        title: `${change.name} is the only project admin`,
        body: `Making them ${aLabel} leaves ${change.projectName} with no project admin; only a global administrator could then manage its members.`,
        severity: 'danger',
        confirmLabel: `Make them ${label}`,
      },
    };
  }
  return { kind: 'proceed' };
}

/** Before `DELETE /projects/{id}/members/{user}`. */
export function removalDecision(change: MemberChange): MemberDecision {
  if (isOnlyAdmin(change)) {
    return {
      kind: 'refuse',
      reason: change.isSelf
        ? `You are the only project admin of ${change.projectName} and cannot be removed. Make another member a project admin first.`
        : `${change.name} is the only project admin of ${change.projectName} and cannot be removed. Make another member a project admin first.`,
    };
  }
  if (change.isSelf) {
    return {
      kind: 'confirm',
      confirm: {
        title: `Remove yourself from ${change.projectName}?`,
        body: `You lose access to ${change.projectName}. Your notes, findings and reviews stay. Unless you are a global administrator, you cannot add yourself again.`,
        severity: 'danger',
        confirmLabel: 'Remove me',
      },
    };
  }
  return {
    kind: 'confirm',
    confirm: {
      title: `Remove ${change.name}?`,
      body: `${change.name} loses access to ${change.projectName}. Their notes, findings and reviews stay. They can be added again later.`,
      severity: 'danger',
      confirmLabel: 'Remove',
    },
  };
}

/** Run a decision on a screen: true when the change may be sent. */
export async function allowMemberChange(
  decision: MemberDecision,
  ask: (confirm: MemberConfirmation) => Promise<boolean>,
  refuse: (reason: string) => void,
): Promise<boolean> {
  if (decision.kind === 'refuse') { refuse(decision.reason); return false; }
  if (decision.kind === 'confirm') return ask(decision.confirm);
  return true;
}
