/**
 * Small SVG/CSS visual primitives for the Security Posture page. Theme-aware
 * (hsl(var(--token))), gentle mount animation. No external charting dependency.
 *
 * Severity distributions now use the shared <SeverityBar> (components/ui);
 * single-value percentages use <Meter>; systemic prevalence uses <PrevalenceBar>.
 * (The donut ArcGauge was removed — the design deliberately avoids donut charts;
 * a labelled horizontal meter reads faster and lines up with the other cards.)
 */
import React, { useEffect, useState } from 'react';

/** Animate a number from 0 → target on mount (eased by the consumer's CSS
 *  transition; this flips the value after first paint). */
function useReveal(target: number): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setV(target));
    return () => cancelAnimationFrame(id);
  }, [target]);
  return v;
}

// ---------------------------------------------------------------------------
// Meter — a single-value horizontal progress bar with a track. Used for the
// coverage + ownership headline measures (replaces the donut gauges).
// ---------------------------------------------------------------------------
interface MeterProps {
  pct: number | null;          // 0..100, or null → empty track ("Unknown")
  color: string;               // hsl(...)
  height?: number;
}

export const Meter: React.FC<MeterProps> = ({ pct, color, height = 8 }) => {
  const safe = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const shown = useReveal(safe);
  return (
    <div className="w-full rounded-full bg-muted" style={{ height }}
      role="img" aria-label={pct == null ? 'unknown' : `${safe}%`}>
      <div className="h-full rounded-full"
        style={{
          width: `${shown}%`, background: color,
          transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
        }} />
    </div>
  );
};

// ---------------------------------------------------------------------------
// PrevalenceBar — "% of hosts affected", for the systemic-weaknesses panel.
// ---------------------------------------------------------------------------
interface PrevalenceBarProps {
  fraction: number;            // 0..1
  color: string;               // hsl(...)
  height?: number;
}

export const PrevalenceBar: React.FC<PrevalenceBarProps> = ({ fraction, color, height = 8 }) => {
  const pct = Math.max(0, Math.min(100, fraction * 100));
  const shown = useReveal(pct);
  return (
    <div className="w-full rounded-full bg-muted" style={{ height }} aria-hidden>
      <div className="h-full rounded-full"
        style={{
          width: `${shown}%`, background: color, opacity: 0.92,
          transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
        }} />
    </div>
  );
};
