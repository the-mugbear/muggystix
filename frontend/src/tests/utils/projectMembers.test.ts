/**
 * The member rules shared by Project settings, Portfolio's members sheet and
 * the administrator's memberships dialog (defect 1.4): what is asked, and
 * what is refused, before a role change or a removal is sent.
 */
import { describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_MEMBER_ROLE, PROJECT_ROLES, allowMemberChange, countProjectAdmins, removalDecision, roleChangeDecision,
  type MemberChange,
} from '../../utils/projectMembers';

const change = (over: Partial<MemberChange> = {}): MemberChange => ({
  name: 'Ana', role: 'analyst', projectName: 'Acme', isSelf: false, adminCount: 2, ...over,
});

describe('project roles', () => {
  it('is the four roles, highest first', () => {
    expect(PROJECT_ROLES.map((r) => r.value)).toEqual(['admin', 'analyst', 'auditor', 'viewer']);
    expect(PROJECT_ROLES.map((r) => r.label)).toEqual(['Admin', 'Analyst', 'Auditor', 'Viewer']);
  });

  // Owner decision 2026-10-10: one default for every add-member screen.
  it('a new member starts as a viewer — the lowest role', () => {
    expect(DEFAULT_MEMBER_ROLE).toBe('viewer');
    expect(PROJECT_ROLES[PROJECT_ROLES.length - 1].value).toBe(DEFAULT_MEMBER_ROLE);
  });

  it('counts the admins of a roster', () => {
    expect(countProjectAdmins([{ role: 'admin' }, { role: 'viewer' }, { role: 'admin' }])).toBe(2);
  });
});

describe('changing a role', () => {
  it('sends an ordinary change without asking', () => {
    expect(roleChangeDecision(change(), 'viewer')).toEqual({ kind: 'proceed' });
    // An admin who is not the only one.
    expect(roleChangeDecision(change({ role: 'admin' }), 'viewer')).toEqual({ kind: 'proceed' });
  });

  it('asks before the only project admin is demoted (the server would not refuse)', () => {
    expect(roleChangeDecision(change({ role: 'admin', adminCount: 1 }), 'viewer')).toEqual({
      kind: 'confirm',
      confirm: {
        title: 'Ana is the only project admin',
        body: 'Making them a Viewer leaves Acme with no project admin; only a global administrator could then manage its members.',
        severity: 'danger',
        confirmLabel: 'Make them Viewer',
      },
    });
  });

  it('asks before the reader changes their own role', () => {
    expect(roleChangeDecision(change({ role: 'admin', isSelf: true }), 'analyst')).toEqual({
      kind: 'confirm',
      confirm: {
        title: 'Change your own role?',
        body: 'You will be an Analyst on Acme. Unless you are a global administrator, you will no longer be able to change settings or members there.',
        severity: 'danger',
        confirmLabel: 'Make me Analyst',
      },
    });
  });

  it('says both when the reader is the only project admin', () => {
    const decision = roleChangeDecision(change({ role: 'admin', isSelf: true, adminCount: 1 }), 'viewer');
    expect(decision.kind).toBe('confirm');
    expect(decision.kind === 'confirm' && decision.confirm.body).toMatch(/You are its only project admin/);
  });
});

describe('removing a member', () => {
  it('asks, in one wording', () => {
    expect(removalDecision(change())).toEqual({
      kind: 'confirm',
      confirm: {
        title: 'Remove Ana?',
        body: 'Ana loses access to Acme. Their notes, findings and reviews stay. They can be added again later.',
        severity: 'danger',
        confirmLabel: 'Remove',
      },
    });
  });

  it('refuses the only project admin, as the server does, and says what to do', () => {
    expect(removalDecision(change({ role: 'admin', adminCount: 1 }))).toEqual({
      kind: 'refuse',
      reason: 'Ana is the only project admin of Acme and cannot be removed. Make another member a project admin first.',
    });
  });

  it('asks the reader differently about themselves', () => {
    const decision = removalDecision(change({ isSelf: true }));
    expect(decision.kind === 'confirm' && decision.confirm.title).toBe('Remove yourself from Acme?');
  });
});

describe('allowMemberChange', () => {
  it('a refusal is said and nothing is asked', async () => {
    const ask = vi.fn();
    const refuse = vi.fn();
    expect(await allowMemberChange({ kind: 'refuse', reason: 'no' }, ask, refuse)).toBe(false);
    expect(refuse).toHaveBeenCalledWith('no');
    expect(ask).not.toHaveBeenCalled();
  });

  it('a confirmation is the reader\'s answer', async () => {
    const confirm = { title: 't', body: 'b', severity: 'danger' as const, confirmLabel: 'c' };
    expect(await allowMemberChange({ kind: 'confirm', confirm }, async () => false, vi.fn())).toBe(false);
    expect(await allowMemberChange({ kind: 'confirm', confirm }, async () => true, vi.fn())).toBe(true);
  });

  it('an ordinary change goes ahead', async () => {
    expect(await allowMemberChange({ kind: 'proceed' }, vi.fn(), vi.fn())).toBe(true);
  });
});
