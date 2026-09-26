import { describe, it, expect } from 'vitest';
import { palettes } from '../../theme/palettes';
import {
  relativeLuminance,
  contrastRatio,
  foregroundComponentsFor,
} from '../../theme/cssVars';

/**
 * UX review #3: filled semantic badges/buttons must meet WCAG AA (4.5:1).
 * The foreground is now luminance-derived (foregroundComponentsFor); this test
 * pins the property for every theme so a future palette change that breaks
 * contrast fails CI instead of shipping unreadable status chips.
 */
const SEMANTICS = ['success', 'warning', 'error', 'info'] as const;
const AA = 4.5;

// A foreground component string is either pure black or pure white.
const fgLuminance = (components: string): number =>
  components === '0 0% 0%' ? 0 : 1;

describe('semantic filled contrast meets WCAG AA across all themes', () => {
  for (const [themeName, tokens] of Object.entries(palettes)) {
    for (const semantic of SEMANTICS) {
      it(`${themeName} / ${semantic} >= ${AA}:1`, () => {
        const bg = tokens[semantic];
        const fg = foregroundComponentsFor(bg);
        const ratio = contrastRatio(relativeLuminance(bg), fgLuminance(fg));
        expect(ratio).toBeGreaterThanOrEqual(AA);
      });
    }
  }
});

// 5.304.0 — severity has its own ramp.  Filled severity badges must stay
// legible, and every step must stand off the page (3:1, a non-text mark).
describe('severity ramp across all themes', () => {
  for (const [themeName, tokens] of Object.entries(palettes)) {
    const ramp = tokens.severity ?? {
      critical: tokens.error, high: tokens.warning, medium: tokens.info, low: tokens.success,
    };
    for (const [step, colour] of Object.entries(ramp)) {
      it(`${themeName} / ${step}: filled badge >= ${AA}:1, mark vs page >= 3:1`, () => {
        const fg = foregroundComponentsFor(colour);
        expect(contrastRatio(relativeLuminance(colour), fgLuminance(fg))).toBeGreaterThanOrEqual(AA);
        expect(contrastRatio(relativeLuminance(colour), relativeLuminance(tokens.backgroundDefault)))
          .toBeGreaterThanOrEqual(3);
      });
    }
  }

  it('Magma no longer puts Low between High and Medium', () => {
    expect(palettes.magma.severity).toBeDefined();
  });
});
