/**
 * The finding page's cached record, and the one way a write puts its answer
 * on it.
 *
 * Every write route of a finding answers with the finding (its endpoints and
 * their segments included), so the component that sent the write puts that
 * answer on the cached record itself — no callback to the page, and no second
 * read of thousands of endpoints.
 *
 * An answer is the WHOLE finding as it stood when that write committed, and
 * two writes of different fields can answer in the other order than they
 * committed ("Assign to me" answering after a rename that was sent later put
 * the old title back — review 2026-10-10).  Every answer and every read
 * carries the finding's `revision`, which the server raises by one for each
 * committed write (`models_findings._count_finding_revisions`), so of two
 * records the higher revision is the newer: an answer goes on the cache only
 * over a lower one, and a read that was on its way while a newer answer
 * landed does not put the older finding back (`keepNewerFinding`).
 */
import { replaceEqualDeep, type QueryClient } from '@tanstack/react-query';

import type { Finding } from '../../services/api';

/** The key of the page's one read of a finding (`getFinding`). */
export const findingKey = (projectId: number, findingId: number) =>
  ['getFinding', projectId, findingId] as const;

/** Its history trail (`getFindingHistory`). */
export const findingHistoryKey = (projectId: number, findingId: number) =>
  ['getFindingHistory', projectId, findingId] as const;

/**
 * The finding query's `structuralSharing`: a read that answers with a LOWER
 * revision than the record already cached (it was taken before a write whose
 * answer has since landed) keeps the cached record.  An equal revision is the
 * same committed finding and takes the read (what it derives — a segment, a
 * retest standing — may be newer).
 */
export const keepNewerFinding = (cached: unknown, read: unknown): unknown => {
  const held = cached as Finding | undefined;
  const fresh = read as Finding | undefined;
  if (held && fresh && held.id === fresh.id && held.revision > fresh.revision) return held;
  return replaceEqualDeep(cached, read);
};

/** The answer rule: a write's answer replaces a held record only when that
 *  record is not already at its revision or a later one. */
const takesAnswer = (held: Finding | undefined, answer: Finding): boolean =>
  !held || !(held.revision >= answer.revision);

/**
 * The same rule over a LIST's rows (the host inspector's Findings section):
 * the row of the answer's finding takes the answer only over a lower
 * revision, so two changes answering in the other order than they committed
 * leave the newer one on the row (review 2026-10-23, H-FC3).
 */
export function putFindingAnswerInRows<F extends Finding>(rows: F[], answer: F): F[] {
  return rows.map((f) => (f.id === answer.id && takesAnswer(f, answer) ? answer : f));
}

/**
 * Put a write's ANSWER on the cached finding — unless the cache already holds
 * that revision or a later one.  `history: true` when the write appended to
 * the finding's history (a status, an endpoint's state, added hosts, an
 * edit): the trail is then read again.  Returns true when the answer was put.
 *
 * Use this for every write of a finding: any answer may cross another
 * write's.  `FindingEndpoints` does too: its queue orders its own changes,
 * not a rename or a report-text save made while one is on its way (review
 * 2026-10-12).
 */
export function putFindingAnswer(
  queryClient: QueryClient, projectId: number, updated: Finding, { history }: { history: boolean },
): boolean {
  let put = false;
  queryClient.setQueryData<Finding>(findingKey(projectId, updated.id), (cached) => {
    if (!takesAnswer(cached, updated)) return cached as Finding;
    put = true;
    return updated;
  });
  if (history) void queryClient.invalidateQueries({ queryKey: findingHistoryKey(projectId, updated.id) });
  return put;
}
