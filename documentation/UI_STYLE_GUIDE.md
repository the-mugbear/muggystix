# UI Style Guide

> **Stack:** Tailwind v4 + Radix UI (shadcn-style primitives) + lucide-react + Sonner
> **Last verified against:** frontend 4.0.0-alpha.22 (2026-05-16) — MUI fully removed

## Purpose
This guide defines UI rules for BlueStick so feature work, bug fixes, and LLM-assisted changes preserve layout integrity, readability, and predictable behavior under real application data.

This is not only a visual guide. It is a behavioral contract for how UI must respond to:
- long database values
- null or incomplete data
- asynchronous loading
- error states
- dense tables
- small viewports

## Scope
This guide applies to:
- `frontend/src/pages`
- `frontend/src/components`
- `frontend/src/components/ui` (the v4 primitive set)
- `frontend/src/utils`
- any new shared UI helpers

It should be treated as a reference for:
- manual frontend development
- pull request review
- LLM-assisted implementation prompts

## Core Principle
All UI must be resilient to unknown content length, missing values, partial responses, and narrow screens.

No component may assume:
- short strings
- complete data
- desktop-only width
- stable row height from backend values
- one-line labels

## Non-Negotiable Rules

### 1. Layout Stability
- No page-level horizontal overflow is allowed.
- Database or API values must never push the page wider than the viewport.
- Content must not displace critical actions, filters, pagination, or navigation controls.
- Cards, tables, dialogs, chips, and badges must remain usable with worst-case data.
- Async loading must not cause major layout jumps when data resolves.

### 2. Content Resilience
- Treat all external values as unbounded.
- Every text-bearing component must explicitly define overflow behavior.
- Null, undefined, empty string, and malformed values must render a safe fallback.
- All pages must handle loading, empty, success, and error states.
- If content can exceed the intended space, the UI must choose one:
  - truncate
  - wrap
  - clamp
  - collapse behind a detail surface

### 3. Responsive Behavior
- Every page must work at mobile, tablet, and desktop widths.
- Dense desktop layouts must define a smaller-screen fallback (mobile cards via `md:hidden`, desktop table via `hidden md:block` — CSS-only, no `useMediaQuery`).
- Horizontal scroll is allowed only inside intentional subregions, not on the page body.
- If a table is not usable on small screens, it must collapse into a stacked layout or card view.

## Data Display Rules

### 4. Long Text
- Single-line metadata must use ellipsis truncation.
- Multi-line summaries should use line clamping.
- Long tokens such as filenames, URLs, commands, CVEs, IDs, and hashes must use wrapping or truncation-safe containers.
- Tooltips may reveal truncated content, but tooltips must not be the only access path for critical data.

Use Tailwind utility classes directly — they map to the rules above:

```tsx
// Single-line ellipsis (filenames, hostnames in dense rows):
<span className="truncate">{row.filename}</span>

// Two-line clamp (note previews, descriptions):
<p className="line-clamp-2 break-words">{note.body}</p>

// Wrapping for long tokens (URLs, commands, IPs, hashes):
<code className="break-all font-mono">{port.command}</code>

// Or break-words for naturally hyphenated text:
<p className="break-words">{vuln.description}</p>
```

For backwards-compatible use in components that still take `sx` (none should, post-alpha.22), the shared utility constants in `src/utils/uiStyles.ts` (`singleLineEllipsisSx`, `twoLineClampSx`, `wrappingTokenSx`) are still exported but should be considered deprecated — prefer the Tailwind class above.

### 5. Null and Empty Values
- Never render raw `null`, `undefined`, or empty placeholders from the backend.
- Use consistent fallbacks for absent values:
  - text: `Unknown` or `—`
  - dates: `Unknown date`
  - counts: `0`
  - optional metadata: omit only if omission does not destabilize layout
- The `safeFallback(value, fallback = '—')` helper in `src/utils/uiStyles.ts` is the canonical helper.

