import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Replace the barrel outright — importOriginal would pull in the real axios
// client and blow up on interceptor setup under jsdom. Findings only calls
// these two.
vi.mock('../../services/api', () => ({
  listFindings: vi.fn(),
  setFindingStatus: vi.fn(),
  listProjectMembers: vi.fn(),
  bulkSetFindingStatus: vi.fn(),
  bulkAssignFindings: vi.fn(),
}));

// useConfirm returns a TUPLE [dialogElement, confirmFn]; mocking it as a bare
// function makes React fail with "function is not iterable".
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => [null, confirmMock],
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester' }, hasPermission: () => true }),
}));

import * as api from '../../services/api';
import Findings from '../../pages/Findings';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const makeFinding = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  project_id: 1,
  title: `Finding ${id}`,
  severity: 'high',
  status: 'open',
  source: 'manual',
  owner_id: null,
  owner_name: null,
  evidence_annotation_id: null,
  vuln_id: null,
  exec_result_id: null,
  host_count: 1,
  hosts: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: null,
  ...over,
});

// Held as a single stable response object. Returning a fresh literal per
// call gives `severity_counts` a new identity each fetch, which drives the
// page into a refetch loop and hangs the test run.
const EMPTY_SEV_COUNTS = {};
let currentResponse: { items: ReturnType<typeof makeFinding>[]; total: number; severity_counts: object } = {
  items: [], total: 0, severity_counts: EMPTY_SEV_COUNTS,
};
const setResponse = (items: ReturnType<typeof makeFinding>[], total = items.length) => {
  currentResponse = { items, total, severity_counts: EMPTY_SEV_COUNTS };
};

const renderFindings = () =>
  render(
    <MemoryRouter initialEntries={['/findings']}>
      <Routes>
        <Route path="/findings" element={<Findings />} />
      </Routes>
    </MemoryRouter>,
  );

/** Types into the search box — a plain <input>, and a genuine
 *  membership-changing filter (it lands in the URL after a 300ms debounce).
 *  Chosen over the Radix filter Selects, which don't drive reliably in jsdom. */
const searchFor = (text: string) => {
  fireEvent.change(screen.getByPlaceholderText(/Search finding titles/i), {
    target: { value: text },
  });
};

// NOTE: these use fireEvent rather than userEvent deliberately — userEvent's
// pointer-events/act handling hangs indefinitely against this page's Radix
// controls under jsdom. fireEvent drives the same handlers.
const selectFinding = async (id: number) => {
  fireEvent.click(await screen.findByLabelText(`Select Finding ${id}`));
};

describe('Findings — bulk selection scope', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setResponse([makeFinding(1), makeFinding(2), makeFinding(3)]);
    mocked.listFindings.mockImplementation(async () => currentResponse);
    mocked.listProjectMembers.mockResolvedValue([]);
    mocked.bulkSetFindingStatus.mockResolvedValue({ affected: 1, requested: 1, skipped_ids: [] });
    mocked.bulkAssignFindings.mockResolvedValue({ affected: 1, requested: 1, skipped_ids: [] });
    confirmMock.mockResolvedValue(true);
  });

  it('surfaces a bulk bar once a finding is selected', async () => {
    renderFindings();
    await screen.findByText('Finding 1');

    await selectFinding(1);
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());
  });

  // The defect this suite exists for. A selection made under one filter must
  // not survive into a different result set, or a bulk disposition can hit
  // findings the operator can no longer see and never chose under that filter.
  it('clears the selection when a filter changes the result set', async () => {
    renderFindings();
    await screen.findByText('Finding 1');

    await selectFinding(1);
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());

    setResponse([makeFinding(9)]);
    searchFor('nine');

    // Debounced into the URL at 300ms, then the signature changes.
    await waitFor(
      () => expect(screen.queryByText(/1 selected/)).toBeNull(),
      { timeout: 3000 },
    );
  });

  // Sort and pagination don't change membership, so selection survives them —
  // the convention Hosts.tsx documents. Guards against over-correcting into
  // "clear on any refetch", which would make cross-page triage impossible.
  it('keeps the selection across a sort change', async () => {
    renderFindings();
    await screen.findByText('Finding 1');

    await selectFinding(1);
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());

    setResponse([makeFinding(3), makeFinding(2), makeFinding(1)]);
    fireEvent.click(screen.getByRole('button', { name: /^Title$/ }));
    await screen.findByText('Finding 3');

    // Membership is unchanged, so the selection must survive.
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
  });

  // Because selection legitimately spans pages, the bar must say how much of
  // it is off-screen — a bare count reads as "these rows here".
  it('discloses how much of the selection is off the visible page', async () => {
    renderFindings();
    await screen.findByText('Finding 1');

    await selectFinding(1);
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());
    // Everything selected is visible, so no warning yet.
    expect(screen.queryByText(/not on this page/)).toBeNull();

    // The selected finding scrolls off the page via a sort (not a membership
    // change), so the selection correctly persists but is now unseen.
    setResponse([makeFinding(7), makeFinding(8)], 5);
    fireEvent.click(screen.getByRole('button', { name: /^Title$/ }));
    await screen.findByText('Finding 7');

    await waitFor(() =>
      expect(screen.getByText(/1 not on this page/)).toBeInTheDocument(),
    );
  });

  // Selection spans pages, so the header checkbox must union/subtract the
  // current page — replacing the set made select-all on page 2 silently drop
  // page 1's work, and unchecking wiped everything.
  it('select-all adds this page without discarding off-page selections', async () => {
    renderFindings();
    await screen.findByText('Finding 1');

    await selectFinding(1);
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());

    // Page over (a sort — not a membership change), keeping the selection.
    setResponse([makeFinding(7), makeFinding(8)], 5);
    fireEvent.click(screen.getByRole('button', { name: /^Title$/ }));
    await screen.findByText('Finding 7');
    await waitFor(() => expect(screen.getByText(/1 not on this page/)).toBeInTheDocument());

    // Select all on this page: 1 (off-page) + 7 + 8 = 3.
    fireEvent.click(screen.getByLabelText('Select all findings on this page'));
    await waitFor(() => expect(screen.getByText(/3 selected/)).toBeInTheDocument());

    // Unchecking removes only this page, leaving the off-page one.
    fireEvent.click(screen.getByLabelText('Select all findings on this page'));
    await waitFor(() => expect(screen.getByText(/1 selected/)).toBeInTheDocument());
  });
});
