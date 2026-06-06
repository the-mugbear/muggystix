# V4 Migration — Session Handoff Notes

**Last session ended:** 2026-05-16, after shipping `4.0.0-alpha.0` through `4.0.0-alpha.22`.  Every page + every shared component is on v4 primitives, MUI + Emotion + MUI X are uninstalled, and the shared chunk dropped from 207.54 → 139.59 KB gzip (-32.7%).  All that's left is phase F (style guide rewrite — pure doc work), the deferred IA + cmd-K command palette (additive feature), and the v4.0.0-beta.0 promotion.

This doc is the briefing for whoever (you, another agent, future-you) starts
the next session. Read this + `MIGRATION_PLAN.md` + `CHANGELOG.md` (top
~17 entries) and you're caught up.

## TL;DR

- v4 MUI-removal migration is **~97% done by page count** (~34 of ~35 surfaces — only Layout shell remains; all data pages are on v4).
- All shipped alphas are **build-green**. Commits: `b9b5fc6` (alpha.0..6), `02c8f12` (alpha.7..12), `f976939` (alpha.13), `1908c29` (alpha.14), `76850a4` (alpha.15..17), `25ae081` (Docker build fix), alpha.18 in current tree (commit pending).
- Substrate (Tailwind v4 + Radix + Sonner + shadcn-style primitives +
  CSS-variable theme bridge + lazy routes) is **done and frozen** — do not
  relitigate substrate decisions.
- Bundle: shared chunk **209 KB gzip flat** across alpha.0 → alpha.18. Will not drop materially until Layout migrates + MUI uninstalls (final phase E).
- Remaining work: ~3,000 LOC — phase D shared-components sweep + phase E Layout shell + MUI uninstall. Realistically **1-2 more sessions**.

## Phase C complete (alpha.15..17 — this session)

**Alpha.15** seeded the missing primitives:
- `src/components/ui/data-table.tsx` — TanStack wrapper (`useDataTable`, `<DataTableShell>`, `<DataTablePagination>`, `selectionColumn<T>()`, `<SortableHeader>`).  Supports both client-managed and `manualSorting` / `manualPagination` modes.
- `src/components/ui/side-sheet.tsx` — Radix Dialog with `modal={false}` + right-edge slide-over.  Four width tiers (`md` / `lg` / `xl` / `full`).  Default behavior: outside-click does NOT close (operators expect to interact with the list behind).
- `src/components/ui/popover.tsx` — Radix Popover with shadcn styling.
- `src/components/ui/combobox.tsx` — cmdk + Popover searchable multi/single select.  Replaces every MUI Autocomplete.
- `src/index.css` — new `slide-in-from-right` / `slide-out-to-right` full-distance edge slides for the side-sheet (the existing `*-from-right-2` 4px nudge stays for popovers).

**Alpha.16** migrated the Hosts list:
- `pages/Hosts.tsx` (2079 → 1273 LOC) on `useDataTable`/`<DataTableShell>` + `<DataTablePagination>`.  Per-row follow menu is a Radix DropdownMenu (no more `followMenu = { hostId, anchorEl }` state).  Mobile cards + desktop table render unconditionally; CSS handles the swap.
- `components/HostFilters.tsx` (873 → 524 LOC) — 8 MUI Autocompletes collapse to the Combobox primitive.
- Hosts chunk grew 97.3 → 138.1 KB raw because this is the first TanStack-Table + cmdk consumer.  Amortizes as future pages adopt.

**Alpha.17** migrated HostDetail:
- `pages/HostDetail.tsx` (1840 → 1075 LOC).  All sections rewritten on v4 (header chrome, host overview Card, Proposed Tests Card with status Select per entry, vulnerabilities Card, add-note + team-notes Cards with threaded replies, conflicts Card, port-details Card with three Accordions, connection-helpers Popover).  Snackbar replaced with `useToast.info('Copied to clipboard')`.  HostDetail chunk dropped 57.4 → 51.3 KB raw.
- `components/HostLineagePanel.tsx` (294 → 198 LOC) — shared `<LineageRow>` for the three sections.  Tests still pass 4/4.

### Phase C tail — shipped (alpha.18)

- **Hosts row → SideSheet wiring** complete in alpha.18.  `HostInspector` extracted to `components/HostInspector.tsx` (owns data + body).  `HostDetail` page shrank to 156 LOC chrome only.  Hosts list row click opens the alpha.15 SideSheet primitive (`modal={false}`) with `<HostInspector density="sheet">` inside; header has prev/next, position counter, and "Open standalone" → `/hosts/:id` with the same navState the old flow built.  **No URL sync** — sheet state is purely local; refresh closes.  Add `?inspect=<id>` URL syncing later only if session-survival becomes a need.