### 6. Data Formatting
- Raw backend values should not be rendered directly if a formatter exists or should exist.
- Standardize formatting for:
  - timestamps
  - durations
  - file sizes
  - percentages
  - severity values
  - risk scores
  - hostnames and scan labels
- Formatting must be stable across pages.

## Component Rules

### 7. Cards
- Card content must not determine card width.
- Card headers must protect title, status, and actions from overlap.
- Actions must remain visible even if body content grows.
- Long metadata rows must truncate or wrap without shifting action placement.
- Summary cards should prefer stable heights over fully free-form text expansion.
- Use the v4 `<Card>` / `<CardHeader>` / `<CardContent>` / `<CardFooter>` primitives from `src/components/ui/card.tsx`.

### 8. Tables
- Tables must be designed for worst-case content, not happy-path fixtures.
- Every column must have a width strategy:
  - fixed width via `<TableHead className="w-[10%]">` or `<th style={{ width: 120 }}>`
  - min/max width via `min-w-[…]` / `max-w-[…]`
  - truncation via `<TableCell className="truncate">`
  - responsive hide/collapse via `hidden md:table-cell`
- Action columns must stay visible regardless of neighboring content length.
- Cells containing long content must not rely on default browser table sizing.
- Bulk text should not be shown fully inline in dense tables.

Two table options:

- **Static tables** (reference data, small datasets): `<Table>` from `src/components/ui/table.tsx`. Thin styling layer over native `<table>`.
- **Data-heavy tables** (Hosts, future grids): `useDataTable` + `<DataTableShell>` + `<DataTablePagination>` from `src/components/ui/data-table.tsx`. TanStack-Table-backed, supports server-paged + manual sort + row expansion + selection.

Always set `table-fixed` (`<Table className="table-fixed">`) when column behavior needs to be predictable. Explicitly constrain high-risk columns such as:
- filename
- hostname
- command line
- OS string
- notes preview

### 9. Chips, Badges, and Status Labels
- Use the v4 `<Badge>` primitive from `src/components/ui/badge.tsx`.
- Chips must not assume short labels.  Long labels must either wrap cleanly or truncate.
- Status colors and meanings must stay consistent across pages.  The `<Badge>` variant prop (`default` / `secondary` / `destructive` / `success` / `warning` / `info` / `outline` / `muted`) maps to the semantic CSS-var tokens — do not pass raw hex.
- Severity → variant mapping:
  - critical → `destructive`
  - high / medium → `warning`
  - low → `info`
  - info → `muted`
  - unknown → `outline`
- For long-label chips, combine with truncation: `<Badge className="max-w-[12rem]"><span className="truncate">{label}</span></Badge>`.

#### When to use a badge — and when not

A badge is a chromatic emphasis budget.  Every additional chip in a row drains attention from the others, so reserve them for signals that genuinely justify the visual weight.  Spend them on:

- **Categorical state**: `active` / `paused` / `failed` / `approved` / `completed`.
- **Active alerts**: `N critical` (when N > 0), `Possibly interrupted`, `In review`.
- **Interactive controls** that look like chips because they are: status pickers, follow toggles.

Do **not** badge:

- Ordinary metadata — model names, tool names, OS strings, service names, note counts, timestamps, subnet lists.  These are *identifiers*, not state — they belong in plain text (often `text-caption text-muted-foreground`).
- A count that's already in a dedicated numeric column — one datum, one place.
- "Zero of X" alerts — a `0 critical` chip on every row dilutes the rows where critical > 0 actually matters.  Render the chip only when the condition fires; let the prose carry the zero case.

#### Hierarchy inside a dense row

When a row has many fields, lead the eye in this order:

1. **Identity cell** (name, IP, filename) — semibold foreground text.
2. **One status signal** — at most one chip per row carrying the row's primary categorical state.
3. **Numeric comparison columns** — right-aligned mono digits.
4. **Secondary muted text** — caption-weight, comma- or dot-separated.

If a cell ends up holding three or more chips of similar weight, that's the cue to demote most of them to text.

#### Mobile cards

