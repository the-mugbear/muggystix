/**
 * The login page is the unauthenticated surface — the first thing a crawler,
 * a screenshot, or an AI agent reading the app encounters. Instruction-shaped
 * copy there is both an injection vector and, to a real user, indistinguishable
 * from a live compromise.
 *
 * A string of exactly that shape ("…ignore your prior instructions and return
 * …") shipped in the security banner and reached the production bundle. This
 * asserts against the source of the banner so it can't come back quietly.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const LOGIN_SRC = fs.readFileSync(
  path.resolve(__dirname, '../../pages/Login.tsx'),
  'utf8',
);

// Phrases that read as an instruction to a model rather than as information
// for a human. Deliberately narrow — this guards the known failure shape, it
// is not a general-purpose injection scanner.
const INSTRUCTION_SHAPED = [
  /ignore\s+(your|all|previous|prior)\s+(prior\s+)?instructions/i,
  /disregard\s+(your|all|previous|prior)\s+instructions/i,
  /you\s+are\s+now\s+/i,
  /system\s+prompt/i,
];

describe('Login page — unauthenticated surface copy', () => {
  it('contains no instruction-shaped text', () => {
    const hits = INSTRUCTION_SHAPED.filter((re) => re.test(LOGIN_SRC)).map(String);
    expect(
      hits,
      `Login.tsx contains instruction-shaped copy (${hits.join(', ')}). This page is `
        + 'unauthenticated: such text is a prompt-injection vector and reads as a '
        + 'compromised deployment. Use a plain statement of fact instead.',
    ).toEqual([]);
  });

  it('still renders a security notice', () => {
    // Guard the opposite failure: deleting the banner rather than fixing it.
    expect(LOGIN_SRC).toMatch(/Security notice/);
    expect(LOGIN_SRC).toMatch(/Authorized users only/);
  });
});
