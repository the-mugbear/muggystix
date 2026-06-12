/**
 * "Where risk concentrates" — an SVG bubble matrix over the project's sites.
 *
 *   X  = % of the site's hosts reviewed        (left = under-reviewed)
 *   Y  = active findings per host              (up = denser exposure)
 *   r  = host count                            (bigger = larger blast radius)
 *   ◾ = site criticality tier                  (colour)
 *
 * The orthogonal encodings (tier as colour, density as height) avoid double-
 * counting tier. The top-left quadrant — under-reviewed AND high-exposure — is
 * shaded as the danger zone, so the sites that most need attention are the ones
 * your eye lands on first. SVG-native tooltip → scales cleanly with the card.
 */
import React, { useState } from 'react';

import type { PostureSite } from '../../services/api';
import { tierHsl, TIER_LABEL } from './postureTheme';

// Wide aspect so a full-width hero card doesn't render an over-tall chart.
const W = 1200;
const H = 420;
const PAD = { l: 52, r: 20, t: 20, b: 42 };

interface Plotted {
  site: PostureSite;
  key: string;
  name: string;
  reviewPct: number;
  perHost: number;
  cx: number;
  cy: number;
  r: number;
  color: string;
}

interface RiskBubbleMatrixProps {
  sites: PostureSite[];
}

const RiskBubbleMatrix: React.FC<RiskBubbleMatrixProps> = ({ sites }) => {
  const [hover, setHover] = useState<string | null>(null);

  const plottable = sites.filter((s) => s.host_count > 0);
  const zeroHost = sites.filter((s) => s.host_count === 0 && !s.unassigned);

  // Per-host density uses finding-HOST incidences (each affected host counts),
  // not distinct findings — a finding spanning 100 hosts must not read as
  // 0.01/host. Falls back to active_findings if incidences are absent.
  const incidencesOf = (s: PostureSite) =>
    s.exposure.finding_host_incidences ?? s.exposure.active_findings;
  const maxPerHost = Math.max(
    0.5,
    ...plottable.map((s) => incidencesOf(s) / Math.max(1, s.host_count)),
  );
  const maxHosts = Math.max(1, ...plottable.map((s) => s.host_count));

  const xOf = (reviewPct: number) => PAD.l + (reviewPct / 100) * (W - PAD.l - PAD.r);
  const yOf = (perHost: number) => (H - PAD.b) - (perHost / maxPerHost) * (H - PAD.t - PAD.b);
  const rOf = (hosts: number) => 9 + Math.sqrt(hosts / maxHosts) * 40;

  const points: Plotted[] = plottable.map((s) => {
    const reviewed = s.host_count - s.neglect.unreviewed_hosts;
    const reviewPct = s.host_count > 0 ? (reviewed / s.host_count) * 100 : 0;
    const perHost = incidencesOf(s) / Math.max(1, s.host_count);
    return {
      site: s,
      key: s.unassigned ? '__unassigned__' : (s.site ?? `site-${s.site_id}`),
      name: s.unassigned ? 'Unassigned' : (s.site ?? 'Unnamed'),
      reviewPct,
      perHost,
      cx: xOf(reviewPct),
      cy: yOf(perHost),
      r: rOf(s.host_count),
      color: s.unassigned ? 'hsl(var(--muted-foreground))' : tierHsl(s.criticality_tier),
    };
  });
  // Draw the largest bubbles first so small ones stay clickable on top.
  points.sort((a, b) => b.r - a.r);

  const midX = xOf(50);
  const dangerW = midX - PAD.l;
  const dangerH = (H - PAD.t - PAD.b) / 2;

  return (
    <div className="w-full">
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
          const tw = 188;
          const th = 86;
          const tx = Math.min(Math.max(p.cx - tw / 2, PAD.l), W - PAD.r - tw);
          const ty = p.cy - p.r - th - 6 > PAD.t ? p.cy - p.r - th - 6 : p.cy + p.r + 6;
          const tier = p.site.criticality_tier;
          return (
            <g pointerEvents="none">
              <rect x={tx} y={ty} width={tw} height={th} rx={8}
                fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={1}
                style={{ filter: 'drop-shadow(0 4px 12px hsl(var(--foreground) / 0.18))' }} />
              <text x={tx + 12} y={ty + 20} className="fill-foreground" fontSize={12} fontWeight={700}>
                {p.name.length > 26 ? `${p.name.slice(0, 25)}…` : p.name}
              </text>
              <text x={tx + 12} y={ty + 37} className="fill-muted-foreground" fontSize={10}>
                {tier ? TIER_LABEL[tier] ?? `Tier ${tier}` : 'Unassigned'} · {p.site.host_count} hosts
              </text>
              <text x={tx + 12} y={ty + 54} className="fill-foreground" fontSize={10}>
                {Math.round(p.reviewPct)}% reviewed · {p.perHost.toFixed(1)} incidence/host
              </text>
              <text x={tx + 12} y={ty + 71} className="fill-foreground" fontSize={10}>
                {p.site.exposure.active_findings} active ({p.site.exposure.by_severity.critical} crit)
              </text>
            </g>
          );
        })()}
      </svg>

      {/* Tier legend + zero-host caption */}
      <div className="mt-sm flex flex-wrap items-center gap-x-md gap-y-xs">
        {[1, 2, 3, 4].map((t) => (
          <span key={t} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
            <span className="size-2.5 rounded-full" style={{ background: tierHsl(t) }} aria-hidden />
            {TIER_LABEL[t]}
          </span>
        ))}
        <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
          <span className="size-2.5 rounded-full" style={{ background: 'hsl(var(--muted-foreground))' }} aria-hidden />
          Unassigned
        </span>
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
