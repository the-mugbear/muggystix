import { describe, expect, it } from 'vitest';
import { filenameSummary } from '../utils/filenameSummary';

describe('filenameSummary', () => {
  it('folds near-identical names into their shared shape', () => {
    expect(filenameSummary([
      'mssql-privesc--block-04.txt', 'mssql-privesc--block-03.txt', 'mssql-privesc--block-02.txt',
    ])).toBe('mssql-privesc--block-0*.txt');
  });

  it('lists unlike names as they are', () => {
    expect(filenameSummary(['a.xml', 'b.nessus'])).toBe('a.xml, b.nessus');
    expect(filenameSummary(['only.xml'])).toBe('only.xml');
  });

  it('never repeats characters when one name is the other plus a tail', () => {
    expect(filenameSummary(['scan-results.xml', 'scan-results.xml.bak'])).toBe('scan-results.xml*');
  });
});
