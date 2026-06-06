/**
 * Pure color palettes — no MUI / no React.  Imported by:
 *   - `contexts/ThemeContext.tsx` to build the MUI theme (during MUI migration)
 *   - `theme/cssVars.ts` to project the same colors onto CSS custom
 *     properties for Tailwind + shadcn primitives
 *
 * Single source of truth for color identity per theme.  The structural
 * tokens (radius, space, type, motion, elevation) live in `tokens.ts`
 * and are shared across every theme.
 */

export type AppThemeName = 'light' | 'dark' | 'phosphor' | 'magma' | 'absolute-zero';

export interface PaletteOption {
  value: AppThemeName;
  label: string;
}

export const PALETTE_OPTIONS: PaletteOption[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'phosphor', label: 'Phosphor Mode' },
  { value: 'magma', label: 'Magma' },
  { value: 'absolute-zero', label: 'Absolute Zero' },
];

export interface ColorTokens {
  mode: 'light' | 'dark';
  primary: string;
  primaryLight?: string;
  primaryDark?: string;
  secondary: string;
  backgroundDefault: string;
  backgroundPaper: string;
  textPrimary: string;
  textSecondary: string;
  success: string;
  warning: string;
  error: string;
  info: string;
  divider: string;
  hover: string;
  selected: string;
  fontFamily?: string;
  selectedNavBackground: string;
  buttonPrimaryText: string;
  tooltipBackground: string;
  tooltipText: string;
}

// Default font families.  Themes can override; phosphor uses
// monospace, others use a humanist sans.
export const SANS_STACK = '"IBM Plex Sans", "Inter", "Segoe UI", system-ui, sans-serif';
export const MONO_STACK = '"IBM Plex Mono", "JetBrains Mono", "Fira Code", "Consolas", monospace';

export const palettes: Record<AppThemeName, ColorTokens> = {
  light: {
    mode: 'light',
    primary: '#135D66',
    primaryLight: '#3E7D83',
    primaryDark: '#0D434B',
    secondary: '#6A8D73',
    backgroundDefault: '#F4F6F4',
    backgroundPaper: '#FFFFFF',
    textPrimary: '#0E1F22',
    textSecondary: '#52656A',
    success: '#2E7D32',
    // Darkened from #ED8B00 → #9A5C00 (HSL 36°/100/30) so `text-warning`
    // clears WCAG AA (~5:1) on both bg-background and bg-card. Previously
    // 3.2:1 — failed for body text in ~21 sites. Foreground on filled
    // bg-warning swaps to white in cssVars.ts to keep badges legible.
    warning: '#9A5C00',
    error: '#C62828',
    info: '#1E88E5',
    divider: 'rgba(19, 93, 102, 0.12)',
    hover: 'rgba(19, 93, 102, 0.06)',
    selected: 'rgba(19, 93, 102, 0.12)',
    selectedNavBackground: 'rgba(19, 93, 102, 0.10)',
    buttonPrimaryText: '#FFFFFF',
    tooltipBackground: '#17373C',
    tooltipText: '#F4FBFC',
  },

  dark: {
    mode: 'dark',
    primary: '#8AA4FF',
    primaryLight: '#C6D0FF',
    primaryDark: '#5B74CC',
    secondary: '#61D4C5',
    backgroundDefault: '#0B1018',
    backgroundPaper: '#131A28',
    textPrimary: '#EEF3FF',
    textSecondary: '#A5B3CC',
    success: '#62D790',
    warning: '#FFBE5C',
    error: '#FF6F7D',
    info: '#69C0FF',
    divider: 'rgba(138, 164, 255, 0.14)',
    hover: 'rgba(138, 164, 255, 0.08)',
    selected: 'rgba(138, 164, 255, 0.16)',
    selectedNavBackground: 'rgba(138, 164, 255, 0.14)',
    buttonPrimaryText: '#08101C',
    tooltipBackground: '#1B2438',
    tooltipText: '#EEF3FF',
  },

  phosphor: {
    mode: 'dark',
    primary: '#7CFF7A',
    primaryLight: '#B6FF9E',
    primaryDark: '#2AAE48',
    secondary: '#35D0FF',
    backgroundDefault: '#050A08',
    backgroundPaper: '#0C1512',
    textPrimary: '#D8FFE1',
    textSecondary: '#86C89A',
    success: '#7CFF7A',
    warning: '#FFB347',
    error: '#FF5F56',
    info: '#35D0FF',
    divider: 'rgba(124, 255, 122, 0.14)',
    hover: 'rgba(124, 255, 122, 0.08)',
    selected: 'rgba(124, 255, 122, 0.16)',
    selectedNavBackground: 'rgba(124, 255, 122, 0.14)',
    buttonPrimaryText: '#041006',
    tooltipBackground: '#0A1510',
    tooltipText: '#D8FFE1',
    fontFamily: MONO_STACK,
  },

  magma: {
    mode: 'dark',
    primary: '#FF7A18',
    primaryLight: '#FFB26B',
    primaryDark: '#C94A00',
    secondary: '#FF4D6D',
    backgroundDefault: '#130B09',
    backgroundPaper: '#21110E',
    textPrimary: '#FFE8DB',
    textSecondary: '#D4A48D',
    success: '#FFB15E',
    warning: '#FFD166',
    error: '#FF6B57',
    info: '#FF8E53',
    divider: 'rgba(255, 122, 24, 0.16)',
    hover: 'rgba(255, 122, 24, 0.10)',
    selected: 'rgba(255, 122, 24, 0.16)',
    selectedNavBackground: 'rgba(255, 122, 24, 0.14)',
    buttonPrimaryText: '#260B05',
    tooltipBackground: '#2A130F',
    tooltipText: '#FFE8DB',
  },

  'absolute-zero': {
    mode: 'dark',
    primary: '#7BE7FF',
    primaryLight: '#C0F6FF',
    primaryDark: '#36A9C6',
    secondary: '#9DAEFF',
    backgroundDefault: '#06131B',
    backgroundPaper: '#0C1F2A',
    textPrimary: '#EAFBFF',
    textSecondary: '#9FC4D1',
    success: '#6CE2C7',
    warning: '#FFD38A',
    error: '#FF7A90',
    info: '#7BE7FF',
    divider: 'rgba(123, 231, 255, 0.16)',
    hover: 'rgba(123, 231, 255, 0.08)',
    selected: 'rgba(123, 231, 255, 0.16)',
    selectedNavBackground: 'rgba(123, 231, 255, 0.14)',
    buttonPrimaryText: '#041017',
    tooltipBackground: '#0D2430',
    tooltipText: '#EAFBFF',
  },
};
