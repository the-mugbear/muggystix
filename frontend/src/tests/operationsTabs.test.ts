/**
 * Operations' tabs (5.331.0): what each counts, which one opens, and how a
 * link names one.  Pure — `utils/operationsTabs`.
 */
import { describe, expect, it } from 'vitest';

import type { WorkbenchResponse } from '../services/api';
import {
  NO_COUNTS, OPERATIONS_TABS, firstNonEmptyTab, needFromParams, operationsTabCounts,
  tabFromParams, tabListSize, tabSearch, testKindFromParams, tierFromParams,
} from '../utils/operationsTabs';
import { fromOperationsQueue, operationsBackPath } from '../utils/operationsQueue';

const workbench = (over: Partial<WorkbenchResponse> = {}): WorkbenchResponse => ({
  my_queue: { items: [], in_review_count: 37 },
  my_tasks: {
    items: [], total_open: 55,
    reason_counts: { assigned: 12, in_review: 30, triage: 15 },
    group_counts: { assigned: 12, in_review: 28, triage: 15 },
  },
  recent_notes: { items: [] },
  my_findings: { items: [], total_open: 24 },
  since_last_visit: {
    last_viewed_at: null, is_first_visit: true, new_scan_count: 0, latest_scan_id: null,
    latest_scan_filename: null, latest_scan_created_at: null, new_host_count: 0,
    new_critical_findings: 0, new_high_findings: 0,
  },
  followups: { items: [], total: 27 },
  my_work: {
    total: 101, hosts_in_review: 37, tests_assigned: 12, tests_on_hosts_in_review: 28,
    findings_needing_me: 24, findings_to_decide: 3, findings_to_write: 21, to_claim: 15,
  },
  ...over,
});

describe('operationsTabCounts', () => {
  it('the bar, in order', () => {
    expect([...OPERATIONS_TABS]).toEqual(['findings', 'hosts', 'tests', 'changed', 'pickup']);
  });

  it('each tab’s count is the server’s, and the kinds within a tab add up to it', () => {
    const c = operationsTabCounts(workbench(), 112);
    expect(c).toEqual({
      findings: 24, findingsDecide: 3, findingsWrite: 21,
      hosts: 37,
      tests: 40, testsAssigned: 12, testsInReview: 28,
      toClaim: 15, changed: 27, pickup: 112,
    });
    // The lead says the kinds apart; each pair is its tab's count in parts.
    expect((c.findingsDecide ?? 0) + (c.findingsWrite ?? 0)).toBe(c.findings);
    expect((c.testsAssigned ?? 0) + (c.testsInReview ?? 0)).toBe(c.tests);
    // The Tests tab LISTS the claimable ones too.
    expect(tabListSize('tests', c)).toBe(55);
    expect(tabListSize('hosts', c)).toBe(37);
  });

  it('without the server’s sums (an older backend) the groups still count; the kinds of finding are not known', () => {
    const old = workbench({ my_work: undefined });
    expect(operationsTabCounts(old, null)).toMatchObject({
      findings: 24, hosts: 37, tests: 40, testsAssigned: 12, testsInReview: 28, toClaim: 15,
      // Not known — never 0.
      findingsDecide: null, findingsWrite: null,
    });
  });

  it('a count that is not known is null — never 0', () => {
    expect(operationsTabCounts(null, null)).toEqual(NO_COUNTS);
    expect(operationsTabCounts(null, 9).pickup).toBe(9);
    expect(operationsTabCounts(workbench({ followups_unavailable: true }), 3).changed).toBeNull();
    expect(tabListSize('tests', NO_COUNTS)).toBeNull();
    expect(Object.values(NO_COUNTS).every((v) => v === null)).toBe(true);
  });
});

describe('firstNonEmptyTab', () => {
  const counts = (over: Partial<typeof NO_COUNTS>) => ({
    findings: 0, findingsDecide: 0, findingsWrite: 0, hosts: 0,
    tests: 0, testsAssigned: 0, testsInReview: 0, toClaim: 0, changed: 0, pickup: 0, ...over,
  });

  it('is the first tab with rows, in bar order', () => {
    expect(firstNonEmptyTab(counts({ findings: 2, hosts: 5 }))).toBe('findings');
    expect(firstNonEmptyTab(counts({ hosts: 5, changed: 9 }))).toBe('hosts');
    expect(firstNonEmptyTab(counts({ changed: 9, pickup: 100 }))).toBe('changed');
  });

  it('Tests counts as non-empty for claimable tests alone — the tab lists them', () => {
    expect(firstNonEmptyTab(counts({ toClaim: 4, changed: 9 }))).toBe('tests');
  });

  it('falls to Pick up when nothing personal has rows, whatever the queue holds', () => {
    expect(firstNonEmptyTab(counts({ pickup: 12 }))).toBe('pickup');
    expect(firstNonEmptyTab(counts({}))).toBe('pickup');
    // The queue's count arrives later: the choice never waits for it.
    expect(firstNonEmptyTab(counts({ pickup: null }))).toBe('pickup');
  });

  it('an unknown count is not a reason to open a tab', () => {
    expect(firstNonEmptyTab(counts({ findings: null, hosts: 3 }))).toBe('hosts');
  });
});

