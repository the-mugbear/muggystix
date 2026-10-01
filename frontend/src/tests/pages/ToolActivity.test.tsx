/**
 * Tool Activity (screenshot review 2026-09-23): the page leads with its
 * cross-project scope, draws the week as a fixed-height binned chart, and
 * does not answer a query nobody asked on arrival.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';
import type { ActivityItem, ActivityKind, ActivityResponse } from '../../services/api';

const getScansAt = vi.fn();
const getScansBetween = vi.fn();
vi.mock('../../services/api', () => ({
  getScansAt: (...a: unknown[]) => getScansAt(...a),
  getScansBetween: (...a: unknown[]) => getScansBetween(...a),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

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
    // 60 scans inside one hour plus one recorded command: the burst that
    // used to stack into a ~1100px column of dots.
    const burst = Array.from({ length: 60 }, (_, i) => item('scan', recent(180 + (i % 30)), i));
    getScansBetween.mockResolvedValue(week([...burst, item('evidence', recent(60 * 30), 999)]));
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
    const chart = await screen.findByTestId('activity-histogram');
    // Fixed height whatever the burst: one Plot row per kind present, each
    // of a set height (5.307.2 — rows are separate figures on one time axis).
    const heights = Array.from(chart.querySelectorAll('svg')).map((s) => Number(s.getAttribute('height')));
    expect(heights.length).toBe(2);
    expect(heights.reduce((a, h) => a + h, 0)).toBeLessThanOrEqual(200);
    const kinds = Array.from(chart.querySelectorAll('[data-kind]')).map((g) => g.getAttribute('data-kind'));
    // The two kinds there are (5.320.0): scan uploads and recorded commands.
    expect(kinds).toEqual(['scan', 'evidence']);
    // Each row is named and counted.
    expect(chart).toHaveTextContent('Scan uploads 60');
    expect(chart).toHaveTextContent(/Commands recorded\s*1/i);
    expect(screen.queryByText(/None in this window/)).not.toBeInTheDocument();
    // The Correlate window (now ± 5 min) is inside the week: its band shows.
    expect(chart.querySelector('.activity-focus-band')).not.toBeNull();
  });

  it('says which kind is absent when only scans are in the window', async () => {
    getScansBetween.mockResolvedValue(week([item('scan', recent(200), 1)]));
    renderPage();
    const chart = await screen.findByTestId('activity-histogram');
    await waitFor(() => expect(chart.querySelectorAll('[data-kind]').length).toBe(1));
    expect(screen.getByText(/None in this window: commands recorded/)).toBeInTheDocument();
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

  // 5.320.0 — the per-command record is an evidence record: it reads
  // "command recorded", shows its outcome and the address it reached, and
  // opens the host (the record lives on the host's page).
  it('lists a recorded command with its outcome and opens its host', async () => {
    navigate.mockReset();
    getScansAt.mockResolvedValue(week([{
      ...item('evidence', recent(2), 41),
      label: 'curl', secondary_label: 'curl -sI https://10.0.0.9/', status: 'finding',
      target: '10.0.0.9', parent_id: 7,
    }]));
    renderPage();
    await screen.findByTestId('activity-histogram');
    fireEvent.click(screen.getByRole('button', { name: /Correlate/ }));
    expect(await screen.findByText('command recorded')).toBeInTheDocument();
    expect(screen.getByText('finding')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.9')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open detail' }));
    expect(navigate).toHaveBeenCalledWith('/hosts?search=10.0.0.9');
  });

  it('opens the recording session when a command has no address', async () => {
    navigate.mockReset();
    getScansAt.mockResolvedValue(week([{ ...item('evidence', recent(2), 42), parent_id: 7 }]));
    renderPage();
    await screen.findByTestId('activity-histogram');
    fireEvent.click(screen.getByRole('button', { name: /Correlate/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open detail' }));
    expect(navigate).toHaveBeenCalledWith('/agent-sessions/7');
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

  // Review 2026-10-01 follow-up — the focused query had no guard: asking
  // twice let the first, slower answer replace the second.
  it('a slow first answer never replaces a later query', async () => {
    let releaseFirst!: (r: ActivityResponse) => void;
    getScansAt
      .mockImplementationOnce(() => new Promise<ActivityResponse>((resolve) => { releaseFirst = resolve; }))
      .mockResolvedValueOnce({ ...week([]), total: 0 });
    renderPage();
    await screen.findByTestId('activity-histogram');
    fireEvent.click(screen.getByRole('button', { name: /Correlate/ }));
    await waitFor(() => expect(getScansAt).toHaveBeenCalledTimes(1));
    fireEvent.submit(screen.getByLabelText('Tool').closest('form')!);
    await waitFor(() => expect(getScansAt).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/0 activities matched/)).toBeInTheDocument();

    releaseFirst({ ...week([item('scan', recent(5), 1), item('scan', recent(6), 2)]), total: 2 });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText(/0 activities matched/)).toBeInTheDocument();
    expect(screen.queryByText(/2 activities matched/)).toBeNull();
  });

  // B15 — "what ran at 14:32" is a link: the query lives in the URL.
  describe('the query is in the URL', () => {
    const Where = () => <output data-testid="where">{useSearchParams()[0].toString()}</output>;
    const renderAt = (url: string) => render(
      <MemoryRouter initialEntries={[url]}>
        <TooltipProvider><ToolActivity /><Where /></TooltipProvider>
      </MemoryRouter>,
    );
    const params = () => new URLSearchParams(screen.getByTestId('where').textContent ?? '');

    it('a link with timestamp, tolerance, tool and target restores them and runs the query', async () => {
      getScansAt.mockResolvedValue({ ...week([]), total: 0 });
      renderAt('/tool-activity?at=2026-09-30T14:32:00.000Z&tolerance=60&tool=nmap&target=10.0.0.5');
      await waitFor(() => expect(getScansAt).toHaveBeenCalledWith({
        ts: '2026-09-30T14:32:00.000Z', toleranceSeconds: 60, tool: 'nmap', target: '10.0.0.5',
      }));
      expect(screen.getByLabelText('Tool')).toHaveValue('nmap');
      expect(screen.getByLabelText(/Target/)).toHaveValue('10.0.0.5');
      // The week snapshot is filtered the same way.
      expect(getScansBetween).toHaveBeenCalledWith(expect.objectContaining({ tool: 'nmap', target: '10.0.0.5' }));
      // …and the URL still names the query.
      expect(params().get('at')).toBe('2026-09-30T14:32:00.000Z');
      expect(params().get('tolerance')).toBe('60');
    });

    it('a from/to link restores a range query', async () => {
      renderAt('/tool-activity?from=2026-09-29T00:00:00.000Z&to=2026-09-30T00:00:00.000Z');
      await waitFor(() => expect(getScansBetween).toHaveBeenCalledWith(expect.objectContaining({
        from: '2026-09-29T00:00:00.000Z', to: '2026-09-30T00:00:00.000Z',
      })));
      expect(getScansAt).not.toHaveBeenCalled();
    });

    it('writes the tool and the window asked for; an untouched form writes nothing', async () => {
      getScansAt.mockResolvedValue({ ...week([]), total: 0 });
      renderAt('/tool-activity');
      await screen.findByTestId('activity-histogram');
      expect(screen.getByTestId('where').textContent).toBe('');

      fireEvent.change(screen.getByLabelText('Tool'), { target: { value: 'nuclei' } });
      await waitFor(() => expect(params().get('tool')).toBe('nuclei'));
      expect(params().get('at')).toBeNull();  // no window was asked for yet

      fireEvent.submit(screen.getByLabelText('Tool').closest('form')!);
      await waitFor(() => expect(getScansAt).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(params().get('at')).toBe(getScansAt.mock.calls[0][0].ts));
      expect(params().get('tolerance')).toBe('300');
    });

    it('ignores an unreadable timestamp or a tolerance the form does not offer', async () => {
      renderAt('/tool-activity?at=yesterday&tolerance=7');
      await screen.findByTestId('activity-histogram');
      expect(getScansAt).not.toHaveBeenCalled();
    });
  });
});
