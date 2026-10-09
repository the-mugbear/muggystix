# UI Style Guide

> **Stack:** Tailwind v4 + Radix UI (shadcn-style primitives) + lucide-react + Sonner
> **Last verified against:** backend 2.450.0 / frontend 5.329.0 (2026-10-02) — MUI-free since 4.0.0; Tailwind v4 + Radix substrate

## Purpose
This guide defines UI rules for BlueStick so feature work, bug fixes, and LLM-assisted changes preserve layout integrity, readability, and predictable behavior under real application data.

This is not only a visual guide. It is a behavioral contract for how UI must respond to:
- long database values
- null or incomplete data
- asynchronous loading
- error states
- dense tables
- narrowed or zoomed desktop windows (BlueStick is desktop-only — see §3)

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
- a wide window (the app is desktop-only, but windows get narrowed, split and zoomed)
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

### 3. Viewport Behavior — desktop only
**BlueStick is a desktop-browser application.** It is an operator tool used at a
workstation alongside a terminal; there is no mobile or tablet target, and pages
are not expected to be usable on a phone.

- **Do not build mobile card fallbacks.** The `md:hidden` card list / `hidden
  md:block` table swap is a retired pattern — `Hosts.tsx` deliberately dropped
  its mobile card layout, and new pages must not reintroduce one. A dense table
  is the correct and only rendering.
- **Horizontal scroll inside a table is fine** at narrow widths. That is the
  intended degradation, not a defect to design around.
- The page **body** must still never scroll horizontally — wide content scrolls
  inside its own container. This rule is about not breaking the shell, and is
  unrelated to small-screen support.
- Layouts should stay sane when a desktop window is resized or the browser is
  zoomed, because that happens in normal use. That is the extent of the
  responsiveness requirement.

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

Use the Tailwind classes above directly. The old `sx`-style constants (`singleLineEllipsisSx`, `twoLineClampSx`, `wrappingTokenSx`, `wrappingChipSx`) were **removed in alpha.22** — `src/utils/uiStyles.ts` now exports only `safeFallback` (null/empty handling) and `stickyBelowChrome` (a sticky-offset style object).

### 5. Null and Empty Values
- Never render raw `null`, `undefined`, or empty placeholders from the backend.
- Use consistent fallbacks for absent values:
  - text: `Unknown` or `—`
  - dates: `—` (the default fallback of `formatTimestamp` / `formatDate`)
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
- **Timestamps have ONE absolute format** (v5.294.0): `formatTimestamp` ("Sep 18, 2026, 10:20 PM") and `formatDate` ("Sep 18, 2026") from `src/utils/relativeTime.ts`. Lists show `<TimeAgo>` (`components/TimeAgo.tsx`) — a relative age, with the absolute moment in the tooltip and the `<time>` element. Durations: `formatDuration` (`src/utils/scanTime.ts`).

## Component Rules

### 7. Sections, not cards
- **Data pages are sections over thin rules, not cards — dashboards included.** A card per measure turns a page into a wall of equal-weight boxes: border, shadow, title and two layers of padding around every number. The house pattern is the Posture Overview (`pages/SecurityPosture.tsx`, v5.254.0), also used by Oversight (`pages/Oversight.tsx`, v5.259.0):
  - **Page header:** `text-page-title`, one caption line saying what the page answers, actions (Refresh…) and provenance ("Updated …") at the right.
  - **Filters:** one wrapping row above everything they scope, closed by a `border-b` — never inside a card or a section.
  - **Lead:** one plain sentence of fact (or the conclusion), `border-l-4` + `text-subheading`, with what it rests on underneath — `PostureLead` (`components/posture/PostureLead.tsx`, tone critical / warning / clear / info / neutral). Every Posture page opens with one: it answers the page's question in words (Segments names the worst segment, Patterns the estate-wide weaknesses, Evidence the largest in-scope gap) before any table.
  - **Empty state** ("no scoped subnets yet", "no hosts yet"): `PostureEmpty` — icon, heading, one line, the recovery action, on a rule; not a card.
  - **Measures:** at most four quiet numbers on ONE baseline — `PostureMeasure` (`components/posture/PostureMeasure.tsx`) in a `grid lg:grid-cols-4 lg:divide-x`: label + (i) `InfoTip`, value, one or two caption lines. No icon, meter or border per number; a number that can link, links (§9).
  - **Sections:** `PostureSection` (`components/posture/PostureSection.tsx`): a sentence-case heading (subheading size, foreground colour) after a short primary accent bar — the heading is what separates sections, v5.269.0 — with an (i) `InfoTip` passed inside `title` when needed and a quiet `SectionCount` for a count or summary, one description line, right-aligned actions or a segmented control, over a `border-b`; the content keeps the full width. Nothing collapses — these pages are read top to bottom.
  - **Explanations** go on an explicit (i) `InfoTip`, never on hover alone.
