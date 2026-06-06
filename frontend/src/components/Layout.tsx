import React, { ReactNode } from 'react';
import { useNavigate, useLocation, useNavigationType, NavLink } from 'react-router-dom';
import {
  FolderOpen,
  MenuIcon,
  Repeat,
  ShieldCheck,
  Sparkles,
  Settings as SettingsIcon,
} from 'lucide-react';
import { useAppTheme } from '../contexts/ThemeContext';
import { useAuth } from '../contexts/AuthContext';
import { useHorizontalOverflowGuard } from '../hooks/useHorizontalOverflowGuard';
import { useProject } from '../contexts/ProjectContext';
import { getUnreadNotificationCount } from '../services/api';
import { formatStatusLabel } from '../utils/statusMeta';
import { cn } from '../utils/cn';
import {
  ActivityPulseIcon,
  BellRingsIcon,
  PaletteSwatchIcon,
  ServerStackIcon,
} from './AppIcons';
import UserMenu from './UserMenu';
import ProjectSelector from './ProjectSelector';
import CommandPalette from './CommandPalette';
import KeyboardShortcutsDialog from './KeyboardShortcutsDialog';
import AgentActivityRail from './AgentActivityRail';
import {
  SideSheet,
  SideSheetContent,
  SideSheetTitle,
} from './ui/side-sheet';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';
import logger from '../utils/logger';

interface LayoutProps {
  children: ReactNode;
}

// Single source of truth for the persistent left-nav drawer width on
// desktop (>= sm).  Previously hardcoded as 240px in 5 places — the
// fixed topbar offset, the secondary nav offset, the inline aside, the
// mobile SideSheet override, and the main content `sm:ml-*`.  Keep
// them in lockstep so a future drawer-width change is one edit.
const DRAWER_WIDTH = 240;
const DRAWER_WIDTH_PX = `${DRAWER_WIDTH}px`;

// ---------------------------------------------------------------------------
// IA: 5-destination sidebar.  Beta.2 reshape — collapses the v3-era 16-entry
// nav into five hubs.  Each hub has a landing page and a set of sub-pages
// reachable from a secondary tab strip below the topbar.  Sub-page URLs
// are preserved verbatim (bookmark stability is a non-negotiable from the
// migration plan).
//
// Portfolio is intentionally NOT in the hub set — the hubs are all
// project-scoped, and mixing a cross-project surface into that list
// confuses the mental model.  It IS surfaced as a standalone "All
// Projects" link rendered ABOVE the ProjectSelector (so it reads as the
// parent context: All Projects → pick a project → its hubs), and from
// the "Switch project" affordance in the topbar chip.
// ---------------------------------------------------------------------------

interface HubChild {
  label: string;
  path: string;
  requiredRole: string;
}

interface Hub {
  id: string;
  label: string;
  path: string;
  Icon: React.FC<{ className?: string }>;
  requiredRole: string;
  /** Empty when the hub destination IS its own page (Operations). */
  children: HubChild[];
}

