/**
 * Reusable SVG visual primitives for the Security Posture page. All theme-aware
 * (hsl(var(--token))), all with a gentle mount animation (managers read these
 * once — a half-second reveal makes the snapshot feel alive without implying a
 * time series). No external charting dependency.
 */
import React, { useEffect, useState } from 'react';

import type { Severity } from '../../services/api';
import { SEVERITY_HSL, SEVERITY_ORDER, SEVERITY_LABEL } from './postureTheme';

/** Animate a value from 0 → target on mount (eased by CSS transition on the
 *  consuming element; this just flips the value after first paint). */
function useReveal<T>(target: T, zero: T): T {
  const [v, setV] = useState<T>(zero);
  useEffect(() => {
    const id = requestAnimationFrame(() => setV(target));
    return () => cancelAnimationFrame(id);
  }, [target]);
  return v;
}

// ---------------------------------------------------------------------------
// ArcGauge — a donut ring with a centred percentage. Used for coverage +
// ownership. Colour is passed by the caller (severity/health tone).
// ---------------------------------------------------------------------------
interface ArcGaugeProps {
  pct: number | null;          // 0..100, or null → "Unknown"
  color: string;               // hsl(...)
  size?: number;
  label?: string;              // tiny caption under the number
  centerTop?: string;          // small line above the big number
}

export const ArcGauge: React.FC<ArcGaugeProps> = ({
  pct, color, size = 104, label, centerTop,
}) => {
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const safe = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const shown = useReveal(safe, 0);
  const offset = c - (shown / 100) * c;
  const gid = `arc-${Math.round(r)}-${color.replace(/\W/g, '')}`;

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" role="img"
        aria-label={pct == null ? `${label ?? 'value'}: unknown` : `${label ?? 'value'}: ${safe}%`}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.65} />
            <stop offset="100%" stopColor={color} stopOpacity={1} />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke="hsl(var(--muted))" strokeWidth={stroke} />
        {pct != null && (
          <circle
            cx={size / 2} cy={size / 2} r={r} fill="none"
            stroke={`url(#${gid})`} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={offset}
            style={{ transition: 'stroke-dashoffset 700ms cubic-bezier(0.22,1,0.36,1)' }}
          />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {centerTop && <span className="text-[0.6rem] uppercase tracking-wide text-muted-foreground">{centerTop}</span>}
        <span className="text-xl font-semibold tabular-nums text-foreground">
          {pct == null ? '—' : `${safe}%`}
        </span>
        {label && <span className="text-[0.6rem] text-muted-foreground">{label}</span>}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// SeverityStack — one horizontal bar split into severity segments. Used for the
// confirmed-exposure headline + per-site exposure.
// ---------------------------------------------------------------------------
interface SeverityStackProps {
  counts: Partial<Record<Severity, number>>;
  height?: number;
  showLegend?: boolean;
}

export const SeverityStack: React.FC<SeverityStackProps> = ({ counts, height = 12, showLegend }) => {
  const total = SEVERITY_ORDER.reduce((s, k) => s + (counts[k] ?? 0), 0);
  const reveal = useReveal(1, 0);
  return (
    <div className="w-full">
      <div
        className="flex w-full overflow-hidden rounded-full bg-muted"
        style={{ height }}
        role="img"
        aria-label={SEVERITY_ORDER.filter((k) => counts[k]).map((k) => `${counts[k]} ${k}`).join(', ') || 'no findings'}
      >
        {total === 0 ? null : SEVERITY_ORDER.map((k) => {
          const n = counts[k] ?? 0;
          if (!n) return null;
          return (
            <div
              key={k}
              title={`${n} ${SEVERITY_LABEL[k]}`}
              style={{
                width: `${(reveal * n / total) * 100}%`,
                background: SEVERITY_HSL[k],
                transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
              }}
            />
          );
        })}
      </div>
      {showLegend && (
        <div className="mt-xs flex flex-wrap gap-x-md gap-y-xxs">
          {SEVERITY_ORDER.filter((k) => counts[k]).map((k) => (
            <span key={k} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
              <span className="size-2 rounded-full" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
              {counts[k]} {SEVERITY_LABEL[k]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// PrevalenceBar — a labelled horizontal bar for "% of hosts affected". Used by
// the systemic-weaknesses panel.
// ---------------------------------------------------------------------------
interface PrevalenceBarProps {
  fraction: number;            // 0..1
  color: string;               // hsl(...)
  height?: number;
}

export const PrevalenceBar: React.FC<PrevalenceBarProps> = ({ fraction, color, height = 8 }) => {
  const pct = Math.max(0, Math.min(100, fraction * 100));
  const shown = useReveal(pct, 0);
  return (
    <div className="w-full rounded-full bg-muted" style={{ height }} aria-hidden>
      <div
        className="h-full rounded-full"
        style={{
          width: `${shown}%`,
          background: `linear-gradient(90deg, ${color} , ${color})`,
          boxShadow: `0 0 10px ${color}`,
          opacity: 0.92,
          transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
        }}
      />
    </div>
  );
};
