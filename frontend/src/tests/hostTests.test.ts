import { describe, expect, it } from 'vitest';

import type { HostTest } from '../services/api';
import { issueTestSummary, resolveCommand, stripAgentMark, testNeedsWork, testResultState } from '../utils/hostTests';

const t = (over: Partial<HostTest>): HostTest =>
  ({ status: 'proposed', evidence_count: 0, last_outcome: null, unpromoted_findings: 0, finding_ids: [], ...over }) as HostTest;

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
