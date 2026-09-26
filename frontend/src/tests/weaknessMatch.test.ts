import { describe, expect, it } from 'vitest';
import { matchedWeaknesses, weaknessMatchCriteria } from '../utils/weaknessMatch';

describe('weaknessMatchCriteria', () => {
  it('is null with no weakness condition', () => {
    expect(weaknessMatchCriteria({ query: 'port:80' })).toBeNull();
  });

  it('reads the structured filters and has: / check: terms in the query', () => {
    expect(weaknessMatchCriteria({
      weaknesses: ['eol'],
      checks: ['smbv1_enabled'],
      query: '(has:smb_unsigned,weak_tls OR check:"vnc_no_auth") AND port:445',
    })).toEqual({ flags: ['eol', 'smb_unsigned', 'weak_tls'], checks: ['smbv1_enabled', 'vnc_no_auth'] });
  });

  it('does not read a field that merely ends in has', () => {
    expect(weaknessMatchCriteria({ query: 'xhas:eol' })).toBeNull();
  });
});

describe('matchedWeaknesses', () => {
  const host = { weakness_flags: ['smb_unsigned', 'eol'], check_ids: ['smbv1_enabled'] };

  it('names what the host carries AND a condition names', () => {
    expect(matchedWeaknesses(host, { flags: ['smb_unsigned', 'weak_tls'], checks: [] }, {
      flag: (f) => (f === 'smb_unsigned' ? 'SMB signing not required' : undefined),
    })).toEqual([{ key: 'has:smb_unsigned', label: 'SMB signing not required' }]);
  });

  it('a negated term names a flag the matched host lacks, so nothing shows', () => {
    const criteria = weaknessMatchCriteria({ query: 'NOT has:weak_tls' });
    expect(matchedWeaknesses(host, criteria)).toEqual([]);
  });

  it('checks fall back to a readable id', () => {
    expect(matchedWeaknesses(host, { flags: [], checks: ['smbv1_enabled'] }))
      .toEqual([{ key: 'check:smbv1_enabled', label: 'smbv1 enabled' }]);
  });
});
