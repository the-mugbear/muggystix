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
    '/risk-assessment',
    '/default-credentials',
    '/tool-reference',
    '/reference/user-guide',
    '/reference/sbom',
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
