import { describe, it, expect } from 'vitest';
import { quote } from '../../components/hosts/dslFromFilters';

describe('quote (shared with command-bar autocomplete)', () => {
  it('leaves bare tokens unquoted', () => {
    expect(quote('nginx')).toBe('nginx');
    expect(quote('CVE-2021-44228')).toBe('CVE-2021-44228');
    expect(quote('10.0.0.0/24')).toBe('10.0.0.0/24');
  });

  it('quotes values with spaces or commas so they stay one DSL value', () => {
    // The autocomplete bug: os:Windows Server 2019 parsed as three AND clauses.
    expect(quote('Windows Server 2019')).toBe('"Windows Server 2019"');
    expect(quote('a,b')).toBe('"a,b"');
  });

  it('quotes values containing a colon (lexer breaks tokens on ":")', () => {
    // IPv6 / URL-shaped values were previously treated as bare and split mid-value.
    expect(quote('fe80::1')).toBe('"fe80::1"');
    expect(quote('http://example.com')).toBe('"http://example.com"');
  });

  it('escapes embedded quotes and backslashes', () => {
    expect(quote('say "hi"')).toBe('"say \\"hi\\""');
    expect(quote('back\\slash')).toBe('"back\\\\slash"');
  });
});