describe('the URL', () => {
  const p = (s: string) => new URLSearchParams(s);

  it('reads ?tab=, ?kind= and ?tier=, and ignores what it does not know', () => {
    expect(tabFromParams(p('tab=changed'))).toBe('changed');
    expect(tabFromParams(p('tab=everything'))).toBeNull();
    expect(tabFromParams(p(''))).toBeNull();
    expect(testKindFromParams(p('kind=triage'))).toBe('triage');
    expect(testKindFromParams(p('kind=mine'))).toBeNull();
    expect(needFromParams(p('need=decide'))).toBe('decide');
    expect(needFromParams(p('need=write'))).toBe('write');
    expect(needFromParams(p('need=everything'))).toBeNull();
    expect(needFromParams(p(''))).toBeNull();
    expect(tierFromParams(p('tier=3'))).toBe(3);
    expect(tierFromParams(p('tier=9'))).toBeNull();
    expect(tierFromParams(p(''))).toBeNull();
  });

  it('a tab link keeps the page’s other parameters; the test kind belongs to the Tests tab', () => {
    expect(tabSearch(p('tier=2'), 'pickup')).toBe('?tier=2&tab=pickup');
    expect(tabSearch(p('tab=hosts&tier=2'), 'tests', 'triage')).toBe('?tab=tests&tier=2&kind=triage');
    // Leaving Tests drops its filter; returning to it with none asked keeps it.
    expect(tabSearch(p('tab=tests&kind=triage'), 'hosts')).toBe('?tab=hosts');
    expect(tabSearch(p('tab=tests&kind=triage'), 'tests')).toBe('?tab=tests&kind=triage');
    expect(tabSearch(p('tab=tests&kind=triage'), 'tests', null)).toBe('?tab=tests');
    expect(tabSearch('?start=x', 'findings')).toBe('?start=x&tab=findings');
  });

  it('a tab link never carries the list’s page: a tab opens on its first', () => {
    expect(tabSearch(p('tab=tests&page=3'), 'hosts')).toBe('?tab=hosts');
    expect(tabSearch(p('tab=tests&kind=triage&page=3'), 'tests', 'assigned')).toBe('?tab=tests&kind=assigned');
  });

  it('the need belongs to the Findings tab, exactly as the kind belongs to Tests', () => {
    expect(tabSearch(p('tab=hosts&tier=2'), 'findings', 'write')).toBe('?tab=findings&tier=2&need=write');
    // Leaving Findings drops its filter; returning to it with none asked keeps it.
    expect(tabSearch(p('tab=findings&need=write'), 'hosts')).toBe('?tab=hosts');
    expect(tabSearch(p('tab=findings&need=write'), 'findings')).toBe('?tab=findings&need=write');
    expect(tabSearch(p('tab=findings&need=write'), 'findings', null)).toBe('?tab=findings');
    expect(tabSearch(p('tab=findings&need=write'), 'findings', 'decide')).toBe('?tab=findings&need=decide');
    // One tab's filter never survives on the other.
    expect(tabSearch(p('tab=findings&need=write'), 'tests', 'assigned')).toBe('?tab=tests&kind=assigned');
    expect(tabSearch(p('tab=tests&kind=triage'), 'findings', 'decide')).toBe('?tab=findings&need=decide');
    // A filter that is not this tab's is not written.
    expect(tabSearch(p(''), 'findings', 'triage')).toBe('?tab=findings');
  });
});

describe('the way back from a host', () => {
  it('"Back to my work" returns to the tab the host was opened from', () => {
    expect(operationsBackPath(fromOperationsQueue([5, 8], 'Hosts I am reviewing', { tab: 'hosts' }).state))
      .toBe('/operations?tab=hosts');
    // A queue of one still knows its tab.
    expect(fromOperationsQueue([5], 'Changed since review', { tab: 'changed' }).state)
      .toEqual({ fromOperations: true, operationsTab: 'changed' });
    expect(operationsBackPath({ operationsTab: 'tests' })).toBe('/operations?tab=tests');
  });

  it('with no tab — or anything that is not one — it is the page', () => {
    expect(operationsBackPath(fromOperationsQueue([5, 8], 'x').state)).toBe('/operations');
    expect(operationsBackPath(null)).toBe('/operations');
    expect(operationsBackPath({ operationsTab: 'hosts&x=1' })).toBe('/operations');
    expect(operationsBackPath({ operationsTab: 7 })).toBe('/operations');
  });
});
