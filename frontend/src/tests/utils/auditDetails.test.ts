import { describe, it, expect } from 'vitest';

import { formatAuditDetails } from '../../utils/auditDetails';
import { personInitials, personName } from '../../utils/people';

describe('formatAuditDetails', () => {
  it('prettifies the known keys and values', () => {
    expect(formatAuditDetails({ method: 'totp' })).toBe('method: TOTP');
    expect(formatAuditDetails({ method: 'password' })).toBe('method: password');
    expect(formatAuditDetails({ username: 'bob', stage: '2fa' })).toBe('username: bob · stage: 2FA');
    expect(formatAuditDetails({ target_user: 'eval-ana' })).toBe('user: eval-ana');
    expect(formatAuditDetails({ new_username: 'x', role: 'member' })).toBe('new user: x · role: member');
  });

  it('shows a change set as old → new', () => {
    expect(
      formatAuditDetails({ changes: { full_name: { old: 'Ana', new: 'Ana Ortiz' } }, target_user: 'eval-ana' }),
    ).toBe('full name: Ana → Ana Ortiz · user: eval-ana');
    expect(formatAuditDetails({ changes: { is_active: { old: true, new: false } } })).toBe('is active: true → false');
    expect(formatAuditDetails({ changes: {} })).toBe('no changes');
  });

  it('shows unknown keys as key: value, lists capped, and marks client events', () => {
    expect(formatAuditDetails({ plan_id: 4, sanity_checks_passed: false })).toBe('plan id: 4 · sanity checks passed: false');
    expect(formatAuditDetails({ finding_ids: [1, 2, 3, 4, 5, 6, 7] })).toBe('finding ids: 1, 2, 3, 4, 5 +2 more');
    expect(formatAuditDetails({ source: 'client' })).toBe('reported by the client');
    expect(formatAuditDetails({ reason: null })).toBe('reason: —');
  });

  it('passes strings through and has nothing to show for empty details', () => {
    expect(formatAuditDetails('free text')).toBe('free text');
    expect(formatAuditDetails(null)).toBeNull();
    expect(formatAuditDetails({})).toBeNull();
    expect(formatAuditDetails('  ')).toBeNull();
  });
});

describe('people', () => {
  it('takes initials from the full name, falling back to the username', () => {
    expect(personInitials('Ana Ortiz', 'eval-ana')).toBe('AO');
    expect(personInitials('Mary Jane van Dyke', 'mj')).toBe('MD');
    expect(personInitials('Cher', 'cher')).toBe('C');
    expect(personInitials(null, 'eval-ana')).toBe('E');
    expect(personInitials('  ', '')).toBe('?');
  });

  it('names a person by full name, then username, then a dash', () => {
    expect(personName('Ana Ortiz', 'eval-ana')).toBe('Ana Ortiz');
    expect(personName(null, 'admin')).toBe('admin');
    expect(personName(null, null)).toBe('—');
  });
});