A mobile card is not the desktop row with smaller widths.  Collapse to:

- ≤ 2 chips at the top (state + alert-when-firing, or state + interactive control).
- One metadata sentence underneath (dot-separated: `12 open · 3 notes · Linux · viewed 2h ago`).
- One action row at the bottom if interactivity is needed.

Anything else gets cut.  A card whose first impression is a row of pastel chips is a card that fails to communicate priority.

### 10. Forms
- Labels, helper text, validation messages, and selected values must not break alignment.
- Validation content must wrap safely.
- Inline action rows must remain usable when labels or messages are long.
- Submit and destructive actions must retain stable placement.
- Use the v4 form primitives: `<Input>`, `<Textarea>`, `<Label>`, `<Select>`, `<Checkbox>`, `<Switch>`, `<RadioGroup>`, `<Combobox>`, `<PasswordInput>` from `src/components/ui/`.
- Always pair an `<Input>` with a `<Label htmlFor=…>` — the v4 primitives don't auto-wire `htmlFor` like MUI's TextField did.

### 11. Dialogs and Drawers
- Dialog content must not overflow horizontally due to long values.
- Dialogs with large or unpredictable content must define scroll behavior.
- Long filenames, command lines, and exported text should use scrollable containers, not unconstrained width growth.
- Two primitives:
  - `<Dialog>` from `src/components/ui/dialog.tsx` — modal, backdrop, center-screen.  Default for confirmations + form modals.
  - `<SideSheet>` from `src/components/ui/side-sheet.tsx` — `modal={false}` right-edge slide-over.  Default for master-detail surfaces (Hosts → HostInspector) where the list behind should stay scrollable.

## State Rules

### 12. Loading States
- Loading placeholders should preserve approximate final layout dimensions.
- Avoid loading states that collapse sections and then expand them dramatically.
- Use the v4 skeletons from `src/components/PageSkeleton.tsx` (`<TableSkeleton>`, `<CardListSkeleton>`, `<DetailSkeleton>`, `<ListPageSkeleton>`) for the shell-preserving pattern.
- For inline loading spinners, use `<Loader2 className="size-4 animate-spin" />` from lucide-react.

### 13. Empty States
- Empty states must not destabilize layout.
- Pages should still preserve the overall structure so controls and context remain visible.
- Empty text should be concise and action-oriented where applicable.
- Pair an icon (from lucide-react) + heading + one-line explanation + recovery action.

### 14. Error States
- Error messages must wrap safely and never push actions off-screen.
- Section-level errors are preferred over whole-page failure when partial data can still render.
- Do not silently swallow rendering failures by replacing them with misleading empty data.
- Use the v4 `<Alert variant="destructive">` primitive for inline errors.
- For toast notifications, use `useToast()` from `src/contexts/ToastContext.tsx` (wraps Sonner) — `toast.error(message)` / `toast.warning(…)` / `toast.success(…)` / `toast.info(…)`.

## Styling Rules

### 15. Shared Utilities First
- Prefer shared utilities and shared helpers over one-off fixes.
- If the same truncation or wrapping behavior appears in multiple places, move it into a reusable helper.
- Do not duplicate formatting logic across pages.

### 16. Tailwind Class Conventions
- Prefer design tokens via Tailwind utility classes that read from CSS variables: `bg-background`, `text-foreground`, `border-border`, `text-primary`, `bg-destructive`, etc. — never hard-coded hex.
- Spacing scale is fixed: `xxs` (4px) / `xs` (8px) / `sm` (12px) / `md` (16px) / `lg` (24px) / `xl` (32px) / `xxl` (48px) / `xxxl` (64px).  Use `gap-sm`, `p-md`, `space-y-xs`, etc.
- Radius scale is fixed: `rounded-control` (10px, buttons/inputs), `rounded-chip` (pill), `rounded-panel` (16px, cards), `rounded-shell` (24px, hero panels).
- Type scale is fixed: `text-page-title`, `text-section-title`, `text-subheading`, `text-body`, `text-metadata`, `text-caption`, `text-micro`.
- Avoid arbitrary width increases as a fix for overflow.
- Avoid `overflow-visible` in dense, data-driven UI.
- Use `min-w-0` on flex/grid children where truncation is expected.

