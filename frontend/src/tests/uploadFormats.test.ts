/**
 * The Scans page's advertised formats must cover every parser the
 * documentation table lists (documentation/UPLOAD_FORMATS.md), which the
 * backend's detection↔dispatch contract test pins to the real dispatcher.
 *
 * v5.204.0 — WhatWeb, testssl.sh and RDAP were detected and dispatched for
 * months while the upload page never mentioned them; nothing tied the two
 * lists together.  Same shape as versionConsistency.test.ts: read the repo
 * file directly rather than trusting a hand-copied fixture.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { SUPPORTED_FORMATS } from '../data/uploadFormats';

const DOC = resolve(__dirname, '../../../documentation/UPLOAD_FORMATS.md');

/** Tool names from the first column of the Markdown table. */
const documentedTools = (): string[] =>
  readFileSync(DOC, 'utf8')
    .split('\n')
    .filter((line) => line.startsWith('| '))
    .map((line) => line.split('|')[1]?.trim() ?? '')
    .filter((tool) => tool && tool !== 'Tool' && !/^-+$/.test(tool));

// Rows in the doc that are deliberately NOT scan uploads on this page.
const NOT_A_SCAN_UPLOAD = new Set(['Subnet lists']); // Scope import page

// Doc name → the word the UI entry must contain (case-insensitive).
const KEY_WORD: Record<string, string> = {
  'DNS inventories': 'DNS records',
  'Eyewitness': 'EyeWitness',
};

describe('advertised upload formats', () => {
  it('lists every parser documented in UPLOAD_FORMATS.md', () => {
    const tools = documentedTools().filter((t) => !NOT_A_SCAN_UPLOAD.has(t));
    expect(tools.length).toBeGreaterThan(10);
    const advertised = SUPPORTED_FORMATS.map((f) => f.tool.toLowerCase());
    const missing = tools.filter((tool) => {
      const key = (KEY_WORD[tool] ?? tool.split(' ')[0]).toLowerCase();
      return !advertised.some((label) => label.includes(key));
    });
    expect(missing, `documented but not advertised on /scans: ${missing.join(', ')}`).toEqual([]);
  });

  it('names the three formats that drifted out of the list', () => {
    const labels = SUPPORTED_FORMATS.map((f) => f.tool);
    expect(labels).toContain('WhatWeb');
    expect(labels).toContain('testssl.sh');
    expect(labels).toContain('RDAP');
  });
});
