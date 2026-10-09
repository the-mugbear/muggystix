/**
 * Host-test vocabulary (5.320.0) — one place for the words and colours of a
 * test's status, so the host page, Operations and the session page agree.
 *
 * A test is *proposed* (nobody has started it), *in progress*, *done*, or
 * *dismissed* (decided not to run, with a reason). A host is "planned" while
 * it has a proposed or in-progress test, and "tested" once evidence with a
 * real outcome exists for it — those are the backend's definitions
 * (`host_test_queries`), not derived here.
 */
import type { Host, HostTest, HostTestStatus } from '../services/api';

/**
 * A host row's test counts, by what they mean.
 *
 * `planned_test_count` is the host's tests proposed or in progress, and
 * `tested_record_count` its evidence records with a tested outcome (the wire
 * named them after test plans until 2.473.0).  A row that does not carry
 * them counts as none.
 */
export const hostTestCounts = (
  row: Pick<Host, 'planned_test_count' | 'tested_record_count'>,
): { toDo: number; recorded: number } => ({
  toDo: row.planned_test_count ?? 0,
  recorded: row.tested_record_count ?? 0,
});

export const HOST_TEST_STATUS_LABEL: Record<HostTestStatus, string> = {
  proposed: 'Proposed',
  in_progress: 'In progress',
  done: 'Done',
  dismissed: 'Dismissed',
};

export const hostTestStatusLabel = (status: string): string =>
  HOST_TEST_STATUS_LABEL[status as HostTestStatus] ?? status.replace(/_/g, ' ');

export const hostTestStatusVariant = (status: string): 'info' | 'warning' | 'success' | 'muted' => {
  switch (status) {
    case 'proposed':
      return 'info';
    case 'in_progress':
      return 'warning';
    case 'done':
      return 'success';
    default:
      return 'muted';
  }
};

/** Statuses that still need someone — the ones that make a host "planned". */
export const ACTIVE_HOST_TEST_STATUSES: HostTestStatus[] = ['proposed', 'in_progress'];

/** Does this test still need a person? It is to do, or its result showed an
 *  issue that nobody has made a finding of — a finished test in that state
 *  must not leave the to-do list, or the decision it is waiting for is lost. */
export const testNeedsWork = (test: Pick<HostTest, 'status' | 'unpromoted_findings'>): boolean =>
  test.status === 'proposed' || test.status === 'in_progress'
  || (test.status === 'done' && (test.unpromoted_findings ?? 0) > 0);

/** A test's command with its placeholders filled: `{ip}` is the host's
 *  address, `{fqdn}` the name the test is aimed at (left as written when the
 *  test names none — never silently replaced by the address). */
export const resolveCommand = (command: string, hostIp: string | undefined, targetFqdn?: string | null): string => {
  const withIp = hostIp ? command.replace(/\{ip\}/g, hostIp) : command;
  return targetFqdn ? withIp.replace(/\{fqdn\}/g, targetFqdn) : withIp;
};

/** An agent's notes used to open with an attribution mark; a test carries its
 *  attribution as data, so the mark is noise in front of the reason. */
export const stripAgentMark = (text: string | null | undefined): string =>
  (text ?? '').replace(/^\s*🤖\s*\*\*Agent-generated\*\*[^\n]*\n+/u, '').trim();

export type ResultTone = 'warn' | 'ok' | 'muted' | 'info';

/** What a test's results come to, in the few words its one-line row carries.
 *  "Issue shown · no finding yet" is the state that needs a person. */
export const testResultState = (
  test: Pick<HostTest, 'evidence_count' | 'last_outcome' | 'unpromoted_findings' | 'finding_ids'>,
): { label: string; tone: ResultTone } => {
  if ((test.unpromoted_findings ?? 0) > 0) return { label: 'issue shown · no finding yet', tone: 'warn' };
  const findings = test.finding_ids ?? [];
  if (findings.length > 0) return { label: `finding #${findings[0]}`, tone: 'info' };
  switch (test.last_outcome) {
    case 'no_finding': return { label: 'not present', tone: 'ok' };
    case 'inconclusive': return { label: 'inconclusive', tone: 'muted' };
    case 'failed': return { label: 'could not run', tone: 'muted' };
    case 'finding': return { label: 'issue shown', tone: 'warn' };
    case 'info': return { label: 'context recorded', tone: 'muted' };
    default: return { label: test.evidence_count > 0 ? `${test.evidence_count} evidence` : 'not run', tone: 'muted' };
  }
};

/** What to say once a test result has become a finding.  The words come from
 *  the RESPONSE: a result that joined an existing finding did not create one,
 *  and did not change that finding's status. */
export const promotedResultMessage = (
  made: { finding_id: number; joined_issue?: boolean; status?: string | null },
): string => {
  const state = made.status ? made.status.replace(/_/g, ' ') : null;
  if (made.joined_issue) {
    return `Joined the existing finding #${made.finding_id}${state ? `, which stays ${state}` : ''}.`;
  }
  return `On finding #${made.finding_id}${state ? ` (${state})` : ''}.`;
};

/** One phrase for a weakness's tests, for its collapsed row. */
export const issueTestSummary = (tests: HostTest[]): { label: string; tone: ResultTone } | null => {
  const live = tests.filter((t) => t.status !== 'dismissed');
  if (live.length === 0) return null;
  if (live.some((t) => (t.unpromoted_findings ?? 0) > 0)) return { label: 'test showed it · no finding yet', tone: 'warn' };
  if (live.some((t) => (t.finding_ids ?? []).length > 0)) return { label: 'confirmed by test', tone: 'info' };
  if (live.some((t) => t.last_outcome === 'no_finding')) return { label: 'tested · not present', tone: 'ok' };
  const toDo = live.filter(testNeedsWork).length;
  if (toDo > 0) return { label: `${toDo} test${toDo === 1 ? '' : 's'} to do`, tone: 'muted' };
  return { label: `${live.length} test${live.length === 1 ? '' : 's'}`, tone: 'muted' };
};
