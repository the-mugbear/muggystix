/**
 * Tailwind v4 configuration for the v4 UI revamp.
 *
 * Scope: only files under src/components/v4/ and src/pages/v4/ are
 * scanned.  MUI pages outside that boundary are untouched.
 *
 * Token source: imports directly from src/theme/tokens.ts so radius,
 * spacing, type roles, motion, and elevation stay in one place.  Edit
 * tokens.ts; Tailwind picks it up on next build.
 *
 * Color tokens are intentionally NOT bridged here.  Theme-flippable
 * colors (light / dark / phosphor / etc.) come from MUI's palette via
 * useTheme().  v4 components access colors with the `theme.palette.*`
 * pattern, not Tailwind utility classes.  When the v4 surface grows
 * enough to justify a CSS-var bridge (Phase 1+), color tokens will be
 * exposed as CSS custom properties set by MUI's GlobalStyles, and
 * referenced here as `var(--surface-paper)` etc.
 */

import type { Config } from 'tailwindcss';
import { radius, space, type, motion, elevation } from './src/theme/tokens';

const config: Config = {
  // During the MUI removal migration, the boundary is being dissolved:
  // shadcn primitives live in src/components/ui/, migrated pages move
  // to src/pages/* (in place, no v4/ subdir).  Scan all source files
  // so utility classes work everywhere we add them.
  content: [
    './src/**/*.{ts,tsx}',
  ],
  // dark: 'class' opt-in — applyThemeToDocument toggles the `dark`
  // class on <html> in addition to setting CSS variables.
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      // ----- Colors ----- driven by CSS variables set by
      // theme/cssVars.ts.  shadcn convention: each color is stored as
      // space-separated HSL components so Tailwind can apply alpha
      // via the slash syntax (`bg-primary/50`).
      colors: {
        background: 'hsl(var(--background) / <alpha-value>)',
        foreground: 'hsl(var(--foreground) / <alpha-value>)',
        card: {
          DEFAULT: 'hsl(var(--card) / <alpha-value>)',
          foreground: 'hsl(var(--card-foreground) / <alpha-value>)',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover) / <alpha-value>)',
          foreground: 'hsl(var(--popover-foreground) / <alpha-value>)',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary) / <alpha-value>)',
          foreground: 'hsl(var(--primary-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary) / <alpha-value>)',
          foreground: 'hsl(var(--secondary-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted) / <alpha-value>)',
          foreground: 'hsl(var(--muted-foreground) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent) / <alpha-value>)',
          foreground: 'hsl(var(--accent-foreground) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive) / <alpha-value>)',
          foreground: 'hsl(var(--destructive-foreground) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'hsl(var(--success) / <alpha-value>)',
          foreground: 'hsl(var(--success-foreground) / <alpha-value>)',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning) / <alpha-value>)',
          foreground: 'hsl(var(--warning-foreground) / <alpha-value>)',
        },
        info: {
          DEFAULT: 'hsl(var(--info) / <alpha-value>)',
          foreground: 'hsl(var(--info-foreground) / <alpha-value>)',
        },
        border: 'hsl(var(--border) / <alpha-value>)',
        input: 'hsl(var(--input) / <alpha-value>)',
        ring: 'hsl(var(--ring) / <alpha-value>)',
        sidebar: {
          DEFAULT: 'hsl(var(--sidebar) / <alpha-value>)',
          foreground: 'hsl(var(--sidebar-foreground) / <alpha-value>)',
          accent: 'hsl(var(--sidebar-accent) / <alpha-value>)',
          'accent-foreground': 'hsl(var(--sidebar-accent-foreground) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        control: `${radius.control}px`,
        chip: `${radius.chip}px`,
        panel: `${radius.panel}px`,
        shell: `${radius.shell}px`,
      },
      spacing: {
        xxs: `${space.xxs}px`,
        xs: `${space.xs}px`,
        sm: `${space.sm}px`,
        md: `${space.md}px`,
        lg: `${space.lg}px`,
        xl: `${space.xl}px`,
        xxl: `${space.xxl}px`,
        xxxl: `${space.xxxl}px`,
      },
      // Note: Tailwind v4 unifies the spacing + sizing scales (max-w-*,
      // min-w-*, w-*, h-* all read from the same source), so our
      // custom spacing keys above silently shadow `max-w-sm` (24rem) /
      // `max-w-md` (28rem) / `max-w-lg` (32rem) / `max-w-xl` (36rem)
      // with our 12 / 16 / 24 / 32 px spacing values.  Neither
      // `theme.extend.maxWidth` here NOR `@theme --container-md` in
      // CSS overrides this — the v3 compat layer pulls the spacing
      // value last.  The actual override lives in `src/index.css` as
      // explicit `.max-w-{sm,md,lg,xl}` rules in `@layer utilities`,
      // which wins by virtue of being in the highest-priority layer.
      fontSize: {
        'page-title': [
          type.pageTitle.fontSize,
          {
            lineHeight: String(type.pageTitle.lineHeight),
            fontWeight: String(type.pageTitle.fontWeight),
            letterSpacing: type.pageTitle.letterSpacing,
          },
        ],
        'section-title': [
          type.sectionTitle.fontSize,
          {
            lineHeight: String(type.sectionTitle.lineHeight),
            fontWeight: String(type.sectionTitle.fontWeight),
          },
        ],
        subheading: [
          type.subheading.fontSize,
          {
            lineHeight: String(type.subheading.lineHeight),
            fontWeight: String(type.subheading.fontWeight),
          },
        ],
        body: [
          type.body.fontSize,
          {
            lineHeight: String(type.body.lineHeight),
            fontWeight: String(type.body.fontWeight),
          },
        ],
        metadata: [
          type.metadata.fontSize,
          {
            lineHeight: String(type.metadata.lineHeight),
            fontWeight: String(type.metadata.fontWeight),
          },
        ],
        caption: [
          type.caption.fontSize,
          {
            lineHeight: String(type.caption.lineHeight),
            fontWeight: String(type.caption.fontWeight),
          },
        ],
        micro: [
          type.microLabel.fontSize,
          {
            lineHeight: String(type.microLabel.lineHeight),
            fontWeight: String(type.microLabel.fontWeight),
            letterSpacing: type.microLabel.letterSpacing,
          },
        ],
      },
      transitionDuration: {
        fast: `${motion.duration.fast}ms`,
        base: `${motion.duration.base}ms`,
        slow: `${motion.duration.slow}ms`,
      },
      transitionTimingFunction: {
        standard: motion.easing.standard,
        accelerate: motion.easing.accelerate,
        decelerate: motion.easing.decelerate,
      },
      boxShadow: {
        raised: elevation.raised,
        hover: elevation.hover,
        overlay: elevation.overlay,
      },
    },
  },
};

export default config;
