# UI Migration Plan — MUI Removal

BlueStick's frontend is migrating off Material UI to a headless
stack (Radix UI + Tailwind v4 + shadcn-style primitives). The Phase 0
substrate has shipped; this document is the operating plan for the
page-by-page migration that follows.

This doc supersedes the UI_STYLE_GUIDE.md until that file is rewritten
from the migrated codebase in the post-migration phase.

## Why we're doing this

The April 2026 UX audit (see chat history at v3.0.0-alpha.15) flagged
that BlueStick is *under-dense* for its audience (pentesters /
power users on big screens), looks like "tuned MUI" instead of having
its own identity, and inherits API-shape mismatches from MUI's
component model (Snackbar queue, Dialog-not-side-sheet, hand-rolled
data grids fighting MUI primitives, etc.). The full reasoning lives
in the audit transcript; the conclusion was a full headless rewrite
rather than the previously-planned MUI + Tailwind hybrid.

## Target stack (post-migration)

| Concern | Choice | Rationale |
|---|---|---|
| Styling | Tailwind v4 (`@tailwindcss/vite`) | Token-driven; zero runtime CSS-in-JS cost |
| Headless primitives | Radix UI (`@radix-ui/*`) | Solid a11y + keyboard, no styling opinions |
| Primitives shape | shadcn-style copy-paste source under `src/components/ui/` | Battle-tested defaults; we own the source and can tune freely |
| Class composer | `clsx` + `tailwind-merge` via `src/lib/cn.ts` | Last-conflicting-Tailwind-wins semantics |
| Variants | `class-variance-authority` (cva) | shadcn convention |
| Toasts | `sonner` (wired inside `ToastContext`) | Proper a11y, severity-tiered duration, dedup |
| Data grid | `@tanstack/react-table` + `@tanstack/react-virtual` | Replaces hand-rolled tables + MUI X DataGrid |
| Command palette | `cmdk` (Phase 2 scope) | Power-user navigation |
| Icons | `lucide-react` | Lightweight, consistent stroke style |
| Date picker | `react-day-picker` | Headless, Tailwind-friendly |
| Theming | CSS variables set by `theme/cssVars.ts`, palette in `theme/palettes.ts` | Decoupled from any UI framework |

## What survives the migration unchanged

- `theme/tokens.ts` (radius/space/type/motion/elevation — already framework-agnostic)
- `theme/palettes.ts` (color identity per theme)
- All five theme variants: light, dark, phosphor, magma, absolute-zero
- The `useToast()` public surface — callers don't change
- `ThemeContext` public surface (`themeName`, `setThemeName`, `isDarkTheme`)
- Page routing structure (URLs stay the same; React Router config preserved)
- All backend API contracts (this migration is frontend-only)

## The boundary during migration

There is no hard file-level boundary anymore — MUI is being *removed*,
not coexisted with. The migration proceeds page-by-page; each page is
whole-file MUI **or** whole-file new stack, never both. Until a page
migrates, it keeps its MUI imports.

The new primitives live in `src/components/ui/` and are imported
freely from migrated pages. They use Tailwind classes that reference
the CSS-variable theme.

After every page migrates, the last step uninstalls `@mui/material`,
`@mui/icons-material`, `@mui/x-data-grid`, `@emotion/react`,
`@emotion/styled`, and removes the MUI ThemeProvider wiring from
`ThemeContext`.

## Migration order (low-risk → high-risk)

Order is chosen so each phase validates a different primitive set
before the next phase's pages need it. Layout migrates LAST because
every page depends on it.

| # | Page family | Validates |
|---|---|---|
| 1 | Login + ForceChangePassword | Form primitives end-to-end (Input, Label, Button, Alert) |
| 2 | Reference pages (UserGuide, ToolReference, SbomReference, Reference) | Typography, Card, Link patterns under load |
| 3 | Settings family (Profile, SystemSettings, LLMSettings, IntegrationSettings, ProjectSettings) | Select, Switch, Checkbox, Radio, Dialog, useConfirm |
| 4 | Simple data pages (Feedback, RiskAssessment, DefaultCredentials, ParseErrors, Activity, ProjectActivity) | First DataTable wrapper + filter bar |
| 5 | Scopes + Scans family | Upload flow, table+detail navigation |
| 6 | Recon + Executions family | Multi-select compare, row checkbox-in-table |
| 7 | Portfolio + widgets | Card grids, metric tiles |
| 8 | Test Plans family (largest workflow) | Tabs-as-routes, agent activity rail, dialogs at scale |
| 9 | Hosts family (largest data surface) | Virtualized DataTable, side-sheet HostDetail |
| 10 | Operations landing | All widgets composed |
| 11 | Shared components sweep (ConfirmDialog, ExportDialog, ReportsDialog, ProjectSelector, UserMenu, etc.) | Final cleanup |
| 12 | Layout shell (LAST — uninstalls MUI) | App chrome rewrite, new IA + command palette |

