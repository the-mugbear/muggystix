/**
 * Review 2026-10-01 R32 — the bulk bar had no role check.  Tagging, assigning
 * and proposing tests are a project analyst's; review status is the caller's
 * own and copying IPs changes nothing, so a reader keeps those.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const role = vi.hoisted(() => ({ value: 'viewer' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 9, username: 'reader', role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
}));
vi.mock('../../services/api', () => ({
  bulkTagHosts: vi.fn(), bulkAssignHosts: vi.fn(), bulkUnassignHosts: vi.fn(), bulkFollowHosts: vi.fn(),
  getMatchingHostIds: vi.fn(),
  listHostTags: vi.fn().mockResolvedValue([]),
  listProjectMembers: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../components/hosts/ProposeTestsDialog', () => ({ default: () => null }));

import HostBulkBar from '../../components/hosts/HostBulkBar';
import * as api from '../../services/api';

const renderBar = () => render(
  <HostBulkBar selectedIds={[1, 2]} selectedIps={['10.0.0.1', '10.0.0.2']} totalMatching={2} bulkCap={5000}
    queryContext={{}} onClear={vi.fn()} onApplied={vi.fn()} />,
);

beforeEach(() => { vi.clearAllMocks(); });

describe('HostBulkBar — what a selection can do follows the project role', () => {
  it('a viewer copies IPs and sets their own review status, and nothing else', () => {
    role.value = 'viewer';
    renderBar();
    expect(screen.getByRole('button', { name: /Copy IPs/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Review/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^\s*Tag/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Assign/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Propose tests/ })).toBeNull();
    // The pickers are not rendered, so their lists are not fetched.
    expect(api.listHostTags).not.toHaveBeenCalled();
    expect(api.listProjectMembers).not.toHaveBeenCalled();
  });

  it('an analyst gets all of them', () => {
    role.value = 'analyst';
    renderBar();
    expect(screen.getByRole('button', { name: /Tag/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Assign/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Propose tests/ })).toBeInTheDocument();
    expect(api.listHostTags).toHaveBeenCalled();
  });
});
