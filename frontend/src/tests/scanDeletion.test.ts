import { describe, it, expect } from 'vitest';
import {
  hostsWithWorkCount,
  hostsWithWorkRefusal,
  importRunningNotice,
  importRunningRefusal,
  workPhrase,
} from '../utils/scanDeletion';

describe('workPhrase', () => {
  it('says one of a kind in the singular and several in the plural', () => {
    expect(workPhrase({ notes: 1 })).toBe('1 note');
    expect(workPhrase({ notes: 2 })).toBe('2 notes');
    expect(workPhrase({ evidence: 1 })).toBe('1 evidence record');
    expect(workPhrase({ evidence: 3 })).toBe('3 evidence records');
    expect(workPhrase({ remediation_entries: 1 })).toBe('1 remediation entry');
    expect(workPhrase({ remediation_entries: 4 })).toBe('4 remediation entries');
    expect(workPhrase({ corrections: 1, tags: 1 })).toBe('1 tag · 1 corrected name');
  });

  it('reads the kinds in one fixed order, whatever order the server sent', () => {
    expect(
      workPhrase({
        tags: 2, reviews: 1, remediation_entries: 1, proposals: 2,
        notes: 2, tests: 1, evidence: 1, findings: 3,
      }),
    ).toBe(
      '3 findings · 1 evidence record · 1 test · 2 notes · 2 proposals · 1 remediation entry · 1 review · 2 tags',
    );
  });

  it('prints a kind it does not know under the server\'s name, after the known ones', () => {
    expect(workPhrase({ zeta_marks: 2, notes: 1, attachments: 1 })).toBe('1 note · 1 attachments · 2 zeta_marks');
  });

  it('leaves out a zero count and survives no work at all', () => {
    expect(workPhrase({ notes: 0, tests: 2 })).toBe('2 tests');
    expect(workPhrase({})).toBe('');
    expect(workPhrase(null)).toBe('');
  });
});

describe('hostsWithWorkCount', () => {
  it('reads a missing field from an older server as none', () => {
    expect(hostsWithWorkCount({})).toBe(0);
    expect(hostsWithWorkCount(null)).toBe(0);
    expect(hostsWithWorkCount({ hosts_with_work: null })).toBe(0);
    expect(hostsWithWorkCount({ hosts_with_work: 3 })).toBe(3);
  });
});

describe('hostsWithWorkRefusal', () => {
  const refusal = (status: number, detail: unknown) => ({ response: { status, data: { detail } } });

  it('recognises the 409 and keeps the server\'s message', () => {
    expect(
      hostsWithWorkRefusal(refusal(409, { error: 'hosts_with_work', hosts_with_work: 2, message: 'Two hosts have work.' })),
    ).toEqual({ hosts_with_work: 2, message: 'Two hosts have work.' });
  });

  it('is null for any other failure', () => {
    expect(hostsWithWorkRefusal(refusal(409, 'An import is running'))).toBeNull();
    expect(hostsWithWorkRefusal(refusal(409, { error: 'cleanup_locked', message: 'x' }))).toBeNull();
    expect(hostsWithWorkRefusal(refusal(500, { error: 'hosts_with_work' }))).toBeNull();
    expect(hostsWithWorkRefusal(new Error('network'))).toBeNull();
    expect(hostsWithWorkRefusal(undefined)).toBeNull();
  });
});

describe('importRunningRefusal', () => {
  const refusal = (status: number, detail: unknown) => ({ response: { status, data: { detail } } });

  it('recognises the 409 and keeps the server\'s message', () => {
    expect(importRunningRefusal(refusal(409, { error: 'import_running', message: 'An import of "a.xml" is running.' })))
      .toBe('An import of "a.xml" is running.');
  });

  it('has words of its own when the server sent none', () => {
    expect(importRunningRefusal(refusal(409, { error: 'import_running' })))
      .toBe('An import is running in this project. Nothing was changed; try again when it finishes.');
  });

  it('is null for any other failure', () => {
    expect(importRunningRefusal(refusal(409, 'An import is running'))).toBeNull();
    expect(importRunningRefusal(refusal(409, { error: 'hosts_with_work', hosts_with_work: 1 }))).toBeNull();
    expect(importRunningRefusal(refusal(500, { error: 'import_running' }))).toBeNull();
    expect(importRunningRefusal(undefined)).toBeNull();
  });
});

describe('importRunningNotice', () => {
  it('names the file when the preview does, and is null when no import is running', () => {
    expect(importRunningNotice({ import_running: true, import_running_filename: 'sweep.nessus' }))
      .toBe('An import of "sweep.nessus" is running in this project. Delete this scan when it finishes.');
    expect(importRunningNotice({ import_running: true, import_running_filename: null }))
      .toBe('An import is running in this project. Delete this scan when it finishes.');
    expect(importRunningNotice({ import_running: false, import_running_filename: 'x' })).toBeNull();
    expect(importRunningNotice({})).toBeNull();
    expect(importRunningNotice(null)).toBeNull();
  });
});
