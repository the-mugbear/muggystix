/**
 * v5.265.0 — the Oversight growth charts shrank to 280 px after a filter left
 * no points and was then cleared: the width observer was attached once and
 * kept watching the detached container.
 */
import { render, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import GrowthCharts from '../../components/oversight/GrowthCharts';

type Callback = (entries: Array<{ contentRect: { width: number } }>) => void;
const observers: Array<{ cb: Callback; els: Element[] }> = [];

class FakeResizeObserver {
  private rec: { cb: Callback; els: Element[] };
  constructor(cb: Callback) { this.rec = { cb, els: [] }; observers.push(this.rec); }
  observe(el: Element) { this.rec.els.push(el); }
  disconnect() { this.rec.els = []; }
}

const point = (start: string, cumulative: number) => ({
  start, cumulative_targets: cumulative, targets_added: 1, reviews_concluded: 0,
});
const points = [point('2026-09-01', 10), point('2026-09-02', 11)];

const svgWidths = (container: HTMLElement) =>
  [...container.querySelectorAll('svg')].map((s) => Number(s.getAttribute('width')));

beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});
afterEach(() => vi.unstubAllGlobals());

describe('GrowthCharts — width survives an empty result', () => {
  it('measures the container again when it comes back', () => {
    const { container, rerender } = render(<GrowthCharts unit="day" points={points} />);
    // Like a real observer: an element still in the page reports the width,
    // a detached one reports 0.
    const report = (width: number) => act(() => {
      observers.forEach((o) => o.els.forEach((el) => o.cb([{ contentRect: { width: el.isConnected ? width : 0 } }])));
    });
    report(900);
    expect(svgWidths(container)).toEqual([900, 900, 900]);

    // A filter leaves nothing: the container goes (and reports 0).
    rerender(<GrowthCharts unit="day" points={[]} />);
    report(900);

    // The filter is cleared: the new container is observed and measured.
    rerender(<GrowthCharts unit="day" points={points} />);
    expect(observers.some((o) => o.els.length > 0)).toBe(true);
    report(900);
    expect(svgWidths(container)).toEqual([900, 900, 900]);
  });
});
