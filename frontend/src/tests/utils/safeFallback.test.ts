/**
 * safeFallback must never throw. A JSON `details` object from the audit-log
 * API reached it as a non-string and `value.trim()` threw
 * "a.trim is not a function", crashing the whole System Settings page through
 * the route error boundary. The typeof guard makes non-strings fall through to
 * the fallback instead of blowing up render.
 */
import { describe, it, expect } from 'vitest';

import { safeFallback } from '../../utils/uiStyles';

describe('safeFallback', () => {
  it('returns the value for a non-empty string', () => {
    expect(safeFallback('hello')).toBe('hello');
  });

  it('falls back for empty / whitespace-only / nullish', () => {
    expect(safeFallback('')).toBe('—');
    expect(safeFallback('   ')).toBe('—');
    expect(safeFallback(null)).toBe('—');
    expect(safeFallback(undefined)).toBe('—');
    expect(safeFallback('', 'none')).toBe('none');
  });

  it('does NOT throw on a non-string (object/number) — the audit-log crash', () => {
    // These reach safeFallback from `any`-typed API data; before the fix each
    // threw `.trim is not a function`.
    expect(() => safeFallback({ method: 'totp' } as unknown as string)).not.toThrow();
    expect(safeFallback({ method: 'totp' } as unknown as string)).toBe('—');
    expect(safeFallback(42 as unknown as string)).toBe('—');
    expect(safeFallback([] as unknown as string)).toBe('—');
  });
});
