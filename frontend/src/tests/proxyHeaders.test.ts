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

/**
 * Branch review 2026-10-01 — a location that declares ANY add_header drops
 * every add_header it would have inherited (nginx's rule, for nested
 * locations too).  The static-asset location sent scripts and stylesheets
 * with `nosniff` and nothing else, its nested font location sent only the
 * CORS header, and `/live` — whose one add_header was a Content-Type — sent no
 * security header at all.
 *
 * So: every location that has an add_header of its OWN repeats the set.  The
 * directives are read per location, without those of the locations nested in
 * it, because that is how nginx applies them.
 */
describe('nginx security headers', () => {
  interface Block {
    name: string;
    own: string[];
  }

  const blocks: Block[] = [];
  const stack: (Block | null)[] = [];
  for (const raw of active.split('\n')) {
    const line = raw.trim();
    const open = /^location\s+([^{]+?)\s*\{$/.exec(line);
    if (open) {
      stack.push({ name: open[1].trim(), own: [] });
    } else if (line.endsWith('{')) {
      stack.push(null); // server { … }
    } else if (line === '}') {
      const closed = stack.pop();
      if (closed) blocks.push(closed);
    } else {
      const top = stack[stack.length - 1];
      if (top) top.own.push(line);
    }
  }
  const withOwnHeaders = blocks.filter((b) => b.own.some((l) => l.startsWith('add_header ')));

  // X-XSS-Protection is left out of the API-docs locations on purpose (their
  // CSP is relaxed for Swagger / ReDoc and the header is legacy); the other
  // five are what every response must carry.
  const required = [
    'Strict-Transport-Security',
    'X-Frame-Options',
    'X-Content-Type-Options',
    'Referrer-Policy',
    'Content-Security-Policy',
  ];

  it('finds the locations it is meant to check', () => {
    expect(stack).toEqual([]); // every brace closed: the reader did not lose its place
    const names = withOwnHeaders.map((b) => b.name);
    expect(names).toContain('/');
    expect(names).toContain('^~ /api/');
    expect(names.filter((n) => n.startsWith('~*') && n.includes('woff')).length).toBe(2);
  });

  it('repeats the security set in every location that declares an add_header', () => {
    for (const block of withOwnHeaders) {
      for (const header of required) {
        const lines = block.own.filter((l) => l.startsWith(`add_header ${header} `));
        expect(lines.length, `location ${block.name}: ${header}`).toBe(1);
        expect(lines[0].endsWith(' always;'), `location ${block.name}: ${header} needs "always"`).toBe(true);
      }
    }
  });

  it('serves static assets and fonts with the complete set, and fonts keep their cache header', () => {
    const assets = withOwnHeaders.filter((b) => b.name.startsWith('~*') && b.name.includes('woff'));
    for (const block of assets) {
      expect(block.own.some((l) => l.startsWith('add_header X-XSS-Protection '))).toBe(true);
      expect(block.own).toContain('add_header Cache-Control "public, immutable";');
    }
    const fonts = assets.find((b) => !b.name.includes('png'));
    expect(fonts?.own).toContain('add_header Access-Control-Allow-Origin "*";');
  });

  it('gives /live no add_header of its own, so it inherits the server-level set', () => {
    const live = blocks.find((b) => b.name === '= /live');
    expect(live).toBeDefined();
    expect(live?.own.some((l) => l.startsWith('add_header '))).toBe(false);
    expect(live?.own).toContain('default_type text/plain;');
  });

  it('declares the complete set at server level, for the locations that inherit it', () => {
    const server = active.slice(0, active.search(/^\s*location\s/m));
    for (const header of [...required, 'X-XSS-Protection']) {
      expect(server).toContain(`add_header ${header} `);
    }
  });
});