const HUBS: Hub[] = [
  {
    id: 'operations',
    label: 'Operations',
    path: '/operations',
    Icon: Sparkles,
    requiredRole: 'viewer',
    children: [],
  },
  {
    id: 'inventory',
    label: 'Inventory',
    path: '/inventory',
    Icon: ServerStackIcon,
    requiredRole: 'viewer',
    children: [
      { label: 'Scans', path: '/scans', requiredRole: 'viewer' },
      { label: 'Hosts', path: '/hosts', requiredRole: 'viewer' },
      { label: 'Scopes', path: '/scopes', requiredRole: 'analyst' },
      { label: 'Topology', path: '/network-topology', requiredRole: 'viewer' },
    ],
  },
  {
    id: 'workflows',
    label: 'Workflows',
    path: '/workflows',
    Icon: ShieldCheck,
    requiredRole: 'viewer',
    children: [
      { label: 'Recon Runs', path: '/recon/runs', requiredRole: 'viewer' },
      { label: 'Test Plans', path: '/test-plans', requiredRole: 'viewer' },
      { label: 'Executions', path: '/executions', requiredRole: 'viewer' },
      { label: 'Agent Runs', path: '/agent-activity', requiredRole: 'viewer' },
    ],
  },
  {
    id: 'collaboration',
    label: 'Collaboration',
    path: '/collaboration',
    Icon: ActivityPulseIcon,
    requiredRole: 'viewer',
    children: [
      { label: 'Activity', path: '/activity', requiredRole: 'viewer' },
      // v2.58.0 — cross-project SOC-correlation surface.  Different
      // intent from /activity (notes/notifications timeline) and
      // /agent-activity (per-project agent timeline): "what tools ran
      // across all my projects at time X" for correlating against SOC
      // alerts.
      { label: 'Tool Activity', path: '/tool-activity', requiredRole: 'viewer' },
      { label: 'Agent Feedback', path: '/feedback', requiredRole: 'admin' },
    ],
  },
  {
    id: 'settings',
    label: 'Settings',
    path: '/settings',
    Icon: SettingsIcon,
    requiredRole: 'viewer',
    children: [
      { label: 'Project', path: '/project-settings', requiredRole: 'analyst' },
      { label: 'LLM Providers', path: '/llm-settings', requiredRole: 'viewer' },
      { label: 'Scanner Integrations', path: '/integrations', requiredRole: 'analyst' },
      { label: 'System', path: '/system-settings', requiredRole: 'admin' },
      { label: 'Profile', path: '/profile', requiredRole: 'viewer' },
      { label: 'Reference', path: '/reference', requiredRole: 'viewer' },
      { label: 'Ingestion Results', path: '/parse-errors', requiredRole: 'analyst' },
    ],
  },
];

// v4.7.5 — platform detection moved to utils/platform.ts so the
// KeyboardShortcutsDialog can use the same isMacLike()/commandModifierLabel()
// helpers.  Pre-fix the dialog hardcoded "Ctrl+K" while the topbar
// rendered "⌘K" on Mac — the help text disagreed with the visible
// chrome on the very platform where the discrepancy mattered.
import { isMacLike } from '../utils/platform';

/**
 * Resolve the active hub from a route.  Matches the hub's landing path
 * OR any of its child paths (including descendants via prefix match).
 * Defaults to Operations when nothing matches — covers /portfolio,
 * /force-change-password, deep test-plan / host detail routes, etc.
 */
function resolveActiveHub(pathname: string): Hub {
  for (const hub of HUBS) {
    if (hub.path !== '/operations' && pathname === hub.path) return hub;
    for (const child of hub.children) {
      if (pathname === child.path || pathname.startsWith(child.path + '/')) {
        return hub;
      }
    }
  }
  // Operations is the catch-all when no other hub matches.
  return HUBS[0];
}

