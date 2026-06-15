/**
 * buildSameVulnQuery — the "find other hosts with this vulnerability" pivot
 * builds a host-query DSL predicate. CVE is preferred (the canonical cross-host
 * key); title is the fallback. The DSL quoted-string lexer only treats \" and
 * \\ as escapes, so a title containing a double-quote must be escaped or it
 * breaks the generated query.
 */
import { describe, it, expect } from 'vitest';

import { buildSameVulnQuery } from '../../utils/vulnQuery';
import type { HostVulnerability } from '../../services/api';

const vuln = (over: Partial<HostVulnerability>): HostVulnerability =>
  ({ id: 1, cve_id: null, title: null, ...over } as HostVulnerability);

describe('buildSameVulnQuery', () => {
  it('prefers the CVE when present', () => {
    expect(buildSameVulnQuery(vuln({ cve_id: 'CVE-2021-44228', title: 'Log4Shell' })))
      .toBe('cve:"CVE-2021-44228"');
  });

  it('falls back to the finding title when there is no CVE', () => {
    expect(buildSameVulnQuery(vuln({ cve_id: null, title: 'SMB Signing Disabled' })))
      .toBe('vuln:"SMB Signing Disabled"');
  });

  it('escapes double-quotes and backslashes in the title for the DSL lexer', () => {
    expect(buildSameVulnQuery(vuln({ title: 'Apache "mod_status" C:\\path' })))
      .toBe('vuln:"Apache \\"mod_status\\" C:\\\\path"');
  });

  it('trims whitespace', () => {
    expect(buildSameVulnQuery(vuln({ cve_id: '  CVE-2023-1234  ' }))).toBe('cve:"CVE-2023-1234"');
  });

  it('returns null when neither a CVE nor a title is available', () => {
    expect(buildSameVulnQuery(vuln({ cve_id: null, title: null }))).toBeNull();
    expect(buildSameVulnQuery(vuln({ cve_id: '', title: '   ' }))).toBeNull();
  });
});