- On a DETAIL surface (the host inspector, a finding, an agent session) use `InspectorSection` (`components/host-inspector/InspectorSection.tsx`): the same heading-over-divider shape, collapsible, its state remembered per viewer. Giving every data source its own Card meant a host with two ports and two observations needed three screens, most of it chrome. `openInspectorSection` / `jumpToInspectorSection` re-open a collapsed target, so a jump link is never dead.
- **A `<Card>` is the exception**: a self-contained object in a grid of like objects (a project tile), a form panel, or an empty/error state that must stand apart. Never a card per metric.
- **Discussions are conversations** (v5.264.0): finding comments, host notes and a finding's source-note thread render as `MessageBubble`s (`components/MessageBubble.tsx`) — the viewer's messages on the RIGHT ("You"), everyone else's on the LEFT (the phone convention, v5.268.0), oldest first, text left-aligned inside a tinted bubble, author/time above and actions below on the same side. A reply quotes what it answers ("Replying to Ana: …") rather than indenting; never wrap a thread in a Card.
- **A repeated evidence row is ONE line** — identity, one status, dot-separated metadata — and expands only on demand (scanner observations, port sightings, earlier observations of a web interface). A 300–400 px row per observation pushes everything else off the screen.
- **A work row may carry a second quiet line for the one thing the reader copies** (v5.322.0) — a host test's command, mono, truncated, with a copy button. That is the limit: two lines closed, everything else on demand. A row that needs a person's decision (a test whose result showed an issue and has no finding yet) opens by itself and stays in the to-do list; a finished row otherwise stays closed.
- **One object shown in two places is loaded once** (v5.322.0): a test appears on the weakness it confirms and in the Tests list, both reading one controller (`host-inspector/hostTestsController.tsx`), so an action in either shows in both. Never fetch the same list per section, and never show the same record in two sections of one page (the host's Evidence section lists only what answers no test).
- **A form that needs reference material in view opens beside the page, not inside the row** (v5.322.0): recording a test result is a `SideSheet` holding the command, what counts as a finding and the weakness, with room to paste output. An inline form is for one or two fields (a dismissal reason); anything the analyst fills while reading something else gets the panel.
- **An object that was just made opens where its next step is** (v5.323.0): a finding with nothing written opens in its editor, and an empty section of a partly written one is a link into the editor at that section. Never a page of "Not written yet" behind an Edit button. Once written, the page reads first.
- **A button never reveals a second button with the same label.** If the first click only uncovers a form, show the form. A button that opens a dialog ends in "…". Two forms on one page never both say "Save".
- **A create action offers its follow-up in the toast and stays on the page** ("Write it up" after a promotion): the analyst working down a host must not be moved off it.
- **One control per hand-off to the agent** (v5.322.0): a section has a single "Ask agent" menu (`hooks/useAgentTask`), not a button per task. With a live session the task is copied and a toast says so; the Start Agent Session dialog opens only when there is none.
- **Whatever an agent can be asked to write, a person can write** (v5.324.0): beside an "Ask agent" control sits the plain action ("Add test"). An analyst with no agent session must never be left without a way to record their own work.
- **A status filter over a list already on the page is a segmented control with counts**, filtered in the browser — not a dropdown that asks the server again and hides how much is behind each choice.
- **A preview of a longer list has ONE way to the rest** (5.329.0, the Operations review): "x of N", then "Open all N in Hosts" (or Findings) linking the EXACT list — the count on the link is the length of the list it opens, pinned by a test against the server's predicate. "Show N more" in place is only for a list no page can express (tests across hosts, a tier with no query), never beside an "Open all". If the only link available opens a wider set, its label says it is wider ("All 251 untouched hosts in Hosts, with or without a reason" under a queue of 112 — "Open all 251 untouched hosts" was read as that queue), never the preview's count. A personal work page does not preview at all — see §42.
- **A work queue is a table of one-line rows** (5.329.0): identity, the reason in words, provenance, the action — `table-fixed`, every text cell truncating with its full value on `title`. What is true of every row by the queue's definition is said once under the heading, never on each row. A queue of near-identical rows gets a selection column and its actions in bulk (`useRowSelection`, `BulkBar`): one request when the server has a bulk route, otherwise `utils/runLimited`; a partial failure says what moved, what did not and why. When two such lists share a page, one of them owns the keyboard at a time — the one the reader last pointed at or focused (Operations shows one list at a time since 5.331.0, §42: the list on screen owns the keys).
- **A personal page carries nothing about the project as a whole** (5.330.0, owner 2026-10-02): Operations is the signed-in person's page — their queue, the reviews THEY finished that changed, what to pick up next, their own agent sessions — and has no measures strip: its two counts are in the lead sentence and the section headings. A number, list or map that describes the project (tested x of y, untouched critical exposure, where the team has been, scanner observations by severity, the scope states) is project status and belongs on the Posture overview, as a `PostureSection`. A measure that only restates what a heading already says, or that the reader cannot act on from that page, is visual vanity: remove it rather than move it. A list on a personal page is filtered to the person on the SERVER, and the link to "all N" opens that same person's list (`follow:revisit`, `follow:mine`) — never the team-wide query.
- **A filter over tiers or categories is a row of chips with counts, in the stated order** — never bars scaled to the largest (the tier to act on first then gets the smallest mark), and never a status or severity colour for something that is neither. Emphasis is order and weight.
- **A priority is not a severity.** A test's priority, a tier, a rank: an `outline` badge that says what it is ("critical priority"). The `severity-*` variants are for a finding's or a scanner observation's severity only. (Operations follows this since 5.329.0; the host inspector's test rows — `HostTestsSection` `PRIORITY_VARIANT` — still wear the severity ramp and move when that section is next reworked.)
- Card content must not determine card width.
- Card headers must protect title, status, and actions from overlap.
- Actions must remain visible even if body content grows.
- Long metadata rows must truncate or wrap without shifting action placement.
- Summary cards should prefer stable heights over fully free-form text expansion.
- Use the v4 `<Card>` / `<CardHeader>` / `<CardContent>` / `<CardFooter>` primitives from `src/components/ui/card.tsx`.

