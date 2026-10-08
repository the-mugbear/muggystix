/**
 * 5.341.0 — the remediation history: honest about when it starts, never a
 * line through one point, and every value in a table.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getRemediationTrend = vi.fn();
vi.mock('../../services/api', () => ({
  getRemediationTrend: (...a: unknown[]) => getRemediationTrend(...a),
}));

import RemediationTrend from '../../components/remediation/RemediationTrend';

const day = (d: string, overdue: number) => ({ day: d, overdue, due_soon: 1, on_track: 2, not_assigned: 3, deferred: 0, closed: 0 });

beforeEach(() => { getRemediationTrend.mockReset(); });

describe('RemediationTrend', () => {
  it('says nothing has been recorded yet rather than drawing an empty chart', async () => {
    getRemediationTrend.mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] });
    render(<RemediationTrend scope="project" />);
    expect(await screen.findByText(/No day has been recorded yet/)).toBeInTheDocument();
    expect(screen.getByText('Nothing was reported fixed in the last twelve months.')).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show as table' })).not.toBeInTheDocument();
  });

  it('one recorded day is a sentence, not a line', async () => {
    getRemediationTrend.mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [day('2026-11-10', 7)], closed_by_month: [] });
    render(<RemediationTrend scope="project" />);
    expect(await screen.findByText(/the only day recorded so far/)).toBeInTheDocument();
    expect(screen.getByText(/History starts/)).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('draws the days and the months, and gives every value as a table', async () => {
    getRemediationTrend.mockResolvedValue({
      as_of: '2026-11-10', days: 90,
      daily: [day('2026-11-08', 9), day('2026-11-09', 8), day('2026-11-10', 7)],
      closed_by_month: [{ month: '2026-10', on_time: 4, late: 1, no_deadline: 0 }, { month: '2026-11', on_time: 2, late: 3, no_deadline: 1 }],
    });
    render(<RemediationTrend scope="all" projectId={9} />);
    expect(await screen.findByRole('img', { name: /Overdue findings on hosts per day.*7 on the last day/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /reported fixed on time per month: 6 in all/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /reported fixed late per month: 4 in all/ })).toBeInTheDocument();
    expect(screen.getByText(/6 reported fixed on time, 4 late, 1 with no deadline to judge by/)).toBeInTheDocument();
    expect(screen.queryByText(/closed/i)).not.toBeInTheDocument();
    expect(getRemediationTrend).toHaveBeenCalledWith('all', 9, expect.anything());
    fireEvent.click(screen.getByRole('button', { name: 'Show as table' }));
    const [days, months] = screen.getAllByRole('table');
    expect(within(days).getAllByRole('row')).toHaveLength(4);
    expect(within(months).getAllByRole('row')).toHaveLength(13);       // twelve consecutive months, zeros included
  });

  it('says the history could not be loaded', async () => {
    getRemediationTrend.mockRejectedValue(new Error('boom'));
    render(<RemediationTrend scope="project" />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/);
  });
});