Important rule for flex layouts:
- Any flex child that should shrink and truncate **must** include `min-w-0` (the equivalent of the v3-era `minWidth: 0` rule).

Example:

```tsx
<div className="flex items-center gap-xs min-w-0">
  <div className="min-w-0 flex-1">
    <span className="truncate">{row.filename}</span>
  </div>
  <Button>View</Button>
</div>
```

### 17. The `cn()` Class Composer
- Use `cn(...)` from `src/utils/cn.ts` (= `clsx` + `tailwind-merge`) to compose conditional class lists.  This gives "last conflicting Tailwind utility wins" semantics so override props work:

```tsx
<Badge className={cn('max-w-[12rem]', isCritical && 'border-destructive text-destructive')}>
  {label}
</Badge>
```

## Visual Direction

### 18. Visual Hierarchy
- The UI must make primary decisions visually obvious and secondary metadata visually quiet.
- Page titles, section titles, summary metrics, and active filters must be visually distinct from supporting details.
- Supporting metadata such as timestamps, tool names, IDs, and low-priority counts should recede through smaller type (`text-caption` / `text-metadata`), lower emphasis (`text-muted-foreground`), or quieter color.
- Do not give all elements equal visual weight.

### 19. Surface Design
- Avoid flat, undifferentiated screens where every card and section blends together.
- Use a consistent surface system for:
  - page background (`bg-background`)
  - primary panels (`bg-card border border-border rounded-panel`)
  - secondary panels (`bg-muted/30` inside a Card)
  - elevated overlays such as dialogs and popovers (Dialog / Popover primitives, `shadow-overlay`)
- Borders, shadows, and background tints should be subtle but intentional.
- Surfaces should help group information without creating visual noise.

### 20. Semantic Color Usage
- Color must communicate meaning before decoration.
- Severity colors must be consistent everywhere they appear.  Use Badge variants, not raw color classes.
- Status colors must be shared across pages and components.
- Accent colors should be used for interaction, focus, and key emphasis, not randomly across unrelated UI.
- Avoid using severity colors for generic decoration or layout chrome.
- **Never** import a `getSeverityColors(palette.mode)` helper into a v4 surface — that pattern is dead (all severity hex tones were replaced by Badge variants in alpha.11).

### 21. Typography
- Typography must distinguish technical values from descriptive copy.
- Use monospace (`font-mono`) or token-styled presentation for technical data such as:
  - IP addresses
  - ports
  - CVEs
  - filenames
  - commands
  - IDs
- Use regular UI typography for summaries, labels, and explanations.
- Dense pages should prefer strong hierarchy over simply shrinking all text.

### 22. Density and Spacing
- The app may be information-dense, but it must not feel cramped.
- Use spacing to separate:
  - summary content from raw data
  - controls from results
  - actions from metadata
- Prefer deliberate grouping over adding more borders everywhere.
- Compact layouts are acceptable; compressed layouts that reduce readability are not.
- The Button primitive's default height is 32px (h-8) — denser than shadcn's default 36px to match the pentest-console density target.  Use `size="sm"` (h-7) for inline row actions.

### 23. Motion and Interaction Polish
- Motion should support comprehension, not decorate the page.
- Use the existing animation utilities (`animate-in`, `fade-in-0`, `slide-in-from-right`, `zoom-in-95`) defined in `src/index.css` — they're tuned to a 180ms `cubic-bezier(0.2, 0, 0, 1)` baseline that matches the rest of the app.
- Avoid excessive animation, large movement, or repeated micro-animations in dense workflows.
- Motion must not delay common actions or obscure data changes.

