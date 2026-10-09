/**
 * Tool Activity — the question that was ASKED belongs to the address, under
 * the REAL router (setupTests replaces `useNavigate` / `useLocation`; a
 * mocked navigation proves nothing about Back).
 *
 * The defect (code review 2026-10-09, finding 6): the form was seeded from
 * the address ONCE and the asked question was kept in state and written back
 * from an effect.  A link to this page naming another window, or Back,
 * changed the address — and the page kept its old fields, asked nothing new,
 * and wrote its old question back over the address the reader had gone to.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';
import type { ActivityItem, ActivityResponse } from '../../services/api';

vi.mock('react-router-dom', async () =>
  vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

const getScansAt = vi.fn();
const getScansBetween = vi.fn();
vi.mock('../../services/api', () => ({
  getScansAt: (...a: unknown[]) => getScansAt(...a),
  getScansBetween: (...a: unknown[]) => getScansBetween(...a),
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: [{ id: 1, name: 'Acme internal' }, { id: 2, name: 'Other' }],
    currentProject: { id: 1, name: 'Acme internal' },
    selectProject: vi.fn(),
  }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import ToolActivity from '../../pages/ToolActivity';

const scan = (minsAgo: number, ref: number): ActivityItem => ({
  kind: 'scan', ref_id: ref, project_id: 1, project_name: 'Acme internal', label: 'nmap',
  secondary_label: null, start_time: new Date(Date.now() - minsAgo * 60_000).toISOString(), end_time: null,
  recorded_time: null, start_time_is_fallback: false, has_end_time: false, host_count: null, status: null,
  target: null, parent_id: null,
});

const answer = (items: ActivityItem[]): ActivityResponse => ({
  items, total: items.length, truncated: false, accessible_project_ids: [1, 2],
  requested_project_ids: null,
  window_start: new Date(Date.now() - 7 * 86400_000).toISOString(),
  window_end: new Date().toISOString(),
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [
      { path: '/tool-activity', element: <TooltipProvider><ToolActivity /></TooltipProvider> },
      { path: '/elsewhere', element: <p>elsewhere</p> },
    ],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

const WEEK_MS = 7 * 86400_000;
type Asked = Record<string, unknown>;
/** The week snapshot is `getScansBetween` too — for the 7 days ending when it was asked. */
const isWeek = (p: Asked) => new Date(p.to as string).getTime() - new Date(p.from as string).getTime() === WEEK_MS;
const betweenCalls = () => getScansBetween.mock.calls.map(([params]) => params as Asked);
/** The focused range questions asked, in order. */
const ranges = () => betweenCalls().filter((p) => !isWeek(p));
/** The week snapshots asked, in order. */
const weeks = () => betweenCalls().filter(isWeek);
/** The focused moment questions asked, in order. */
const moments = () => getScansAt.mock.calls.map(([params]) => params as Asked);
/** Without the keys a question leaves out. */
const stated = (p: Asked | undefined) => JSON.parse(JSON.stringify(p ?? null));

