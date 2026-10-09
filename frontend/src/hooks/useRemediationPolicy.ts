/**
 * This installation's remediation settings — whether remediation tracking
 * exists here at all, and the timeline per severity (5.340.0).
 *
 * One query for the session (`[GLOBAL, 'getRemediationPolicy']`, lib/query),
 * shared by the navigation, the pages and the dialogs.  Until it answers — and
 * when it fails — the feature reads as OFF: an installation that did not opt
 * in must never flash a Remediation link.  A failure is not kept: the next
 * reader to mount asks again.
 * System settings calls `setRemediationPolicy` after a save so the navigation
 * follows at once.
 */
import { useQuery } from '@tanstack/react-query';

import { getRemediationPolicy, type RemediationPolicy } from '../services/api';
import { GLOBAL, queryClient, rememberFor } from '../lib/query';

const KEY = [GLOBAL, 'getRemediationPolicy'];

/** Replace the shared value (after a save): every reader follows. */
export function setRemediationPolicy(policy: RemediationPolicy | null): void {
  queryClient.setQueryData<RemediationPolicy | null>(KEY, policy);
}

/** Forget what the app's client read.  Tests call it between cases; each of
 *  their renders has a client of its own, so there it changes nothing. */
export function resetRemediationPolicy(): void {
  queryClient.removeQueries({ queryKey: KEY });
}

export interface RemediationPolicyState {
  /** Null until read (or when the read failed). */
  policy: RemediationPolicy | null;
  /** True only once the installation is known to track remediation. */
  enabled: boolean;
  loading: boolean;
}

export function useRemediationPolicy(): RemediationPolicyState {
  const query = useQuery<RemediationPolicy | null>({
    queryKey: KEY,
    queryFn: ({ signal }) => getRemediationPolicy(signal),
    ...rememberFor(Infinity),
  });
  const policy = query.data ?? null;
  return { policy, enabled: policy?.enabled === true, loading: query.isPending };
}
