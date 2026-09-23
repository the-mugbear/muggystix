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
