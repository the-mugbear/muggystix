import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

import UpdatedAt from '../../components/UpdatedAt';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-20T12:00:00Z'));
});
afterEach(() => vi.useRealTimers());

// v5.243.0 — Operations is several independent fetches, and some sections keep
// their previous data when a refresh fails. Nothing said how old that data was.
describe('UpdatedAt', () => {
  it('renders nothing before the first successful load', () => {
    const { container } = render(<UpdatedAt at={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is a quiet caption when fresh', () => {
    render(<UpdatedAt at={new Date('2026-09-20T11:59:50Z')} />);
    const el = screen.getByText('updated just now');
    expect(el).toHaveClass('text-muted-foreground');
  });

  it('says the data is the PREVIOUS load when the latest refresh failed', () => {
    render(<UpdatedAt at={new Date('2026-09-20T11:40:00Z')} stale />);
    const el = screen.getByText('showing data from 20m ago');
    expect(el).toHaveClass('text-warning');
    expect(el).toHaveAttribute('title', expect.stringContaining('The last refresh failed'));
  });

  it('keeps counting without a re-render from its parent', () => {
    render(<UpdatedAt at={new Date('2026-09-20T11:59:50Z')} />);
    expect(screen.getByText('updated just now')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(5 * 60_000); });
    expect(screen.getByText('updated 5m ago')).toBeInTheDocument();
  });
});
