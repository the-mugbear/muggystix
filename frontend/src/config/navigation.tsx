/**
 * Navigation manifest — the single source of truth for every navigable
 * page: its path, the role that gates it, where it shows in the sidebar
 * IA, and how it appears in the command palette.
 *
 * Before this file, the same page metadata was authored three times —
 * App.tsx (`<ProtectedRoute requiredRole>`), Layout.tsx (`HUBS`), and
 * CommandPalette.tsx (`NAV_COMMANDS`) — so adding or re-gating a page
 * meant synchronized edits in three places and drift silently hid routes
 * or exposed nav a user couldn't reach (recurring code-review finding).
 *
 * Now: declare the page once in NAV_PAGES (+ the five HUB_DEFS).  The
 * Layout sidebar and the command palette are DERIVED from it, and
 * navigation.test.ts cross-checks the role gates against App.tsx so the
 * route layer can't drift from the manifest either.
 */
import React from 'react';
import {
  BookOpen,
  Bot,
  Compass,
  FileText,
  Folder,
  Gauge,
  Globe,
  KeyRound,
  MessageCircleQuestion,
  MessageSquareHeart,
  Plug,
  Settings as SettingsIcon,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import {
  ActivityPulseIcon,
  AlertHexIcon,
  ScanLinesIcon,
  ScopeIcon,
  ServerStackIcon,
} from '../components/AppIcons';

export type IconComponent = React.FC<{ className?: string }>;

/**
 * Roles used by the nav surfaces.  These mirror the global-role names in
 * AuthContext's ROLE_HIERARCHY; `hasPermission` resolves the hierarchy
 * (e.g. an admin satisfies an `analyst` gate).  `member` maps to analyst
 * level there, so the nav only needs to express these three thresholds.
 */
export type NavRole = 'viewer' | 'analyst' | 'admin';

export type HubId =
  | 'operations'
  | 'inventory'
  | 'posture'
  | 'workflows'
  | 'collaboration'
  | 'settings'
  | 'reference';

/** Presentation for a page's command-palette entry (Pages group). */
interface PalettePresentation {
  /** Defaults to the page's `label` when omitted. */
  label?: string;
  Icon: IconComponent;
  keywords?: string[];
  /** Stable display order in the palette's Pages group (no active search). */
  order: number;
}

export interface NavPage {
  id: string;
  path: string;
  /** Canonical / sidebar label. */
  label: string;
  requiredRole: NavRole;
  /** Sidebar hub this page lists under (omit for palette-only pages). */
  hub?: HubId;
  /** Command-palette presentation; omit for sidebar-only pages. */
  palette?: PalettePresentation;
}

export interface HubDef {
  id: HubId;
  label: string;
  path: string;
  requiredRole: NavRole;
  Icon: IconComponent;
  /**
   * Child the hub path redirects to (the hub no longer renders an interim
   * landing — it sends you straight to a child, since the secondary-nav tab
   * strip already lists the siblings).  Omit to use the first role-visible
   * child in manifest order; set it when that first child is a poor default
   * (e.g. Inventory lists Scans first but Hosts is the natural landing).
   */
  defaultChildPath?: string;
  /**
   * Paths (and everything under them) that belong to this hub WITHOUT being
   * tab-strip children — Reference's pages are cards on its landing, and two of
   * them (`/tool-reference`, `/default-credentials`) sit outside `/reference/`.
   * Drives only the sidebar's "you are here".
   */
  ownedPaths?: string[];
  /**
   * `utility` hubs sit at the foot of the sidebar, apart from the project
   * workflow (v5.253.0): they support the work from anywhere, they are not a
   * step in it.
   */
  placement?: 'utility';
}

// ---------------------------------------------------------------------------
// Source of truth
// ---------------------------------------------------------------------------

/**
 * The top-level hubs.  Operations, Posture and Reference are their own landing
 * pages; the others render a secondary tab strip of their child pages (those
 * NAV_PAGES whose `hub` matches).  Reference has no strip at all.
 */
export const HUB_DEFS: HubDef[] = [
  { id: 'operations', label: 'Operations', path: '/operations', requiredRole: 'viewer', Icon: Sparkles },
  { id: 'inventory', label: 'Inventory', path: '/inventory', requiredRole: 'viewer', Icon: ServerStackIcon, defaultChildPath: '/hosts' },
  // Posture is a real landing page (/posture) like Operations — its hub path
  // renders the roll-up directly, with Insights + Systemic as drill-down tabs.
  { id: 'posture', label: 'Posture', path: '/posture', requiredRole: 'viewer', Icon: Gauge },
  { id: 'workflows', label: 'Workflows', path: '/workflows', requiredRole: 'viewer', Icon: ShieldCheck },
  { id: 'collaboration', label: 'Collaboration', path: '/collaboration', requiredRole: 'viewer', Icon: ActivityPulseIcon },
  { id: 'settings', label: 'Settings', path: '/settings', requiredRole: 'viewer', Icon: SettingsIcon, placement: 'utility' },
  // v5.253.0 — its own destination, not a Settings tab: guides, the tool
  // reference, default credentials and the API docs are reading material used
  // from every page; under Settings they read as configuration and were hard to
  // find.  Its own landing page (no tab strip), like Operations.
  {
    id: 'reference', label: 'Reference', path: '/reference', requiredRole: 'viewer', Icon: BookOpen,
    placement: 'utility', ownedPaths: ['/reference', '/tool-reference', '/default-credentials'],
  },
];

/**
 * Every individual navigable page.  Array order within a hub is the
 * sidebar tab-strip order; palette order is the explicit `palette.order`.
 *
 * Keep this in lockstep with the routes declared in App.tsx — the role
 * here must equal the route's `requiredRole` (navigation.test.ts enforces
 * it).
 */
export const NAV_PAGES: NavPage[] = [
  // Palette-only / hub-landing entries (not rendered as sidebar children).
  {
    id: 'portfolio', path: '/portfolio', label: 'Portfolio', requiredRole: 'viewer',
    palette: { Icon: Folder, order: 0 },
  },
  {
    // Global administrators' programme dashboard (5.258.0).  Its sidebar
    // entry sits under "All Projects" in Layout.tsx, admins only.
    id: 'oversight', path: '/oversight', label: 'Oversight', requiredRole: 'admin',
    palette: { Icon: Gauge, keywords: ['metrics', 'programme', 'program', 'manager', 'testers'], order: 0 },
  },
  {
    id: 'operations', path: '/operations', label: 'Operations', requiredRole: 'viewer',
    palette: { Icon: Sparkles, order: 1 },
  },

  // Inventory hub
  {
    id: 'scans', path: '/scans', label: 'Scans', requiredRole: 'viewer', hub: 'inventory',
    palette: { Icon: ScanLinesIcon, order: 2 },
  },
  {
    id: 'hosts', path: '/hosts', label: 'Hosts', requiredRole: 'viewer', hub: 'inventory',
    palette: { Icon: ServerStackIcon, order: 3 },
  },
  {
    id: 'names', path: '/names', label: 'Names', requiredRole: 'viewer', hub: 'inventory',
    palette: { Icon: Globe, keywords: ['name', 'fqdn', 'dns', 'domain', 'hostname', 'vhost'], order: 3.5 },
  },
  {
    id: 'findings', path: '/findings', label: 'Findings', requiredRole: 'viewer', hub: 'inventory',
    palette: { Icon: AlertHexIcon, keywords: ['finding', 'vuln', 'triage', 'result'], order: 4 },
  },
  {
    // v5.261.0 — the client report (Quarto): drafts, issued history, addenda.
    id: 'reports', path: '/reports', label: 'Reports', requiredRole: 'viewer', hub: 'inventory',
    palette: { Icon: FileText, keywords: ['report', 'deliverable', 'addendum', 'client', 'docx', 'pdf'], order: 4.5 },
  },
  {
    id: 'scopes', path: '/scopes', label: 'Scopes', requiredRole: 'analyst', hub: 'inventory',
    palette: { Icon: ScopeIcon, order: 6 },
  },
  {
    id: 'network-topology', path: '/network-topology', label: 'Topology', requiredRole: 'viewer', hub: 'inventory',
  },
  // Posture hub — the analytical roll-up + its drill-downs. Tab order here is
  // the strip order: Posture (landing) | Insights | Systemic.
  {
    id: 'security-posture', path: '/posture', label: 'Posture', requiredRole: 'viewer', hub: 'posture',
    palette: { Icon: Gauge, keywords: ['posture', 'security', 'manager', 'exposure', 'coverage', 'ownership', 'summary', 'dashboard'], order: 6.4 },
  },
  {
    id: 'posture-segments', path: '/posture/segments', label: 'Segments', requiredRole: 'viewer', hub: 'posture',
    palette: { Icon: AlertHexIcon, keywords: ['segment', 'site', 'subnet', 'hygiene', 'neglect', 'exposure', 'eol', 'attention'], order: 6.5 },
  },
  {
    id: 'posture-patterns', path: '/posture/patterns', label: 'Patterns', requiredRole: 'viewer', hub: 'posture',
    palette: { Icon: AlertHexIcon, keywords: ['pattern', 'family', 'systemic', 'blind spot', 'estate', 'outlier', 'vector', 'spread', 'diagnostic', 'identity', 'encryption', 'lifecycle'], order: 6.6 },
  },
  {
    id: 'posture-evidence', path: '/posture/evidence', label: 'Evidence', requiredRole: 'viewer', hub: 'posture',
    palette: { Icon: Gauge, keywords: ['evidence', 'coverage', 'assessed', 'eligible', 'trust', 'assurance', 'gap', 'parser', 'quality'], order: 6.7 },
  },

  // Workflows hub — v2.337.0: agents run one project session that does every
  // kind of work, so the hub's primary views are Agent Runs (the unified
  // session timeline, one row per session) and Test Plans (the approval
  // surface a human owns). Recon Runs and Executions are now per-artifact
  // detail views subsumed by the session timeline — kept as routes and
  // reachable from the command palette and by drilling into a run on Agent
  // Runs, but off the hub strip so it stops presenting the old four-workflow
  // split. (Remove the `hub` field = palette-only, like the MCP reference.)
  {
    id: 'agent-activity', path: '/agent-activity', label: 'Agent Runs', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: Bot, keywords: ['agent', 'sessions', 'llm', 'recon', 'execution'], order: 5 },
  },
  {
    id: 'test-plans', path: '/test-plans', label: 'Test Plans', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: ShieldCheck, order: 8 },
  },
  {
    id: 'assist-sessions', path: '/assist-sessions', label: 'Agent Sessions', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: MessageCircleQuestion, keywords: ['assist', 'ask', 'agent', 'session', 'chat', 'review'], order: 9 },
  },
  {
    id: 'recon-runs', path: '/recon/runs', label: 'Recon Runs', requiredRole: 'viewer',
    palette: { Icon: Compass, keywords: ['discovery', 'recon', 'runs'], order: 7 },
  },
  {
    id: 'executions', path: '/executions', label: 'Executions', requiredRole: 'viewer',
    palette: { Icon: TerminalSquare, keywords: ['runs', 'execution'], order: 9 },
  },

  // Collaboration hub
  {
    id: 'activity', path: '/activity', label: 'Activity', requiredRole: 'viewer', hub: 'collaboration',
    palette: { label: 'Collaboration', Icon: ActivityPulseIcon, keywords: ['notes', 'team', 'comments'], order: 4 },
  },
  {
    id: 'tool-activity', path: '/tool-activity', label: 'Tool Activity', requiredRole: 'viewer', hub: 'collaboration',
  },
  {
    id: 'feedback', path: '/feedback', label: 'Agent Feedback', requiredRole: 'admin', hub: 'collaboration',
    palette: { Icon: MessageSquareHeart, order: 11 },
  },

  // Settings hub
  {
    id: 'project-settings', path: '/project-settings', label: 'Project', requiredRole: 'analyst', hub: 'settings',
    palette: { label: 'Project Settings', Icon: SettingsIcon, keywords: ['members', 'webhooks', 'tags', 'dates'], order: 14 },
  },
  {
    // v5.265.0 — every project (create, open settings); global admins only.
    id: 'all-projects', path: '/settings/projects', label: 'All projects', requiredRole: 'admin', hub: 'settings',
    palette: { label: 'All projects', Icon: Folder, keywords: ['create project', 'new project', 'projects'], order: 14.5 },
  },
  {
    id: 'llm-settings', path: '/llm-settings', label: 'LLM Providers', requiredRole: 'viewer', hub: 'settings',
    palette: { Icon: Sparkles, keywords: ['ai', 'openai', 'anthropic', 'gemini'], order: 12 },
  },
  {
    id: 'integrations', path: '/integrations', label: 'Scanner Integrations', requiredRole: 'analyst', hub: 'settings',
    palette: { Icon: KeyRound, keywords: ['nessus', 'shodan', 'api'], order: 13 },
  },
  {
    id: 'system-settings', path: '/system-settings', label: 'System', requiredRole: 'admin', hub: 'settings',
    palette: { label: 'System Settings', Icon: SettingsIcon, keywords: ['users', 'admin'], order: 17 },
  },
  {
    id: 'profile', path: '/profile', label: 'Profile', requiredRole: 'viewer', hub: 'settings',
    palette: { Icon: SettingsIcon, keywords: ['account', 'password'], order: 16 },
  },
  {
    // Palette-only: the sidebar entry is the `reference` HUB (see HUB_DEFS).
    id: 'reference', path: '/reference', label: 'Reference', requiredRole: 'viewer',
    palette: { Icon: BookOpen, keywords: ['docs', 'help', 'guide'], order: 15 },
  },
  {
    // Palette-only (no hub): the page is a card under the Reference hub, but it
    // was reachable only two clicks deep with no keyword search. For a first
    // MCP user, "mcp" in the command palette is the shortest path to the
    // explainer, the connect recipes, and the certificate fingerprint.
    id: 'mcp-reference', path: '/reference/mcp', label: 'MCP', requiredRole: 'viewer',
    palette: {
      Icon: Plug,
      keywords: ['mcp', 'model context protocol', 'agent', 'tools', 'connect', 'claude', 'codex', 'cursor', 'assist'],
      order: 15.5,
    },
  },
  // v5.222.0 — beside Scans, not under Settings: import problems are found
  // during collection, when the operator is on the inventory (design review
  // item 5).  Path unchanged so existing links keep working.
  {
    id: 'parse-errors', path: '/parse-errors', label: 'Ingestion Results', requiredRole: 'analyst', hub: 'inventory',
    palette: { Icon: AlertHexIcon, keywords: ['errors', 'parse', 'import', 'upload', 'ingestion'], order: 2.5 },
  },
];