#### Charts
Charts with real axes are drawn with Observable Plot (§35, adopted 5.307.0; reference `components/oversight/GrowthCharts.tsx`); small inline visuals stay hand-built SVG inside a section. The one 3D surface, the address terrain ("Where the team has been", a section of the Posture overview since 5.330.0 — it was on Operations), uses three.js under the same rules: colours from theme tokens (`utils/terrainPalette.ts`, validator-checked on every theme), every value in a table view, every count a link. References: `components/posture/FocusComparison.tsx` (ranked rows with inline bars), `components/oversight/JudgmentBySeverity.tsx` (part-to-whole per row), `components/oversight/GrowthCharts.tsx` (small multiples over time). Plot charts render through `components/charts/PlotFigure.tsx` (memoised options; it reports the x scale's `invert` so a chart can drive one shared crosshair) — users: `GrowthCharts`, `ActivityHistogram`.
- **Pick the form first; sometimes it is not a chart.** A single number is a measure (§7), a handful of exact values is a table. Draw only what a table cannot show (a gap, a trend, a rank).
- **One y-axis per plot.** Two measures of different scale (a running total and per-day counts) are two charts on a shared x-axis — never a dual-axis plot.
- **Colour follows meaning.** Severity colours only for severity (`utils/severity.ts`); status tokens only for status; one accent (`--info`) for a single series, or accent + recessive grey when one part is the point (emphasis). Check any colour pair against every theme (light, dark, phosphor, magma, absolute-zero) with the dataviz palette validator before shipping.
- **Every value is readable without hovering**: direct labels (the end value, the gap), plus a table view where the series is long. Hover and keyboard (arrow keys) move one crosshair and a readout; tooltips enhance, never gate.
- **Marks are thin**: 2px lines, ≤ 24px columns with a 2px gap, 4px rounded data-ends, hairline grid; text wears text tokens, never the series colour.

### 8. Tables
- Tables must be designed for worst-case content, not happy-path fixtures.
- Every column must have a width strategy:
  - fixed width via `<TableHead className="w-[10%]">` or `<th style={{ width: 120 }}>`
  - min/max width via `min-w-[…]` / `max-w-[…]`
  - truncation via `<TableCell className="truncate">`
  - collapse behind a detail surface when a column is low-value
  - in a `DataTableShell` table: a pixel `size`, a share of the table through `meta: { width: '21%' }` (`columnWidth`; the Hosts list — spare width goes to the columns that would otherwise wrap, not to one column without bound), or unsized (no `size`: the column takes what the others leave). A `size` is exactly what it says — 150 included; there is no number that means "unsized"
- Fixed widths must never be able to add up to the whole content width: the one unsized column would get 0 px (Agent Sessions' SESSION column did at a 1,126 px window). Give the table a minimum width inside an `overflow-x-auto` wrapper so that column keeps a floor.
- A row's key column (a finding's title, Operations' NEEDS) wraps to two lines (`line-clamp-2 break-words`, title kept) rather than truncating; an address never wraps (`whitespace-nowrap`) — the tag beside it wraps instead.
- A measure's caption wraps; a link inside it is `whitespace-nowrap` and is never cut off.
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
- Status colors and meanings must stay consistent across pages.  The `<Badge>` variant prop (`default` / `secondary` / `destructive` / `success` / `warning` / `info` / `outline` / `muted`) maps to the semantic CSS-var tokens — do not pass raw hex. There are **17** variants: those eight semantic ones, five `severity-critical|high|medium|low|info` on the theme's own severity ramp (`--sev-*`, 5.304.0 — not the semantic tokens), and four lighter `destructive|warning|info|success-outline` chips that keep the semantic colour (they replaced ~19 sites of bespoke `border-warning/40 text-warning` class soup).
- Severity → variant mapping: **import `SEVERITY_BADGE_VARIANT` from `src/utils/severity.ts`** — critical → `severity-critical`, high → `severity-high`, medium → `severity-medium`, low → `severity-low`, info → `severity-info`. For a severity chip, render `<SeverityBadge severity=…>` (`components/ui/SeverityBadge.tsx`, v5.288.0), which takes both colour and label from `severity.ts`. Do not re-derive it per page; `severity.ts` is the one source for severity order, label, colour and variant, created precisely because pages had grown their own maps.
- **Every count is a link.** A number shown to an operator navigates to the rows it summarises, or acts as the filter for them. If it cannot, cut it — an inert stat card is a vanity metric.
- For long-label chips, combine with truncation: `<Badge className="max-w-[12rem]"><span className="truncate">{label}</span></Badge>`.

#### When to use a badge — and when not

A badge is a chromatic emphasis budget.  Every additional chip in a row drains attention from the others, so reserve them for signals that genuinely justify the visual weight.  Spend them on:

- **Categorical state**: `active` / `paused` / `failed` / `archived` / `completed`.
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

#### Card surfaces

Cards are the exception (§7), never a small-screen fallback for tables (see §3 —
there is no mobile target) and never a container for a single metric. Where a
card is the right surface, the same density discipline applies:

- ≤ 2 chips at the top (state + alert-when-firing, or state + interactive control).
- One metadata sentence underneath (dot-separated: `12 open · 3 notes · Linux · viewed 2h ago`).
- One action row at the bottom if interactivity is needed.

Anything else gets cut.  A card whose first impression is a row of pastel chips is a card that fails to communicate priority.

### 10. Forms
- Labels, helper text, validation messages, and selected values must not break alignment.
- Validation content must wrap safely.
- Inline action rows must remain usable when labels or messages are long.
- Submit and destructive actions must retain stable placement.
- Use the v4 form primitives: `<Input>`, `<Textarea>`, `<Label>`, `<Select>`, `<Checkbox>`, `<Switch>`, `<Combobox>`, `<PasswordInput>` from `src/components/ui/`.
- Always pair an `<Input>` with a `<Label htmlFor=…>` — `<Input>` does not generate an id, so always pass `id` and a matching `htmlFor`.
- A `<Switch>` or `<Checkbox>` has no text of its own: name it with a `<Label htmlFor>` for its `id`, a `<label>` wrapped around it, or `aria-label` (a row's tick). The wrappers warn in the development console when one is mounted with no name (`components/ui/accessible-name.ts`).

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
- **Unavailable is not empty.** A section that failed to load, or has not loaded yet, renders as *unavailable* (say what could not be checked, offer a retry) — never as a zero or an empty list. "Nothing needs your approval" is a claim about the data; a failed request has not earned it. The API follows the same rule (`*_unavailable` flags on the workbench), so render them.
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
- **A token that carries its own alpha is never wrapped with `/ <alpha-value>`** (5.327.0). `--muted`, `--accent`, `--border` and `--sidebar-accent` are written as "H S% L% / A" (`theme/cssVars.ts` `toHslComponentsWithAlpha`), so in `tailwind.config.ts` they are plain `hsl(var(--x))`; every other colour token is `hsl(var(--x) / <alpha-value>)`. Wrapped, the four compiled to `hsl(… / A / 1)`, which is invalid CSS: hover, selected-row and muted fills painted nothing and `border-border` fell back to the text colour. A `/NN` modifier still works on them (`bg-accent/45` is 45% of the token). A new token with its own alpha follows the same rule; `tests/themeAlphaTokens.test.ts` derives the list from the theme source and fails on a wrapped one. A highlight that must show in every theme uses opaque tokens (`LIST_CURSOR_CLASS` in `hooks/useListCursor.ts` is `bg-primary/10` plus a `ring-ring` ring).
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
- Use `cn(...)` from `src/utils/cn.ts` (= `clsx` + `tailwind-merge`, extended with the named spacing scale and the type scale, so `className="p-sm"` overrides a primitive's `p-md` and `text-caption` does not drop a text colour) to compose conditional class lists.  This gives "last conflicting Tailwind utility wins" semantics so override props work:

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
- Avoid flat, undifferentiated screens where every block has the same weight. Group with headings, thin rules and whitespace first (§7); reach for a panel only when a block is genuinely a separate object.
- Use a consistent surface system for:
  - page background (`bg-background`)
  - section rules (`border-b border-border` under a `PostureSection` / `InspectorSection` heading)
  - primary panels, where a panel is warranted (`bg-card border border-border rounded-panel`)
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
- The Button primitive's default is `size="md"` — 40px (h-10). Use `size="sm"` (h-8, 32px) for inline row actions and dense toolbars; `lg` is 44px (h-11) and `icon` is 40×40. In a one-line row, 28px (`h-7`) icon buttons are set with a class, not a size.

### 23. Motion and Interaction Polish
- Motion should support comprehension, not decorate the page.
- Use the existing animation utilities (`animate-in`, `fade-in-0`, `slide-in-from-right`, `zoom-in-95`) defined in `src/index.css` — they run 180ms (220ms in / 200ms out for the `SideSheet` edge slides) with the default easing. `cubic-bezier(0.2, 0, 0, 1)` is the transition token (`motion.standard` in `theme/tokens.ts`), for CSS transitions.
- Avoid excessive animation, large movement, or repeated micro-animations in dense workflows.
- Motion must not delay common actions or obscure data changes.
- A notice that changes what the reader is looking at without their asking (the Hosts list's "Project default view applied" banner) may draw the eye ONCE as it appears: `attention-once` (`src/index.css`, two soft rings in the warning colour, 1.8 s, never looping, removed under reduced motion), with a solid coloured edge that stays. Do not use it for ordinary status or success messages.

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
- decide whether it belongs inline, clamped, or in a detail surface

A new field is not complete if it only renders correctly for short fixture values.

### 26. Action Placement
- Primary actions should remain in a predictable location (header right, or footer right inside Dialogs).
- Destructive actions should remain visually distinct (`<Button variant="destructive">`) and consistently placed.
- Long content must not move action groups below the fold unless that layout is intentional.

### 27. Navigation and Filters
- List pages use `ListFilterBar` / `ListFilterSearch` (`components/ListFilterBar.tsx`, v5.294.0): one wrapping row over a `border-b`, unlabelled `h-8` select triggers (`FILTER_TRIGGER_CLASS`) whose first option names the dimension ("All statuses") and which carry an `aria-label`, and a right-aligned count of what is listed. Hosts keeps its query language on its own row at the same control height.
- Filter rows must wrap (`flex-wrap`). Do not add breakpoint-stacked variants (`flex-col sm:flex-row`) — that is the mobile pattern §3 retired. Give toolbar controls a fixed width: a bare `SelectTrigger` is `w-full` and will stack the row.
- Search, dropdowns, toggles, and sort controls must remain usable under narrow layouts.
- **A menu is never taller than the window** (v5.346.0): `DropdownMenuContent` is bounded by the room Radix measures beside its trigger (`--radix-dropdown-menu-content-available-height`) and scrolls inside itself. A menu fed by data (the project selector) is a list of any length — never give its content `overflow-hidden` without a scrolling list inside it.
- **The project selector filters by year** (v5.347.0) once the projects span more than one: a row of years (newest first, then All) pinned above the scrolling list, as `menuitemradio`s so the arrow keys reach them, chosen without closing the menu. A project's year is the year it STARTS (`utils/projectYears.projectYear`: the stored start date's own year, else the year it was created — never a browser-time-zone conversion). The menu opens on the current project's year, so the project the reader is in is always listed; a pick lasts while the menu is open. The menu is as wide as its longest name — from the trigger's width up to 28rem — and a name longer than that is cut with the whole of it on `title`, as is the trigger's.
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
- standard desktop width
- a narrowed desktop window (the table scrolls horizontally; the shell does not)
- zoomed browser UI if practical

Mobile and tablet widths are **not** targets — see §3.

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
- Target desktop browsers only — no mobile card fallbacks (see §3).
- Handle loading, empty, and error states for new data surfaces. A failed or
  unloaded section is UNAVAILABLE, never rendered as empty or zero.
- Data pages use PostureSection / PostureMeasure (heading + rule, one strip of
  quiet measures); detail surfaces use InspectorSection. Never a Card per metric
  or per data source; a repeated evidence row is one line that expands on demand.
- Charts with axes use Observable Plot (§35); inline visuals are hand-built SVG. Either way: one y-axis per plot (small multiples, never dual
  axes), every value also reachable as a direct label or a table view, colours
  from theme tokens checked with the dataviz palette validator.
- Every count navigates to, or filters to, the rows it summarises. No inert stat cards.
- Severity colours and badge variants come from src/utils/severity.ts.
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
- the layout holds up when the desktop window is narrowed or zoomed
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
| Command palette | `cmdk` — `src/components/CommandPalette.tsx` (shipped) and `Combobox` |
| Icons | `lucide-react` (default); `AppIcons.tsx` for custom hand-rolled SVGs |
| Dates | no date library: `Intl` via `formatTimestamp` / `formatDate` / `formatRelativeTime` (`src/utils/relativeTime.ts`) and `<TimeAgo>`; there is no date-picker dependency (`react-day-picker` was removed unused in 5.247.1) |
| Graphs | **Observable Plot** (`@observablehq/plot`, pinned exact) — adopted 5.307.0 after a trial on Oversight's Host growth (`components/oversight/GrowthCharts.tsx`, the reference). The old "no chart library" rule was born of chart.js's dated, hard-to-read output; Plot is a grammar of graphics that draws plain SVG under the §7 rules: colours from theme tokens (`hsl(var(--info))`), `currentColor` axes, direct end labels, one y-axis per plot, a table view. Render it through `components/charts/PlotFigure.tsx`, imported from the chart's own component so Plot stays in that page's lazy chunk. Small inline visuals (`ui/SeverityBar.tsx`, meters, part-to-whole rows) stay hand-rolled — reach for Plot when a chart needs real axes, time scales or intervals. `reactflow` was removed with the Topology page in 5.285.0. |
| 3D | `three` (pinned exact) for the address terrain only — a section of the Posture overview since 5.330.0 (`components/operations/TerrainScene.tsx`, 5.306.0; the files keep their folder) — chosen by the owner. It is loaded in its own chunk by `components/operations/AddressTerrainSection.tsx` only when the reader opens the map ("Show the map", remembered per viewer; 5.329.0 — the section's sentence and its hottest block are always shown without it), draws on demand (idle = no frames), and every number it shows is also in the section's Table view. Not a chart library: 2D charts with axes use Observable Plot; small inline visuals stay hand-built SVG. |
| File drop | `react-dropzone` (`components/scans/UploadReviewDialog.tsx`, `pages/Scopes.tsx`) |
| Theming | CSS variables set by `theme/cssVars.ts`, palette in `theme/palettes.ts` |

### 36. Available v4 Primitives
Every primitive lives under `src/components/ui/`:

- Surface: `Card` / `CardHeader` / `CardTitle` / `CardDescription` / `CardContent` / `CardFooter`
- Form: `Input` / `Textarea` / `Label` / `Select` / `Checkbox` / `Switch` / `PasswordInput` / `Combobox` / `CharacterCount`
- Action: `Button` / `Badge` (17 variants — see §9) / `SeverityBadge`
- Feedback: `Alert` (info / success / warning / destructive / default) / `Tooltip` / `InfoTip` / `InlineLoader`
- Layout: `Tabs` / `Accordion` / `Separator` / `Avatar`
- Overlay: `Dialog` / `ConfirmDialog` (`src/components/ConfirmDialog.tsx`, always via `useConfirm`) / `SideSheet` / `Popover` / `DropdownMenu`
- Data: `Table` (static) / `DataTable` + `DataTableShell` + `DataTablePagination` (TanStack-backed) / `CodeBlock` / `SeverityBar` / `BreakableName`

### 37. Suggested Shared Utilities
These are good candidates for standardization if repeated:
- `cn(...)` — class composer (from `src/utils/cn.ts`)
- `safeFallback(value, fallback = '—')` (from `src/utils/uiStyles.ts`)
- `formatApiError(err, fallback)` (from `src/utils/apiErrors.ts`)
- `useToast()` (from `src/contexts/ToastContext.tsx`)
- `useConfirm()` (from `src/hooks/useConfirm.tsx`) — typed-name confirmation dialogs
- `projectScopedKey(name)` (from `src/utils/scopedStorage.ts`) — namespaced localStorage keys
- `formatTimestamp` / `formatDate` (from `src/utils/relativeTime.ts`) — the one absolute date format (§6)
- `<TimeAgo>` (from `src/components/TimeAgo.tsx`) — a relative age in lists, the exact time on hover
- `<RunKindBadge>` (from `src/components/RunKindBadge.tsx`) — one run-kind badge wherever agent runs are listed
- `ListFilterBar` / `ListFilterSearch` (from `src/components/ListFilterBar.tsx`) — the shared filter row (§27)

### 38. An effect never fetches (2026-10-09; was the `cancelled`-flag convention)
A `useEffect` does not call the API.  Reading from the server is a query and
writing to it is a mutation (§48); the `let cancelled = false` flag, the
generation counter and the hand-made `AbortController` that an effect-fetch
needed are gone with it, and the lint rule refuses a new one.  Pass the
query's `signal` to an API function that takes one — the library cancels a
read nobody is waiting for.

An effect is still right for what is not server state: a subscription, a
timer, focus, the address bar.  The Rules of Hooks and effect dependencies
are lint rules (`npm run lint`, part of the gate, which allows no warning).

"Only while the reader is still here" after a write is said with the callback
given to `mutate(vars, { onSuccess })` (it does not run once the component is
gone); "wherever the reader is now" (a toast, an invalidation) goes in the
`useMutation` options.

### 39. List pages fetch with `useListQuery` (2026-10-01)
Every read is a query (§48).  A list with "Show more" — rows that refetch
when a filter or sort changes — uses `hooks/useListQuery.ts`, a thin helper
on `useInfiniteQuery`.  Do not hand-roll `loading` / `error` / `rows` state.

```tsx
const list = useListQuery(
  'listThings',                   // the API function the fetcher calls: the key's name
  ({ offset, limit, signal }) => listThings({ status, offset, limit }, signal),
  [status],                       // plain values: with the name, the query key
  { pageSize: 50, poll: 60_000 }, // poll is optional; `global: true` for a list that is not one project's
);
// list.rows (null until loaded) · list.total · list.loading · list.error
// list.reload() after a change · list.loadMore() for "Show more"
```

The same on every list:

- **The rows are this filter's or none.**  The filter is part of the query
  key, so an answer for an earlier filter cannot be shown under the current
  one; `rows` is `null` until the current one has loaded.
- **The background never takes what the reader asked for.**  A "Show more"
  asked for during a re-read waits for it and appends after the fresh rows.
- **A failed load is an `error`, never an empty list.**  A failed reload
  keeps the rows that were shown.
- **A reload keeps what "Show more" had loaded** (it re-reads each loaded
  page).

**A list that polls or reloads anchors its keyboard cursor by id.**  Rows move
under an index: pass `useListCursor(count, onOpen, { getId })` the id of row `index`
and the cursor follows its row through a reload (`cursorId`), instead of
landing an `a` / `r` on whatever slid into that position.  Proposals,
Findings, Names, Collaboration and Scanner observations do.

**Single-letter shortcuts ask `utils/keyboard.isPageShortcutEvent`** before
they act — never a private "is the user typing" check.  It refuses a key
with a modifier, an auto-repeat (`allowRepeat` for cursor movement), a text
field, a Select trigger or an open list / menu (their typeahead owns the
letters) and an open dialog (`allowDialog` for a surface that is one).

Proposals, Feedback, the scope's names, a session's tests and the scanner
observations use it.  There is no other guard to choose from: a hand-made
generation counter, a `cancelled` flag or an `AbortController` around a fetch
is the old mechanism and is not written again (§48).

A record's data is keyed by the record (`['getHost', hostId]`), so a late
answer for the previous record has no query to land in — on a detail page and
in a panel that stays open across records alike.  A panel that shows one
record is still **keyed by the record** (`<Body key={hostId} …/>` — the host
inspector, `HostFindingsCard`, the standalone `HostTestsSection`, the
remediation timeline's body) for its LOCAL state: an open dialog, a draft, a
selection must not follow the reader to another host.  Do not write
per-request "is this still the host on screen?" checks.

**A paged list keeps its page in the address.**  Use `usePagedList('apiFn',
fetchPage, deps, …)` (on `useQuery`) with the
page from `hooks/useUrlPage` (`{ pageSize, page: useUrlPage() }`): `?page=`,
1-based and left out for the first page, replaced (not pushed) when the
reader pages.  New deps — a filter, a tab — start from page 1, and a link to
a tab or a filter never carries `page`.  Remediation, Remediation deadlines,
the Operations tabs and Names do.

**The Hosts page derives its state from the address.**  Filters, sort and
page are read from the URL on every render, never copied into component
state and synced back; `sessionStorage` only seeds a bare `/hosts` (the
reader's last filters), and a link within the page is
`navigate(buildHostsUrl(...))`.  Keep one copy of a filter — the address —
so Back, a reload and a shared link all show the same list.

### 40. Controls follow the project role (2026-10-01)
"May this person do that here" is answered by `hooks/useProjectRole.ts`:
`canWrite` (project analyst and above), `canExport` (auditor and above),
`isProjectAdmin`, `isGlobalAdmin`.  The account role is binary (admin /
member), so `hasPermission('analyst')` is true for every member and must not
gate anything; keep `hasPermission('admin')` for instance-wide surfaces
(users, system settings, audit log, Oversight).  A control the SERVER gives
to a project admin follows `isProjectAdmin` — never the account role: the
Hosts page's "set / clear the project default view" was hidden from project
admins for that reason until 5.351.1.

**One set of member rules, on every screen that manages members**
(`utils/projectMembers.ts`): the role list, and what is confirmed or refused
before a change is sent — the only project admin cannot be removed (the
server refuses it); demoting the only admin, changing your own role and
removing yourself are confirmed, in the same words on Project settings, the
Portfolio members sheet and the administrators' memberships dialog.

- **Hidden, not disabled.**  A control the caller's role cannot use is not
  rendered.  A viewer or auditor gets a read-only page — rows, counts, links,
  and exports where the role allows — without add rows, editors, selection
  checkboxes or action columns.
- **No dead-end copy.**  An empty state or hint must not tell a reader to use
  a control they do not have.
- **An unknown role shows the control.**  Until the project role has loaded
  the server decides; only a role known to be too low hides a control.
- **A page follows the server's READ rule, a control its WRITE rule.**  When
  the server lets a role read a page's data, the page stays in the nav and
  reachable for that role, read-only — route and nav entry are `viewer` for
  Scope (every member reads it, analysts change it), Project settings (every
  member reads the project's details and tags) and Scanner Integrations
  (every member reads them, global admins change them).  A page leaves the
  nav, and its route refuses, only when the server refuses that role the
  read as well (Ingestion Results: its GETs need analyst; Reports — `/reports`
  and `/reports/:id` — every client-report route needs auditor).  Never hide a
  readable page because its writes are out of reach.  Proposals and Agent
  Sessions are every member's pages: the server gives any member those reads
  (deciding a proposal is analyst, starting a session is auditor).
- **Getting data out is auditor.**  Every export and report route is AUDITOR
  on the server (`/export`, `/reports`, `/client-reports`, `/hosts/tool-ready`,
  `/names/export`), so their controls follow `canExport`: the Hosts page's
  "Export targets" / "Download inventory" (and the `?reports=1` link, which
  opens that dialog, and it reads `/reports/jobs` at once), "Create briefing" on Posture,
  Patterns and a Segments site, the Scope and Names exports.  A copy or
  download of what the page already shows (Patterns' "Copy summary" / JSON)
  is not an export.
- **A SECTION whose read the server restricts is hidden for that role**, not
  shown failing: Project settings' outbound webhooks and webhook deliveries
  are read by project admins only, so nobody else is offered them (they used
  to answer "Failed to load webhooks").
- **`requiredRole` names two different roles.**  On a route or nav entry,
  `analyst` / `auditor` mean the project role (`useRoleGate`), `admin` means
  the account role, and `viewer` means any signed-in account.  The route in
  `App.tsx` and the entry in `config/navigation.tsx` must agree
  (`tests/navigation.test.ts`).
- **A tab shows the account whose token it sends.**  The token is in
  localStorage, which every tab shares; the account and project a tab shows
  are in its memory.  When another tab signs in as someone else (or signs
  out), `AuthProvider` reloads this one (`utils/authSession`) — otherwise it
  keeps the first account's project on screen and polls it with the second
  account's token (403 "Not a member of this project"), and a change made
  there would be recorded as the other person's.

### 41. A selected row looks selected (2026-10-01)
- A row whose checkbox is ticked carries `data-state="selected"` (the fill
  `TableRow` and `DataTableShell` paint) and `aria-selected`; a table with no
  selection states neither.  `DataTableShell` does this by itself for a table
  built with `selectionColumn()`; a hand-built table sets both on its row.
- A row that is selected AND under the keyboard cursor keeps both marks: the
  selected fill, and the cursor's ring (`LIST_CURSOR_CLASS`) over it.
- A "select all" box has three states — `utils/selection.selectAllState`:
  empty for none, the tick for all, a **dash** (`checked="indeterminate"`)
  for some.  `Checkbox` draws the dash; never give "some" the tick or the
  empty box.  Its name stays the same in every state ("Select all rows on
  this page"): the state is announced beside it (checked / mixed), and a
  click on "some" selects the rest.
- A list keeps its own order across responses (the finding's endpoints: by
  address — numerically, `utils/ipAddress.compareAddresses` — then name).  A
  row must not move because the server answered a change with it last.

### 42. A personal work page is tabs: one full list at a time (2026-10-02)
A page that holds several lists of the reader's own work (Operations) does not
stack them.  Six lists at one visual weight, each a five-row sample with its
own "more", in two row styles, were "hard to parse and follow" on a large
project, and the same host could appear three times.  The shape is:

    title row → lead sentence → callouts (only when there is something)
    → tab bar with counts → the ONE selected list as a full table → foot line

- **A tab bar with counts** (`components/ui/tabs`, Radix: arrow keys move
  between tabs).  A count that is still loading reads "…"; one that could not
  be checked reads "—" — never 0 — and the tab's panel then says "could not be
  checked", never an empty list.  When a tab LISTS more than it counts (Tests
  lists claimable tests, which are not the reader's), the label shows the
  second number separately ("Tests 40 + 15 to claim") and the panel says which
  number is which.
- **One list on screen, complete, paged.**  No samples, no "Show N more".  One
  footer on every tab (`QueueParts.PagedFooter`): "1–10 of N", previous / next,
  10 rows a page (owner, 2026-10-02: 25 still read as overwhelming).  It carries ONE link to another page, and only when that
  page lists EXACTLY the tab's list ("Open all N in Hosts"); a link to a wider
  list says so in its label, and a tab with no exact list has no link.
- **Counts and rows are separate requests.**  The counts come from one light
  call; a tab's rows are fetched when the tab is opened, through
  `hooks/usePagedList` (§39).  Each list route is the
  function that produced the tab's count, and a backend test pins count ==
  the list paged through, with fixtures larger than a page.
- **The tab is in the URL** (`?tab=`, plus the tab's own filters), a click is
  a history entry, and nothing is remembered in localStorage.  With no `?tab=`
  the first NON-EMPTY tab in bar order opens — decided once per visit, so
  finishing a list does not move the reader.  The lead's numbers are links
  that open a tab and its filter (`?kind=`, `?need=`), not `#anchors`.
- **The lead never adds unlike work into one total** (owner, 2026-10-02 —
  "101 items in your queue" summed decisions, report writing and tests the
  reader was never assigned).  It says the kinds apart, in the order to act:
  what needs a decision, what needs writing, what is assigned; then what the
  reader holds in review; then what can be picked up.  A filter change starts
  at page 1 (`usePagedList`).
- **Every tab is a table** (§8): labelled headers — the age column included —
  explicit widths, one line per row, the full value on `title`.  The tab is
  the list's heading; the panel repeats none.
- **Three states in the panel, the tab bar staying put** (`QueueParts.ListBody`):
  a skeleton while the list loads; "Could not be checked … This is not an
  empty list" with Retry when it failed; and one line when it is empty —
  "Nothing here — …" saying what would put something here.
- Write controls follow §40; selection follows §41; a row cursor is
  `useListCursor({getId})` and belongs to the list on screen.

### 43. A proposed change is reviewed where it applies, against what it replaces (2026-10-02)
A walkthrough of a finding with drafts: the current text sat in a closed
"Current text" toggle under each draft, in caption grey, read after the draft;
the drafts were listed a screen above the Report text they change, and endpoint
changes under the table rather than on their row. Reviewers "could not compare",
and the page was "confusing". The rules:

- **The change sits on the thing it changes.** A report-text draft is reviewed
  inside its section (`FieldDraftsReview`); an endpoint change on its row
  (`FindingEndpoints`). A summary at the top of the page says what waits and
  goes there; only a proposal with no home on the page is decided in the summary.
- **The current value is always visible, shown once, and read first.** "Now in
  the report" on the left, "Proposed" on the right (`TextComparison`), or one
  pane with the words added and removed marked ("Changes"). Never behind a
  toggle, never repeated per draft. An empty field keeps the same two panes: the
  left says nothing is written yet and that accepting fills it (5.334.5 — the
  proposal alone, full width, made a finding's written and empty sections look
  laid out differently). **Every draft on a page sits in the same layout.**
- **Say when the base moved.** A proposal that replaces a whole value and was
  written against an older one says so before its controls
  (`changed_since_proposed`), with the text it was written against.
- **Several drafts are lettered** (Draft A, B…, oldest first, so the letters stay
  put) and switched as tabs; their source lines alone cannot tell them apart, so
  a tab names its model only when no other draft shares it, else its opening words.
- **Show the from and the to together.** An endpoint row's proposal reads
  "Still present → Retest here": the state column is a table's width away.
- **A jump lands below the chrome.** A target scrolled to with `block: 'start'`
  takes `uiStyles.scrollBelowChrome(extra)` (the layout's `--topbar-h` +
  `--secondary-nav-h`), never a fixed `scroll-mt-*` shorter than the header.
- **A field with drafts waiting** says so instead of "Not written yet", never
  opens an empty editor by itself, and is not drafted again.
- **One decision path** (`hooks/useProposalDecision`): every place a proposal is
  decided uses it, and "Accept and edit" can start from the draft or from the
  current text.

### 44. A page explains itself once (2026-10-08)

The UX walkthrough of 2026-10-08 found most data pages saying what they are
three or four times before the data: a subtitle, a lead, a caption under the
lead, a description under every section heading, a legend under every table —
beside an (i) that already held the same words.

- **Subtitle or lead, never both.** A page with a lead sentence (the one with
  the numbers) has no subtitle under its title. A page with no lead may keep
  one line.
- **At most one line under a section heading**, and only when it says something
  about THIS data (what is still empty, how many are waiting). What the section
  *is*, how a figure is derived, and every "X, not Y" caveat go on the heading's
  `InfoTip`. A caveat is never deleted to save space — it moves.
- **A lead does not restate the strip under it.** The strip carries the counts
  as links; the lead says the one thing to act on (Scope: the hosts outside
  every scope, not the three counts again).
- **Nothing is said three times.** An empty state says "nothing here" once,
  with its action — the lead and the section description do not also say it
  (Agent Sessions did). A number shown in a heading is not repeated inside its
  bar and again in a legend.
- **What is true of every row is not printed on every row** (§8's queue rule,
  applied to placeholders and badges): an empty cell is a muted dash with the
  words on `title` / `sr-only` ("No contact yet" filled 44 rows); a badge that
  every row carries is dropped; a column that says the same thing on every row
  is not rendered.
- **Two tables never list the same rows.** A group of one is its member: list
  it once and carry the group's extra fact on that row (Patterns: a family of
  one weakness).

### 45. What could not be loaded is said, and a reader keeps their place (2026-10-07)

From the codebase review of 2026-10-07 (5.339.0).

- **A picker whose options failed to load says so, with Retry** — never "No
  members" or an empty list. Project members come from the one cached loader,
  `hooks/useProjectMembers` (`useProjectRoster` → `{members, status, retry}`);
  `components/MembersLoadError` is the message. Do not call
  `listProjectMembers()` from a page.
- **A count that could not be read is not another filter's count.** When a
  summary request fails, its numbers are cleared and the page says they could
  not be counted (Scans); the previous filter's figures never stay on screen.
- **A reader keeps their place.** A paged list keeps its page in the URL
  (`?page=`, omitted for 1; `hooks/useUrlPage`, §39) and rows-per-page as a
  per-viewer preference (`localStorage`, or `?per=`); a filter or sort change
  still returns to page 1. A cross-project page (Portfolio, Oversight,
  Remediation deadlines) keeps its filters when the reader switches project;
  a project page drops them, because they name the previous project's ids. A narrowing that came from the URL (`?host=`, `?finding=`) is always
  shown as a removable chip naming what it narrows to.
- **Unsaved writing is guarded.** An editor holding text the reader typed uses
  `hooks/useDiscardGuard`; an expired session returns to where the reader was
  (`utils/loginReturn` — only a path inside the app is honoured).
- **A session stays open while the reader works, and ends after they stop.**
  It ends a fixed time after the reader last pressed a key or clicked
  (`hooks/useSessionRenewal`, which asks `AuthContext.renewSession` once the
  stored token is a few minutes old). A request alone never renews: pages
  poll, and an unattended tab must not keep itself signed in. A new source of
  "the reader did something" is added to that hook's list and nowhere else.
- **A session that is about to end says so.** Ten minutes before its end, and
  again one minute before, ONE toast that stays until closed or the session is
  renewed (`components/SessionExpiryNotice`, a single toast id, so each stage
  replaces the last) says when it ends in the reader's local time unless they
  carry on working, with "Stay signed in"; after the end it says the session
  has ended, with "Sign in again", which returns to the page the reader is on.
  It is a toast, never a dialog: it takes no focus and blocks no typing.
  The end is read from the stored token's expiry and used for nothing but the
  notice and the renewal; a token that does not give one shows no notice. The
  wait is one timeout for the next moment something changes, worked out again
  when the tab is shown and when another tab replaces the token — never a
  ticking interval.
- **A startup check that fails for a reason other than "not signed in" does
  not sign the reader out.** Only a 401 ends a session.
- **One helper each, enforced by lint:** a file save goes through `utils/download`
  (`saveBlob`, `filenameFromContentDisposition`); an absolute moment through
  `formatTimestamp` (ESLint refuses a hand-set `.download =` and a bare
  `new Date(x).toLocaleString()`). An export's error is `formatApiError`,
  never the raw "Request failed with status code 403".
- **A panel that shows one record is keyed by that record** (§39), so nothing
  from the previous record can land in it. The one surface that still stays
  mounted across records — the remediation timeline sheet — checks every
  async completion is still for the host on screen; a new one is keyed
  instead.

### 46. Two statuses, two facts, one name each (2026-10-08)

A finding on a host carries two statuses that record different facts. Each has
ONE name wherever a person reads it; the stored values do not change.

| Fact | Stored | Said as |
|---|---|---|
| The CONTACT's progress (remediation record, kept by a project admin) | `closed` | **Reported fixed** — never "Closed" |
| | `open`, `deferred` | Open, Deferred |
| The ASSESSOR's conclusion (the finding's endpoint) | `remediated` | **Remediated** |

- A date, a count, a chart, a CSV column and a timeline entry follow the same
  word: "Reported fixed on", "Reported fixed late", "reported fixed on time".
- The two are never synced, so the remediation pages show where they disagree
  as two named, countable, openable states: **Reported fixed, not retested**
  and **Remediated, record still open**. The server derives them
  (`verification`); a page never works one out.
- In a row the relation is a short line in the cell that already holds the
  state (the Deadline cell), with the definition on `title`; it is not a column,
  which would be empty on most rows. The counts are buttons that set
  `?verification=` and show a clearable chip; their definitions are on an
  `InfoTip`.
- The same line carries two more counts, each absent at 0, a button that sets
  `?flag=` and shows a clearable chip, defined on an `InfoTip`: **Deferrals to
  review** and **Due date set by hand**. The server derives both; opening one
  drops the state, band, follow-up and gap filters, as a gap count does.
- The Deadline cell says both in words, still in ONE column:

  | Row | Deadline cell |
  |---|---|
  | Due date set by hand, clock running | the usual phrase and date, then "set by hand · policy *date*" ("set by hand · no policy date" when the policy gives none); the full sentence on `title` |
  | Deferred, review date ahead | "Deferred · review *date*" |
  | Deferred, review date reached | "Deferred · review due" in the warning tone, the date under it |
  | Deferred, no review date | "Deferred · no review date" in the warning tone |

  Whether a review is due is the server's answer (`deferral_review_due`); the
  page never compares the date with today. A due date set by hand, a return to
  the policy's date, and a deferral are saved only with a note for the timeline:
  the editor says so inline and Save stays disabled until it is written.
- These words belong to the remediation pages only. The finding page, Posture,
  Operations and the client report keep their own vocabulary
  (`utils/findingStatus.ts`).

### 47. A detail page with a long list

A detail page that holds a list which can run to thousands of rows (a finding's
affected hosts) still has to show its other sections. Reference: the finding
page — `components/findings/FindingEndpoints.tsx`, `EndpointStateBar.tsx`,
helpers in `utils/findingEndpoints.ts`, and the shared
`components/SectionJumpBar.tsx`. There is ONE layout: three rows get the same
panel as three thousand, only shorter. No threshold, no second layout for
"small" records.

- **A jump bar, on any page longer than a few screens with more than one
  section** — a detail page, and a long reference page alike (What BlueStick
  reads, the MCP reference, the Tool reference). It is the ONE component,
  `SectionJumpBar`: a sticky strip pinned flush under the chrome, one entry per
  section the page actually renders, in page order: the section's own heading
  as the label and, where the page already knows one, a count. It is
  navigation, not a second explanation (§44) — no descriptions, no caption.
  A section that renders nothing has no entry: the bar asks the section's
  wrapper whether it has content, so a section that loads for itself needs no
  wiring. Each section wrapper has a stable `id` and carries `jumpTargetStyle`,
  which clears the chrome AND the bar. The section in view is marked with an
  IntersectionObserver held inside the bar — never a scroll listener, and
  never state on the page, or reading the page re-renders it. A page never
  builds its own list of in-page links.
- **Buttons for a few sections, a picker for many.** Up to eight shown
  sections are one button each. More than eight (`JUMP_PICKER_ABOVE`) would
  wrap into a wall of buttons, so the same bar shows ONE "Jump to…" picker
  (the searchable `Combobox`): it filters as the reader types, jumps on choose,
  and shows the section in view. The bar chooses by the number of sections
  shown; a page whose section count moves under its own search or filter, or
  whose sections arrive after the page, fixes the form with `presentation`
  so the control does not change shape while the reader types. The picker
  lists what the page's filter leaves — the filter narrows the page, the
  picker goes to a place on it; it is never a second search.
- **A section can be linked.** A jump writes the section's id to the address
  as `#id`, replacing the history entry (Back leaves the page, it does not
  replay the jumps), and a page opened with `#id` lands on that section, below
  the chrome and the bar, once the section has rendered. A page with its own
  deep link to a row (the finding page's `?endpoint=`) passes `hash={false}`:
  two things must not both scroll the page on load.
- **The list is a bounded panel.** It scrolls INSIDE a panel about twelve rows
  tall (a max-height, so a short list is a short panel with no inner scrollbar
  and no empty space), with a sticky table header. The sections below it are
  then always within a screen. The border is the edge of a scroll region, not
  a card (§7).
- **The list's controls are pinned to the panel, outside the scrolling body:**
  filters, chips and the bulk bar above the rows; "Showing N of M" and its
  buttons below them. A control that scrolls away with row 40 cannot be used on
  row 400.
- **A link to one row scrolls the panel, then the page.** The row is centred in
  the panel's own scroll and the PANEL is brought into view — once per link.
  If the row is in a closed group or not mounted, that is opened and mounted
  first.
- **Rows are compact: one line, about 32 px.** Identity first (the address, a
  mono link), then the name (truncated, the full text on `title`, a dash when
  there is none), then the row's state control. Nothing else on the row.
- **A destructive action lives in the row's "⋯" menu**, never as an icon beside
  the control the reader uses most: a slip of a few pixels must not remove a
  record. It keeps its confirmation and its Undo. This holds for any table
  whose rows carry a routine action (Scope's subnets: Edit is the icon, Delete
  is in the menu): the trigger is named "Actions for <the row>", the item ends
  in "…" because a confirmation follows.
- **Rows are grouped by the server's segment, never by arithmetic in the
  browser.** The groups are the project's one segment rule (the Posture grid's
  and the Evidence matrix's columns), sent with each row; a /24 worked out from
  an address would be a second, disagreeing definition. A group header carries
  the label, the count, the group's own state counts in the filter chips'
  words, and a tick for the group's rows THAT MATCH THE CURRENT FILTER
  (`selectAllState`; a bulk action never reaches a row the filter hides, §41).
  Under a filter a header reads "matching of total", and a group with no match
  is hidden.
- **One group is no group:** when every row is in the same segment the list is
  flat, with no header. Several groups start open when the filtered list is
  short (25 rows or fewer) and closed otherwise; what the reader opens or
  closes is kept for the visit, not stored.
- **Mounted rows are bounded, and the bound is stated in code.** The list
  mounts a first page, more on request, and never more than a fixed cap — past
  it the mounted rows are a window that moves on ("Showing 201–700 of 2,000",
  with a way back). Rows of a closed group are not mounted. "Show all" is
  offered only when all of them fit under the cap. Paging is by rows across
  the open groups in order, with one footer — not a "show more" per group,
  which has no single bound.
- **A stacked bar is a companion to the sentence, not a legend.** Beside the
  sentence that says how the rows stand, one thin hand-built bar (no chart
  library) of the same states: each part is a button with the same action as
  its filter chip and an accessible name and `title` that say the count, the
  state and that it filters. Fills come from the states' theme tones in
  `utils/findingStatus.ts`; two states that would differ only by red against
  green differ by pattern too. No legend — the chips already name the states.

### 48. Server state is TanStack Query (2026-10-09)
There is ONE way the app talks to the server: `@tanstack/react-query`.  A read
is `useQuery` (or `useInfiniteQuery`), a write is `useMutation`, and an API
function is called only inside a `queryFn` or a `mutationFn` — never in an
effect, never bare in an event handler, never in a hand-made `load()`.  The
lint rule `bluestick/api-in-query-only` enforces it.  `src/lib/query.ts` holds
what the whole app shares.

```tsx
const thing = useQuery({
  queryKey: ['getThing', thingId],                      // the API function's name, then its arguments
  queryFn: ({ signal }) => getThing(thingId, signal),
  enabled: thingId != null,
});
const save = useMutation({
  mutationFn: (body: Body) => updateThing(thingId, body),
  onSuccess: (updated) => {
    queryClient.setQueryData(['getThing', thingId], updated);   // the server's answer, or…
    void invalidateReads(queryClient, 'listThings');            // …say which reads are out of date
  },
  onError: (err) => toast.error(formatApiError(err, 'Could not save.')),
});
```

- **Key = the API function's name, then its arguments.**  There is no registry
  of keys; the name is the identity, so a write reaches every read of that
  function with `invalidateReads(queryClient, 'listThings')`.
- **A key never names the project.**  The cache is partitioned by signed-in
  user and project inside the key's hash (`setQueryScope`, set by the two
  contexts): one project's rows cannot answer another's question.  Data that
  is not one project's — the project list, users, installation settings,
  Oversight, Portfolio — starts its key with `GLOBAL`.
- **An async completion keeps the identity it started with.**  The scope is
  read when a key is hashed, which protects a read but not a write that lands
  late (a save started in project A answering after a switch to B).  So a
  component takes the client from `useQueryClient()` — under the project
  provider that is `scopedClient`, which drops a `setQueryData` made under
  another scope — and never writes project data through the module's
  `queryClient`.
- **Nothing is copied out of a query.**  The page renders `data`; sorting and
  grouping are `useMemo` over it.  A form seeded from the server keeps only
  the reader's EDITS in state and lays them over the data — no effect that
  copies an answer into the form.
- **A write refreshes what it changed, itself** (`setQueryData` or
  `invalidateReads`).  No `reload()` handed down as a prop, no `refreshKey`
  bumped by a parent, no window event announcing a change: those were the old
  mechanism.  A callback that changes local UI state (closes a dialog) stays.
- **Defaults, on purpose** (`lib/query.ts`): no automatic retry, and a
  FAILED read is not asked again when a second reader mounts — a failure is
  said, with Retry (`refetch`).  (A successful read IS asked again by a
  reader of the same key that mounts later; one that should not says the
  answer is recent enough with `staleTime` on its own observer.)  No refetch
  on focus; nothing kept once nothing shows it
  (`gcTime: 0`), so a page that is opened reads from the server; requests are
  never paused for a browser that believes it is offline (isolated networks).
  A read that should be remembered says so (`rememberFor(ms)`: project
  members, the installation's settings).  A query whose own lifecycle differs
  from a default overrides it explicitly, with a comment and a test — never
  to work around a page's problem.
- **Polling is an option of the query**: `...pollEvery(ms)`, or
  `pollEvery((query) => stillRunning(query.state.data) ? 2500 : null)` for a
  job — visible tab only, once on return to the tab, half as often while the
  server is failing.  No `setInterval`.
- **An action from a click that is not a write** (a download, a dry run, a
  one-off lookup) is a `useMutation` too: it carries the pending and error
  state.  A preview that follows what is typed is a `useQuery` with the
  debounced input in its key.
- **A secret shown once** (a new agent key, a password being sent) lives in a
  mutation with `gcTime: 0` that is `reset()` when done — never in a query.
- **Tests** mock the `services/api` barrel as before; `setupTests.ts` wraps
  every `render` / `renderHook` in a fresh client, so an ordinary test does
  not wrap its renders, and no test mocks the library.  A test of what is
  remembered between two mounts shares one client on purpose, passed as the
  `wrapper`; the recipe and the scenarios a shared mechanism needs are in
  `TESTING_FRAMEWORK_DOCUMENTATION.md` ("Testing server state").

Named exceptions (each carries an `eslint-disable` saying why): the sign-in
check on load in `AuthContext` and the staged-upload orchestration
(`hooks/useUploadReview`, which takes its API as an injected object).

## Final Rule
If a UI change looks correct only with fixture data, it is not finished.
