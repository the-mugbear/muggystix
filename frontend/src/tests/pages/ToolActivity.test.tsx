/**
 * Tool Activity (screenshot review 2026-09-23): the page leads with its
 * cross-project scope, draws the week as a fixed-height binned chart, and
 * does not answer a query nobody asked on arrival.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';
import type { ActivityItem, ActivityKind, ActivityResponse } from '../../services/api';

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

import ToolActivity from '../../pages/ToolActivity';

const item = (kind: ActivityKind, start: string, ref: number): ActivityItem => ({
  kind,
  ref_id: ref,
  project_id: 1,
  project_name: 'Acme internal',
  label: 'nmap',
  secondary_label: null,
  start_time: start,
  end_time: null,
  recorded_time: null,
  start_time_is_fallback: false,
  has_end_time: false,
  host_count: null,
  status: null,
  target: null,
  parent_id: null,
});

const week = (items: ActivityItem[]): ActivityResponse => ({
  items,
  total: items.length,
  truncated: false,
  accessible_project_ids: [1, 2, 3],
  requested_project_ids: null,
  window_start: new Date(Date.now() - 7 * 86400_000).toISOString(),
  window_end: new Date().toISOString(),
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <ToolActivity />
      </TooltipProvider>
    </MemoryRouter>,
  );

describe('ToolActivity', () => {
  const recent = (minsAgo: number) => new Date(Date.now() - minsAgo * 60_000).toISOString();

  beforeEach(() => {
    getScansAt.mockReset();
    getScansBetween.mockReset();
    // 60 scans inside one hour plus one execution run: the burst that used
    // to stack into a ~1100px column of dots.
    const burst = Array.from({ length: 60 }, (_, i) => item('scan', recent(180 + (i % 30)), i));
    getScansBetween.mockResolvedValue(week([...burst, item('execution_session', recent(60 * 30), 999)]));
  });

  it('leads with the cross-project scope', async () => {
    renderPage();
    const lead = await screen.findByTestId('tool-activity-lead');
    await waitFor(() => expect(lead).toHaveTextContent('Across all 3 projects you can see'));
    expect(lead).toHaveTextContent('not only Acme internal');
  });

  it('does not run a focused query on arrival — it prompts instead', async () => {
    renderPage();
    await screen.findByTestId('activity-histogram');
    // Only the week snapshot was fetched; no "now ± 5 minutes" query.
    expect(getScansAt).not.toHaveBeenCalled();
    expect(getScansBetween).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('correlate-prompt')).toHaveTextContent(
      'Pick a tool, address or time to correlate',
    );
    expect(screen.queryByText(/activities matched/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No activity in this window/)).not.toBeInTheDocument();
  });

  it('draws a burst as a fixed-height chart with a row per kind present', async () => {
    renderPage();
    const svg = await screen.findByTestId('activity-histogram');
    expect(Number(svg.getAttribute('height'))).toBeLessThanOrEqual(200);
    const kinds = Array.from(svg.querySelectorAll('g[data-kind]')).map((g) => g.getAttribute('data-kind'));
    expect(kinds).toEqual(['scan', 'execution_session']);
    // Legend-by-row carries counts; absent kinds are said to be absent.
    expect(svg).toHaveTextContent('Scan uploads 60');
    expect(screen.getByText(/None in this window: recon runs, commands run, target probes/)).toBeInTheDocument();
    // The Correlate window (now ± 5 min) is inside the week: its band shows.
    expect(screen.getByTestId('activity-focus-band')).toBeInTheDocument();
  });

  it('correlates a chosen chart bin as a range query', async () => {
    renderPage();
    await screen.findByTestId('activity-histogram');
    const chart = screen.getByLabelText(/Activity per time bin/);
    fireEvent.keyDown(chart, { key: 'ArrowRight' }); // onto the latest bin
    fireEvent.keyDown(chart, { key: 'Enter' });
    await waitFor(() => expect(getScansBetween).toHaveBeenCalledTimes(2));
    const { from, to } = getScansBetween.mock.calls[1][0];
    expect(new Date(to).getTime() - new Date(from).getTime()).toBe(3600_000);
    expect(new Date(to).getTime()).toBeGreaterThanOrEqual(Date.now() - 60_000);
    expect(getScansAt).not.toHaveBeenCalled();
    expect(await screen.findByText(/activities matched/)).toBeInTheDocument();
  });

  it('runs the focused query when the analyst asks', async () => {
    getScansAt.mockResolvedValue({ ...week([]), total: 0 });
    renderPage();
    await screen.findByTestId('activity-histogram');
    fireEvent.click(screen.getByRole('button', { name: /Correlate/ }));
    await waitFor(() => expect(getScansAt).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/0 activities matched/)).toBeInTheDocument();
    expect(screen.queryByTestId('correlate-prompt')).not.toBeInTheDocument();
  });
});