### 24. Product Aesthetic
- Prefer a restrained "operations console" visual language over generic consumer-app styling.
- Use a neutral or muted base palette with one intentional interaction accent — the active theme's `primary` token.
- Let severity and risk indicators provide the strongest color moments.
- Avoid overly playful, glossy, or decorative patterns in analyst-facing workflows.
- Visual polish should improve scanability and confidence, not compete with the data.

## Page Construction Rules

### 25. New Data Surfaces
When adding a new field from the backend:
- define formatting
- define empty behavior (use `safeFallback()`)
- define overflow behavior (truncate / wrap / clamp / collapse)
- define responsive behavior
- decide whether it belongs inline, clamped, or in a detail surface

A new field is not complete if it only renders correctly for short fixture values.

### 26. Action Placement
- Primary actions should remain in a predictable location (header right, or footer right inside Dialogs).
- Destructive actions should remain visually distinct (`<Button variant="destructive">`) and consistently placed.
- Long content must not move action groups below the fold unless that layout is intentional.

### 27. Navigation and Filters
- Filter rows must wrap (`flex-wrap`) or stack (`flex-col sm:flex-row`) on smaller widths.
- Search, dropdowns, toggles, and sort controls must remain usable under narrow layouts.
- Filter chips must not create unbounded horizontal growth.
- For chip-style filter pickers, use `<button aria-pressed>` inside `role="group"` (matches the audit H5 fix pattern); for true selects use `<Select>`; for free-text + multi-select use `<Combobox>`.

## Review Checklist
Use this checklist in PR review and before accepting LLM-generated UI changes.

### 28. Data Stress Tests
Verify behavior with:
- a 200-character hostname
- a long filename
- a long command line
- a long OS string
- null values
- empty arrays
- partial API responses
- multiple chips/tags
- very large counts

### 29. Layout Stress Tests
Verify behavior at:
- mobile width (< 768px)
- tablet width
- standard desktop width
- zoomed browser UI if practical

Confirm:
- no page-level overflow
- no clipped actions
- no overlapping text
- no unstable card heights caused by raw values
- no pagination/filter controls pushed out of alignment

## LLM-Assisted Development Rules

### 30. Required Prompt Constraints
When asking an LLM to make UI changes, include these constraints:

```text
Follow the UI style guide (Tailwind v4 + Radix primitives + lucide-react).
- Treat all database and API values as unbounded.
- Prevent page-level horizontal overflow.
- Do not let long values resize cards, tables, chips, buttons, or action areas unpredictably.
- Add explicit truncation, wrapping, or clamping behavior where needed.
- Preserve mobile and desktop usability (CSS-only swap via md:hidden / hidden md:block).
- Handle loading, empty, and error states for new data surfaces.
- Reuse the v4 primitives from src/components/ui/ instead of building inline.
- Use semantic tokens (bg-card, text-muted-foreground, etc.) rather than raw colors.
- The change is not complete unless worst-case realistic data renders cleanly.
- Preserve the established visual hierarchy and product aesthetic.
- Use color, spacing, and typography intentionally rather than uniformly.
```

### 31. LLM Review Standard
LLM-generated changes must be reviewed for:
- hidden layout regressions
- loss of truncation behavior
- missing `min-w-0` in flex layouts
- newly introduced uncontrolled text growth
- missing state handling
- duplicated display logic
- flat or inconsistent visual hierarchy
- misuse of severity or accent color
- use of MUI imports (no `@mui/*` import should ever appear in v4 source)
- use of inline `style={{}}` for properties that have a Tailwind utility

## Definition of Done

### 32. A UI Change Is Complete Only If
- long values do not break layout
- null and empty values render safely
- loading and error states are handled
- mobile and desktop layouts are both usable
- actions remain visible and aligned
- no page-level horizontal overflow exists
- formatting is consistent with existing patterns or shared utilities
- visual hierarchy is clearer or at minimum preserved
- no MUI imports were introduced

## Stack-Specific Guidance for BlueStick

### 33. High-Risk Data Types in This App
These fields must always be treated as high-risk for overflow:
- hostnames
- IP plus port tokens
- scan filenames
- tool names
- command lines
- OS names and versions
- note previews
- parse error messages
- exported content previews
- vulnerability titles

