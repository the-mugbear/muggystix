/**
 * This installation's remediation settings — whether remediation tracking
 * exists here at all, and the timeline per severity (5.340.0).
 *
 * One query for the session (`[GLOBAL, 'getRemediationPolicy']`, lib/query),
 * shared by the navigation, the pages and the dialogs.  Until it answers — and
 * when it fails — the feature reads as OFF: an installation that did not opt
 * in must never flash a Remediation link.
 *
 * "Could not be read" is not "off", and it does not last (code review
 * 2026-10-09: the answer is remembered for the session and the shell that
 * reads it never unmounts, so one failed read at sign-in hid remediation
 * until a reload).  A failed read is therefore `failed`, with `retry` for a
 * page to offer; it is asked again by each reader that mounts
 * (`retryOnMount`, against the default) and once a minute while it is
 * failing — only then: an answer is never polled.
 * System settings calls `setRemediationPolicy` after a save so the navigation
 * follows at once.
 */
import { useQuery } from '@tanstack/react-query';

import { getRemediationPolicy, type RemediationPolicy } from '../services/api';
import { GLOBAL, pollEvery, queryClient, queryErrorText, rememberFor } from '../lib/query';

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
  /** Why it is not known whether the installation tracks remediation, or
   *  null.  `enabled` is false then — which is "not known", not "off". */
  error: string | null;
  /** Ask again now. */
  retry: () => void;
}

/** While the read is failing it is asked again this often (twice as long by
 *  `pollEvery`'s rule for a failing read: once a minute). */
const RETRY_WHILE_FAILING_MS = 30_000;

export function useRemediationPolicy(): RemediationPolicyState {
  const query = useQuery<RemediationPolicy | null>({
    queryKey: KEY,
    queryFn: ({ signal }) => getRemediationPolicy(signal),
    ...rememberFor(Infinity),
    retryOnMount: true,
    ...pollEvery((read) => (read.state.status === 'error' ? RETRY_WHILE_FAILING_MS : null)),
  });
  const policy = query.data ?? null;
  const { refetch } = query;
  return {
    policy,
    enabled: policy?.enabled === true,
    loading: query.isPending,
    error: policy == null ? queryErrorText(query.error, 'Could not check whether remediation tracking is on.') : null,
    retry: () => { void refetch(); },
  };
}
