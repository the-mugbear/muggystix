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
  Bot,
  Compass,
  Folder,
  KeyRound,
  MessageSquareHeart,
  Network as NetworkIcon,
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
  | 'workflows'
  | 'collaboration'
  | 'settings';

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
}

// ---------------------------------------------------------------------------
// Source of truth
// ---------------------------------------------------------------------------

/**
 * The five top-level hubs.  Operations is its own landing page (no child
 * tab strip); the other four render a secondary tab strip of their child
 * pages (those NAV_PAGES whose `hub` matches).
 */
export const HUB_DEFS: HubDef[] = [
  { id: 'operations', label: 'Operations', path: '/operations', requiredRole: 'viewer', Icon: Sparkles },
  { id: 'inventory', label: 'Inventory', path: '/inventory', requiredRole: 'viewer', Icon: ServerStackIcon },
  { id: 'workflows', label: 'Workflows', path: '/workflows', requiredRole: 'viewer', Icon: ShieldCheck },
  { id: 'collaboration', label: 'Collaboration', path: '/collaboration', requiredRole: 'viewer', Icon: ActivityPulseIcon },
  { id: 'settings', label: 'Settings', path: '/settings', requiredRole: 'viewer', Icon: SettingsIcon },
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
    id: 'scopes', path: '/scopes', label: 'Scopes', requiredRole: 'analyst', hub: 'inventory',
    palette: { Icon: ScopeIcon, order: 6 },
  },
  {
    id: 'network-topology', path: '/network-topology', label: 'Topology', requiredRole: 'viewer', hub: 'inventory',
  },

  // Workflows hub
  {
    id: 'recon-runs', path: '/recon/runs', label: 'Recon Runs', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: Compass, keywords: ['discovery'], order: 7 },
  },
  {
    id: 'test-plans', path: '/test-plans', label: 'Test Plans', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: ShieldCheck, order: 8 },
  },
  {
    id: 'executions', path: '/executions', label: 'Executions', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: TerminalSquare, keywords: ['runs'], order: 9 },
  },
  {
    id: 'agent-activity', path: '/agent-activity', label: 'Agent Runs', requiredRole: 'viewer', hub: 'workflows',
    palette: { Icon: Bot, keywords: ['agent', 'sessions', 'llm'], order: 5 },
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
    palette: { label: 'Project Settings', Icon: SettingsIcon, keywords: ['members'], order: 14 },
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
    id: 'reference', path: '/reference', label: 'Reference', requiredRole: 'viewer', hub: 'settings',
    palette: { Icon: NetworkIcon, keywords: ['docs', 'help', 'guide'], order: 15 },
  },
  {
    id: 'parse-errors', path: '/parse-errors', label: 'Ingestion Results', requiredRole: 'analyst', hub: 'settings',
    palette: { Icon: AlertHexIcon, keywords: ['errors', 'parse'], order: 10 },
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
}));

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
