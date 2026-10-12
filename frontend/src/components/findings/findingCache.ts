/**
 * The finding page's cached record, and the one way a write puts its answer
 * on it.
 *
 * Every write route of a finding answers with the finding (its endpoints and
 * their segments included), so the component that sent the write puts that
 * answer on the cached record itself — no callback to the page, and no second
 * read of thousands of endpoints.
 */
import type { QueryClient } from '@tanstack/react-query';

import type { Finding } from '../../services/api';

/** The key of the page's one read of a finding (`getFinding`). */
export const findingKey = (projectId: number, findingId: number) =>
  ['getFinding', projectId, findingId] as const;

/** Its history trail (`getFindingHistory`). */
export const findingHistoryKey = (projectId: number, findingId: number) =>
  ['getFindingHistory', projectId, findingId] as const;

/**
 * Put a write's answer on the cached finding.  `history: true` when the write
 * appended to the finding's history (a status, an endpoint's state, added
 * hosts): the trail is then read again.
 */
export function putFinding(
  queryClient: QueryClient, projectId: number, updated: Finding, { history }: { history: boolean },
): void {
  queryClient.setQueryData<Finding>(findingKey(projectId, updated.id), updated);
  if (history) void queryClient.invalidateQueries({ queryKey: findingHistoryKey(projectId, updated.id) });
}

/**
 * The record a write is about to be sent over.  A mutation takes it in
 * `onMutate` and hands it back to `putFindingAnswer`:
 *
 *   onMutate: () => findingAsSent(queryClient, projectId, id),
 *   onSuccess: (updated, _v, sent) => putFindingAnswer(queryClient, projectId, updated, sent, { history: false }),
 */
export const findingAsSent = (queryClient: QueryClient, projectId: number, findingId: number) => ({
  sentOver: queryClient.getQueryData<Finding>(findingKey(projectId, findingId)),
});

/**
 * Put a write's ANSWER on the cached finding — when it is safe to.
 *
 * An answer is the WHOLE finding as it stood when that write committed.  Two
 * writes of different fields can answer in the other order than they
 * committed ("Assign to me" answering after a rename that was sent later):
 * the older snapshot, laid over the record, put the old title back on a page
 * that had just shown the new one (review 2026-10-10).  So the answer goes on
 * the record only when nothing has written it, and nothing is reading it,
 * since the write was sent; otherwise which is newer cannot be told from the
 * answer and the finding is read again.  Returns true when the answer was put.
 *
 * Use this — not `putFinding` — for every write of a finding: any answer may
 * cross another write's.  `FindingEndpoints` does too: its queue orders its
 * own changes, not a rename or a report-text save made while one is on its
 * way (review 2026-10-12).
 */
export function putFindingAnswer(
  queryClient: QueryClient, projectId: number, updated: Finding,
  sent: { sentOver: Finding | undefined } | undefined, { history }: { history: boolean },
): boolean {
  const key = findingKey(projectId, updated.id);
  const record = queryClient.getQueryState<Finding>(key);
  if (sent && record?.fetchStatus === 'idle' && record.data === sent.sentOver) {
    putFinding(queryClient, projectId, updated, { history });
    return true;
  }
  void queryClient.invalidateQueries({ queryKey: key, exact: true });
  if (history) void queryClient.invalidateQueries({ queryKey: findingHistoryKey(projectId, updated.id) });
  return false;
}
