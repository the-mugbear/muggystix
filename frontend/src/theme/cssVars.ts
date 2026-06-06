/**
 * CSS-variable bridge between palettes.ts and Tailwind + shadcn
 * primitives.
 *
 * shadcn convention: each color is exposed as space-separated HSL
 * components (e.g. `--primary: 222 47% 11%`) so Tailwind can wrap it
 * with hsl() AND optionally apply alpha via the slash syntax:
 *
 *    background: hsl(var(--primary));            // solid
 *    background: hsl(var(--primary) / 0.5);      // 50% alpha
 *
 * `applyThemeToDocument` is called from ThemeContext whenever the
 * theme changes and sets every variable on <html> (plus a
 * `data-theme="..."` attribute so we can also write CSS like
 * `[data-theme="phosphor"] .foo`).
 *
 * Variable names follow shadcn/ui's vocabulary so copy-paste shadcn
 * components Just Work without renaming:
 *   --background, --foreground
 *   --card, --card-foreground
 *   --popover, --popover-foreground
 *   --primary, --primary-foreground
 *   --secondary, --secondary-foreground
 *   --muted, --muted-foreground
 *   --accent, --accent-foreground
 *   --destructive, --destructive-foreground
 *   --border, --input, --ring
 *
 * Plus our extras for severity coverage:
 *   --success, --success-foreground
 *   --warning, --warning-foreground
 *   --info, --info-foreground
 */

import { palettes, type AppThemeName, type ColorTokens } from './palettes';

// ---------------------------------------------------------------------------
// Color conversion: hex / rgba() / rgb() -> HSL components string
// ---------------------------------------------------------------------------

/** Parse "#RRGGBB" or "#RGB" -> {r,g,b} (0-255). */
function parseHex(hex: string): { r: number; g: number; b: number } {
  const h = hex.replace('#', '').trim();
  const expanded = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return {
    r: parseInt(expanded.slice(0, 2), 16),
    g: parseInt(expanded.slice(2, 4), 16),
    b: parseInt(expanded.slice(4, 6), 16),
  };
}

/** Parse "rgba(r,g,b,a)" or "rgb(r,g,b)" -> {r,g,b,a}. */
function parseRgb(str: string): { r: number; g: number; b: number; a: number } {
  const m = str.match(/rgba?\(([^)]+)\)/);
  if (!m) throw new Error(`unparseable rgb string: ${str}`);
  const parts = m[1].split(',').map((s) => s.trim());
  return {
    r: Number(parts[0]),
    g: Number(parts[1]),
    b: Number(parts[2]),
    a: parts.length >= 4 ? Number(parts[3]) : 1,
  };
}

/** Convert any of #hex / rgb() / rgba() to {h,s,l,a} (h in 0-360, s/l in 0-100, a in 0-1). */
function toHsla(input: string): { h: number; s: number; l: number; a: number } {
  let r: number, g: number, b: number, a = 1;
  if (input.startsWith('#')) {
    ({ r, g, b } = parseHex(input));
  } else if (input.startsWith('rgb')) {
    ({ r, g, b, a } = parseRgb(input));
  } else {
    throw new Error(`unsupported color format: ${input}`);
  }
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case rn:
        h = (gn - bn) / d + (gn < bn ? 6 : 0);
        break;
      case gn:
        h = (bn - rn) / d + 2;
        break;
      case bn:
        h = (rn - gn) / d + 4;
        break;
    }
    h *= 60;
  }
  return {
    h: Math.round(h),
    s: Math.round(s * 100),
    l: Math.round(l * 100),
    a,
  };
}

/** Returns the space-separated component string shadcn expects ("222 47% 11%"). */
function toHslComponents(input: string): string {
  const { h, s, l } = toHsla(input);
  return `${h} ${s}% ${l}%`;
}

/** Returns "H S% L% / A" when alpha < 1, else "H S% L%" — for tokens like dividers. */
function toHslComponentsWithAlpha(input: string): string {
  const { h, s, l, a } = toHsla(input);
  return a >= 1 ? `${h} ${s}% ${l}%` : `${h} ${s}% ${l}% / ${a.toFixed(3)}`;
}

/**
 * Derive a tonally-shifted variant of an input color — used to give
 * surfaces like the sidebar a subtle distinction from the main page
 * background.  Positive `deltaL` lightens; negative darkens.  Hue and
 * saturation are preserved.
 *
 * Beta.3: the visual-identity overhaul uses this to keep the sidebar
 * and topbar from feeling like the same flat surface as the content
 * area — a ~5% lightness shift in the right direction (away from the
 * background) gives the chrome a tactile feel without needing heavy
 * borders.
 */
