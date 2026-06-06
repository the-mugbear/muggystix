/**
 * Foundation design tokens — shared across every theme.
 *
 * Why this file exists: before this refactor, every theme defined its
 * own borderRadius, its own letterSpacing, its own type weights, its
 * own surface recipes.  Result: the only thing that varied between
 * themes was supposed to be color, but in practice they drifted apart
 * on spacing, hierarchy, and density too.  When the previous UI review
 * said the app reads as "MUI with custom colors" in places, this is
 * the structural reason.
 *
 * The fix is to keep the *colors* per-theme but make every other
 * structural choice — radius, spacing, type scale, motion, surface
 * recipes — share a single foundation.  Themes consume the foundation
 * + their own color tokens; nothing else may diverge.
 *
 * Nothing in this file imports from MUI.  These are intentionally
 * framework-agnostic primitives so the same tokens can feed `sx`
 * helpers, plain CSS, or eventually be exported to a token JSON for
 * design tools.
 */

// ---------------------------------------------------------------------------
// Radius scale — three tiers, no fourth.  More is noise.
// ---------------------------------------------------------------------------
//
// `control` — buttons, chips, inputs, small cards (10-12px feels modern
//             without slipping into pill territory)
// `panel`   — cards, dialogs, accordions, popovers
// `shell`   — top-level containers, page wrappers, hero panels
//
// We deliberately do NOT use 999 (full pill) for primary buttons.
// Pills are a strong period signal and currently make the app feel
// dated.  Chips remain pill-shaped because that's still a meaningful
// status indicator.

export const radius = {
  control: 10,    // buttons, inputs, small toggles
  chip: 999,      // status chips stay pill-shaped (intentional)
  panel: 16,      // cards, dialogs, accordions
  shell: 24,      // hero panels, top-level containers
} as const;

// ---------------------------------------------------------------------------
// Spacing scale — single source of truth.  MUI's default theme.spacing(n)
// uses 8px increments; we keep that base but commit to a fixed set of
// step values so pages stop reaching for `gap: 1.5` and `gap: 2.25`.
// ---------------------------------------------------------------------------

export const space = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
  xxxl: 64,
} as const;

// ---------------------------------------------------------------------------
// Type scale — seven explicit roles with intentional contrast.
//
// MUI's default `h1..h6` shrink in arithmetic increments and end up
// barely distinguishable from `body1` once `fontWeight: 700` is
// applied uniformly.  This scale fixes that with explicit size,
// weight, line-height, and letter-spacing per role so a page title
// is *visibly* different from a section title from a subhead.
//
// Sizes use rems so they scale with user preferences.  Line heights
// are unitless ratios.
// ---------------------------------------------------------------------------

export interface TypeRole {
  fontSize: string;
  fontWeight: number;
  lineHeight: number;
  letterSpacing?: string;
}

export const type = {
  /** Top-of-page page title. One per page. */
  pageTitle: {
    fontSize: '1.875rem',  // 30px
    fontWeight: 700,
    lineHeight: 1.2,
    letterSpacing: '-0.01em',
  },
  /** Major section heading inside a page. */
  sectionTitle: {
    fontSize: '1.25rem',   // 20px
    fontWeight: 600,
    lineHeight: 1.3,
  },
  /** Subhead above a group of fields, list, or panel. */
  subheading: {
    fontSize: '1rem',      // 16px
    fontWeight: 600,
    lineHeight: 1.4,
  },
  /** Default body copy. */
  body: {
    fontSize: '0.9375rem', // 15px
    fontWeight: 400,
    lineHeight: 1.5,
  },
  /** Secondary metadata: counts, dates, status text in tables. */
  metadata: {
    fontSize: '0.8125rem', // 13px
    fontWeight: 400,
    lineHeight: 1.45,
  },
  /** Caption / helper text under inputs / footnotes. */
  caption: {
    fontSize: '0.75rem',   // 12px
    fontWeight: 400,
    lineHeight: 1.4,
  },
  /** Micro labels for status chips, badges, eyebrows. */
  microLabel: {
    fontSize: '0.6875rem', // 11px
    fontWeight: 600,
    lineHeight: 1.2,
    letterSpacing: '0.04em',
  },
} as const satisfies Record<string, TypeRole>;

