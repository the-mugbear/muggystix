import { describe, expect, it } from 'vitest';
import { matchedWeaknesses } from '../utils/weaknessMatch';

// Which flags and checks the conditions name is the server's
// (`matched_weakness_flags` / `matched_check_ids`, review 2026-10-23b,
// FEP-5): the query-reading cases this file held are in
// backend/tests/test_review_2026_10_23b_host_row_weaknesses.py, against the
// filter's own parse.  This only words the answer.
describe('matchedWeaknesses', () => {
  it('words the flags and checks the server matched, with their readable names', () => {
    expect(matchedWeaknesses({ matched_weakness_flags: ['smb_unsigned'], matched_check_ids: ['smbv1_enabled'] }, {
      flag: (f) => (f === 'smb_unsigned' ? 'SMB signing not required' : undefined),
    })).toEqual([
      { key: 'has:smb_unsigned', label: 'SMB signing not required' },
      { key: 'check:smbv1_enabled', label: 'smbv1 enabled' },
    ]);
  });

  it('names nothing when the server matched nothing, whatever the host carries', () => {
    expect(matchedWeaknesses({ matched_weakness_flags: [], matched_check_ids: [] })).toEqual([]);
    expect(matchedWeaknesses({})).toEqual([]);
  });
});
