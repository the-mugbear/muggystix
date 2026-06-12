/**
 * "Where risk concentrates" — an SVG bubble matrix over the project's sites.
 *
 *   X  = % of the site's hosts reviewed        (left = under-reviewed)
 *   Y  = finding incidences per host           (up = denser exposure)
 *   r  = host count                            (bigger = larger blast radius)
 *   ◾ = colour — switchable: worst active severity in the site (default), or
 *        a per-site identity colour. Tier was dropped as the colour encoding
 *        because it's optional (unset sites all default to one tier).
 *
 * The top-left quadrant — under-reviewed AND high-exposure — is shaded as the
 * danger zone, so the sites that most need attention are the ones your eye
 * lands on first. SVG-native tooltip → scales cleanly with the card.
 */
import React, { useMemo, useState } from 'react';

import type { PostureSite } from '../../services/api';
import {
  type Severity, SEVERITY_ORDER, SEVERITY_HSL, SEVERITY_LABEL,
} from '../../utils/severity';
import { TIER_LABEL } from './postureTheme';

// Wide aspect so a full-width hero card doesn't render an over-tall chart.
const W = 1200;
const H = 420;
const PAD = { l: 52, r: 20, t: 20, b: 42 };
const MUTED = 'hsl(var(--muted-foreground))';

// Distinct mid-tone hues for per-site identity colouring — categorical, not
// theme tokens (there's no "site token"); mid lightness reads on light + dark.
const SITE_PALETTE = [
  'hsl(199 89% 48%)', 'hsl(160 84% 39%)', 'hsl(38 92% 50%)', 'hsl(280 65% 60%)',
  'hsl(346 77% 50%)', 'hsl(220 70% 56%)', 'hsl(95 55% 45%)', 'hsl(24 90% 55%)',
  'hsl(180 60% 42%)', 'hsl(320 60% 55%)',
];

type ColourMode = 'severity' | 'site';

const worstSeverity = (s: PostureSite): Severity | null => {
  for (const k of SEVERITY_ORDER) if ((s.exposure.by_severity[k] ?? 0) > 0) return k;
  return null;
};

interface RiskBubbleMatrixProps {
  sites: PostureSite[];
}

