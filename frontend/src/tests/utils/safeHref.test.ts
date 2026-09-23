import { describe, it, expect } from 'vitest';

import { safeHttpHref } from '../../utils/safeHref';

describe('safeHttpHref', () => {
  it('keeps http and https URLs', () => {
    expect(safeHttpHref('https://10.0.0.1:8443/admin')).toBe('https://10.0.0.1:8443/admin');
    expect(safeHttpHref(' http://[2001:db8::1]/x ')).toBe('http://[2001:db8::1]/x');
  });

  it('drops every other scheme and anything unparseable', () => {
    for (const bad of [
      'javascript:alert(1)', 'JavaScript://10.0.0.1/%0aalert(1)', 'data:text/html,<b>x</b>',
      'vbscript:x', 'file:///etc/passwd', '/relative/path', 'not a url', '', null, undefined,
    ]) {
      expect(safeHttpHref(bad as string | null | undefined)).toBeUndefined();
    }
  });
});
