/**
 * Navigation manifest consistency guard.
 *
 * The manifest (src/config/navigation.tsx) is the single source of truth
 * for sidebar + command-palette nav.  This test also cross-checks it
 * against the third surface — the App.tsx route gates — so a role changed
 * in one place but not the other fails CI instead of silently exposing or
 * hiding a page.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, it, expect } from 'vitest';

import {
  NAV_PAGES,
  HUBS,
  NAV_COMMANDS,
  HUB_DEFS,
  documentTitleFor,
  isCrossProjectPath,
  pageLabelFor,
  resolveActiveHub,
} from '../config/navigation';

/**
 * Extract path -> requiredRole from App.tsx by scanning the source in
 * order: each `path="..."` is associated with the FIRST `requiredRole="..."`
 * that follows it (i.e. its own <ProtectedRoute>).  Routes without a
 * requiredRole (redirects, nested tab routes) simply never get an entry.
 */
function appRouteRoles(): Record<string, string> {
  const src = readFileSync(join(__dirname, '..', 'App.tsx'), 'utf8');
  const token = /(?:path="([^"]+)")|(?:requiredRole="([^"]+)")/g;
  const roles: Record<string, string> = {};
  let pendingPath: string | null = null;
  let m: RegExpExecArray | null;
  while ((m = token.exec(src)) !== null) {
    if (m[1] !== undefined) {
      pendingPath = m[1];
    } else if (m[2] !== undefined && pendingPath && !(pendingPath in roles)) {
      roles[pendingPath] = m[2];
      pendingPath = null;
    }
  }
  return roles;
}

