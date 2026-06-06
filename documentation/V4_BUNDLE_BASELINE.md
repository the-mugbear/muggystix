# Bundle baseline — v4.0.0-alpha.0

Captured after Phase 0 lands (Tailwind v4 + Radix + Sonner + lazy
routes, MUI still present). Used as the reference point for every
subsequent migration PR — when MUI is removed entirely the shared
chunk should drop by 60-70%.

Run the analyzer locally:

```bash
cd frontend
npm run analyze
# Opens build/stats.html as an interactive treemap
```

## Top chunks (gzip)

| Chunk | Raw | Gzip | Notes |
|---|---|---|---|
| `index-*.js` (shared) | 622 KB | **195 KB** | MUI + Emotion + React + Sonner. Target after MUI removal: <80 KB gzip |
| `Hosts-*.js` | 60 KB | 17 KB | Hand-rolled table; will shrink when migrated to TanStack Table |
| `HostDetail-*.js` | 57 KB | 18 KB | Will split when migrated to routed tabs |
| `TestPlanDetail-*.js` | 55 KB | 15 KB | Same — 2134 LOC monolith to split |
| `Scans-*.js` | 37 KB | 12 KB | |
| `Scopes-*.js` | 26 KB | 8 KB | |
| `ToolReference-*.js` | 25 KB | 8 KB | |
| `Autocomplete-*.js` (MUI vendor) | 23 KB | 8 KB | Disappears with MUI removal |

## Initial paint (cold cache, anonymous user → /login)

- `index-*.js` (shared): 195 KB gzip
- `Login-*.js` (eager): TBD (Login still MUI; will measure post-migration)
- **Total cold start**: ~210 KB gzip

## What this number means

The 195 KB shared chunk is mostly MUI + Emotion. Lazy-loading routes
helped distribute *page* code, but the shared dependencies are still
loaded upfront. Replacing MUI with Radix + Tailwind should drop the
shared chunk to ~50-80 KB gzip.

## Migration tracking

After each migration phase, re-run the analyzer and append a row:

