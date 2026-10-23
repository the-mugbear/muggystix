/**
 * CR4-8 — version single-source guard.
 *
 * platform_version.json is the source of truth for both versions.  This test
 * fails the build if frontend/package.json drifts from it (the exact bug this
 * was added for: package.json stuck at 5.10.0 while platform said 5.15.0, so
 * the version shown in the user menu's About item — stamped by
 * generate-build-info.js — reported a stale build).  Keep them in sync: platform_version.json, package.json, and
 * the frontend image's two build-arg fallbacks in docker-compose (scripts/bump-version.py writes them all).
 *
 * Read via fs (not import) so vite's fs-allow guard doesn't block reading
 * platform_version.json from the repo root, and so a missing file is a clear
 * skip rather than a module-resolution crash.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, it, expect } from 'vitest';

const frontendRoot = join(__dirname, '..', '..');
const repoRoot = join(frontendRoot, '..');

function readJson(path: string): any {
  return JSON.parse(readFileSync(path, 'utf8'));
}

describe('version consistency', () => {
  const pkg = readJson(join(frontendRoot, 'package.json'));
  const platform = readJson(join(repoRoot, 'platform_version.json'));

  it('frontend/package.json matches platform_version.json "frontend"', () => {
    expect(pkg.version).toBe(platform.frontend);
  });

  it('platform_version.json declares both versions', () => {
    expect(platform.frontend).toMatch(/^\d+\.\d+\.\d+$/);
    expect(platform.backend).toMatch(/^\d+\.\d+\.\d+$/);
  });

  // CR5-R2 — the docker-compose APP_VERSION / FRONTEND_VERSION fallbacks
  // (``${VAR:-x.y.z}``) are hand-maintained and stamp the deployed build
  // when the env var is unset.  This guard caught nothing before because it
  // never read them; now every fallback must equal platform_version.json so
  // a default can't silently drift and ship the wrong deployment metadata.
  describe('docker-compose version fallbacks match platform_version.json', () => {
    const compose = readFileSync(join(repoRoot, 'docker-compose.yml'), 'utf8');
    const fallbacks = (varName: string): string[] => {
      const re = new RegExp(`\\$\\{${varName}:-([0-9]+\\.[0-9]+\\.[0-9]+)\\}`, 'g');
      return [...compose.matchAll(re)].map((m) => m[1]);
    };

    // Only the frontend image needs a typed version: it is built from
    // frontend/ alone and cannot read platform_version.json.  The backend,
    // worker and report-worker read the mounted file (PLATFORM_VERSION_FILE);
    // a fallback in their `environment:` would override the file and could
    // drift from it (backlog R10 removed the four that were there).
    it('has exactly the two frontend build-arg fallbacks, and no version in a service environment', () => {
      expect(fallbacks('APP_VERSION')).toHaveLength(1);
      expect(fallbacks('FRONTEND_VERSION')).toHaveLength(1);
      expect(compose).toMatch(/^\s+BACKEND_VERSION: \$\{APP_VERSION:-[0-9.]+\}$/m);
      expect(compose).toMatch(/^\s+FRONTEND_VERSION: \$\{FRONTEND_VERSION:-[0-9.]+\}$/m);
      expect(compose).not.toMatch(/^\s+APP_VERSION:/m);
    });

    it('every APP_VERSION fallback equals platform_version.json "backend"', () => {
      for (const v of fallbacks('APP_VERSION')) expect(v).toBe(platform.backend);
    });

    it('every FRONTEND_VERSION fallback equals platform_version.json "frontend"', () => {
      for (const v of fallbacks('FRONTEND_VERSION')) expect(v).toBe(platform.frontend);
    });
  });
});
