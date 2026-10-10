/**
 * LastUpdated: when the data was read, a refresh button and — in the full
 * form — the "Auto" switch.  The switch is the PAGE's (5.351.0): the
 * component runs no timer and never refreshes by itself; the page polls its
 * own query (`pollEvery`) while the switch is on.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import LastUpdated from '../../components/LastUpdated';
import { TooltipProvider } from '../../components/ui/tooltip';

const show = (ui: React.ReactElement) => render(<TooltipProvider>{ui}</TooltipProvider>);

afterEach(() => { vi.useRealTimers(); });

describe('LastUpdated', () => {
  it('says when the data was read and refreshes on the button, named for its data', () => {
    const onRefresh = vi.fn();
    show(<LastUpdated compact lastFetched={new Date()} onRefresh={onRefresh} label="ingestion jobs" />);
    expect(screen.getByText('Updated just now')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh ingestion jobs' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // Compact: no Auto switch.
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('says "never" before a first read, and disables the button while reading', () => {
    show(<LastUpdated compact lastFetched={null} onRefresh={vi.fn()} isLoading label="names" />);
    expect(screen.getByText('Updated never')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh names' })).toBeDisabled();
  });

  it('shows the Auto switch where the page says it is, and tells the page when it is moved', () => {
    const onAutoRefreshChange = vi.fn();
    const { rerender } = show(
      <LastUpdated lastFetched={new Date()} onRefresh={vi.fn()} autoRefresh={false} onAutoRefreshChange={onAutoRefreshChange}
        label="ingestion jobs" />,
    );
    // Owner decision 42: the switch's name says what it switches (it was the
    // bare "Auto"); the visible label is unchanged, and is part of the name.
    expect(screen.getByText('Auto')).toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: 'Auto' })).toBeNull();
    const auto = screen.getByRole('switch', { name: 'Auto-refresh ingestion jobs' });
    expect(auto).not.toBeChecked();
    fireEvent.click(auto);
    expect(onAutoRefreshChange).toHaveBeenCalledWith(true);
    // The position is the page's: it does not move until the page says so.
    expect(auto).not.toBeChecked();
    rerender(
      <TooltipProvider>
        <LastUpdated lastFetched={new Date()} onRefresh={vi.fn()} autoRefresh onAutoRefreshChange={onAutoRefreshChange}
          label="ingestion jobs" />
      </TooltipProvider>,
    );
    expect(screen.getByRole('switch', { name: 'Auto-refresh ingestion jobs' })).toBeChecked();
  });

  it('runs no timer of its own: with Auto on it never calls the refresh', async () => {
    vi.useFakeTimers();
    const onRefresh = vi.fn();
    show(<LastUpdated lastFetched={new Date()} onRefresh={onRefresh} autoRefresh onAutoRefreshChange={vi.fn()} intervalMs={1000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(onRefresh).not.toHaveBeenCalled();
  });
});
