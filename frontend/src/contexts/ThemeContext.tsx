/**
 * Theme context — single source of truth for the active theme name,
 * persistence to localStorage, and the CSS-variable bridge that feeds
 * Tailwind + the shadcn-style primitives.
 *
 * The v4 MUI removal landed in alpha.22 — this file no longer creates
 * an MUI `createTheme()` or wraps the app in `<ThemeProvider>`.  The
 * only theming pathway now is `applyThemeToDocument(themeName)` from
 * `theme/cssVars.ts`, which writes HSL components to CSS custom
 * properties that Tailwind reads via `hsl(var(--token) / <alpha>)`.
 *
 * Public surface unchanged: `useAppTheme()` returns the same
 * `themeName / setThemeName / availableThemes / isDarkTheme` shape it
 * always has.
 */
import React, { createContext, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { PALETTE_OPTIONS, palettes, type AppThemeName } from '../theme/palettes';
import { applyThemeToDocument } from '../theme/cssVars';

export type { AppThemeName } from '../theme/palettes';

interface ThemeOption {
  value: AppThemeName;
  label: string;
}

interface ThemeContextType {
  themeName: AppThemeName;
  setThemeName: (themeName: AppThemeName) => void;
  availableThemes: ThemeOption[];
  isDarkTheme: boolean;
}

const STORAGE_KEY = 'appTheme';
const LEGACY_STORAGE_KEY = 'darkMode';

const THEME_OPTIONS: ThemeOption[] = PALETTE_OPTIONS;

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const isAppThemeName = (value: string | null): value is AppThemeName =>
  THEME_OPTIONS.some((theme) => theme.value === value);

const resolveInitialTheme = (): AppThemeName => {
  const savedTheme = localStorage.getItem(STORAGE_KEY);
  if (isAppThemeName(savedTheme)) {
    return savedTheme;
  }
  // Legacy: pre-v4 the app stored a boolean `darkMode` in localStorage.
  // Map it forward so returning users keep something reasonable instead
  // of being yanked back to the default light theme.
  const legacyDarkMode = localStorage.getItem(LEGACY_STORAGE_KEY);
  if (legacyDarkMode) {
    try {
      return JSON.parse(legacyDarkMode) ? 'phosphor' : 'light';
    } catch {
      return 'light';
    }
  }
  return 'light';
};

export const useAppTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useAppTheme must be used within a CustomThemeProvider');
  }
  return context;
};

// Backwards-compatible alias for code that imported `useTheme` from
// this file (a few v3 pages did this before realising it shadowed
// MUI's own useTheme).  Safe to drop once those last call sites move
// to `useAppTheme`.
export const useTheme = useAppTheme;

interface CustomThemeProviderProps {
  children: ReactNode;
}

export const CustomThemeProvider: React.FC<CustomThemeProviderProps> = ({ children }) => {
  const [themeName, setThemeName] = useState<AppThemeName>(resolveInitialTheme);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, themeName);
    // Project palette onto CSS custom properties so Tailwind +
    // shadcn primitives see the same colors.  This is the only theme
    // pathway after MUI removal — `applyThemeToDocument` writes
    // `--background`, `--foreground`, `--primary`, etc., and every
    // primitive reads them via `hsl(var(--token) / <alpha>)`.
    applyThemeToDocument(themeName);
  }, [themeName]);

  const isDarkTheme = useMemo(() => palettes[themeName].mode === 'dark', [themeName]);

  // Memoize the value so theme consumers (Layout, every page) don't
  // re-render whenever a parent does.  setThemeName is the stable
  // setter from useState, THEME_OPTIONS is a module-level constant.
  const contextValue = useMemo(
    () => ({
      themeName,
      setThemeName,
      availableThemes: THEME_OPTIONS,
      isDarkTheme,
    }),
    [themeName, isDarkTheme],
  );

  return (
    <ThemeContext.Provider value={contextValue}>
      {children}
    </ThemeContext.Provider>
  );
};
