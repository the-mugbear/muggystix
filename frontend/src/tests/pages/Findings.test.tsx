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
// Stable identity, like the real context value: a fresh object per render
// would change `fetchFindings`' identity every render and refetch in a loop.
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => toastMock,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester' }, hasPermission: () => true }),
}));

import * as api from '../../services/api';
import Findings from '../../pages/Findings';
import { TooltipProvider } from '../../components/ui/tooltip';

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

// The page's (i) tips are Radix Tooltips, which need the app-level provider.
const renderFindings = (url = '/findings') =>
  render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/findings" element={<Findings />} />
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
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


// Review 2026-09-09 #4 — a slow response for an OLDER filter set must never
// land under a NEWER one.  Two requests resolve out of order; the table must
// show the later request's rows and no error.
describe('Findings — superseded responses', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listProjectMembers.mockResolvedValue([]);
    confirmMock.mockResolvedValue(true);
  });

  it('shows the latest request even when an earlier one resolves last', async () => {
    type Resp = typeof currentResponse;
    const deferreds: Array<{ resolve: (r: Resp) => void; reject: (e: unknown) => void; signal?: AbortSignal }> = [];
    mocked.listFindings.mockImplementation(
      (_filters: unknown, signal?: AbortSignal) =>
        new Promise<Resp>((resolve, reject) => {
          deferreds.push({ resolve, reject, signal });
          // Mirror axios: an aborted request rejects with CanceledError.
          signal?.addEventListener('abort', () => {
            const err = new Error('canceled') as Error & { code?: string };
            err.name = 'CanceledError';
            err.code = 'ERR_CANCELED';
            reject(err);
          });
        }),
    );

    renderFindings();
    // Mount fires one or more fetches (URL-param sync); leave them all pending.
    await waitFor(() => expect(deferreds.length).toBeGreaterThanOrEqual(1));
    await new Promise((r) => setTimeout(r, 50));
    const beforeSearch = deferreds.length;

    // Change a membership filter → a newer request B.
    searchFor('nine');
    await waitFor(() => expect(deferreds.length).toBeGreaterThan(beforeSearch), { timeout: 3000 });
    // The URL/page sync can issue more than one fetch; wait until the count
    // is stable so "B" is genuinely the newest request.
    let settled = deferreds.length;
    for (;;) {
      await new Promise((r) => setTimeout(r, 200));
      if (deferreds.length === settled) break;
      settled = deferreds.length;
    }
    const b = deferreds[deferreds.length - 1];
    const stale = deferreds.slice(0, -1);
    expect(b.signal?.aborted).toBe(false);
    // Every earlier request was aborted when a newer one started.
    for (const d of stale) expect(d.signal?.aborted).toBe(true);

    // B (newest) resolves first with its rows …
    b.resolve({ items: [makeFinding(9)], total: 1, severity_counts: EMPTY_SEV_COUNTS });
    await screen.findByText('Finding 9');

    // … then the stale requests try to deliver a different result set.  Even
    // if the transport had not honoured the abort, the generation guard drops it.
    for (const d of stale) {
      d.resolve({ items: [makeFinding(1), makeFinding(2)], total: 2, severity_counts: EMPTY_SEV_COUNTS });
    }
    await new Promise((r) => setTimeout(r, 50));

    expect(screen.getByText('Finding 9')).toBeInTheDocument();
    expect(screen.queryByText('Finding 1')).toBeNull();
    expect(screen.queryByText(/Failed to load findings/)).toBeNull();
  });
});

describe('Findings — M1: row links carry the queue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setResponse([makeFinding(1)]);
    mocked.listFindings.mockImplementation(async () => currentResponse);
    mocked.listProjectMembers.mockResolvedValue([]);
  });

  it('the detail href carries filters + page + sort so the detail can return to this exact queue', async () => {
    renderFindings('/findings?status=all&severity=high&page=3&sort=severity&dir=desc');
    const link = await screen.findByRole('link', { name: /Finding 1/ });
    const href = link.getAttribute('href') ?? '';
    expect(href.startsWith('/findings/1?from=')).toBe(true);
    const from = decodeURIComponent(href.split('from=')[1]);
    expect(from).toBe('/findings?status=all&severity=high&page=3&sort=severity&dir=desc');
    // The URL page drove the request (page 3 of 50 → offset 100).
    expect(mocked.listFindings.mock.calls[0][0]).toEqual(
      expect.objectContaining({ offset: 100, limit: 50, sort: 'severity', dir: 'desc' }),
    );
  });

  it('a clean list URL yields a plain detail link', async () => {
    renderFindings();
    const link = await screen.findByRole('link', { name: /Finding 1/ });
    expect(link.getAttribute('href')).toBe('/findings/1');
  });
});

