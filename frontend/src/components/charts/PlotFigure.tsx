/**
 * One Observable Plot figure as a React component (5.307.2) — the charts'
 * shared wrapper (UI_STYLE_GUIDE §35: charts with axes use Plot).
 *
 * Plot draws plain SVG from declarative options; this mounts it, redraws it
 * when the options change, and reports its x scale's `invert` so a chart can
 * map the pointer to a data point (one crosshair across several figures).
 * Memoise `options` — every new object redraws.
 */
import React, { useEffect, useRef } from 'react';
import * as Plot from '@observablehq/plot';

export interface PlotFigureProps {
  options: Plot.PlotOptions;
  /** The figure's accessible name (it is an image to assistive tech). */
  label: string;
  /** Receives the x scale's inverse after each draw (null when it has none). */
  onScale?: (invert: ((px: number) => unknown) | null) => void;
  className?: string;
  testId?: string;
}

export const PlotFigure: React.FC<PlotFigureProps> = ({ options, label, onScale, className, testId }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host) return undefined;
    const figure = Plot.plot(options);
    figure.setAttribute('role', 'img');
    figure.setAttribute('aria-label', label);
    host.replaceChildren(figure);
    const x = figure.scale('x');
    onScale?.(x?.invert ? (px: number) => x.invert!(px) : null);
    return () => figure.remove();
  }, [options, onScale, label]);
  return (
    <div ref={ref} data-testid={testId}
      className={className ?? 'min-w-0 [&_svg]:block [&_svg]:overflow-visible'} />
  );
};

export default PlotFigure;
