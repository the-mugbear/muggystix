/**
 * This installation's remediation settings — whether remediation tracking
 * exists here at all, and the timeline per severity (5.340.0).
 *
 * One request per session, shared by the navigation, the pages and the
 * dialogs.  Until it answers — and when it fails — the feature reads as OFF:
 * an installation that did not opt in must never flash a Remediation link.
 * System settings calls `setRemediationPolicy` after a save so the navigation
 * follows at once.
 */
import { useEffect, useState } from 'react';

import { getRemediationPolicy, type RemediationPolicy } from '../services/api';

let cached: RemediationPolicy | null = null;
let pending: Promise<RemediationPolicy> | null = null;
const listeners = new Set<(policy: RemediationPolicy | null) => void>();

function load(): Promise<RemediationPolicy> {
  if (!pending) {
    pending = Promise.resolve()
      .then(() => getRemediationPolicy())
      .then((policy) => { setRemediationPolicy(policy); return policy; })
      .catch((err) => { pending = null; throw err; });
  }
  return pending;
}

/** Replace the shared value (after a save) and tell every reader. */
export function setRemediationPolicy(policy: RemediationPolicy | null): void {
  cached = policy;
  listeners.forEach((listener) => listener(policy));
}

/** Test hook, and sign-out: forget what was read. */
export function resetRemediationPolicy(): void {
  cached = null;
  pending = null;
  listeners.forEach((listener) => listener(null));
}

export interface RemediationPolicyState {
  /** Null until read (or when the read failed). */
  policy: RemediationPolicy | null;
  /** True only once the installation is known to track remediation. */
  enabled: boolean;
  loading: boolean;
}

export function useRemediationPolicy(): RemediationPolicyState {
  const [policy, setPolicy] = useState<RemediationPolicy | null>(cached);
  const [loading, setLoading] = useState(cached === null);

  useEffect(() => {
    let live = true;
    const listener = (next: RemediationPolicy | null) => { if (live) setPolicy(next); };
    listeners.add(listener);
    if (cached === null) {
      load().catch(() => undefined).finally(() => { if (live) setLoading(false); });
    } else {
      setPolicy(cached);
      setLoading(false);
    }
    return () => { live = false; listeners.delete(listener); };
  }, []);

  return { policy, enabled: policy?.enabled === true, loading };
}