describe('navigation manifest', () => {
  it('has unique page ids and paths', () => {
    const ids = NAV_PAGES.map((p) => p.id);
    const paths = NAV_PAGES.map((p) => p.path);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('every sidebar child references a known hub and a manifest page', () => {
    const hubIds = new Set(HUB_DEFS.map((h) => h.id));
    for (const page of NAV_PAGES) {
      if (page.hub) expect(hubIds.has(page.hub)).toBe(true);
    }
    // Every hub child path is a real manifest page with a matching role.
    const roleByPath = Object.fromEntries(NAV_PAGES.map((p) => [p.path, p.requiredRole]));
    for (const hub of HUBS) {
      for (const child of hub.children) {
        expect(roleByPath[child.path]).toBe(child.requiredRole);
      }
    }
  });

  // v5.253.0 — Reference is its own sidebar destination, not a Settings tab.
  it('Reference is a hub that stays highlighted on every page it owns', () => {
    const settings = HUBS.find((h) => h.id === 'settings')!;
    expect(settings.children.map((c) => c.path)).not.toContain('/reference');
    // Still in the command palette.
    expect(NAV_COMMANDS.map((c) => c.path)).toContain('/reference');

    for (const path of [
      '/reference', '/reference/user-guide/triage', '/reference/mcp', '/reference/sbom',
      // Two of its pages live outside /reference/ — a prefix rule would miss them.
      '/tool-reference', '/default-credentials',
    ]) {
      expect(resolveActiveHub(path)?.id, path).toBe('reference');
    }
    // A prefix is a path segment, not a string prefix — and an unknown path
    // belongs to no hub (v5.294.0: it used to fall back to Operations).
    expect(resolveActiveHub('/reference-data')).toBeNull();
    expect(resolveActiveHub('/project-settings')?.id).toBe('settings');
    expect(resolveActiveHub('/hosts/12')?.id).toBe('inventory');
  });

  // v5.294.0 (UX review) — a 404 lit "Operations" as where you were, and so
  // did the personal and cross-project pages.
  it('a page no hub owns marks no hub; every project detail route still has one', () => {
    for (const path of ['/no-such-page', '/profile', '/llm-settings', '/portfolio', '/oversight']) {
      expect(resolveActiveHub(path), path).toBeNull();
    }
    const owned: Array<[string, string]> = [
      ['/operations', 'operations'],
      ['/hosts/12', 'inventory'],
      ['/scans/compare', 'inventory'],
      ['/scopes/3', 'inventory'],
      ['/findings/37', 'findings'],
      ['/reports/4', 'findings'],
      ['/test-plans/4/runs', 'workflows'],
      ['/test-plans/compare', 'workflows'],
      ['/recon/runs/9', 'workflows'],
      ['/recon/compare', 'workflows'],
      ['/executions/2', 'workflows'],
      ['/assist-sessions/28', 'workflows'],
      ['/activity', 'collaboration'],
      ['/settings/projects', 'administration'],
      ['/system-settings', 'administration'],
    ];
    for (const [path, hub] of owned) {
      expect(resolveActiveHub(path)?.id, path).toBe(hub);
    }
  });

  it('the IA the UX review settled on (v5.294.0)', () => {
    const tabs = (id: string) => HUBS.find((h) => h.id === id)!.children.map((c) => c.label);
    expect(tabs('inventory')).toEqual(['Hosts', 'Names', 'Scans', 'Ingestion Results', 'Scope']);
    expect(tabs('findings')).toEqual(['Findings', 'Reports']);
    expect(tabs('workflows')).toEqual(['Test Plans', 'Agent Runs', 'Tool Activity', 'Agent Feedback']);
    expect(HUBS.find((h) => h.id === 'workflows')!.defaultChildPath).toBe('/test-plans');
    expect(tabs('collaboration')).toEqual(['Collaboration']);
    expect(tabs('settings')).toEqual(['Project', 'Scanner Integrations']);
    expect(tabs('administration')).toEqual(['All projects', 'System']);
    expect(HUBS.find((h) => h.id === 'administration')!.requiredRole).toBe('admin');
    // Findings sits right after Inventory in the sidebar.
    const order = HUBS.map((h) => h.id);
    expect(order.indexOf('findings')).toBe(order.indexOf('inventory') + 1);
    // Agent Sessions, Profile and LLM Providers are palette-only now.
    const palettePaths = NAV_COMMANDS.map((c) => c.path);
    for (const path of ['/assist-sessions', '/profile', '/llm-settings']) {
      expect(NAV_PAGES.find((p) => p.path === path)?.hub, path).toBeUndefined();
      expect(palettePaths, path).toContain(path);
    }
  });

  it('App.tsx sends the old paths to their new homes', () => {
    const src = readFileSync(join(__dirname, '..', 'App.tsx'), 'utf8');
    expect(src).toMatch(/path="\/parse-errors" element={<RedirectKeepingQuery to="\/ingestion-results" \/>}/);
    expect(src).toMatch(/path="\/assist-sessions"[\s\S]{0,200}?<Navigate to="\/agent-activity\?view=sessions" replace \/>/);
    // The per-session detail keeps its page.
    expect(src).toMatch(/path="\/assist-sessions\/:sessionId"/);
  });

  // B4 — every page was "BlueStick" in the browser tab.
  it('each route names itself in the browser title', () => {
    expect(documentTitleFor('/hosts', 'Demo — Insights Eval')).toBe('Hosts · Demo — Insights Eval · BlueStick');
    expect(documentTitleFor('/findings/37', 'Demo')).toBe('Finding · Demo · BlueStick');
    expect(documentTitleFor('/test-plans/4/runs', 'Demo')).toBe('Test plan · Demo · BlueStick');
    expect(documentTitleFor('/project-settings', 'Demo')).toBe('Project Settings · Demo · BlueStick');
    expect(documentTitleFor('/activity', 'Demo')).toBe('Collaboration · Demo · BlueStick');
    // Cross-project pages leave the project out.
    expect(documentTitleFor('/portfolio', 'Demo')).toBe('Portfolio · BlueStick');
    expect(documentTitleFor('/reference/user-guide/triage', 'Demo')).toBe('User guide · BlueStick');
    expect(documentTitleFor('/nope', 'Demo')).toBe('Page not found · Demo · BlueStick');
    expect(documentTitleFor('/hosts', null)).toBe('Hosts · BlueStick');
    // No manifest page falls through to "Page not found".
    for (const page of NAV_PAGES) {
      expect(pageLabelFor(page.path), page.path).not.toBe('Page not found');
    }
    expect(isCrossProjectPath('/portfolio')).toBe(true);
    expect(isCrossProjectPath('/hosts')).toBe(false);
  });

  it('utility hubs come last, so the sidebar draws one rule above them', () => {
    const placements = HUBS.map((h) => h.placement === 'utility');
    expect(placements.indexOf(true)).toBeGreaterThan(0);
    expect(placements.slice(placements.indexOf(true)).every(Boolean)).toBe(true);
  });

  it('command-palette entries are a subset of manifest pages with matching roles', () => {
    const roleByPath = Object.fromEntries(NAV_PAGES.map((p) => [p.path, p.requiredRole]));
    for (const cmd of NAV_COMMANDS) {
      expect(roleByPath[cmd.path]).toBe(cmd.requiredRole);
    }
  });

  it('matches App.tsx route role gates (no drift across the three surfaces)', () => {
    const appRoles = appRouteRoles();
    const mismatches: string[] = [];
    for (const page of NAV_PAGES) {
      const appRole = appRoles[page.path];
      if (appRole === undefined) {
        mismatches.push(`${page.path}: no <ProtectedRoute requiredRole> in App.tsx`);
      } else if (appRole !== page.requiredRole) {
        mismatches.push(
          `${page.path}: manifest=${page.requiredRole} but App.tsx=${appRole}`,
        );
      }
    }
    expect(mismatches).toEqual([]);
  });

  it('every hub landing has a route gated at the hub role (HUB_DEFS coverage)', () => {
    const appRoles = appRouteRoles();
    for (const hub of HUB_DEFS) {
      expect(appRoles[hub.path]).toBe(hub.requiredRole);
    }
  });

  // CR5-R1 — reverse direction: a static, role-gated top-level route must be
  // accounted for by the manifest (a nav page or a hub landing) OR explicitly
  // listed as an intentional non-nav surface below.  This catches a new page
  // wired into App.tsx but forgotten in the IA (so it'd be reachable only by
  // deep link).  Param routes (detail/compare/sub-tabs) are excluded.
  const INTENTIONAL_NON_NAV = new Set<string>([
    '/recon/compare',
    '/scans/compare',
    '/test-plans/compare',
    '/default-credentials',
    '/tool-reference',
    '/reference/user-guide',
    // User-guide sub-pages — reached via the in-page section tab strip, not the
    // sidebar IA (the guide is one logical destination split for readability).
    '/reference/user-guide/data',
    '/reference/user-guide/triage',
    '/reference/user-guide/agents',
    '/reference/user-guide/admin',
    '/reference/sbom',
    // v5.296.0 — a Reference hub entry, like the SBOM.
    '/reference/tool-coverage',
  ]);

  it('no static top-level route is missing from the manifest', () => {
    const appRoles = appRouteRoles();
    const manifestPaths = new Set(NAV_PAGES.map((p) => p.path));
    const hubPaths = new Set(HUB_DEFS.map((h) => h.path));
    const orphans = Object.keys(appRoles).filter(
      (path) =>
        path.startsWith('/') &&
        !path.includes(':') &&
        !manifestPaths.has(path) &&
        !hubPaths.has(path) &&
        !INTENTIONAL_NON_NAV.has(path),
    );
    expect(orphans).toEqual([]);
  });
});
