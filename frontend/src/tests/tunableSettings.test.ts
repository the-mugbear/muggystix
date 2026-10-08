/**
 * The settings a deploy and status.sh show (`print_tunable_settings` in
 * scripts/stack-lib.sh) read each default from where it is defined:
 * `${NAME:-default}` in docker-compose.yml or a script, else the commented
 * `# NAME=value` line of .env.example.  A name on the list with no default in
 * any of those would print "built-in" on every host — so each one must
 * resolve here, and none may be a secret.
 *
 * Here rather than in the backend suite: this test can read the repository
 * root (the backend's runs only mount backend/ and scripts/).
 */
import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

import { describe, it, expect } from 'vitest';

const repoRoot = join(__dirname, '..', '..', '..');
const read = (...parts: string[]) => readFileSync(join(repoRoot, ...parts), 'utf8');

function listedNames(): string[] {
  const lib = read('scripts', 'stack-lib.sh');
  const block = lib.slice(lib.indexOf('TUNABLE_SETTINGS=('), lib.indexOf('\n)\n', lib.indexOf('TUNABLE_SETTINGS=(')));
  return [...block.matchAll(/^\s+"([A-Z][A-Z0-9_]*)\|/gm)].map((m) => m[1]);
}

function definedDefault(name: string): string | null {
  const sources = [
    read('docker-compose.yml'),
    ...readdirSync(join(repoRoot, 'scripts')).filter((f) => f.endsWith('.sh')).map((f) => read('scripts', f)),
  ];
  for (const text of sources) {
    const hit = text.match(new RegExp(`\\$\\{${name}:-([^}]*)\\}`));
    if (hit) return hit[1];
  }
  const example = read('.env.example').match(new RegExp(`^# ?${name}=(\\S+)$`, 'm'));
  return example ? example[1] : null;
}

describe('the settings a deploy shows', () => {
  const names = listedNames();

  it('lists the settings', () => {
    expect(names.length).toBeGreaterThanOrEqual(25);
    expect(new Set(names).size).toBe(names.length);
  });

  it('every one has a default this repository defines', () => {
    const missing = names.filter((name) => !definedDefault(name));
    expect(missing).toEqual([]);
  });

  it('none is a secret', () => {
    expect(names.filter((name) => /SECRET|PASSWORD|KEY|TOKEN/.test(name))).toEqual([]);
  });
});