| Date | Phase | Shared (gzip) | Largest page (gzip) | Notes |
|---|---|---|---|---|
| TBD | 4.0.0-alpha.0 | 195 KB | Hosts 17 KB | Baseline |
| 2026-05-15 | 4.0.0-alpha.7 | 209 KB | Hosts 27 KB | Scopes migrated (29 KB raw / 9 KB gzip). Shared chunk flat as expected — MUI still in tree. |
| 2026-05-15 | 4.0.0-alpha.8 | 209 KB | Hosts 27 KB | Scans migrated (39 KB raw / 12 KB gzip — biggest page in phase A). Shared chunk flat. |
| 2026-05-15 | 4.0.0-alpha.9 | 209 KB | Hosts 27 KB | Recon family migrated (List 5.6 KB / Detail 10.5 KB / Compare 6.5 KB raw). Shared chunk flat. |
| 2026-05-15 | 4.0.0-alpha.10 | 209 KB | Hosts 27 KB | Executions family migrated (List 6.0 KB / Detail 5.7 KB / Compare 7.3 KB raw). Shared chunk flat. |
| 2026-05-15 | 4.0.0-alpha.11 | 209 KB | Hosts 27 KB | Risk widgets migrated (CriticalFindings / RiskSummary / RiskAssessment / HostRiskAnalysis — fold into parent chunks). Shared chunk flat. |
| 2026-05-15 | 4.0.0-alpha.12 | 209 KB | Hosts 27 KB | Portfolio (8.8 KB) + Operations (18.0 KB — down from 23.6 KB). **Phase A complete.** Shared chunk still flat — MUI doesn't drop until phase E uninstalls. |
| 2026-05-16 | 4.0.0-alpha.13 | 208.5 KB | Hosts 27 KB | Test Plans support cast (TestPlans 22 KB / TestPlanCompare 8.6 KB / shared components fold into parents). First measurable shared-chunk drop (-0.1 KB) as the migrated tree begins to outweigh MUI in the consumer pages. |
| 2026-05-16 | 4.0.0-alpha.14 | 209.2 KB | Hosts 29 KB | TestPlanDetail 2134 LOC monolith → routed sub-tabs (Layout 27 KB + PlanTab 20 KB raw). Shared chunk +0.7 from NavLink/Outlet code; the monolith chunk (72.6 KB) is gone. |
| 2026-05-16 | 4.0.0-alpha.15 | 209.2 KB | Hosts 29 KB | Phase C primitives (DataTable / SideSheet / Combobox / Popover) — substrate-only, no consumers yet, so the new code is tree-shaken out of every page chunk and bundle is flat. Hosts and HostDetail baselines (Hosts 97.3 KB raw / 28.9 KB gzip, HostDetail 57.4 KB raw / 17.7 KB gzip) are what alpha.16/alpha.17 should beat. |
| 2026-05-16 | 4.0.0-alpha.16 | 209.2 KB | Hosts 40 KB | Hosts list (2079 → 1273 LOC) + HostFilters (873 → 524 LOC) migrated. Hosts chunk grew 97.3 → 138.1 KB raw (28.9 → 39.8 KB gzip) because this is the first TanStack-Table consumer + cmdk consumer in the tree. Both packages were installed in alpha.0 with no usage; they amortize as future pages adopt DataTable / Combobox. DropdownMenu primitive split into its own 20.7 KB chunk (multiple migrated pages reference it now). |
| 2026-05-16 | 4.0.0-alpha.17 | 209.2 KB | Hosts 38 KB | HostDetail (1840 → 1075 LOC) + HostLineagePanel (294 → 198 LOC) migrated. HostDetail chunk dropped 57.4 → 51.3 KB raw (17.7 → 15.2 KB gzip, -14%) — v4 primitives + lucide are cheaper than the MUI Card/Chip/Accordion/Snackbar/Popover stack they replaced. Hosts chunk also nudged down (138.1 → 132.1 KB raw) since HostLineagePanel folds into Hosts via HostDetail. Shared chunk flat. |
| 2026-05-16 | 4.0.0-alpha.18 | 209.2 KB | Hosts 39 KB | HostInspector extracted into its own chunk (56.24 KB raw / 16.62 KB gzip), shared between the standalone HostDetail page and the new Hosts-list SideSheet.  HostDetail chunk collapsed to 3.28 KB raw / 1.55 KB gzip (thin chrome only).  Hosts chunk +3.81 KB raw / +1.13 KB gzip for SideSheet primitive + inspector hook-up.  Net: row-click inspection costs ~16 KB gzip on first open (then cached). |
| 2026-05-16 | 4.0.0-alpha.19 | 209.3 KB | Hosts 39 KB | Phase B tail: TestPlanDetail `/api-calls` (1.35 KB raw / 0.75 KB gzip) and `/danger` (2.63 KB raw / 1.26 KB gzip) sub-tabs shipped.  TestPlanLayout flat (+0.06 KB raw — Delete button removed from action bar offsets the new NavLinks).  Shared chunk +0.11 KB gzip (basically flat). |
| 2026-05-16 | 4.0.0-alpha.20 | **208.0 KB** | Hosts 42 KB | Phase D batch 1: deleted PasswordField + FilterTooltip + RiskTooltip (~560 LOC of MUI dead code), migrated PageSkeleton + LastUpdated + ProtectedRoute + ScreenshotLightbox.  **First measurable shared-chunk drop (-1.33 KB gzip)** as MUI Skeleton / IconButton / CircularProgress / Box / Stack instances on the shared path get purged. |
| 2026-05-16 | 4.0.0-alpha.21 | **207.5 KB** | Hosts 39 KB | Phase D batch 2: deleted ExportDialog + ServiceActions (~785 LOC of MUI dead code), migrated 7 workflow components (ReportsDialog, ToolReadyOutput, OutOfScopeExport, ScopeExport, InAppAgentPanel, CommandExplanation, WebInterfacesCard).  Shared chunk -0.42 KB gzip; Hosts -3.60 KB gzip; HostInspector -1.12 KB gzip.  Only four MUI consumers remain (Layout / ProjectSelector / UserMenu / AppIcons) — phase E migrates them together with the MUI uninstall. |
| 2026-05-16 | 4.0.0-alpha.22 | **139.6 KB** | Hosts 39 KB | **Phase E — MUI removed.**  Layout family migrated + ThemeContext rewritten + `npm uninstall @mui/material @mui/icons-material @mui/x-data-grid @emotion/react @emotion/styled`.  Shared chunk drops **207.5 → 139.6 KB gzip (-67.95 KB / -32.7%)** — the milestone drop the migration promised.  Original alpha.0 baseline was 195 KB; target was "<80 KB after MUI removal".  Landed at 139.6 KB — halfway to the floor; remaining mass is React / React-DOM / axios / chart.js / Sonner / Radix primitives. |
| 2026-05-16 | 4.0.0-beta.0 | 139.6 KB | Hosts 39 KB | **Beta milestone.**  Migration plan's beta gate ("all pages migrated + MUI uninstalled") satisfied as of alpha.22.  This entry promotes the version label + ships the v4-stack UI Style Guide rewrite that gates v4.0.0 final.  Bundle unchanged from alpha.22. |
| 2026-05-16 | 4.0.0-beta.1 | 146.6 KB | Hosts 34 KB | cmd-K command palette wired globally (CommandPalette.tsx + Layout keyboard listener + topbar trigger pill).  Shared chunk +6.95 KB gzip for cmdk + palette code; Hosts -5 KB gzip because cmdk previously folded into the Hosts chunk via Combobox.  Net: ~+2 KB shared, palette code amortized across every page. |
| 2026-05-16 | 4.0.0-beta.2 | 149.8 KB | Hosts 34 KB | 5-destination IA reshape + 4 hub landing pages + AgentActivityRail popover.  Sidebar collapses 16 → 5 entries; secondary nav strip renders below topbar.  Shared chunk +3.29 KB gzip for the reshape (hub landings + rail + new HUBS structure).  Per-page chunks essentially unchanged (HostInspector -5 KB raw from chunk-splitting reshuffle when Layout shrank). |
| 2026-05-16 | 4.0.0-beta.3 | 149.9 KB | Hosts 34 KB | Visual-identity overhaul — chrome refinement.  Dropped Layout's card-in-a-card wrapper; sidebar got a derived tonal shift via shiftLightness(); sidebar + secondary nav got accent-edge active states; elevation tokens tightened to "tactile panel" recipes.  CSS-only changes; bundle essentially unchanged (+0.06 KB gzip from token string growth). |
| 2026-05-16 | 4.0.0-beta.4 | 150.7 KB | Hosts 33 KB | a11y audit fix batch (Critical + High): mobile drawer → real Radix Dialog (SideSheet `side="left"` variant); Combobox chips + clear → real buttons + keyboard (Backspace-to-remove); DataTable rows tabIndex+Enter/Space handler with focus-visible ring; chrome heights → ResizeObserver-driven CSS custom properties.  Shared chunk +0.8 KB gzip from the ResizeObserver wiring + larger keyboard handlers. |
