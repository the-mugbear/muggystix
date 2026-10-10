/**
 * The format chooser offers the formats that match the file first (owner
 * decision 39, 2026-10-10) — `utils/formatChoices`.
 *
 * The extensions per format are declared on the frontend because the server's
 * format list does not carry them.  Two pins keep that table honest: its keys
 * are exactly the server's registry (`format_registry.FORMATS`, read from the
 * Python source as `uploadFormatContract.test.ts` reads the allowlist), and
 * its extensions are the dropzone's.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

import { ACCEPTED_EXTENSION_LIST } from '../data/uploadFormats';
import { FORMAT_EXTENSIONS, fileExtension, formatChoices } from '../utils/formatChoices';

const repoRoot = join(__dirname, '..', '..', '..');

const registryKeys = (): string[] => {
  const src = readFileSync(join(repoRoot, 'backend', 'app', 'services', 'format_registry.py'), 'utf8');
  return [...src.matchAll(/_spec\(\s*"([a-z0-9_]+)"/g)].map((m) => m[1]);
};

const option = (file_type: string) => ({ file_type, label: file_type, family: 'other' });
const FORMATS = ['nmap_xml', 'nessus_xml', 'masscan_json', 'nikto_csv', 'naabu_output', 'eyewitness_zip', 'nmap_gnmap']
  .map(option);
const shownFor = (filename: string, opts?: Parameters<typeof formatChoices>[2]) =>
  formatChoices(FORMATS, filename, opts).shown.map((f) => f.file_type);

describe('FORMAT_EXTENSIONS — pinned to the server and the dropzone', () => {
  it('declares every format the server registry has, and no other', () => {
    const keys = registryKeys();
    expect(keys.length).toBeGreaterThan(20);
    expect(Object.keys(FORMAT_EXTENSIONS).sort()).toEqual([...keys].sort());
  });

  it('names only extensions the upload accepts, and every accepted extension has a format', () => {
    const declared = new Set(Object.values(FORMAT_EXTENSIONS).flat());
    expect([...declared].filter((ext) => !ACCEPTED_EXTENSION_LIST.includes(ext))).toEqual([]);
    expect(ACCEPTED_EXTENSION_LIST.filter((ext) => !declared.has(ext))).toEqual([]);
  });
});

describe('fileExtension', () => {
  it('is the last dotted part, lower-cased; none for a bare or dot-led name', () => {
    expect(fileExtension('scan.XML')).toBe('.xml');
    expect(fileExtension('a.b/results.final.jsonl')).toBe('.jsonl');
    expect(fileExtension('results')).toBeNull();
    expect(fileExtension('.hidden')).toBeNull();
    expect(fileExtension('trailing.')).toBeNull();
  });
});

describe('formatChoices', () => {
  it('an .xml file is offered the XML formats, the rest held back', () => {
    const choices = formatChoices(FORMATS, 'internal-sweep.xml');
    expect(choices.shown.map((f) => f.file_type)).toEqual(['nmap_xml', 'nessus_xml']);
    expect(choices.hidden).toBe(5);
    expect(choices.extension).toBe('.xml');
  });

  it('matches whatever the case of the extension, and each accepted kind', () => {
    expect(shownFor('SCAN.NESSUS')).toEqual(['nessus_xml']);
    expect(shownFor('out.ndjson')).toEqual(['masscan_json']);
    expect(shownFor('ports.txt')).toEqual(['naabu_output']);
    expect(shownFor('bundle.zip')).toEqual(['eyewitness_zip']);
    expect(shownFor('hosts.gnmap')).toEqual(['nmap_gnmap']);
  });

  it('"Show all formats" offers everything', () => {
    const choices = formatChoices(FORMATS, 'internal-sweep.xml', { showAll: true });
    expect(choices.shown).toHaveLength(FORMATS.length);
    expect(choices.hidden).toBe(0);
    expect(choices.extension).toBeNull();
  });

  it('a file with no extension, or one no format declares, is offered everything', () => {
    expect(shownFor('results')).toHaveLength(FORMATS.length);
    expect(shownFor('results.log')).toHaveLength(FORMATS.length);
    expect(formatChoices(FORMATS, 'results.log').hidden).toBe(0);
  });

  it('never holds back the format that is selected', () => {
    expect(shownFor('internal-sweep.xml', { keep: 'nikto_csv' })).toEqual(['nmap_xml', 'nessus_xml', 'nikto_csv']);
  });

  it('a format the table does not know is always offered', () => {
    const withNew = [...FORMATS, option('brand_new_format')];
    expect(formatChoices(withNew, 'internal-sweep.xml').shown.map((f) => f.file_type))
      .toEqual(['nmap_xml', 'nessus_xml', 'brand_new_format']);
  });
});
