/**
 * Reads that are on screen somewhere else on the page — for a test of a
 * component that WRITES (5.351.0).
 *
 * A write no longer calls a parent's `onChanged` / `reload` (which re-read a
 * list and its counts): it says which reads are out of date, by API function
 * name (`invalidateReads`), and whatever shows them is read again.  A
 * component test has no page around it, so these stand in for the page's
 * reads; `reread` says which of them was asked for again.
 *
 *   const { reread, ReadsOnScreen } = readsOnScreen({ getMyTestsPage: 'list', getWorkbench: 'counts' });
 *   render(<><ReadsOnScreen /><MyTestsTable … /></>);
 *   …the action…
 *   await waitFor(() => expect(reread).toHaveBeenCalledWith('list'));
 *   expect(reread).not.toHaveBeenCalled();      // a refused action re-reads nothing
 *
 * Each stand-in holds an answer already (`initialData`, never stale), so it
 * asks for nothing when it mounts: every call of `reread` is a re-read.
 * `reread` is a `vi.fn()` — `vi.clearAllMocks()` in a `beforeEach` clears it.
 *
 * A read that is not one project's (lib/query `GLOBAL` — the bell's count,
 * the project list) is named in `global`, so the test also proves the
 * invalidation reaches a `[GLOBAL, name, …]` key.
 */
import React from 'react';
import { useQueries } from '@tanstack/react-query';
import { vi, type Mock } from 'vitest';

import { GLOBAL } from '../../lib/query';

export interface ReadsOnScreenProbe {
  /** Called with a read's label each time that read is asked for again. */
  reread: Mock<(what: string) => void>;
  /** Render it beside the component under test (inside the same `render`). */
  ReadsOnScreen: React.FC;
}

export function readsOnScreen(
  /** API function name → the label `reread` is called with. */
  reads: Record<string, string>,
  { global = [] }: { global?: string[] } = {},
): ReadsOnScreenProbe {
  const reread = vi.fn<(what: string) => void>();
  const queries = Object.entries(reads).map(([name, what]) => ({
    queryKey: [...(global.includes(name) ? [GLOBAL] : []), name, 'on screen'],
    queryFn: () => { reread(what); return 1; },
    initialData: 0,
    staleTime: Infinity,
  }));
  const ReadsOnScreen: React.FC = () => {
    useQueries({ queries });
    return null;
  };
  return { reread, ReadsOnScreen };
}
