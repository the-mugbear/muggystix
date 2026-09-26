import { describe, it, expect } from 'vitest';
import { cn } from '../../utils/cn';

describe('cn — custom font-size vs text-color', () => {
  // Regression: a custom font-size token (text-caption, from a size="sm"
  // Button) was being treated as a text-COLOR and clobbering a real colour,
  // leaving filled buttons with illegible inherited text (white-on-green in
  // the phosphor theme).
  it('keeps a text colour alongside a custom font-size', () => {
    const out = cn('text-primary-foreground', 'text-caption');
    expect(out).toContain('text-primary-foreground');
    expect(out).toContain('text-caption');
  });

  it('mirrors the Button default+sm composition', () => {
    const out = cn('bg-primary text-primary-foreground', 'h-8 px-sm text-caption');
    expect(out).toContain('text-primary-foreground');
    expect(out).toContain('text-caption');
  });

  // Genuine conflicts still collapse to the last one.
  it('still dedupes two real text colours', () => {
    expect(cn('text-primary-foreground', 'text-destructive')).toBe('text-destructive');
  });

  it('still dedupes two font sizes', () => {
    expect(cn('text-caption', 'text-body')).toBe('text-body');
  });
});

describe('cn — named spacing scale', () => {
  // Regression: CardContent's `p-md pt-0` survived a caller's `p-sm`, so the
  // host inspector's facts box had no top padding.
  it("a caller's padding replaces the primitive's default, pt-0 included", () => {
    expect(cn('p-md pt-0', 'space-y-sm p-sm')).toBe('space-y-sm p-sm');
  });

  it('dedupes named gaps and margins', () => {
    expect(cn('gap-sm', 'gap-md')).toBe('gap-md');
    expect(cn('mt-xs', 'mt-lg')).toBe('mt-lg');
  });

  it('keeps independent axes', () => {
    expect(cn('px-sm', 'py-md')).toBe('px-sm py-md');
  });
});
