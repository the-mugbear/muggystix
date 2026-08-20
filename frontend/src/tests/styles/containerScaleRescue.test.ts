/**
 * Guard for the spacing-scale / container-scale collision.
 *
 * Tailwind v4 unified the spacing and sizing scales, so this project's
 * custom `spacing.{xs,sm,md,lg,xl}` tokens (8 / 12 / 16 / 24 / 32 px) shadow
 * the built-in `max-w-*` container widths of the same name. `max-w-sm` means
 * "24rem" to a developer and "12px" to the compiler.
 *
 * `src/index.css` rescues the affected utilities by redeclaring them in
 * `@layer utilities`. The catch is that Tailwind emits a SEPARATE rule per
 * responsive variant, and each is shadowed independently — so rescuing
 * `.max-w-sm` does nothing for `.md\:max-w-sm`.
 *
 * This has now shipped visible breakage three times: three dialogs at 12–16px
 * wide (the `sm:` variants), and the Security Posture "Security condition"
 * card, where `md:max-w-sm` gave the reasons list a 12px cap — zero content
 * width once its 24px left padding was applied — so its text overflowed the
 * card at one word per line and stretched the card to ~600px tall.
 *
 * A prose warning in index.css did not stop the third one. This does: it
 * fails the build if any source file uses a breakpoint-prefixed sizing
 * utility on a colliding key that index.css does not rescue at that
 * breakpoint.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

/** Spacing tokens whose names collide with Tailwind's container scale. */
const COLLIDING_KEYS = ['xs', 'sm', 'md', 'lg', 'xl'] as const;

const SRC = join(__dirname, '..', '..');
const INDEX_CSS = join(SRC, 'index.css');

const sourceFiles = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(tsx?|css)$/.test(entry) ? [full] : [];
  });

describe('container-scale rescue', () => {
  const css = readFileSync(INDEX_CSS, 'utf8');

  it('rescues every breakpoint-prefixed sizing utility the app actually uses', () => {
    const pattern = new RegExp(
      String.raw`\b(sm|md|lg|xl|2xl):(max|min)-w-(${COLLIDING_KEYS.join('|')})\b`,
      'g',
    );

    const unrescued: string[] = [];
    for (const file of sourceFiles(SRC)) {
      // index.css is the rescue itself — its own escaped selectors would
      // otherwise read as usages.
      if (file === INDEX_CSS) continue;
      const contents = readFileSync(file, 'utf8');
      for (const [utility, breakpoint, dimension, key] of contents.matchAll(pattern)) {
        // The rescue writes the class escaped, as CSS requires: `.md\:max-w-sm`.
        const rescued = css.includes(`.${breakpoint}\\:${dimension}-w-${key} {`);
        if (!rescued) {
          unrescued.push(`${file.replace(SRC, 'src')} uses ${utility}`);
        }
      }
    }

    expect(
      unrescued,
      'These utilities resolve to a SPACING value (8–32 PIXELS), not a container ' +
        'width, because the custom spacing scale shadows them. Add the rescued ' +
        'declaration to the matching @media block in src/index.css, or use an ' +
        'unprefixed max-w-* / an explicit arbitrary value instead.',
    ).toEqual([]);
  });

  it('keeps the unprefixed rescue in place', () => {
    // The base case the responsive blocks are modelled on. If this ever
    // disappears, every plain max-w-sm in the app silently becomes 12px.
    for (const key of COLLIDING_KEYS) {
      expect(css).toMatch(new RegExp(String.raw`\.max-w-${key} \{ max-width: \d+rem; \}`));
    }
  });
});