// ---------------------------------------------------------------------------
// Derived shapes consumed by the sidebar (Layout) and command palette
// ---------------------------------------------------------------------------

export interface HubChild {
  label: string;
  path: string;
  requiredRole: string;
}

export interface Hub {
  id: string;
  label: string;
  path: string;
  Icon: IconComponent;
  requiredRole: string;
  /** Empty when the hub destination IS its own page (Operations). */
  children: HubChild[];
  /** Designated redirect target (see HubDef.defaultChildPath). */
  defaultChildPath?: string;
  /** See HubDef.ownedPaths / HubDef.placement. */
  ownedPaths: string[];
  placement?: 'utility';
}

export interface NavCommand {
  label: string;
  path: string;
  requiredRole: string;
  Icon: IconComponent;
  keywords?: string[];
}

/** Sidebar hubs with their child tab strips, derived from the manifest. */
export const HUBS: Hub[] = HUB_DEFS.map((hub) => ({
  id: hub.id,
  label: hub.label,
  path: hub.path,
  Icon: hub.Icon,
  requiredRole: hub.requiredRole,
  children: NAV_PAGES.filter((p) => p.hub === hub.id).map((p) => ({
    label: p.label,
    path: p.path,
    requiredRole: p.requiredRole,
  })),
  defaultChildPath: hub.defaultChildPath,
  ownedPaths: hub.ownedPaths ?? [],
  placement: hub.placement,
}));

