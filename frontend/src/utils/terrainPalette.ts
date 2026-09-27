/**
 * The terrain's colours, derived from the active theme's tokens (5.306.0).
 *
 * Progress is ONE hue — the accent (`--info`) — stepped from the surface to
 * full strength: tested is the accent, planned and "someone has it" lighter
 * steps of it, untouched a recessive grey (UI_STYLE_GUIDE §7: one accent plus
 * grey when one part is the point). The beacon over untouched critical
 * exposure is the theme's critical severity colour — severity only. Steps
 * validated with the dataviz palette validator on every theme's surface.
 */

export interface TerrainPalette {
  tested: string;
  planned: string;
  worked: string;
  untouched: string;
  beacon: string;
  ground: string;
  grid: string;
  outline: string;
  hover: string;
}

/** Theme tokens as the CSS variables carry them: "H S% L%". */
export interface TerrainTokens {
  info: string;
  background: string;
  foreground: string;
  mutedForeground: string;
  critical: string;
}

type Rgb = [number, number, number];

export function hslComponentsToRgb(value: string): Rgb {
  const m = value.trim().match(/^(-?[\d.]+)\s+([\d.]+)%\s+([\d.]+)%/);
  if (!m) return [128, 128, 128];
  const h = (((Number(m[1]) % 360) + 360) % 360) / 360;
  const s = Number(m[2]) / 100;
  const l = Number(m[3]) / 100;
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t: number) => {
    let u = t;
    if (u < 0) u += 1;
    if (u > 1) u -= 1;
    if (u < 1 / 6) return p + (q - p) * 6 * u;
    if (u < 1 / 2) return q;
    if (u < 2 / 3) return p + (q - p) * (2 / 3 - u) * 6;
    return p;
  };
  return [channel(h + 1 / 3) * 255, channel(h) * 255, channel(h - 1 / 3) * 255];
}

const isLight = ([r, g, b]: Rgb) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.5;
const mix = (a: Rgb, b: Rgb, t: number): Rgb => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * t) as Rgb;
const hex = (c: Rgb) => `#${c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')}`;

/** Accent strength per stage: how far from the surface towards the accent. */
export const STAGE_STRENGTH = { planned: 0.62, worked: 0.26, untouched: 0.75 } as const;

export function terrainPalette(t: TerrainTokens): TerrainPalette {
  const surface = hslComponentsToRgb(t.background);
  const accent = hslComponentsToRgb(t.info);
  const muted = hslComponentsToRgb(t.mutedForeground);
  const ink = hslComponentsToRgb(t.foreground);
  return {
    // On a light surface the accent is too pale to anchor three steps
    // (ΔE 13.6 and 14.5 between neighbours): tested goes past it towards the
    // ink and planned sits nearer the accent. Every theme's neighbours are
    // then ΔE ≥ 17 apart, normal vision and CVD alike (validator, 5.306.0).
    tested: hex(isLight(surface) ? mix(accent, ink, 0.3) : accent),
    planned: hex(mix(surface, accent, isLight(surface) ? 0.78 : STAGE_STRENGTH.planned)),
    worked: hex(mix(surface, accent, STAGE_STRENGTH.worked)),
    untouched: hex(mix(surface, muted, STAGE_STRENGTH.untouched)),
    beacon: hex(hslComponentsToRgb(t.critical)),
    ground: hex(mix(surface, ink, 0.045)),
    grid: hex(mix(surface, ink, 0.13)),
    outline: hex(ink),
    hover: hex(mix(surface, ink, 0.55)),
  };
}

/** The live theme's tokens (read when the scene mounts and when the theme changes). */
export function readTerrainTokens(el: HTMLElement = document.documentElement): TerrainTokens {
  const css = getComputedStyle(el);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    info: v('--info', '210 80% 50%'),
    background: v('--background', '0 0% 100%'),
    foreground: v('--foreground', '0 0% 10%'),
    mutedForeground: v('--muted-foreground', '0 0% 45%'),
    critical: v('--sev-critical', '0 70% 50%'),
  };
}