const field = (label: string | RegExp) => screen.getByLabelText(label) as HTMLInputElement;
/** A datetime-local field's instant. */
const instant = (label: string) => new Date(field(label).value).toISOString();
/** Long enough for any write that was going to happen to have happened. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 150)); });

const AT = '/tool-activity?at=2026-09-30T14%3A32%3A17.000Z&tolerance=60&tool=nmap&target=10.0.0.5';
const RANGE = '/tool-activity?from=2026-09-29T00%3A00%3A00.000Z&to=2026-09-30T00%3A00%3A00.000Z&tool=masscan';
const search = (url: string) => url.slice(url.indexOf('?'));

beforeEach(() => {
  getScansAt.mockReset().mockResolvedValue(answer([]));
  getScansBetween.mockReset().mockResolvedValue(answer([scan(180, 1), scan(200, 2)]));
});

describe('Tool Activity — the asked question is the address (real router)', () => {
  it('a link that names a window is asked on arrival, and the form shows it', async () => {
    const router = open(AT);
    await waitFor(() => expect(moments()).toHaveLength(1));
    expect(stated(moments()[0])).toEqual({
      ts: '2026-09-30T14:32:17.000Z', toleranceSeconds: 60, tool: 'nmap', target: '10.0.0.5',
    });
    expect(field('Tool').value).toBe('nmap');
    expect(field(/Target/).value).toBe('10.0.0.5');
    expect(instant('Timestamp (local)')).toBe('2026-09-30T14:32:17.000Z');
    expect(screen.getByRole('combobox', { name: 'Tolerance' })).toHaveTextContent('± 1 minute');
    // The week snapshot is narrowed the same way.
    expect(weeks()).toHaveLength(1);
    expect(stated(weeks()[0])).toMatchObject({ tool: 'nmap', target: '10.0.0.5' });
    // Opening it asked once and changed nothing in the address.
    await settle();
    expect(moments()).toHaveLength(1);
    expect(ranges()).toHaveLength(0);
    expect(router.state.location.search).toBe(search(AT));
  });

  it('a bare arrival asks nothing and writes nothing', async () => {
    const router = open('/tool-activity');
    await screen.findByTestId('activity-histogram');
    await settle();
    expect(getScansAt).not.toHaveBeenCalled();
    expect(betweenCalls()).toHaveLength(1);          // the week snapshot only
    expect(screen.getByTestId('correlate-prompt')).toHaveTextContent('Pick a tool, address or time to correlate');
    expect(field('Tool').value).toBe('');
    expect(router.state.location.search).toBe('');
  });

  it('Correlate writes the question into the address (replace) and asks it', async () => {
    const router = open('/tool-activity');
    await screen.findByTestId('activity-histogram');
    fireEvent.change(field('Tool'), { target: { value: '  nuclei ' } });
    fireEvent.change(field('Timestamp (local)'), { target: { value: '2026-09-30T14:32:17' } });
    await settle();
    expect(router.state.location.search).toBe('');   // a draft is not a question
    expect(getScansAt).not.toHaveBeenCalled();

    fireEvent.submit(field('Tool').closest('form')!);
    await waitFor(() => expect(moments()).toHaveLength(1));
    const ts = new Date('2026-09-30T14:32:17').toISOString();
    expect(stated(moments()[0])).toEqual({ ts, toleranceSeconds: 300, tool: 'nuclei' });
    await settle();
    expect(router.state.location.search).toBe(`?${new URLSearchParams({ at: ts, tolerance: '300', tool: 'nuclei' })}`);
    expect(router.state.historyAction).toBe('REPLACE');
    // Asked once: the address it wrote did not come back as another question,
    // and the form keeps what the reader typed.
    expect(moments()).toHaveLength(1);
    expect(field('Tool').value).toBe('  nuclei ');
    expect(instant('Timestamp (local)')).toBe(ts);
    // The snapshot was asked again, for that tool.
    expect(stated(weeks()[weeks().length - 1])).toMatchObject({ tool: 'nuclei' });
  });

  it('a chart column writes its range into the address and asks it', async () => {
    const router = open('/tool-activity');
    await screen.findByTestId('activity-histogram');
    const chart = screen.getByLabelText(/Activity per time bin/);
    fireEvent.keyDown(chart, { key: 'ArrowRight' });
    fireEvent.keyDown(chart, { key: 'Enter' });
    await waitFor(() => expect(ranges()).toHaveLength(1));
    const { from, to } = ranges()[0] as { from: string; to: string };
    await settle();
    expect(router.state.location.search).toBe(`?${new URLSearchParams({ from, to })}`);
    expect(router.state.historyAction).toBe('REPLACE');
    expect(instant('From (local)')).toBe(from);
    expect(instant('To (local)')).toBe(to);
    expect(ranges()).toHaveLength(1);
    expect(getScansAt).not.toHaveBeenCalled();
  });

  it('a link to this page naming another window, and Back, re-seed the form; what is asked follows; the address is left alone', async () => {
    const router = open(AT);
    await waitFor(() => expect(moments()).toHaveLength(1));

    await act(async () => { await router.navigate(RANGE); });
    await waitFor(() => expect(ranges()).toHaveLength(1));
    expect(stated(ranges()[0])).toEqual({
      from: '2026-09-29T00:00:00.000Z', to: '2026-09-30T00:00:00.000Z', tool: 'masscan',
    });
    expect(field('Tool').value).toBe('masscan');
    expect(field(/Target/).value).toBe('');
    expect(instant('From (local)')).toBe('2026-09-29T00:00:00.000Z');
    expect(instant('To (local)')).toBe('2026-09-30T00:00:00.000Z');
    expect(screen.queryByLabelText('Timestamp (local)')).toBeNull();
    // The snapshot follows the link's tool, as it does on arrival.
    expect(stated(weeks()[weeks().length - 1]).tool).toBe('masscan');
    expect(stated(weeks()[weeks().length - 1]).target).toBeUndefined();
    // Nothing puts the previous question back over the address.
    await settle();
    expect(router.state.location.search).toBe(search(RANGE));
    expect(moments()).toHaveLength(1);
    expect(ranges()).toHaveLength(1);

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(moments()).toHaveLength(2));
    expect(stated(moments()[1])).toEqual({
      ts: '2026-09-30T14:32:17.000Z', toleranceSeconds: 60, tool: 'nmap', target: '10.0.0.5',
    });
    expect(field('Tool').value).toBe('nmap');
    expect(field(/Target/).value).toBe('10.0.0.5');
    expect(instant('Timestamp (local)')).toBe('2026-09-30T14:32:17.000Z');
    expect(screen.getByRole('combobox', { name: 'Tolerance' })).toHaveTextContent('± 1 minute');
    await settle();
    expect(router.state.location.search).toBe(search(AT));
    expect(ranges()).toHaveLength(1);
  });

  it('an unasked draft is dropped when the address changes from elsewhere', async () => {
    const router = open(AT);
    await waitFor(() => expect(moments()).toHaveLength(1));
    fireEvent.change(field('Tool'), { target: { value: 'half-typed' } });
    await act(async () => { await router.navigate(RANGE); });
    await waitFor(() => expect(ranges()).toHaveLength(1));
    expect(field('Tool').value).toBe('masscan');
    await settle();
    expect(router.state.location.search).toBe(search(RANGE));
    expect(betweenCalls().some((p) => p.tool === 'half-typed')).toBe(false);
  });

  // The address reads no range that does not run forward, so such a question
  // cannot live in it — but the reader asked it, and still gets the server's
  // word on it (as before), not silence.
  it('a backwards range from the form is still asked, and is not written as a window', async () => {
    const router = open(RANGE);
    await waitFor(() => expect(ranges()).toHaveLength(1));
    getScansBetween.mockImplementation(async (p: Asked) => {
      if (isWeek(p)) return answer([]);
      throw new Error('refused');
    });
    fireEvent.change(field('To (local)'), { target: { value: '2026-09-01T00:00' } });
    fireEvent.submit(field('Tool').closest('form')!);
    await waitFor(() => expect(ranges()).toHaveLength(2));
    expect(stated(ranges()[1])).toEqual({
      from: '2026-09-29T00:00:00.000Z', to: new Date('2026-09-01T00:00').toISOString(), tool: 'masscan',
    });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('?tool=masscan');
    expect(field('To (local)').value).toBe('2026-09-01T00:00');
    expect(ranges()).toHaveLength(2);
  });

  it('a link to the bare page asks nothing and shows no answer to the question left behind', async () => {
    const router = open(AT);
    expect(await screen.findByText(/0 activities matched/)).toBeInTheDocument();
    await act(async () => { await router.navigate('/tool-activity'); });
    expect(await screen.findByTestId('correlate-prompt')).toHaveTextContent('Pick a tool, address or time to correlate');
    expect(screen.queryByText(/activities matched/)).toBeNull();
    expect(field('Tool').value).toBe('');
    await settle();
    expect(router.state.location.search).toBe('');
    expect(moments()).toHaveLength(1);
    expect(ranges()).toHaveLength(0);
  });
});