/**
 * Resolve the active hub from a route.  Matches the hub's landing path, any
 * of its child paths, or any path it owns (descendants included).  Defaults to
 * Operations when nothing matches — covers /portfolio,
 * /force-change-password, deep test-plan / host detail routes, etc.
 */
export function resolveActiveHub(pathname: string): Hub {
  for (const hub of HUBS) {
    if (hub.path !== '/operations' && pathname === hub.path) return hub;
    // Children and owned paths match the same way (Reference owns pages that
    // are cards on its landing, not tabs — two of them outside /reference/).
    for (const path of [...hub.children.map((c) => c.path), ...hub.ownedPaths]) {
      if (pathname === path || pathname.startsWith(path + '/')) {
        return hub;
      }
    }
  }
  // Operations is the catch-all when no other hub matches.
  return HUBS[0];
}

/** Command-palette "Pages" entries, in their curated display order. */
export const NAV_COMMANDS: NavCommand[] = NAV_PAGES
  .filter((p): p is NavPage & { palette: PalettePresentation } => Boolean(p.palette))
  .sort((a, b) => a.palette.order - b.palette.order)
  .map((p) => ({
    label: p.palette.label ?? p.label,
    path: p.path,
    requiredRole: p.requiredRole,
    Icon: p.palette.Icon,
    keywords: p.palette.keywords,
  }));

/** Map of path → required role for every page in the manifest. */
export const NAV_ROLE_BY_PATH: Record<string, NavRole> = Object.fromEntries(
  NAV_PAGES.map((p) => [p.path, p.requiredRole]),
);

/**
 * Required role for a navigable path, sourced from the manifest.  Use this
 * wherever a nav surface declares a link (e.g. the hub landing pages) so the
 * role gate has ONE source of truth and can't drift.  Throws on an unknown
 * path — a dead link or a page missing from NAV_PAGES is a bug, surfaced
 * loudly at module load rather than silently mis-gating navigation.
 */
export function roleForPath(path: string): NavRole {
  const role = NAV_ROLE_BY_PATH[path];
  if (!role) {
    throw new Error(
      `navigation: no NAV_PAGES entry for path "${path}" — add it to the manifest`,
    );
  }
  return role;
}
