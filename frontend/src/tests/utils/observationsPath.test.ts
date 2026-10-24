/**
 * The Scanner observations page's addresses.  (An old `/findings?view=
 * observations` link redirected here until every installation had upgraded;
 * the redirect and its helper are gone — owner, 2026-10-23.)
 */
import { describe, expect, it } from 'vitest';

import {
  OBSERVATIONS_TO_JUDGE_PATH, issueFromWeaknessHash, observationPath, weaknessHash,
} from '../../utils/observationsPath';

describe('observationsPath', () => {
  it('names the list of what still waits', () => {
    expect(OBSERVATIONS_TO_JUDGE_PATH).toBe('/observations?obs_judged=waiting');
  });

  it('opens one issue in the inspector', () => {
    expect(observationPath('check:smb-signing')).toBe('/observations?issue=check%3Asmb-signing');
  });

  it("a host page's hash names the issue back, and nothing else", () => {
    expect(issueFromWeaknessHash(weaknessHash('title:Weak TLS / 1.0'))).toBe('title:Weak TLS / 1.0');
    expect(issueFromWeaknessHash('#section-ports')).toBeNull();
    expect(issueFromWeaknessHash('weakness-x')).toBeNull();
    expect(issueFromWeaknessHash('#%E0%A4%A')).toBeNull();
  });
});
