import { describe, expect, it } from 'vitest';

import type { HostTest } from '../services/api';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

import {
  hostTestCounts, issueTestSummary, resolveCommand, stripAgentMark, testNeedsWork, testResultState,
} from '../utils/hostTests';

const t = (over: Partial<HostTest>): HostTest =>
  ({ status: 'proposed', evidence_count: 0, last_outcome: null, unpromoted_findings: 0, finding_ids: [], ...over }) as HostTest;

// The host row's two counts travelled under their test-plan names until
// 2.473.0 / 5.355.0, with this helper as the one reader so that the rename
// would be a change in two files.  It was; the guard now says the retired
// names are read nowhere.
describe('hostTestCounts', () => {
  it('names the counts by what they mean, zero when absent', () => {
    expect(hostTestCounts({ planned_test_count: 2, tested_record_count: 3 })).toEqual({ toDo: 2, recorded: 3 });
    expect(hostTestCounts({})).toEqual({ toDo: 0, recorded: 0 });
  });

  it('nothing reads the retired wire names any more', () => {
    const src = join(__dirname, '..');
    const allowed = new Set<string>();
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) {
          if (name !== 'tests') walk(full);
        } else if (/\.tsx?$/.test(name)) {
          const rel = full.slice(src.length + 1).split('\\').join('/');
          if (!allowed.has(rel) && /test_plan_entry_count|test_execution_count/.test(readFileSync(full, 'utf8'))) {
            offenders.push(rel);
          }
        }
      }
    };
    walk(src);
    expect(offenders).toEqual([]);
  });
});

describe('host test helpers', () => {
  it('fills {ip}, and {fqdn} only when the test names one', () => {
    expect(resolveCommand('curl https://{ip}/ -H "Host: {fqdn}"', '10.0.0.5', 'a.example'))
      .toBe('curl https://10.0.0.5/ -H "Host: a.example"');
    expect(resolveCommand('curl https://{fqdn}/', '10.0.0.5', null)).toBe('curl https://{fqdn}/');
    expect(resolveCommand('nmap {ip}', undefined)).toBe('nmap {ip}');
  });

  it('drops the agent mark from the front of a reason and nothing else', () => {
    expect(stripAgentMark('🤖 **Agent-generated** (codex)\n\nBecause **x**.')).toBe('Because **x**.');
    expect(stripAgentMark('Because 🤖 said so.')).toBe('Because 🤖 said so.');
    expect(stripAgentMark(null)).toBe('');
  });

  it('a result with no finding yet outranks every other state', () => {
    expect(testResultState(t({ evidence_count: 2, unpromoted_findings: 1, finding_ids: [4], last_outcome: 'no_finding' })))
      .toEqual({ label: 'issue shown · no finding yet', tone: 'warn' });
    expect(testResultState(t({ evidence_count: 1, finding_ids: [4], last_outcome: 'finding' })).label).toBe('finding #4');
    expect(testResultState(t({ evidence_count: 1, last_outcome: 'no_finding' })).label).toBe('not present');
    expect(testResultState(t({ evidence_count: 1, last_outcome: 'failed' })).label).toBe('could not run');
    expect(testResultState(t({})).label).toBe('not run');
  });

  it('a finished test whose result nobody decided on still needs work', () => {
    expect(testNeedsWork(t({ status: 'in_progress' }))).toBe(true);
    expect(testNeedsWork(t({ status: 'done', unpromoted_findings: 1 }))).toBe(true);
    expect(testNeedsWork(t({ status: 'done' }))).toBe(false);
    expect(testNeedsWork(t({ status: 'dismissed', unpromoted_findings: 1 }))).toBe(false);
  });

  it('sums a weakness\'s tests into one phrase, undecided results first', () => {
    expect(issueTestSummary([])).toBeNull();
    expect(issueTestSummary([t({ status: 'dismissed' })])).toBeNull();
    expect(issueTestSummary([t({}), t({ status: 'done', unpromoted_findings: 1 })])?.label).toBe('test showed it · no finding yet');
    expect(issueTestSummary([t({ status: 'done', finding_ids: [9] })])?.label).toBe('confirmed by test');
    expect(issueTestSummary([t({ status: 'done', last_outcome: 'no_finding' })])?.label).toBe('tested · not present');
    expect(issueTestSummary([t({})])?.label).toBe('1 test to do');
  });
});

// 2026-10-01 — a result that JOINED a concluded finding does not re-status
// it; the toast is written from the response, not from what was asked for.
describe('promotedResultMessage', () => {
  it('says it joined the existing finding, and what that finding still is', async () => {
    const { promotedResultMessage } = await import('../utils/hostTests');
    expect(promotedResultMessage({ finding_id: 7, joined_issue: true, status: 'accepted_risk' }))
      .toBe('Joined the existing finding #7, which stays accepted risk.');
    expect(promotedResultMessage({ finding_id: 7, joined_issue: true })).toBe('Joined the existing finding #7.');
  });

  it('names the status of a finding it made from the response', async () => {
    const { promotedResultMessage } = await import('../utils/hostTests');
    expect(promotedResultMessage({ finding_id: 7, joined_issue: false, status: 'open' })).toBe('On finding #7 (open).');
    expect(promotedResultMessage({ finding_id: 7 })).toBe('On finding #7.');
  });
});