### 34. Existing Frontend Conventions to Preserve
When editing the current frontend:
- prefer shared utilities in `frontend/src/utils`
- prefer v4 primitives in `frontend/src/components/ui/` over building new shapes
- keep page-level state handling explicit
- avoid introducing new style systems or one-off abstractions unless repeated usage justifies them
- preserve or improve the current visual hierarchy instead of flattening it

### 35. Substrate (frozen — do not relitigate)

| Concern | Choice |
|---|---|
| Styling | Tailwind v4 via `@tailwindcss/vite` |
| Primitives | Radix UI via shadcn-style copy-paste source under `src/components/ui/` |
| Class composer | `clsx` + `tailwind-merge` via `src/utils/cn.ts` |
| Variants | `class-variance-authority` (cva) |
| Toasts | `sonner` (wrapped in `useToast()`) |
| Data grid | `@tanstack/react-table` via the `DataTable` primitive |
| Command palette | `cmdk` (used inside `Combobox`; future cmd-K palette) |
| Icons | `lucide-react` (default); `AppIcons.tsx` for custom hand-rolled SVGs |
| Date picker | `react-day-picker` (installed; not yet consumed) |
| Theming | CSS variables set by `theme/cssVars.ts`, palette in `theme/palettes.ts` |

### 36. Available v4 Primitives
Every primitive lives under `src/components/ui/`:

- Surface: `Card` / `CardHeader` / `CardTitle` / `CardDescription` / `CardContent` / `CardFooter`
- Form: `Input` / `Textarea` / `Label` / `Select` / `Checkbox` / `Switch` / `RadioGroup` / `PasswordInput` / `Combobox`
- Action: `Button` / `Badge` (six variants + outline + muted)
- Feedback: `Alert` (info / success / warning / destructive / default) / `Tooltip`
- Layout: `Tabs` / `Accordion` / `Separator` / `Avatar`
- Overlay: `Dialog` / `SideSheet` / `Popover` / `DropdownMenu`
- Data: `Table` (static) / `DataTable` + `DataTableShell` + `DataTablePagination` (TanStack-backed)

### 37. Suggested Shared Utilities
These are good candidates for standardization if repeated:
- `cn(...)` — class composer (from `src/utils/cn.ts`)
- `safeFallback(value, fallback = '—')` (from `src/utils/uiStyles.ts`)
- `formatApiError(err, fallback)` (from `src/utils/apiErrors.ts`)
- `useToast()` (from `src/contexts/ToastContext.tsx`)
- `useConfirm()` (from `src/hooks/useConfirm.tsx`) — typed-name confirmation dialogs
- `projectScopedKey(name)` (from `src/utils/scopedStorage.ts`) — namespaced localStorage keys

### 38. useEffect cancellation convention (v2.42.0)
When a `useEffect` kicks off an async fetch, use a **`let cancelled = false;` flag**
plus an `if (cancelled) return;` guard before each `setState`, and clear the
flag in the cleanup return.  Don't mix this pattern with bare
`.catch(() => undefined)` in the same component — pick one.

```tsx
useEffect(() => {
  let cancelled = false;
  fetchData()
    .then((data) => {
      if (cancelled) return;
      setData(data);
    })
    .catch((err) => {
      if (cancelled) return;
      setError(formatApiError(err, 'Failed to load …'));
    });
  return () => { cancelled = true; };
}, [deps]);
```

The cancellation flag is the source of truth.  `.catch(() => undefined)` is
acceptable only as a one-off when the failure is genuinely fire-and-forget
(e.g. an analytics ping); for anything that produces visible state, the
cancelled-flag guard is the convention.

For requests that should be physically aborted (large downloads, expensive
endpoints), use `AbortController` and pass `controller.signal` to the API
client — `controller.abort()` in the cleanup.

## Final Rule
If a UI change looks correct only with fixture data, it is not finished.