const RiskBubbleMatrix: React.FC<RiskBubbleMatrixProps> = ({ sites }) => {
  const [hover, setHover] = useState<string | null>(null);
  const [mode, setMode] = useState<ColourMode>('severity');

  const plottable = sites.filter((s) => s.host_count > 0);
  const zeroHost = sites.filter((s) => s.host_count === 0 && !s.unassigned);

  const incidencesOf = (s: PostureSite) =>
    s.exposure.finding_host_incidences ?? s.exposure.active_findings;
  const maxPerHost = Math.max(
    0.5, ...plottable.map((s) => incidencesOf(s) / Math.max(1, s.host_count)),
  );
  const maxHosts = Math.max(1, ...plottable.map((s) => s.host_count));

  const xOf = (reviewPct: number) => PAD.l + (reviewPct / 100) * (W - PAD.l - PAD.r);
  const yOf = (perHost: number) => (H - PAD.b) - (perHost / maxPerHost) * (H - PAD.t - PAD.b);
  const rOf = (hosts: number) => 9 + Math.sqrt(hosts / maxHosts) * 40;

  // Stable per-site palette index (from worst-first input order, before the
  // draw-order sort) so a site keeps its colour as you hover/re-render.
  const siteColorIndex = useMemo(() => {
    const m = new Map<string, number>();
    plottable.forEach((s, i) => m.set(s.unassigned ? '__unassigned__' : (s.site ?? `site-${s.site_id}`), i));
    return m;
  }, [plottable]);

  const colorOf = (s: PostureSite, key: string): string => {
    if (mode === 'site') {
      return s.unassigned ? MUTED : SITE_PALETTE[(siteColorIndex.get(key) ?? 0) % SITE_PALETTE.length];
    }
    const ws = worstSeverity(s);
    return ws ? SEVERITY_HSL[ws] : MUTED;
  };

  const points = plottable.map((s) => {
    const key = s.unassigned ? '__unassigned__' : (s.site ?? `site-${s.site_id}`);
    const reviewed = s.host_count - s.neglect.unreviewed_hosts;
    const reviewPct = s.host_count > 0 ? (reviewed / s.host_count) * 100 : 0;
    const perHost = incidencesOf(s) / Math.max(1, s.host_count);
    return {
      site: s, key,
      name: s.unassigned ? 'Unassigned' : (s.site ?? 'Unnamed'),
      reviewPct, perHost,
      cx: xOf(reviewPct), cy: yOf(perHost), r: rOf(s.host_count),
      color: colorOf(s, key),
    };
  });
  // Draw the largest bubbles first so small ones stay clickable on top.
  points.sort((a, b) => b.r - a.r);

  const midX = xOf(50);
  const dangerW = midX - PAD.l;
  const dangerH = (H - PAD.t - PAD.b) / 2;

  // Legend entries depend on the colour mode.
  const severityLegend = SEVERITY_ORDER.filter((k) => plottable.some((s) => worstSeverity(s) === k));
  const anyNoFindings = plottable.some((s) => worstSeverity(s) === null);
  const siteLegend = plottable.slice(0, 12);

  const ToggleBtn: React.FC<{ value: ColourMode; label: string }> = ({ value, label }) => (
    <button
      type="button"
      aria-pressed={mode === value}
      onClick={() => setMode(value)}
      className={`px-sm py-xxs text-caption transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        value === 'site' ? 'border-l border-border' : ''
      } ${mode === value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
    >
      {label}
    </button>
  );

  return (
    <div className="w-full">
      <div className="mb-sm flex items-center justify-end gap-xs">
        <span className="text-caption text-muted-foreground">Colour by</span>
        <div className="inline-flex overflow-hidden rounded-control border border-border" role="group" aria-label="Bubble colour mode">
          <ToggleBtn value="severity" label="Severity" />
          <ToggleBtn value="site" label="Site" />
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full"
        style={{ maxHeight: 460 }} preserveAspectRatio="xMidYMid meet" role="img"
        aria-label="Bubble matrix of sites by review coverage and exposure density">
        <defs>
          <linearGradient id="danger-zone" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="hsl(var(--destructive))" stopOpacity={0.14} />
            <stop offset="100%" stopColor="hsl(var(--destructive))" stopOpacity={0.02} />
          </linearGradient>
        </defs>

        {/* Danger quadrant: under-reviewed (left) + high exposure (top). */}
        <rect x={PAD.l} y={PAD.t} width={dangerW} height={dangerH} fill="url(#danger-zone)" />
        <text x={PAD.l + 8} y={PAD.t + 16} className="fill-destructive" fontSize={11} opacity={0.8}>
          Under-reviewed · high exposure
        </text>

        {/* Gridlines + X ticks */}
        {[0, 25, 50, 75, 100].map((t) => (
          <g key={t}>
            <line x1={xOf(t)} y1={PAD.t} x2={xOf(t)} y2={H - PAD.b}
              stroke="hsl(var(--border))" strokeWidth={1} strokeDasharray="2 4" />
            <text x={xOf(t)} y={H - PAD.b + 16} textAnchor="middle"
              className="fill-muted-foreground" fontSize={10}>{t}%</text>
          </g>
        ))}

        {/* Axis frame */}
        <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} stroke="hsl(var(--border))" strokeWidth={1.5} />
        <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} stroke="hsl(var(--border))" strokeWidth={1.5} />

        {/* Axis titles */}
        <text x={(PAD.l + W - PAD.r) / 2} y={H - 6} textAnchor="middle"
          className="fill-muted-foreground" fontSize={11}>Hosts reviewed →</text>
        <text x={14} y={(PAD.t + H - PAD.b) / 2} textAnchor="middle"
          transform={`rotate(-90 14 ${(PAD.t + H - PAD.b) / 2})`}
          className="fill-muted-foreground" fontSize={11}>Finding incidence / host ↑</text>

        {/* Bubbles */}
        {points.map((p) => {
          const active = hover === p.key;
          return (
            <g key={p.key}
              onMouseEnter={() => setHover(p.key)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }}>
              <circle
                cx={p.cx} cy={p.cy} r={p.r}
                fill={p.color} fillOpacity={active ? 0.5 : 0.32}
                stroke={p.color} strokeWidth={active ? 2.5 : 1.5}
                style={{
                  filter: active ? `drop-shadow(0 0 8px ${p.color})` : 'none',
                  transition: 'fill-opacity 150ms, stroke-width 150ms',
                }}
              />
              {p.r >= 16 && (
                <text x={p.cx} y={p.cy + 3} textAnchor="middle" pointerEvents="none"
                  className="fill-foreground" fontSize={10} fontWeight={600}>
                  {p.site.exposure.active_findings}
                </text>
              )}
            </g>
          );
        })}

        {/* Tooltip — drawn last so it overlays. */}
        {(() => {
          const p = points.find((q) => q.key === hover);
          if (!p) return null;
          const tw = 200;
          const th = 86;
          const tx = Math.min(Math.max(p.cx - tw / 2, PAD.l), W - PAD.r - tw);
          const ty = p.cy - p.r - th - 6 > PAD.t ? p.cy - p.r - th - 6 : p.cy + p.r + 6;
          const tier = p.site.criticality_tier;
          const ws = worstSeverity(p.site);
          return (
            <g pointerEvents="none">
              <rect x={tx} y={ty} width={tw} height={th} rx={8}
                fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={1}
                style={{ filter: 'drop-shadow(0 4px 12px hsl(var(--foreground) / 0.18))' }} />
              <text x={tx + 12} y={ty + 20} className="fill-foreground" fontSize={12} fontWeight={700}>
                {p.name.length > 28 ? `${p.name.slice(0, 27)}…` : p.name}
              </text>
              <text x={tx + 12} y={ty + 37} className="fill-muted-foreground" fontSize={10}>
                {tier ? TIER_LABEL[tier] ?? `Tier ${tier}` : 'Unassigned'} · {p.site.host_count} hosts
              </text>
              <text x={tx + 12} y={ty + 54} className="fill-foreground" fontSize={10}>
                {Math.round(p.reviewPct)}% reviewed · {p.perHost.toFixed(1)} incidence/host
              </text>
              <text x={tx + 12} y={ty + 71} className="fill-foreground" fontSize={10}>
                {p.site.exposure.active_findings} active{ws ? ` · worst ${SEVERITY_LABEL[ws]}` : ''}
              </text>
            </g>
          );
        })()}
      </svg>

      {/* Legend — depends on the colour mode. */}
      <div className="mt-sm flex flex-wrap items-center gap-x-md gap-y-xs">
        {mode === 'severity' ? (
          <>
            {severityLegend.map((k) => (
              <span key={k} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                <span className="size-2.5 rounded-full" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
                {SEVERITY_LABEL[k]}
              </span>
            ))}
            {anyNoFindings && (
              <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                <span className="size-2.5 rounded-full" style={{ background: MUTED }} aria-hidden />
                No active findings
              </span>
            )}
            <span className="text-caption text-muted-foreground/70">· colour = worst active severity in the site</span>
          </>
        ) : (
          <>
            {siteLegend.map((s) => {
              const key = s.unassigned ? '__unassigned__' : (s.site ?? `site-${s.site_id}`);
              return (
                <span key={key} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                  <span className="size-2.5 rounded-full" style={{ background: colorOf(s, key) }} aria-hidden />
                  {s.unassigned ? 'Unassigned' : (s.site ?? 'Unnamed')}
                </span>
              );
            })}
            {plottable.length > siteLegend.length && (
              <span className="text-caption text-muted-foreground/70">+{plottable.length - siteLegend.length} more</span>
            )}
          </>
        )}
      </div>

      {zeroHost.length > 0 && (
        <p className="mt-xs text-caption text-warning">
          {zeroHost.length} configured site{zeroHost.length === 1 ? '' : 's'} ha
          {zeroHost.length === 1 ? 's' : 've'} no discovered hosts —
          absence of results is not evidence of safety:{' '}
          <span className="text-muted-foreground">{zeroHost.map((s) => s.site).filter(Boolean).join(', ')}</span>
        </p>
      )}
    </div>
  );
};

export default RiskBubbleMatrix;
