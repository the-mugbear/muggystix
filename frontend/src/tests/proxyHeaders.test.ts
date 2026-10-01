/**
 * Review 2026-09-23 C1 — the client IP must not be client-controlled.
 *
 * uvicorn runs with --forwarded-allow-ips=* and takes the LEFTMOST
 * X-Forwarded-For entry.  nginx is the only hop, so every proxied location
 * must replace the header with the peer address; `$proxy_add_x_forwarded_for`
 * appends to whatever the client sent, which let a request choose its own IP
 * for the per-IP login throttle and the audit log.
 *
 * Read via fs, like versionConsistency.test.ts, because the file lives at the
 * repo root.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, it, expect } from 'vitest';

const repoRoot = join(__dirname, '..', '..', '..');
const conf = readFileSync(join(repoRoot, 'ssl-nginx.conf'), 'utf8');
const active = conf
  .split('\n')
  .filter((line) => !line.trim().startsWith('#'))
  .join('\n');

describe('nginx forwarded headers', () => {
  it('never appends to a client-supplied X-Forwarded-For', () => {
    expect(active).not.toContain('$proxy_add_x_forwarded_for');
  });

  it('sets X-Forwarded-For to the peer address in every location that sets it', () => {
    const lines = active.split('\n').filter((l) => /X-Forwarded-For/i.test(l));
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect(line.trim()).toBe('proxy_set_header X-Forwarded-For $remote_addr;');
    }
  });
});

/**
 * Review 2026-10-01 N7 — an API path must never be answered by a regex
 * location.  nginx picks the longest matching PREFIX location, then tests the
 * regex locations unless that prefix carries `^~`.  Without it,
 * `location ~* \.(js|css|png|…)$` took any `/api/…` path ending in one of
 * those extensions and served it from the React root with a one-year
 * immutable cache header.
 */
describe('nginx API locations', () => {
  const locations = Array.from(active.matchAll(/^\s*location\s+([^{]+?)\s*\{/gm)).map((m) =>
    m[1].trim(),
  );
  const apiLocations = locations.filter((l) => /(^|\s)\/api\//.test(l));

  it('declares the API prefix and the auth prefix', () => {
    expect(apiLocations).toContain('^~ /api/');
    expect(apiLocations).toContain('^~ /api/v1/auth/');
  });

  it('gives every /api/ location the ^~ modifier, so no regex location can shadow it', () => {
    expect(apiLocations.length).toBeGreaterThan(0);
    for (const location of apiLocations) {
      expect(location.startsWith('^~ ')).toBe(true);
    }
  });

  it('keeps a static-asset regex location for the API prefix to be protected from', () => {
    // If this regex goes away the rule above is still right, but this test
    // should be re-read: it documents what `^~` protects against.
    expect(locations.some((l) => l.startsWith('~*') && l.includes('png'))).toBe(true);
  });

  it('keeps the auth surface capped at 1m and proxied', () => {
    const start = active.indexOf('location ^~ /api/v1/auth/ {');
    const block = active.slice(start, active.indexOf('\n    }', start));
    expect(block).toContain('client_max_body_size 1m;');
    expect(block).toContain('proxy_pass $backend_upstream$request_uri;');
  });

  it('repeats the full security-header set in every /api/ location', () => {
    // A location with any add_header of its own drops the server-level set.
    for (const prefix of ['location ^~ /api/ {', 'location ^~ /api/v1/auth/ {']) {
      const start = active.indexOf(prefix);
      expect(start).toBeGreaterThan(-1);
      const block = active.slice(start, active.indexOf('\n    }', start));
      for (const header of [
        'Strict-Transport-Security',
        'X-Frame-Options',
        'X-Content-Type-Options',
        'X-XSS-Protection',
        'Referrer-Policy',
        'Content-Security-Policy',
      ]) {
        expect(block).toContain(`add_header ${header} `);
      }
    }
  });
});
