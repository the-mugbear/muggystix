/**
 * The context half of `hostTestsController` — the shape, the provider and the
 * "open this test" event, with NO import of the API client, so a component
 * that only READS the host's tests (a weakness row) can be rendered and
 * tested without it.
 */
import { createContext, useContext } from 'react';

import type { HostTest } from '../../services/api';
import { openInspectorSection } from './InspectorSection';

/** The weakness a hand-written test is meant to confirm. */
export interface AddTestTarget {
  vulnerabilityId: number;
  title: string;
  severity?: string | null;
}

export interface HostTestsController {
  hostId: number;
  canEdit: boolean;
  userId?: number;
  /** Null until the first read lands. */
  tests: HostTest[] | null;
  total: number;
  loading: boolean;
  error: string | null;
  reload: () => Promise<HostTest[]>;
  /** Put a changed test in place (after a write returned it). */
  replace: (updated: HostTest) => void;
  /** A write was refused because the test changed underneath it. */
  staleNotice: boolean;
  markStale: () => void;
  clearStale: () => void;
  /** Open the result panel for a test. */
  openResult: (test: HostTest) => void;
  /** Open the panel where a person writes a test for this host, optionally
   *  one that confirms a scanner observation on it. */
  openAdd: (confirms?: AddTestTarget) => void;
  /** evidence id → the pending agent proposal for a finding that cites it. */
  proposalByEvidence: Record<number, number>;
  /** Hand a task to the operator's agent (copied when a session is live). */
  askAgent: (instruction: string) => void;
  canAskAgent: boolean;
  /** Text typed in the panel and not saved. */
  resultDraft: boolean;
  /** A finding was made (or joined) from a result. */
  onFindingCreated: (findingId: number) => void;
}

const Ctx = createContext<HostTestsController | null>(null);
export const HostTestsProvider = Ctx.Provider;
export const useHostTests = (): HostTestsController | null => useContext(Ctx);

/** Open one test in the Tests section from elsewhere on the host page (the
 *  weakness it confirms). */
export const OPEN_HOST_TEST_EVENT = 'bluestick:open-host-test';
export const openHostTest = (testId: number): void => {
  if (typeof window === 'undefined') return;
  openInspectorSection('host-detail-proposed-tests');
  window.dispatchEvent(new CustomEvent(OPEN_HOST_TEST_EVENT, { detail: testId }));
};