## Phase A complete + alpha.13 (previous session)

**Phase A (alpha.7 → alpha.12)** shipped 12 pages + 4 widgets: Scopes,
Scans, ReconRunsList, ReconRunDetail, ReconCompare, ExecutionsList,
ExecutionDetail, PlanCompare, PortfolioDashboard, Operations + the four
risk widgets.

**Alpha.13** (start of phase B) shipped the seven satellite surfaces that
feed TestPlanDetail's tabbed split: TestPlans list, TestPlanCompare,
ProposedTestList, EntryResultsPanel, AgentActivityLog, and the three
execution/* shared components. Going children-first means the
TestPlanDetail split (alpha.14) lands when every consumer is already on v4.

Audit findings closed: C10 (Scans upload aria-live), C4
(critical-findings card keyboard access), H5 (recon/executions/
operations/agent-activity filter chips as aria-pressed), M6 (severity
helpers stopped pushing MUI hex). Operations chunk shrank 23.6 KB → 18.0
KB raw because ToggleButtonGroup + MUI Stack disappeared.

## State as of alpha.13

### Pages migrated (31)

Login, ForceChangePassword, Reference, UserGuide, ToolReference,
SbomReference, Profile, SystemSettings, LLMSettings, IntegrationSettings,
ProjectSettings, Feedback, RiskAssessment, DefaultCredentials, ParseErrors,
Activity, ProjectActivity, ScanDetail, ScopeDetail, Scopes, Scans,
ReconRunsList, ReconRunDetail, ReconCompare, ExecutionsList,
ExecutionDetail, PlanCompare, PortfolioDashboard, Operations, **TestPlans,
TestPlanCompare**.

### Shared components migrated (17)

Earlier sweep: ConfirmDialog, AccessibleIconButton, VersionFooter,
MyQueueCard, MyTasksCard, ScopeCoverageWidget, PasswordInput, plus the
entire `src/components/ui/` primitive set.

Phase A: CriticalFindingsWidget, RiskSummaryWidget, RiskAssessmentWidget,
HostRiskAnalysis.

Alpha.13: ProposedTestList (StructuredTestCard), EntryResultsPanel,
AgentActivityLog, execution/ExecutionSessionHeader, execution/
ExecutionSessionPicker, execution/ExecutionCompareLinks.

### Remaining pages still MUI (1)

Layout.  Hosts + HostDetail migrated in alpha.16 + alpha.17.

### Phase B tail — what alpha.15 should pick up

TestPlanDetail is split (alpha.14) but only 3 of the 5 sub-tabs from
the original IA decision shipped: `/plan`, `/runs`, `/activity`. The
two deferred:

- **`/api-calls`** — split from `/activity`. `AgentActivityLog`
  already has method/status filters; the split is just a separate
  route preset (e.g. filter to write-method `POST`/`PATCH` calls by
  default vs the broader audit log).
- **`/danger`** — currently the Delete button lives on the action bar.
  Move it (and any other destructive actions like "reset all entries
  to proposed") to a dedicated sub-tab with a banner explaining the
  consequences. The DELETE-typed-name confirmation already exists in
  the layout's delete dialog — move it whole.

Both are small enough (~150 LOC each) that they could be one alpha.

### Shared components migrated (8) — superseded list, kept for diff context

ConfirmDialog (used by useConfirm hook), AccessibleIconButton,
VersionFooter, MyQueueCard, MyTasksCard, ScopeCoverageWidget,
PasswordInput (`src/components/ui/password-input.tsx` — replaces
`PasswordField.tsx` at call sites; old file still in tree but unused),
the entire `src/components/ui/` primitive set (Button, Input, Label,
Card, Dialog, DropdownMenu, Tooltip, Tabs, Switch, Checkbox, Radio,
Select, Avatar, Badge, Separator, Accordion, Table, Textarea, Alert).

### Primitives layer (`src/components/ui/`)

shadcn-style — copy-paste TS source, you own it.  Tuned for the
pentest-console density target (Button h-8 vs shadcn's h-10, denser
typography).  Tokens in `src/theme/tokens.ts` (structural) +
`src/theme/palettes.ts` (color packs) + `src/theme/cssVars.ts` (CSS-var
bridge).  Tailwind config in `frontend/tailwind.config.ts`.

### Audit findings closed during migration

C1 (no code-splitting), C2 (toast not announced), C5 (Login a11y), C6
(broken tab/tabpanel ARIA — per migrated page), C7 partial (destructive
friction — Profile/SystemSettings/LLM/Integration), H4 (Profile +
Settings bypass useToast), H5 (filter chips no aria-pressed — per
migrated page), H12 (formatApiError bypassed — per migrated page), M4
+ M5 (toast queue + duration — Sonner), M6 (hardcoded hex — per page),
M8 (typed-name confirm — System delete user), M15 (PasswordField
accessibility), M20 (ForceChangePassword checklist a11y).

The full list per alpha is in `CHANGELOG.md`.

## Substrate decisions (frozen — do not relitigate)

| Concern | Choice | Where |
|---|---|---|
| Styling | Tailwind v4 via `@tailwindcss/vite` | `vite.config.mts`, `tailwind.config.ts`, `src/index.css` |
| Primitives | shadcn-style on Radix UI | `src/components/ui/*` |
| Class composer | `clsx` + `tailwind-merge` | `src/lib/cn.ts` |
| Variants | `class-variance-authority` (cva) | used by Button, Badge, Alert |
| Toasts | `sonner` (wrapped inside `useToast`) | `src/contexts/ToastContext.tsx` |
| Data grid | `@tanstack/react-table` (installed; **not yet used** — first consumer will be Hosts) | TBD |
| Command palette | `cmdk` (installed; **not yet used**) | TBD — Layout migration |
| Icons | `lucide-react` | every migrated page |
| Date picker | `react-day-picker` (installed; **not yet used**) | TBD |
| Theming | CSS-var bridge over MUI palette (during transition) | `src/theme/cssVars.ts` |

## Remaining work (in recommended order)

| Phase | Files | LOC | Why this order |
|---|---|---|---|
| ~~A — Mechanical cleanup~~ | DONE alpha.7..12 | ~10,000 | ✓ Shipped previous session |
| ~~B — Test plans family (DONE alpha.13..14, .19)~~ | ~~TestPlans~~, ~~TestPlanDetail split~~, ~~TestPlanCompare~~, ~~all support cast~~, ~~`/api-calls` + `/danger` sub-tabs~~ | 0 | ✓ Phase B complete. |
| ~~C — Hosts family (DONE alpha.15..18)~~ | ~~Hosts~~, ~~HostDetail~~, ~~HostFilters~~, ~~HostLineagePanel~~, ~~HostInspector extraction~~, ~~SideSheet wiring~~, ~~DataTable / SideSheet / Combobox / Popover primitives~~ | 0 | ✓ Phase C complete. |
| **D — Shared sweep** | ExportDialog, ReportsDialog, ProjectSelector, UserMenu, PageSkeleton, AppIcons, ScreenshotLightbox, CommandExplanation, InAppAgentPanel, ToolReadyOutput, WebInterfacesCard, FilterTooltip, LastUpdated, ExecutionSessionHeader, EntryResultsPanel residuals, ScopeExport, OutOfScopeExport | ~3,500 | Many small files. Phase A picked up several stragglers (LastUpdated, PageSkeleton, ExecutionSessionHeader, ScopeExport, OutOfScopeExport, InAppAgentPanel still MUI but render fine as subtrees) |
| **E — Layout shell + MUI uninstall** | Layout.tsx + **new IA + cmd-K** (IA decision locked) + final `npm uninstall @mui/* @emotion/*` | ~1,500 | **Last** — depends on D; this is the cutover |
| **F — Style guide rewrite** | `documentation/UI_STYLE_GUIDE.md` | doc | Free; write last so it documents what shipped, not what was planned |

## IA decisions (all three resolved — do not relitigate)

These three calls were made before phase A started. Phase B/C/E must
implement them, not re-debate them.

### 1. TestPlanDetail — split into routed sub-tabs
`/test-plans/:id/plan`, `/test-plans/:id/runs`, `/test-plans/:id/activity`,
`/test-plans/:id/api-calls`, `/test-plans/:id/danger`. Existing
`/test-plans/:id` bookmarks should redirect to `/plan`.

### 2. Hosts + HostDetail — DataTable + side-sheet rewrite
TanStack Table (already installed in alpha.0) for the list, Radix
Dialog `modal={false}` for the side-sheet HostDetail. Operators get
master-detail UX without navigating away from the list.

### 3. Layout shell — new IA + cmd-K
5-destination IA + `cmdk` command palette + agent activity rail. Layout
migration is the cutover, so this lands in phase E alongside the
`npm uninstall @mui/* @emotion/*`.

## Suggested opening prompt for the next session

Paste this verbatim:

```
Continue the v4 MUI-removal migration. State briefing in
documentation/V4_HANDOFF.md — read it first, do NOT relitigate
substrate decisions or re-migrate already-migrated pages.

Current state: 4.0.0-alpha.6 shipped, build-green, committed.
17 of ~35 pages migrated, 8 shared components migrated.
Substrate (Tailwind v4 + Radix + Sonner + shadcn-style primitives +
CSS-var theme bridge + lazy routes) is frozen.

Scope for this session: <pick one>
  - Phase A — Mechanical cleanup (Scopes/Scans/recon-exec lists/
    widgets/PortfolioDashboard/Operations).  ~10k LOC, low risk.
  - Phase B — Test plans family (decision needed: split
    TestPlanDetail to routed sub-tabs or keep monolith?).
  - Phase C — Hosts family (decision needed: DataTable + side-sheet
    or like-for-like?).
  - Phase E — Layout shell + MUI uninstall (decision needed:
    include new IA + cmd-K or keep current IA?).

IA decisions made (or "defer"):
  - TestPlanDetail tabs-as-routes: <yes / keep monolith / defer>
  - Hosts DataTable + side-sheet: <yes / like-for-like / defer>
  - Layout new IA + cmd-K: <yes / like-for-like / defer>

Ship as 4.0.0-alpha.7 onward.  Follow MIGRATION_PLAN.md.
Bump CHANGELOG.md + platform_version.json + frontend/package.json +
docker-compose.yml per alpha.  Build must stay green throughout.
```

## Pitfalls to avoid (lessons from this session)

- **Don't re-read the substrate files** at the start of the next session.
  `tokens.ts`, `palettes.ts`, `cssVars.ts`, `tailwind.config.ts`,
  every `src/components/ui/*` — these are stable. Read only when
  about to modify.
- **Don't try to migrate Hosts or TestPlanDetail without a primitive
  audit first.** Both will likely surface a missing primitive (Combobox,
  DataTable, ContextMenu). Build the primitive once, then both consume it.
- **Don't migrate Layout before everything else is done.** It's the
  capstone — uninstalling MUI happens here. If Layout migrates first
  but other pages still import `@mui/material`, the uninstall breaks them.
- **Don't migrate page-by-page in lockstep with the alpha bump.** Some
  migrations are mechanical enough to batch (e.g. ReconRunsList +
  ExecutionsList in one alpha). Use judgement.
- **PasswordField.tsx is dead code.** Delete it as part of phase D
  cleanup; nothing imports it anymore.
- **CommandExplanation.tsx is still MUI** and is imported by
  ScanDetail (migrated). The MUI subtree inside a v4 page renders
  fine; clean it up in phase D.
- **The `react-app` ESLint preset in package.json is dead config.**
  `react-app` is not installed; ESLint itself was uninstalled. The
  block is harmless but cosmetic cleanup material.

## Build / test verification before any commit

```bash
cd frontend && npm run build           # must succeed
                                         # current baseline: 209 KB gzip shared,
                                         # < 95 KB gzip per page chunk
npm test                                 # vitest; verify no regressions
```

If a migration touches a tested file, run vitest on just that test:

```bash
npm test -- src/tests/pages/PageName.test.tsx
```

## Bundle baseline tracking

After each alpha ships, append to
`documentation/V4_BUNDLE_BASELINE.md`. The trend line tells you
whether the migration is delivering on the bundle-size promise. The
big drop will land with phase E (MUI uninstall) — until then the
shared chunk should stay flat ± 5%.

## File map for fast onboarding

Most important files to know about:

- `src/components/ui/*.tsx` — the 19 primitives
- `src/utils/cn.ts` — class-name composer used by every primitive (clsx + tailwind-merge)
- `src/theme/palettes.ts` — color tokens (5 themes preserved)
- `src/theme/cssVars.ts` — projects palettes onto CSS variables
- `src/theme/tokens.ts` — structural tokens (radius, space, type, motion, elevation)
- `src/contexts/ThemeContext.tsx` — calls both MUI ThemeProvider AND applyThemeToDocument
- `src/contexts/ToastContext.tsx` — wraps Sonner; public API unchanged
- `src/components/ConfirmDialog.tsx` — used by `src/hooks/useConfirm.tsx`
- `src/index.css` — Tailwind directives + `:root` default CSS vars + animation utilities
- `frontend/tailwind.config.ts` — TS-native config, reads from tokens.ts
- `frontend/vite.config.mts` — Tailwind plugin + rollup-plugin-visualizer (off by default)
- `documentation/MIGRATION_PLAN.md` — the playbook
- `documentation/V4_BUNDLE_BASELINE.md` — trend tracking
- `CHANGELOG.md` — top ~7 entries are the v4 alpha narrative