// v5.267.0 — the list follows the Posture layout (UI_STYLE_GUIDE §7): no
// Card, one-line caption with the vocabulary on an (i), the title carrying
// its first host, quiet status text, compact age, no Source column.
describe('Findings — presentation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listProjectMembers.mockResolvedValue([]);
    mocked.listFindings.mockImplementation(async () => currentResponse);
  });

  it('puts the vocabulary behind an (i), not in a paragraph under the title', async () => {
    setResponse([makeFinding(1)]);
    renderFindings();
    await screen.findByText('Finding 1');
    expect(screen.getByRole('button', { name: 'What the statuses mean' })).toBeInTheDocument();
    expect(screen.queryByText(/stay on each host until promoted/)).toBeNull();
  });

  it('renders the table on the page without a Card, and without a Source column', async () => {
    setResponse([makeFinding(1)]);
    const { container } = renderFindings();
    await screen.findByText('Finding 1');
    expect(container.querySelector('.shadow-raised')).toBeNull();
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(headers).toEqual(expect.arrayContaining(['Severity', 'Title', 'Status', 'Hosts', 'Owner', 'Age']));
    expect(headers).not.toContain('Source');
    // Source stays a filter.
    expect(screen.getByLabelText('Source')).toBeInTheDocument();
  });

  it('shows the first host, "+N" and the endpoint states under the title', async () => {
    setResponse([
      makeFinding(1, {
        host_count: 3,
        hosts: [
          { id: 1, host_id: 42, ip_address: '10.0.0.5', hostname: 'db01' },
          { id: 2, host_id: 43, ip_address: '10.0.0.6', hostname: null },
        ],
        endpoint_status_counts: { open: 2, remediated: 1 },
      }),
    ]);
    renderFindings();
    const caption = await screen.findByTestId('finding-hosts-1');
    expect(caption.textContent).toContain('10.0.0.5');
    expect(caption.textContent).toContain('db01');
    expect(caption.textContent).toContain('+2');
    expect(caption.textContent).toContain('still present on 2 of 3');
    expect(screen.getByRole('link', { name: '10.0.0.5' }).getAttribute('href')).toBe('/hosts/42');
    // The full host list is on hover.
    expect(caption.getAttribute('title')).toBe('10.0.0.5 (db01), 10.0.0.6');
  });

  it('shows age as a compact value with the full date on hover', async () => {
    const created = new Date(Date.now() - 31 * 24 * 60 * 60 * 1000 - 60_000).toISOString();
    setResponse([makeFinding(1, { created_at: created })]);
    renderFindings();
    await screen.findByText('Finding 1');
    const age = screen.getByText('31d');
    // The one absolute format (utils/relativeTime.formatTimestamp).
    expect(age.getAttribute('title')).toBe(
      new Date(created).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }),
    );
  });

  // UX review 2026-09-24: Owner was the only column that could not be sorted.
  it('sorts by owner on the server', async () => {
    setResponse([makeFinding(1)]);
    renderFindings();
    await screen.findByText('Finding 1');
    fireEvent.click(screen.getByRole('button', { name: /^Owner$/ }));
    await waitFor(() => {
      const calls = mocked.listFindings.mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last).toMatchObject({ sort: 'owner', dir: 'asc' });
    });
  });

  it('renders status as a quiet picker with no per-row history button', async () => {
    setResponse([makeFinding(1, { status: 'confirmed' })]);
    renderFindings();
    await screen.findByText('Finding 1');
    const trigger = screen.getByRole('combobox', { name: 'Change status for Finding 1' });
    expect(trigger.textContent).toContain('Confirmed');
    expect(trigger.className).toContain('border-0');
    expect(screen.queryByRole('button', { name: /history/i })).toBeNull();
  });

  // v5.288.0: the dotted underline sat under an overflow-clipped span and
  // showed on some rows only; every row now says "Change status" the same way.
  it('marks every status picker the same way, with no clipped underline', async () => {
    setResponse([makeFinding(1, { status: 'confirmed' }), makeFinding(2, { status: 'open' })]);
    renderFindings();
    await screen.findByText('Finding 2');
    for (const name of ['Change status for Finding 1', 'Change status for Finding 2']) {
      const trigger = screen.getByRole('combobox', { name });
      expect(trigger).toHaveAttribute('title', 'Change status');
      expect(trigger.className).not.toMatch(/underline/);
    }
  });

  // v5.288.0: newest-first alone interleaved severities; with no sort chosen
  // the list is worst first (ties newest first, server-side), and a chosen
  // sort is still the operator's.
  it('defaults to severity order and keeps a chosen sort', async () => {
    renderFindings();
    await screen.findByText('Finding 1');
    expect(mocked.listFindings.mock.calls[0][0]).toEqual(expect.objectContaining({ sort: 'severity', dir: 'asc' }));
    expect(screen.getByRole('columnheader', { name: /Severity/ })).toHaveAttribute('aria-sort', 'ascending');

    fireEvent.click(screen.getByRole('button', { name: /^Age$/ }));
    await waitFor(() => expect(mocked.listFindings.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ sort: 'created_at', dir: 'desc' }),
    ));
  });
});

// Report 2026-09-26: the page opens on status "active", so an empty project
// was told "No findings match these filters" about a filter nobody set.
describe('Findings — empty states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listProjectMembers.mockResolvedValue([]);
    mocked.listFindings.mockImplementation(async () => currentResponse);
    setResponse([]);
  });

  it('on a clean URL, says no ACTIVE findings and does not blame filters', async () => {
    renderFindings();
    expect(await screen.findByText(/No active findings/)).toBeInTheDocument();
    expect(screen.queryByText(/match these filters/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Include closed ones' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Promote from scanner observations' })).toBeInTheDocument();
  });

  it('blames the filters only when the analyst set one', async () => {
    renderFindings('/findings?severity=critical');
    expect(await screen.findByText(/No findings match these filters/)).toBeInTheDocument();
  });

  it('with every filter cleared, explains how findings are made', async () => {
    renderFindings('/findings?status=all');
    expect(await screen.findByText(/Promote a scanner observation/)).toBeInTheDocument();
  });
});
