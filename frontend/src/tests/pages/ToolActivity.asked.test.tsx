/**
 * Tool activity — the results are labelled by the question that was ASKED,
 * and a range that does not run forward is never asked (owner decisions 49
 * and 50, 2026-10-10).
 *
 * 49: the week section's title, the summary line under the form and the
 * chart's highlight band read the form's current fields, so typing a tool
 * name without asking relabelled an answer that was for another tool.
 *
 * 50: a backwards range (to at or before from) was sent and the server's 400
 * shown.  The form refuses it — Correlate unavailable, one line saying why —
 * and a backwards range in the address is not asked either.
 *
 * The chart is a stand-in that prints the band it is given: the real one
 * draws it with Plot, which jsdom cannot measure.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
vi.mock('../../components/ActivityHistogram', () => ({
  ActivityHistogram: ({ highlightStart, highlightEnd }: { highlightStart?: string | null; highlightEnd?: string | null }) => (
    <output data-testid="band">{highlightStart && highlightEnd ? `${highlightStart} → ${highlightEnd}` : 'no band'}</output>
  ),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import ToolActivity from '../../pages/ToolActivity';

const answer = (items: ActivityItem[] = []): ActivityResponse => ({
  items, total: items.length, truncated: false, accessible_project_ids: [1, 2],
  requested_project_ids: null,
  window_start: new Date(Date.now() - 7 * 86400_000).toISOString(),
  window_end: new Date().toISOString(),
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/tool-activity', element: <TooltipProvider><ToolActivity /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

const WEEK_MS = 7 * 86400_000;
type Asked = Record<string, unknown>;
const isWeek = (p: Asked) => new Date(p.to as string).getTime() - new Date(p.from as string).getTime() === WEEK_MS;
/** The focused range questions asked, in order (the week snapshot left out). */
const ranges = () => getScansBetween.mock.calls.map(([params]) => params as Asked).filter((p) => !isWeek(p));
const field = (label: string | RegExp) => screen.getByLabelText(label) as HTMLInputElement;
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 150)); });
const correlate = () => screen.getByRole('button', { name: 'Correlate' });
const BACKWARDS = /“To” must be later than “From”/;

// Yesterday, to the second: inside the week the snapshot covers.
const AT_INSTANT = new Date(Math.floor((Date.now() - 86400_000) / 1000) * 1000);
const AT = `/tool-activity?at=${encodeURIComponent(AT_INSTANT.toISOString())}&tolerance=60&tool=nmap&target=10.0.0.5`;
const BAND = `${new Date(AT_INSTANT.getTime() - 60_000).toISOString()} → ${new Date(AT_INSTANT.getTime() + 60_000).toISOString()}`;

beforeEach(() => {
  getScansAt.mockReset().mockResolvedValue(answer());
  getScansBetween.mockReset().mockResolvedValue(answer());
});

describe('Tool activity — what is on screen is labelled by the question that was asked', () => {
  it('editing the form without asking changes neither the title, the summary line nor the band', async () => {
    open(AT);
    const summary = await screen.findByText(/0 activities matched/);
    const title = () => screen.getByRole('heading', { name: /Past 7 days/ });
    expect(summary).toHaveTextContent('for nmap on 10.0.0.5');
    expect(title()).toHaveTextContent('for “nmap” on 10.0.0.5');
    expect(screen.getByTestId('band')).toHaveTextContent(BAND);

    // A draft: another tool, no target, another moment, a wider tolerance — not asked.
    fireEvent.change(field('Tool'), { target: { value: 'masscan' } });
    fireEvent.change(field('Target IP'), { target: { value: '' } });
    fireEvent.change(field('Timestamp (local)'), { target: { value: '2026-01-01T00:00' } });

    expect(screen.getByText(/0 activities matched/)).toHaveTextContent('for nmap on 10.0.0.5');
    expect(screen.getByText(/0 activities matched/)).not.toHaveTextContent('masscan');
    expect(title()).toHaveTextContent('for “nmap” on 10.0.0.5');
    expect(title()).not.toHaveTextContent('masscan');
    expect(screen.getByTestId('band')).toHaveTextContent(BAND);
    expect(screen.queryByText(/outside the past 7 days/)).toBeNull();
    await settle();
    expect(getScansAt).toHaveBeenCalledTimes(1);
  });

  it('asking relabels all three with the new question', async () => {
    open(AT);
    await screen.findByText(/0 activities matched/);
    fireEvent.change(field('Tool'), { target: { value: 'masscan' } });
    fireEvent.change(field('Target IP'), { target: { value: '' } });
    fireEvent.submit(field('Tool').closest('form')!);

    await waitFor(() => expect(getScansAt).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText(/0 activities matched/)).toHaveTextContent('for masscan'));
    expect(screen.getByText(/0 activities matched/)).not.toHaveTextContent('nmap');
    expect(screen.getByRole('heading', { name: /Past 7 days/ })).toHaveTextContent('for “masscan”');
    expect(screen.getByTestId('band')).toHaveTextContent(BAND);
  });

  it('with nothing asked there is no band, whatever the form holds', async () => {
    open('/tool-activity');
    await screen.findByTestId('correlate-prompt');
    expect(screen.getByTestId('band')).toHaveTextContent('no band');
    fireEvent.change(field('Tool'), { target: { value: 'masscan' } });
    expect(screen.getByRole('heading', { name: /Past 7 days/ })).not.toHaveTextContent('masscan');
    expect(screen.getByTestId('band')).toHaveTextContent('no band');
  });
});

