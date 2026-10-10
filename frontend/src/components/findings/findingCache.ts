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