## Per-page migration checklist

For every page PR:

1. **Read the existing MUI page** end-to-end before touching it. Understand the data flow, the state machine, the special cases.
2. **Replace imports**: `@mui/material` → `src/components/ui/*`, MUI icons → `lucide-react`.
3. **Rewrite JSX**: structural changes welcome; if a page should be a
   route-per-tab instead of a Tab component, do it now.
4. **Fix audit findings** for that page as part of the migration. Don't
   carry MUI-era bugs into the new component.
5. **Keep the route URL stable**. Bookmarks must continue to work.
6. **Test the page** in light + dark + at least one accent theme
   (phosphor or magma) before declaring done.
7. **Bump the frontend version** (PATCH bump per migration; MINOR when
   a phase completes).
8. **Update CHANGELOG.md**.
9. **Update V4_BUNDLE_BASELINE.md** with new chunk sizes.

## Audit findings to address during migration

The April 2026 audit produced four classes of Critical/High findings.
Each migration PR should knock these out for the page being touched:

- **C1 (no code-splitting)** — DONE in Phase 0.
- **C2 (toast not announced)** — DONE in Phase 0.
- **C3 (silent failures)** — Fix per-page as encountered.
- **C4 (clickable TableRow keyboard-inaccessible)** — Replaced by
  DataTable's built-in keyboard nav.
- **C5 (Login a11y)** — Fixed in Migration #1.
- **C6 (broken tab/tabpanel ARIA)** — Built into Tabs primitive.
- **C7 (destructive-action friction inversion)** — Fixed during
  Settings + TestPlans migrations.
- **C8 (table overflow at tablet)** — Accepted as desktop-first; the
  audit downgraded this when style guide constraints were lifted.
- **C9 (Portfolio → removed Dashboard)** — Fix in Portfolio migration.
- **C10 (upload dialog mid-transfer)** — Fix in Scans migration.

## When to bump versions

- **Frontend `4.0.0-alpha.N`**: increment N on every shipped migration PR.
- **Frontend `4.0.0-beta.0`**: when all pages have migrated and MUI is uninstalled.
- **Frontend `4.0.0`**: after style guide is rewritten + visual QA pass.

Backend version is not affected by this migration.

## Risk model

| Risk | Mitigation |
|---|---|
| Visual inconsistency during migration (some pages new, some old) | Accept it; no prod users. Solo dev's tolerance for inconsistency is high; users dogfooding can see progress weekly. |
| The new primitives have subtle bugs (focus, IME, etc.) | Radix has solved these; shadcn copy-paste is widely battle-tested. Compose carefully, don't reinvent. |
| Migration drags on indefinitely | Sequence is fixed; each PR is small enough to ship in 1-3 days. Stalling on one page means moving to the next or asking for help. |
| MUI removal forgets a transitive dep | `npm ls @mui/material` after the Layout migration verifies. The Phase 0 boundary docs are gone — no per-import enforcement, just the final uninstall step. |

## Out of scope for this migration

- Backend changes
- New features (every PR is a migration, not a feature)
- Re-theming (the five themes stay)
- React 18 → 19 upgrade (track separately)
- TypeScript strict-mode tightening (track separately)
- Test framework changes (Vitest stays)

## Reference

- Phase 0 substrate PR: see CHANGELOG entry for `4.0.0-alpha.0`
- Audit transcript: chat history through 2026-05-15
- Bundle baseline: `documentation/V4_BUNDLE_BASELINE.md`
- Primitives: `frontend/src/components/ui/*.tsx`
- Theme bridge: `frontend/src/theme/cssVars.ts`, `frontend/src/theme/palettes.ts`