describe('Tool activity — a range that does not run forward is not asked', () => {
  const RANGE = '/tool-activity?from=2026-09-29T00%3A00%3A00.000Z&to=2026-09-30T00%3A00%3A00.000Z&tool=masscan';

  it('the form refuses it: Correlate is unavailable and one line says why; nothing is sent', async () => {
    const router = open(RANGE);
    await waitFor(() => expect(ranges()).toHaveLength(1));
    expect(screen.queryByText(BACKWARDS)).toBeNull();
    // (Correlate also waits while a question is being answered.)
    await waitFor(() => expect(correlate()).toBeEnabled());

    fireEvent.change(field('To (local)'), { target: { value: '2026-09-01T00:00' } });
    expect(screen.getByRole('alert')).toHaveTextContent(BACKWARDS);
    expect(field('To (local)')).toHaveAttribute('aria-invalid', 'true');
    expect(correlate()).toBeDisabled();
    // Enter in a field submits the form whatever the button's state.
    fireEvent.submit(field('Tool').closest('form')!);
    await settle();
    expect(ranges()).toHaveLength(1);
    // The address still names the question that was asked.
    expect(router.state.location.search).toBe(RANGE.slice(RANGE.indexOf('?')));

    // Put right, it can be asked again.
    fireEvent.change(field('To (local)'), { target: { value: '2026-09-29T12:00' } });
    expect(screen.queryByText(BACKWARDS)).toBeNull();
    expect(correlate()).toBeEnabled();
    fireEvent.submit(field('Tool').closest('form')!);
    await waitFor(() => expect(ranges()).toHaveLength(2));
  });

  it('a range that ends when it starts is refused too', async () => {
    open(RANGE);
    await waitFor(() => expect(ranges()).toHaveLength(1));
    fireEvent.change(field('To (local)'), { target: { value: field('From (local)').value } });
    expect(screen.getByRole('alert')).toHaveTextContent(BACKWARDS);
    expect(correlate()).toBeDisabled();
  });

  it('a backwards range in the address is not asked, fills the form, and says the same thing', async () => {
    const router = open('/tool-activity?from=2026-09-30T00%3A00%3A00.000Z&to=2026-09-29T00%3A00%3A00.000Z&tool=nmap');
    await screen.findByTestId('correlate-prompt');
    expect(screen.getByRole('alert')).toHaveTextContent(BACKWARDS);
    expect(new Date(field('From (local)').value).toISOString()).toBe('2026-09-30T00:00:00.000Z');
    expect(new Date(field('To (local)').value).toISOString()).toBe('2026-09-29T00:00:00.000Z');
    expect(field('Tool').value).toBe('nmap');
    expect(correlate()).toBeDisabled();
    await settle();
    // Only the week snapshot was asked, and no refusal from the server is shown.
    expect(ranges()).toHaveLength(0);
    expect(getScansAt).not.toHaveBeenCalled();
    expect(within(screen.getByRole('alert')).queryByText(/Failed to load/)).toBeNull();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByTestId('band')).toHaveTextContent('no band');

    // A same-page link to it says so as well (the form follows the address).
    await act(async () => { await router.navigate('/tool-activity?from=2026-09-29T00%3A00%3A00.000Z&to=2026-09-30T00%3A00%3A00.000Z'); });
    await waitFor(() => expect(ranges()).toHaveLength(1));
    expect(screen.queryByText(BACKWARDS)).toBeNull();
    await act(async () => { await router.navigate('/tool-activity?from=2026-09-28T00%3A00%3A00.000Z&to=2026-09-27T00%3A00%3A00.000Z'); });
    expect(await screen.findByText(BACKWARDS)).toBeInTheDocument();
    await settle();
    expect(ranges()).toHaveLength(1);
  });
});