function shiftLightness(input: string, deltaL: number): string {
  const { h, s, l } = toHsla(input);
  const next = Math.max(0, Math.min(100, l + deltaL));
  return `${h} ${s}% ${next}%`;
}

// ---------------------------------------------------------------------------
// Mapping: ColorTokens -> {var name: components string}
// ---------------------------------------------------------------------------

function buildVarMap(t: ColorTokens): Record<string, string> {
  // Foreground colors for filled surfaces.  Most are taken from the
  // palette directly; a few are derived (white for destructive when on
  // a dark error color, etc).
  return {
    '--background': toHslComponents(t.backgroundDefault),
    '--foreground': toHslComponents(t.textPrimary),

    '--card': toHslComponents(t.backgroundPaper),
    '--card-foreground': toHslComponents(t.textPrimary),

    '--popover': toHslComponents(t.backgroundPaper),
    '--popover-foreground': toHslComponents(t.textPrimary),

    '--primary': toHslComponents(t.primary),
    '--primary-foreground': toHslComponents(t.buttonPrimaryText),

    '--secondary': toHslComponents(t.secondary),
    '--secondary-foreground': toHslComponents(t.textPrimary),

    // shadcn's "muted" maps to a subtle filled surface — we use the
    // hover token (very soft tint of the primary) which is what feels
    // right under chips and badges.
    '--muted': toHslComponentsWithAlpha(t.hover),
    '--muted-foreground': toHslComponents(t.textSecondary),

    '--accent': toHslComponentsWithAlpha(t.selected),
    '--accent-foreground': toHslComponents(t.textPrimary),

    '--destructive': toHslComponents(t.error),
    // White text on red works for every theme we ship.
    '--destructive-foreground': '0 0% 100%',

    '--border': toHslComponentsWithAlpha(t.divider),
    '--input': toHslComponentsWithAlpha(t.divider),
    '--ring': toHslComponents(t.primary),

    '--success': toHslComponents(t.success),
    '--success-foreground': '0 0% 100%',
    '--warning': toHslComponents(t.warning),
    // Theme-aware foreground: dark themes ship a light-orange warning
    // (#FFBE5C-ish) that needs black text; light themes now ship a
    // darker warning (#9A5C00) that needs white text. Picking the
    // foreground per palette mode keeps badges + buttons legible
    // either way and decouples the foreground from `--warning`'s
    // luminance.
    '--warning-foreground': t.mode === 'dark' ? '0 0% 0%' : '0 0% 100%',
    '--info': toHslComponents(t.info),
    '--info-foreground': '0 0% 100%',

    // Sidebar / chrome.  Beta.3: tonally shifted away from the page
    // background — light themes get a subtly darker sidebar (gives
    // depth without a heavy border); dark themes get a subtly lighter
    // sidebar (the rail "lifts" off the dark canvas).  ~4 L*.
    '--sidebar':
      t.mode === 'dark'
        ? shiftLightness(t.backgroundDefault, 4)
        : shiftLightness(t.backgroundDefault, -4),
    '--sidebar-foreground': toHslComponents(t.textPrimary),
    '--sidebar-accent': toHslComponentsWithAlpha(t.selectedNavBackground),
    '--sidebar-accent-foreground': toHslComponents(t.textPrimary),

    // Font family token — Tailwind reads this via the `font-sans`
    // utility's CSS var override.
    '--font-sans': t.fontFamily ?? '"IBM Plex Sans", "Inter", "Segoe UI", system-ui, sans-serif',
  };
}

// ---------------------------------------------------------------------------
// Apply to document
// ---------------------------------------------------------------------------

export function applyThemeToDocument(name: AppThemeName): void {
  if (typeof document === 'undefined') return;
  const palette = palettes[name];
  let vars: Record<string, string>;
  try {
    vars = buildVarMap(palette);
  } catch (err) {
    // The color parsers throw on a malformed palette token.  Palettes are
    // developer-authored, so this should never happen in production — but a
    // single typo must not white-out the whole app.  Fall back to the
    // built-in light theme; if that ALSO fails (shared-code bug), bail
    // without applying rather than throwing through the render.
    // eslint-disable-next-line no-console
    console.error(`Failed to build theme "${name}", falling back to light:`, err);
    try {
      vars = buildVarMap(palettes.light);
    } catch {
      return;
    }
  }
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  // data-theme drives CSS-only theme branches (e.g. theme-specific
  // hover effects) and Sonner / shadcn dark-mode opt-ins.
  root.dataset.theme = palette.mode;
  // shadcn convention: also set the `dark` class on <html> so
  // `dark:` Tailwind variants work in addition to data-theme.
  root.classList.toggle('dark', palette.mode === 'dark');
  // Per-theme attribute for theme-specific overrides if ever needed.
  root.dataset.palette = name;
}