// ---------------------------------------------------------------------------
// Motion — duration + easing tokens.  Not used aggressively (this is an
// ops console, not a marketing site) but defined so transitions are
// consistent when they do happen.
// ---------------------------------------------------------------------------

export const motion = {
  duration: {
    fast: 120,
    base: 180,
    slow: 280,
  },
  easing: {
    standard: 'cubic-bezier(0.2, 0, 0, 1)',
    accelerate: 'cubic-bezier(0.3, 0, 1, 1)',
    decelerate: 'cubic-bezier(0, 0, 0, 1)',
  },
} as const;

// ---------------------------------------------------------------------------
// Elevation — single soft shadow recipe per tier.  No layered shadow
// stacks, no `inset 0 0 0 1px` hacks pretending to be borders.
// ---------------------------------------------------------------------------

/*
 * Beta.3 visual identity — tightened to feel like an "operations
 * console" rather than a marketing site.  Cards sit on the surface
 * with a tactile edge rather than floating with a soft fluffy halo;
 * the new recipes pair a thin shadow with an inset top highlight so
 * surfaces read as panels stamped from the page rather than cards on
 * top of it.
 */
export const elevation = {
  /** Flat — base surface, no lift. */
  none: 'none',
  /** Subtle tactile panel — bottom-edge shadow + 1px top highlight.
   *  Cards, secondary panels, anything that wants to read as a
   *  defined surface on the page background. */
  raised:
    '0 1px 0 rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.55)',
  /** Hover or focus state on raised — slightly more pronounced
   *  bottom shadow signals lift without becoming distractingly
   *  shadowy. */
  hover:
    '0 1px 0 rgba(0, 0, 0, 0.06), 0 2px 6px rgba(0, 0, 0, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.60)',
  /** Modal / popover / dropdown — needs to clearly hover, but
   *  keep the shadow shape consistent with raised. */
  overlay:
    '0 4px 16px rgba(0, 0, 0, 0.12), 0 12px 32px rgba(0, 0, 0, 0.10), inset 0 1px 0 rgba(255, 255, 255, 0.6)',
} as const;

// Dark-mode shadow variants — drop the white top-highlight (it'd
// read as a hard line on dark) and lean harder on the bottom shadow.
export const elevationDark = {
  none: 'none',
  raised:
    '0 1px 0 rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.04)',
  hover:
    '0 1px 0 rgba(0, 0, 0, 0.50), 0 2px 6px rgba(0, 0, 0, 0.40), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
  overlay:
    '0 4px 16px rgba(0, 0, 0, 0.50), 0 12px 32px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
} as const;

// ---------------------------------------------------------------------------
// Borders — subtle 1px borders on surfaces.  Replaces the previous
// `inset 0 0 0 1px alpha(primary, 0.05)` shadow trick.
// ---------------------------------------------------------------------------

export const border = {
  hairlineLight: 'rgba(0, 0, 0, 0.08)',
  hairlineDark: 'rgba(255, 255, 255, 0.08)',
} as const;

// ---------------------------------------------------------------------------
// Density — comfortable vs compact.  Not yet exposed as a user
// preference but defined so pages can pick up the right padding when
// we wire that up later.
// ---------------------------------------------------------------------------

export const density = {
  comfortable: {
    rowPaddingY: space.sm,
    rowPaddingX: space.md,
    sectionGap: space.lg,
  },
  compact: {
    rowPaddingY: space.xs,
    rowPaddingX: space.sm,
    sectionGap: space.md,
  },
} as const;

// ---------------------------------------------------------------------------
// Layout — shell + auth surface dimensions.  These were previously
// hardcoded in Layout.tsx / Login.tsx / ForceChangePassword.tsx and drifted
// (the two auth cards used 450 vs 440 maxWidth).  Centralised here so the
// app shell and auth flow stay visually consistent.
// ---------------------------------------------------------------------------

export const layout = {
  /** Persistent left navigation drawer width on desktop (>= sm). */
  drawerWidth: 240,
  /** Max width of the auth-flow cards (Login, ForceChangePassword). */
  authCardMaxWidth: 450,
  /** Square avatar/logo cell at the top of the auth cards. */
  authLogoSize: 88,
} as const;
