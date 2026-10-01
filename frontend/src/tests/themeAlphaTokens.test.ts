import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 5.327.0 — a colour token that carries its OWN alpha ("H S% L% / A") must
 * not be wrapped as `hsl(var(--x) / <alpha-value>)` in tailwind.config.ts:
 * that compiles to `hsl(H S% L% / A / 1)`, which is invalid, and the browser
 * drops the declaration.  `--accent`, `--muted`, `--border` and
 * `--sidebar-accent` were wrapped that way, so every hover, selected-row and
 * muted fill painted nothing (`--input` had the same bug until 5.72.0).
 */
const root = resolve(__dirname, '..', '..');
const config = readFileSync(resolve(root, 'tailwind.config.ts'), 'utf8');
const cssVars = readFileSync(resolve(root, 'src/theme/cssVars.ts'), 'utf8');
const indexCss = readFileSync(resolve(root, 'src/index.css'), 'utf8');

function tokensWithOwnAlpha(): string[] {
  const names = new Set<string>();
  // Generated at runtime with an alpha when the theme colour has one.
  for (const m of cssVars.matchAll(/'--([a-z-]+)':\s*toHslComponentsWithAlpha\(/g)) names.add(m[1]);
  // Declared in the stylesheet's defaults as "H S% L% / A".
  for (const m of indexCss.matchAll(/--([a-z-]+):\s*[\d.]+\s+[\d.]+%\s+[\d.]+%\s*\/\s*[\d.]+\s*;/g)) names.add(m[1]);
  return [...names].sort();
}

describe('theme tokens that carry their own alpha', () => {
  const tokens = tokensWithOwnAlpha();

  it('finds the tokens this guard exists for', () => {
    expect(tokens).toEqual(expect.arrayContaining(['accent', 'border', 'muted', 'sidebar-accent']));
  });

  it.each(tokens)('--%s is not given a second alpha by tailwind.config.ts', (name) => {
    expect(config).not.toContain(`var(--${name}) / <alpha-value>`);
  });
});
