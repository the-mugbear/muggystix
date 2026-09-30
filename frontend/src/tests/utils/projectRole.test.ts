import { describe, it, expect } from 'vitest';

import { canStartAgentSession, projectRoleAtLeast } from '../../utils/projectRole';

// N3 — agent entry points follow the PROJECT role `POST /assist/start` checks
// (auditor and up), never the global one.
describe('projectRole', () => {
  it('orders roles as the backend does', () => {
    expect(projectRoleAtLeast('admin', 'auditor')).toBe(true);
    expect(projectRoleAtLeast('analyst', 'auditor')).toBe(true);
    expect(projectRoleAtLeast('auditor', 'auditor')).toBe(true);
    expect(projectRoleAtLeast('viewer', 'auditor')).toBe(false);
    expect(projectRoleAtLeast('auditor', 'analyst')).toBe(false);
    expect(projectRoleAtLeast(null, 'viewer')).toBe(false);
    expect(projectRoleAtLeast('owner', 'viewer')).toBe(false);
  });

  it('hides agent sessions only from a role known to be below auditor', () => {
    expect(canStartAgentSession({ my_role: 'auditor' })).toBe(true);
    expect(canStartAgentSession({ my_role: 'admin' })).toBe(true);
    expect(canStartAgentSession({ my_role: 'viewer' })).toBe(false);
    // Not a member (null) cannot start one either.
    expect(canStartAgentSession({ my_role: null })).toBe(false);
    // Unknown — the server decides.
    expect(canStartAgentSession({})).toBe(true);
    expect(canStartAgentSession(null)).toBe(true);
  });
});
