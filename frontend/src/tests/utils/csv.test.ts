/**
 * `parseCsv` — the read behind the default-credentials sheet.  The page used
 * to split each line on bare commas, so a quoted password holding a comma was
 * cut at it.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { parseCsv } from '../../utils/csv';

describe('parseCsv', () => {
  it('reads plain rows, LF or CRLF, with or without a last line break', () => {
    expect(parseCsv('a,b,c\n1,2,3\n')).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
    expect(parseCsv('a,b,c\r\n1,2,3')).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
  });

  it('keeps a comma, a doubled quote and a line break inside a quoted field', () => {
    expect(parseCsv('Acme,admin,"pa,ss"\n')).toEqual([['Acme', 'admin', 'pa,ss']]);
    expect(parseCsv('Acme,admin,"say ""hi"""\n')).toEqual([['Acme', 'admin', 'say "hi"']]);
    expect(parseCsv('Acme,"two\nlines",x\n')).toEqual([['Acme', 'two\nlines', 'x']]);
  });

  it('keeps empty fields and a quote that does not open its field', () => {
    expect(parseCsv('Acme,,\n')).toEqual([['Acme', '', '']]);
    expect(parseCsv('Acme,"",x\n')).toEqual([['Acme', '', 'x']]);
    expect(parseCsv('Acme,5"disk,x\n')).toEqual([['Acme', '5"disk', 'x']]);
  });

  it('gives a blank line as one empty field and nothing for empty text', () => {
    expect(parseCsv('a,b\n\nc,d\n')).toEqual([['a', 'b'], [''], ['c', 'd']]);
    expect(parseCsv('')).toEqual([]);
  });

  it('reads the shipped sheet exactly as the comma split did (it has no quoted field today)', () => {
    const text = readFileSync(resolve(process.cwd(), 'public/DefaultCreds-Cheat-Sheet.csv'), 'utf8');
    const naive = text.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => line.split(','));
    const parsed = parseCsv(text).filter((row) => row.some((field) => field.trim() !== ''));
    expect(parsed.length).toBeGreaterThan(3000);
    expect(parsed.map((row) => row.map((field) => field.trim()))).toEqual(naive.map((row) => row.map((f) => f.trim())));
  });
});