export default function Layout({ children }: LayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [unreadCount, setUnreadCount] = React.useState(0);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);

  // Audit PRF·L1: previously evaluated at module load, which forced a
  // navigator probe before the React app mounted. Memoised inside the
  // component instead so the cost is one-shot per Layout instance.
  const cmdKey = React.useMemo(() => (isMacLike() ? '⌘' : 'Ctrl'), []);

  // Audit fix (beta.4): the topbar + secondary-nav heights were
  // hard-coded as `h-[76px]` + `h-10` and the main-content top
  // padding was a literal `pt-[calc(76px+40px+0.75rem)]`.  When a
  // project name wraps, the user zooms the browser, or we add a
  // toolbar in the future, real header height overruns the reserved
  // offset and content disappears behind the chrome.
  //
  // Fix: measure rendered heights via ResizeObserver and feed them
  // back as CSS custom properties on the layout root.  Sticky-
  // positioned children read those vars so the offsets always match
  // the actual rendered height.  A min-height keeps the bar from
  // collapsing during the first paint (before the observer fires).
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const topbarRef = React.useRef<HTMLElement | null>(null);
  const secondaryNavRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof ResizeObserver === 'undefined') return;
    const setVar = (name: string, px: number) => {
      root.style.setProperty(name, `${Math.ceil(px)}px`);
    };
    setVar('--topbar-h', topbarRef.current?.offsetHeight ?? 76);
    setVar('--secondary-nav-h', secondaryNavRef.current?.offsetHeight ?? 0);

    // ResizeObserver callbacks that write to the DOM in-frame can
    // trigger the browser's `ResizeObserver loop completed with
    // undelivered notifications` warning when a downstream sticky/
    // flex consumer of those CSS vars (audit: stickyBelowChrome) ends
    // up re-measured in the same frame.  Deferring the var write to
    // the next animation frame breaks the loop while preserving the
    // single-frame-late offset (invisible to the eye).  A pending
    // raf id per var lets a flurry of resize notifications coalesce
    // to one write per frame.
    let rafTopbar: number | null = null;
    let rafSecondary: number | null = null;
    const schedule = (which: 'topbar' | 'secondary', px: number) => {
      const apply = () => {
        if (which === 'topbar') {
          rafTopbar = null;
          setVar('--topbar-h', px);
        } else {
          rafSecondary = null;
          setVar('--secondary-nav-h', px);
        }
      };
      if (which === 'topbar') {
        if (rafTopbar !== null) cancelAnimationFrame(rafTopbar);
        rafTopbar = requestAnimationFrame(apply);
      } else {
        if (rafSecondary !== null) cancelAnimationFrame(rafSecondary);
        rafSecondary = requestAnimationFrame(apply);
      }
    };

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === topbarRef.current) {
          schedule('topbar', entry.contentRect.height);
        } else if (entry.target === secondaryNavRef.current) {
          schedule('secondary', entry.contentRect.height);
        }
      }
    });
    if (topbarRef.current) observer.observe(topbarRef.current);
    if (secondaryNavRef.current) observer.observe(secondaryNavRef.current);
    // If the secondary nav isn't mounted yet (no-children hub), clear
    // the CSS var so the next route's padding starts from a known
    // baseline.  Otherwise the previous route's height leaks through
    // until the observer fires.
    if (!secondaryNavRef.current) setVar('--secondary-nav-h', 0);
    return () => {
      if (rafTopbar !== null) cancelAnimationFrame(rafTopbar);
      if (rafSecondary !== null) cancelAnimationFrame(rafSecondary);
      observer.disconnect();
    };
    // Depending on `location.pathname` so the observer re-attaches
    // when the secondary nav strip appears/disappears as the user
    // navigates between hubs with and without children (the strip is
    // conditionally rendered).  Pre-fix the effect was `[]`-only and
    // never re-observed a newly-mounted strip, leaving --secondary-
    // nav-h at 0 and the page content rendered underneath the bar.
    //
    // Refs are stable; only pathname is read, so this is still a
    // ~5×-cheaper effect than the previous deps-less version that
    // re-ran on every render.
  }, [location.pathname]);
  const { themeName, setThemeName, availableThemes } = useAppTheme();
  const { hasPermission, isAuthenticated } = useAuth();
  const { currentProject } = useProject();

  // Notifications-poll error/backoff state. On consecutive failures
  // we extend the cadence (60s → 2m → 5m capped) so we don't hammer
  // the API on an outage. A successful tick resets the streak.
  const failureStreakRef = React.useRef(0);
  // v4.58.0 (UX·7) — surface staleness when the poll has failed enough
  // times that the displayed count is probably out of date.  Threshold
  // is conservative (3 consecutive failures ≈ 3 minutes of outage at
  // the 60s cadence) so a transient blip doesn't visually alarm
  // operators; resets to false on the next successful tick.
  const [notificationsStale, setNotificationsStale] = React.useState(false);

  const fetchUnreadCount = React.useCallback(() => {
    if (!isAuthenticated || !currentProject) return;
    getUnreadNotificationCount()
      .then((count) => {
        setUnreadCount(count);
        failureStreakRef.current = 0;
        setNotificationsStale(false);
      })
      .catch((err) => {
        failureStreakRef.current += 1;
        // Silent visually (the badge stays at last-known) but logged
        // for triage — pre-fix this catch was completely swallowed.
        logger.warn('NOTIFICATIONS', 'unread-count poll failed', {
          message: (err as Error | undefined)?.message,
          streak: failureStreakRef.current,
        });
        // v4.58.0 (UX·7) — after 3 consecutive failures, mark the
        // badge stale so operators know the count may be outdated.
        if (failureStreakRef.current >= 3) {
          setNotificationsStale(true);
        }
      });
  }, [isAuthenticated, currentProject]);

  React.useEffect(() => {
    if (!isAuthenticated || !currentProject) {
      setUnreadCount(0);
      return;
    }
    fetchUnreadCount();
    // Pages that mark notifications read can dispatch this custom
    // event to make the bell drop immediately instead of waiting up
    // to 60s for the next scheduled tick.
    const onMarked = () => fetchUnreadCount();
    window.addEventListener('nm:notifications-marked-read', onMarked);
    return () => {
      window.removeEventListener('nm:notifications-marked-read', onMarked);
    };
  }, [isAuthenticated, currentProject, fetchUnreadCount]);

  // Visibility-gated polling (audit CRIT-18). Cadence stretches when
  // recent ticks have failed so we don't add to API load during an
  // outage; collapses back to 60s on a successful tick.
  const pollCadence =
    failureStreakRef.current >= 5 ? 300_000
      : failureStreakRef.current >= 2 ? 120_000
      : 60_000;
  useVisibilityPoll(fetchUnreadCount, pollCadence, isAuthenticated && !!currentProject);

  // Global Cmd/Ctrl+K to open the command palette.  Matches the
  // common command-bar convention (Linear, GitHub, etc.) and gives
  // keyboard-first navigation across the whole app.
  //
  // The input-target guard (audit a11y·H1) prevents the shortcut from
  // hijacking native Firefox Ctrl+K (URL-bar focus) AND from
  // intercepting users mid-typing in any text field.  contenteditable
  // covers the inline note editors in Activity / HostInspector.
  React.useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k')) return;
      const target = event.target as HTMLElement | null;
      const isTextField =
        !!target?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
      // Always allow the palette to close itself even from inside a
      // text field — escape-via-shortcut is the point.
      if (isTextField && !paletteOpen) return;
      event.preventDefault();
      setPaletteOpen((open) => !open);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [paletteOpen]);

  // Audit FRX·H5: vim-style two-step `g` combos for the hubs the
  // operator visits most, `?` for the cheat sheet, and `/` so page-
  // level search inputs can grab focus without the operator hunting
  // for the field with the mouse.  Only Hosts wires the search-focus
  // event today; others ignore the dispatched event.
  // v2.43.0 — UX review #4: dev-time guard logs a console warning when
  // anything pushes the document body wider than the viewport, replacing
  // the overflow-x-hidden safety belt that used to mask such bugs.
  useHorizontalOverflowGuard();

  useKeyboardShortcuts({
    '?': () => setShortcutsOpen(true),
    '/': () => window.dispatchEvent(new CustomEvent('nm:focus-search')),
    'g h': () => navigate('/hosts'),
    'g p': () => navigate('/test-plans'),
    'g s': () => navigate('/scans'),
    'g i': () => navigate('/inventory'),
    'g o': () => navigate('/operations'),
  });

  // v4.57.0 (UX·3) — scroll-restoration on browser-back.
  //
  // Pre-fix the effect below forced both window AND #main-content to
  // scrollTop=0 on EVERY pathname change, including POP (back/forward
  // navigation).  An analyst returning from a host / scan / plan
  // detail page landed at the top of a long operational list and had
  // to re-find the row they came from.
  //
  // Post-fix: keep "scroll to top" for forward navigation (PUSH /
  // REPLACE) — that's the natural expectation for an explicit
  // navigation — but on POP, restore the prior scroll position for
  // the location we're returning to.  Per-key positions live in a
  // ref so they survive route remounts; sessionStorage backs them so
  // a back-into-a-soft-reloaded-page also restores (best-effort —
  // the route component still needs to have rendered enough content
  // for the scroll to land on a real position).
  const navigationType = useNavigationType();
  const scrollPositionsRef = React.useRef<Record<string, number>>({});

  // Continuously track scroll position for the current location key
  // so the next pathname change can save the outgoing position.
  React.useEffect(() => {
    const key = location.key;
    const mainContent = document.getElementById('main-content');
    const handler = () => {
      const y = mainContent ? mainContent.scrollTop : window.scrollY;
      scrollPositionsRef.current[key] = y;
    };
    // Capture on both window and #main-content so whichever is the
    // active scroller in the current layout is observed.
    window.addEventListener('scroll', handler, { passive: true });
    mainContent?.addEventListener('scroll', handler, { passive: true });
    return () => {
      window.removeEventListener('scroll', handler);
      mainContent?.removeEventListener('scroll', handler);
    };
  }, [location.key]);

  React.useEffect(() => {
    const mainContent = document.getElementById('main-content');
    if (navigationType === 'POP') {
      const saved = scrollPositionsRef.current[location.key];
      if (typeof saved === 'number') {
        // Defer to the next paint so the new route component has had
        // a chance to render its content; without this the scroll
        // target may not exist yet and the request silently no-ops.
        requestAnimationFrame(() => {
          if (mainContent) mainContent.scrollTo({ top: saved });
          else window.scrollTo({ top: saved });
        });
        return;
      }
    }
    window.scrollTo({ top: 0 });
    mainContent?.scrollTo({ top: 0 });
  }, [location.pathname, location.key, navigationType]);

  const handleNavigation = (path: string) => {
    navigate(path);
    setMobileOpen(false);
  };

  // v4.58.0 (UX·5) — hub-link auto-resume retired.  Pre-fix a plain
  // left-click on a hub link silently redirected to a localStorage'd
  // child path instead of the advertised href; the Shift+click escape
  // was only discoverable via the hover title (broken for touch /
  // keyboard / screen-reader users).  The reviewer flagged this as
  // navigation that doesn't go where it says it goes.  Hub links now
  // always navigate to their declared `hub.path`; the legacy
  // ``nm.hub.lastChild.*`` localStorage entries are harmless and
  // self-clean over time (no migration needed; the keys are read by
  // nothing).  If the resume-last-section affordance becomes valuable
  // again, it should land as an explicitly labeled button, not as a
  // hidden override of the visible link.

  const activeHub = React.useMemo(() => resolveActiveHub(location.pathname), [location.pathname]);
  const currentTheme = availableThemes.find((option) => option.value === themeName);

  // Visible children for the active hub — filtered by role so users
  // don't see secondary tabs they can't navigate to. Memoised so
  // background re-renders (notification ticks, theme changes) don't
  // re-filter on every pass.
  const visibleHubChildren = React.useMemo(
    () => activeHub.children.filter((child) => hasPermission(child.requiredRole)),
    [activeHub, hasPermission],
  );

  // Page title in the topbar: prefer the most-specific match.  If the
  // user is on a child path, show that child's label; otherwise show
  // the hub label.
  const currentSection = React.useMemo(() => {
    const child = activeHub.children.find((c) => location.pathname === c.path);
    return child?.label ?? activeHub.label;
  }, [activeHub, location.pathname]);

  const drawer = (
    <div className="flex h-full flex-col">
      <div className="brand-sidebar-header flex h-[88px] items-center gap-sm border-b border-border px-md">
        <img
          src="/bs.svg"
          alt=""
          aria-hidden
          className="size-9 shrink-0"
        />
        <h1
          className={cn(
            'brand-wordmark brand-wordmark--sidebar min-w-0 truncate text-subheading',
            themeName === 'phosphor' && 'brand-wordmark--phosphor',
          )}
        >
          BlueStick
        </h1>
      </div>

      {/* All Projects — the cross-project control plane.  Rendered above
          the ProjectSelector because it's the level ABOVE a single
          project; the divider separates it from the project-scoped
          context (selector + hubs) below. */}
      {hasPermission('viewer') && (
        <div className="px-xs pt-xs">
          <NavLink
            to="/portfolio"
            className={({ isActive }) =>
              cn(
                'relative flex w-full items-center gap-sm rounded-control border-l-2 px-sm py-xs text-left text-metadata transition-colors no-underline',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
                isActive
                  ? 'border-l-primary bg-sidebar-accent font-semibold text-foreground'
                  : 'border-l-transparent font-medium text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                themeName === 'phosphor' && 'tracking-[0.04em]',
              )
            }
          >
            {({ isActive }) => (
              <>
                <FolderOpen
                  className={cn('size-4 shrink-0', isActive ? 'text-primary' : 'text-muted-foreground')}
                />
                <span className="truncate">All Projects</span>
              </>
            )}
          </NavLink>
        </div>
      )}

      <div className="mx-sm my-xs border-t border-border" aria-hidden />

      <div>
        <ProjectSelector />
      </div>

      <nav
        className="flex-1 overflow-y-auto px-xs py-xs"
        aria-label="Primary navigation"
      >
        {HUBS.map((hub) => {
          if (!hasPermission(hub.requiredRole)) return null;
          const selected = hub.id === activeHub.id;
          const { Icon } = hub;
          return (
            // v4.58.0 (UX·5) — plain <NavLink> with no onClick
            // hijack.  Always navigates to hub.path so the visible
            // destination is the actual destination; modifier-clicks
            // ("Open in new tab", middle-click) still work via the
            // native link behaviour the v2.44.1 fix introduced.
            <NavLink
              key={hub.id}
              to={hub.path}
              className={cn(
                // Left-edge accent rail on active gives the nav a
                // tactile "you are here" without relying on bg fill
                // alone.  Inactive entries keep the bare-text feel.
                'relative mb-xxs flex w-full items-center gap-sm rounded-control border-l-2 px-sm py-xs text-left text-metadata transition-colors no-underline',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
                selected
                  ? 'border-l-primary bg-sidebar-accent font-semibold text-foreground'
                  : 'border-l-transparent font-medium text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                themeName === 'phosphor' && 'tracking-[0.04em]',
              )}
              aria-current={selected ? 'page' : undefined}
            >
              <Icon
                className={cn(
                  'size-4 shrink-0',
                  selected ? 'text-primary' : 'text-muted-foreground',
                )}
              />
              <span className="truncate">{hub.label}</span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );

  /**
   * Secondary nav strip — rendered below the topbar when the active
   * hub has children.  Operations has none, so the strip collapses to
   * nothing there.  Tab-style buttons; the active sub-route is
   * highlighted via NavLink's `isActive` callback.
   */
  const secondaryNav = visibleHubChildren.length > 0 ? (
    <div
      ref={secondaryNavRef}
      // `fixed` is already a positioning context for absolute
      // descendants — do NOT add `relative` here.  tailwind-merge
      // resolves the conflicting position family last-wins, which
      // strips `fixed` and drops the strip into the flex-row flow.
      // The parent's default `align-items: stretch` then stretches
      // the strip to the row's full height; main's `padding-top:
      // calc(var(--secondary-nav-h) + …)` makes main taller; main
      // taller → row taller → strip stretches further → observer
      // bumps `--secondary-nav-h` → repeat.  Geometric runaway that
      // pushed paddingTop to ~2.3M pixels and rendered every hub
      // landing as a blank viewport.  The scroll-hint gradient
      // overlay below is `absolute` inside the `fixed` strip, which
      // is a valid positioning chain.
      className={cn(
        'fixed inset-x-0 z-20 flex min-h-10 items-center gap-xs overflow-x-auto border-b border-border bg-background/80 px-sm backdrop-blur sm:left-[var(--drawer-width)] sm:px-md',
      )}
      style={{
        '--drawer-width': DRAWER_WIDTH_PX,
        top: 'var(--topbar-h, 76px)',
      } as React.CSSProperties}
      role="navigation"
      aria-label={`${activeHub.label} sections`}
    >
      {visibleHubChildren.map((child) => (
        <NavLink
          key={child.path}
          to={child.path}
          end={false}
          className={({ isActive }) =>
            cn(
              // Bottom-border indicator on active matches the strip's
              // "tab row" reading.  Hover lifts to text-foreground
              // for keyboard discoverability without painting the bg.
              // Labels wrap below md so narrow viewports don't depend
              // entirely on horizontal scroll to reach every tab.
              'relative inline-flex items-center md:whitespace-nowrap border-b-2 px-sm py-xxs text-metadata font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
              isActive
                ? 'border-b-primary text-foreground'
                : 'border-b-transparent text-muted-foreground hover:text-foreground',
            )
          }
        >
          {child.label}
        </NavLink>
      ))}
      {/* Right-edge fade hints there's more to scroll into view; the
          pointer-events-none keeps clicks pass-through to the tabs. */}
      <div
        className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background"
        aria-hidden
      />
    </div>
  ) : null;

  return (
    <div ref={rootRef} className="flex min-h-screen w-full bg-background">
      {/* Skip-to-content link — hidden until it receives focus. */}
      <a
        href="#main-content"
        className={cn(
          'fixed left-xs top-[-3rem] z-[100] rounded-control border border-border bg-card px-md py-xs text-metadata font-semibold text-foreground no-underline',
          'transition-[top] duration-fast ease-out',
          'focus:top-xs focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
          'focus-visible:top-xs',
        )}
      >
        Skip to main content
      </a>

      {/* Top bar — fixed so it spans the full viewport above the
          sidebar.  Height is min-only (was a literal h-[76px] before
          the beta.4 audit fix), so the bar grows naturally when its
          content wraps; ResizeObserver up at the layout root feeds
          the rendered height back via `--topbar-h` so the secondary
          nav + main content know where to start. */}
      <header
        ref={topbarRef}
        className={cn(
          'fixed inset-x-0 top-0 z-30 flex min-h-[76px] items-center gap-sm border-b border-border bg-background/80 backdrop-blur',
          'sm:left-[var(--drawer-width)]',
        )}
        style={{ '--drawer-width': DRAWER_WIDTH_PX } as React.CSSProperties}
      >
        <div className="flex flex-1 items-center gap-sm px-sm sm:px-md">
          <Button
            variant="ghost"
            size="icon"
            className="sm:hidden"
            aria-label="Open navigation"
            onClick={() => setMobileOpen((v) => !v)}
          >
            <MenuIcon className="size-4" aria-hidden />
          </Button>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-xs">
              <span className="truncate text-caption uppercase tracking-wider text-muted-foreground">
                {currentProject ? 'Active Project' : 'BlueStick'}
              </span>
              {currentProject && (
                <>
                  <Badge variant="outline">
                    {formatStatusLabel(currentProject.status)}
                  </Badge>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => navigate('/portfolio')}
                        aria-label="Switch project"
                        className="size-6"
                      >
                        <Repeat className="size-3.5" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Switch project</TooltipContent>
                  </Tooltip>
                </>
              )}
            </div>
            <div className="truncate text-section-title font-semibold">
              {currentProject?.name ?? currentSection}
            </div>
          </div>

          {/* Command palette trigger — exposes the keyboard shortcut
              for discoverability (operators won't guess Cmd+K otherwise). */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => setPaletteOpen(true)}
                aria-label="Open command palette"
                className={cn(
                  'hidden items-center gap-xs rounded-control border border-border bg-card px-sm py-xxs text-caption text-muted-foreground sm:inline-flex',
                  'hover:border-primary/30 hover:bg-accent hover:text-foreground',
                  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
                )}
              >
                <Sparkles className="size-3.5" aria-hidden />
                <span>Quick nav</span>
                <kbd className="rounded border border-border bg-muted px-xxs font-mono text-micro">
                  {cmdKey}K
                </kbd>
              </button>
            </TooltipTrigger>
            <TooltipContent>Open command palette ({cmdKey}+K)</TooltipContent>
          </Tooltip>

          {/* Notifications */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                // FRX·H6: bell deep-links into Activity with a
                // `mentions=mine` filter pre-applied so the user lands
                // on what the badge was promising, not the full feed.
                onClick={() => navigate('/activity?mentions=mine')}
                aria-label="Mentions"
                className="relative"
              >
                <BellRingsIcon className="size-4" />
                {unreadCount > 0 && (
                  <span
                    role="status"
                    aria-live="polite"
                    className={cn(
                      'absolute -right-1 -top-1 inline-flex min-w-[1rem] items-center justify-center rounded-full px-1 text-micro font-semibold',
                      notificationsStale
                        ? // v4.58.0 (UX·7) — dimmed muted variant so
                          // the operator can see something's off
                          // without losing the count entirely.
                          'bg-muted text-muted-foreground opacity-70'
                        : 'bg-destructive text-destructive-foreground',
                    )}
                  >
                    {unreadCount > 99 ? '99+' : unreadCount}
                    <span className="sr-only">
                      {notificationsStale
                        ? ' unread mentions (count may be stale — server unreachable)'
                        : ' unread mentions'}
                    </span>
                  </span>
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {notificationsStale
                ? `Counts may be stale — server unreachable. Last known: ${unreadCount} unread.`
                : unreadCount > 0
                ? `${unreadCount} unread mention${unreadCount !== 1 ? 's' : ''} — open Activity to review`
                : 'No unread mentions'}
            </TooltipContent>
          </Tooltip>

          {/* Agent activity rail — floating popover, renders nothing
              when there are zero agent sessions in this project. */}
          <AgentActivityRail />

          {/* Theme picker */}
          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="Choose application theme"
                  >
                    <PaletteSwatchIcon className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>Theme: {currentTheme?.label ?? 'Light'}</TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuRadioGroup
                value={themeName}
                onValueChange={(v) => setThemeName(v as typeof themeName)}
              >
                {availableThemes.map((option) => {
                  const isActive = option.value === themeName;
                  return (
                    <DropdownMenuRadioItem key={option.value} value={option.value}>
                      <span
                        className={cn('flex-1 text-metadata', isActive && 'font-bold')}
                      >
                        {option.label}
                      </span>
                    </DropdownMenuRadioItem>
                  );
                })}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>

          <UserMenu />
        </div>
      </header>

      {/* Secondary nav strip — sits below the topbar, shows the active
          hub's sub-pages.  Nothing renders for hubs with no children
          (Operations). */}
      {secondaryNav}

      {/* Sidebar — permanent on sm+; temporary overlay on xs.
          beta.3: uses bg-sidebar (a subtle tonal shift from the
          page background) so the nav rail visually separates from
          the main content without needing heavy borders. */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 border-r border-border bg-sidebar text-sidebar-foreground',
          'hidden sm:block',
        )}
        style={{ width: DRAWER_WIDTH_PX }}
        aria-label="primary navigation"
      >
        {drawer}
      </aside>

      {/* Mobile drawer — real Radix Dialog via SideSheet primitive.
          modal={true} (the SideSheet default override below), so we
          get a focus trap, Escape-to-close, outside-click close, and
          focus restoration to the menu trigger.  Pre-audit-fix this
          was a hand-rolled div with none of those guarantees.
          Only mounts on xs (sm:hidden on the SideSheet root). */}
      <SideSheet
        open={mobileOpen}
        onOpenChange={setMobileOpen}
        modal
      >
        <SideSheetContent
          side="left"
          width="md"
          overlay
          showClose={false}
          className="gap-0 bg-sidebar p-0 text-sidebar-foreground sm:hidden"
          style={{ width: DRAWER_WIDTH_PX, maxWidth: DRAWER_WIDTH_PX }}
          aria-label="Primary navigation"
        >
          {/* SideSheetTitle is required for screen-reader announce-
              ment on Dialog open; visually hidden via sr-only since
              the drawer already has its own BlueStick heading. */}
          <SideSheetTitle className="sr-only">Primary navigation</SideSheetTitle>
          {drawer}
        </SideSheetContent>
      </SideSheet>

      {/* Main content area — top padding accounts for the fixed topbar
          + secondary nav strip (when present).  beta.4 audit fix:
          padding is derived from `--topbar-h` + `--secondary-nav-h`
          CSS custom properties (set by ResizeObserver on the layout
          root), so wrapping topbar content, browser zoom, or future
          toolbar additions don't bury content behind the chrome.
          Pre-beta.4 these were `pt-[calc(76px+40px+0.75rem)]` —
          hard-coded magic numbers that broke at zoom > 100% or with
          long project names.

          beta.3: dropped the card-in-a-card wrapper — pages own
          their own Cards now; the Layout shell stays flat. */}
      <main
        id="main-content"
        tabIndex={-1}
        // v2.43.0 — UX review #4: dropped `overflow-x-hidden`.  The
        // safety-belt class was masking responsive defects at the shell
        // level — operators on narrow viewports couldn't see clipped
        // content was unreachable.  Cell-level overflow is now solely
        // responsible for handling unbounded values; the dev-time guard
        // in `useHorizontalOverflowGuard()` warns in the console when
        // body width exceeds viewport so regressions surface immediately.
        className={cn(
          'min-w-0 max-w-full flex-1 p-sm focus:outline-none md:p-md',
          'sm:ml-[var(--drawer-width)]',
        )}
        style={{
          '--drawer-width': DRAWER_WIDTH_PX,
          paddingTop:
            'calc(var(--topbar-h, 76px) + var(--secondary-nav-h, 0px) + 0.75rem)',
        } as React.CSSProperties}
      >
        <React.Fragment key={currentProject?.id ?? 'none'}>{children}</React.Fragment>
      </main>

      {/* Global command palette — opens on ⌘K / Ctrl+K (handler above)
          or by clicking the "Quick nav" pill in the topbar. */}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />

      {/* Keyboard shortcuts cheat sheet — opens on `?` (registered via
          useKeyboardShortcuts above). */}
      <KeyboardShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />

      {/* Project-context announcement live region. ProjectContext
          writes the active project name here on every selectProject
          call so screen-reader users hear context-switches that are
          otherwise only visible in the topbar chip. */}
      <div
        id="nm-project-announce"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      />
    </div>
  );
}
