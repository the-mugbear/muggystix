/**
 * The import history's status filter (components/scans/UploadStatusFilter,
 * 5.418.0) — Ingestion results' count strip, moved onto the one history
 * (owner decision 2026-10-22, G15).  Ported from the deleted
 * `pages/IngestionResults.test.tsx`: the counts are the filter, Failed says how
 * many of it are dismissed (G7), a transient state shows only while it holds
 * something or is the active filter, and the bulk dismissals act on exactly
 * the rows shown.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => {
  const named: Record<string, ReturnType<typeof vi.fn>> = {};
  return new Proxy(named, {
    get(target, prop: string) {
      if (prop === '__esModule') return true;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = vi.fn().mockResolvedValue([]);
      return target[prop];
    },
    has: () => true,
  });
});
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: 'analyst' } }),
}));

import UploadStatusFilter from '../../components/scans/UploadStatusFilter';
import type { ImportHistoryStatus, IngestionResultItem } from '../../services/api';

const COUNTS = {
  needs_attention: 2, failed: 3, failed_dismissed: 0, expired: 0, discarded: 0, superseded: 0,
  staged: 0, queued: 0, processing: 0, completed: 7,
};
const item = (over: Record<string, unknown>) => ({
  id: 1, original_filename: 'x.xml', status: 'failed', dismissed_at: null, superseded_by_job_id: null, ...over,
}) as unknown as IngestionResultItem;

const renderFilter = (props: {
  counts?: Partial<typeof COUNTS>;
  active?: ImportHistoryStatus | null;
  shown?: IngestionResultItem[];
  canWrite?: boolean;
} = {}) => {
  const onChange = vi.fn();
  render(
    <UploadStatusFilter
      counts={{ ...COUNTS, ...props.counts }}
      active={props.active ?? null}
      onChange={onChange}
      shownUploads={props.shown ?? []}
      canWrite={props.canWrite ?? true}
      busy={false}
    />,
  );
  return onChange;
};

beforeEach(() => vi.clearAllMocks());

describe('the history\'s status filter — the counts are the filter', () => {
  it('always states Needs attention and Failed, and a press asks for that list', () => {
    const onChange = renderFilter();
    fireEvent.click(screen.getByRole('button', { name: /^Needs attention\s*2$/ }));
    expect(onChange).toHaveBeenLastCalledWith('needs_attention');
    fireEvent.click(screen.getByRole('button', { name: /^Failed\s*3$/ }));
    expect(onChange).toHaveBeenLastCalledWith('failed');
    expect(screen.getByRole('button', { name: 'Whole history' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('the active count pressed again is the whole history', () => {
    const onChange = renderFilter({ active: 'failed' });
    const failed = screen.getByRole('button', { name: /^Failed\s*3$/ });
    expect(failed).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(failed);
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  // G7 — "Failed 3" stood beside Operations' "2 imports failed".
  it('says how many failed uploads are dismissed, right after Failed, and only when there are any', () => {
    const onChange = renderFilter({ counts: { failed_dismissed: 1 } });
    const failed = screen.getByRole('button', { name: /^Failed\s*3$/ });
    const dismissed = screen.getByRole('button', { name: /^of them dismissed\s*1$/ });
    expect(failed.nextElementSibling).toBe(dismissed);
    fireEvent.click(dismissed);
    expect(onChange).toHaveBeenLastCalledWith('failed_dismissed');
  });

  it('says nothing about dismissed failures when there are none', () => {
    renderFilter();
    expect(screen.queryByRole('button', { name: /dismissed/ })).toBeNull();
  });

  it('shows a state only while it holds something, or while it is the filter', () => {
    renderFilter({ counts: { expired: 31, discarded: 2 } });
    expect(screen.getByRole('button', { name: /^Expired\s*31$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Discarded\s*2$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Queued/ })).toBeNull();
    // Completed imports are the history's scan rows: no chip of their own.
    expect(screen.queryByRole('button', { name: /Completed/ })).toBeNull();
  });

  it('keeps a linked filter visible even when its count is zero', () => {
    renderFilter({ active: 'queued' });
    expect(screen.getByRole('button', { name: /^Queued\s*0$/ })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('the history\'s status filter — bulk dismissals act on the rows shown', () => {
  it('dismisses exactly the failed or partial rows shown, after saying what that means', async () => {
    api.dismissIngestionJob.mockResolvedValue({});
    renderFilter({
      active: 'needs_attention',
      shown: [
        item({ id: 9, status: 'completed', partial: true }),
        item({ id: 11 }),
        item({ id: 12, dismissed_at: '2026-09-01T00:00:00Z' }),
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss the 2 shown' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/1 of them imported only part of their file/)).toBeInTheDocument();
    expect(api.dismissIngestionJob).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(api.dismissIngestionJob).toHaveBeenCalledTimes(2));
    expect(api.dismissIngestionJob.mock.calls.map((c) => c.slice(0, 2))).toEqual([[1, 9], [1, 11]]);
  });

  it('offers no bulk dismiss on the whole history', () => {
    renderFilter({ shown: [item({ id: 11 })] });
    expect(screen.queryByRole('button', { name: /Dismiss the/ })).toBeNull();
  });

  it('offers none to someone who cannot write (hidden, not disabled)', () => {
    renderFilter({ active: 'needs_attention', canWrite: false, shown: [item({ id: 11 })] });
    expect(screen.queryByRole('button', { name: /Dismiss the/ })).toBeNull();
  });

  it('never offers to dismiss fewer superseded rows than the count beside it', () => {
    const onChange = renderFilter({ counts: { superseded: 4 }, shown: [item({ id: 478, superseded_by_job_id: 484 })] });
    expect(screen.queryByRole('button', { name: /^Dismiss \d+ superseded$/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Review 4 superseded' }));
    expect(onChange).toHaveBeenLastCalledWith('superseded');
  });

  it('dismisses exactly the superseded rows shown', async () => {
    api.dismissSupersededJobs.mockResolvedValue({ dismissed: 1, job_ids: [478] });
    renderFilter({
      active: 'superseded',
      counts: { superseded: 1 },
      shown: [item({ id: 478, superseded_by_job_id: 484 }), item({ id: 12, superseded_by_job_id: 20, dismissed_at: '2026-09-01T00:00:00Z' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss 1 superseded' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Dismiss 1' }));
    await waitFor(() => expect(api.dismissSupersededJobs).toHaveBeenCalledWith(1, [478]));
  });
});
