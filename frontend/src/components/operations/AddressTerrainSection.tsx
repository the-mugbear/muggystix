/**
 * "Where the team has been" — the address terrain (5.306.0).  A section of
 * the Posture overview since 5.330.0 (it was on Operations, which is the
 * reader's own page now); the file, the storage key and the route
 * `/workbench/terrain` keep their names.  Every link it carries opens a
 * Hosts list, so it reads the same from either page.
 *
 * The project's address space as ground: every /24 (IPv6 /64) a plot on a
 * Hilbert-curve district per /16, every block a tower as tall as its host count,
 * banded from the ground up by how far the team has taken its hosts — tested,
 * planned, someone has it, untouched. A beacon floats over any block whose
 * untouched hosts carry a critical scanner observation.
 *
 * Cost: nothing until the section nears the viewport. Then one request
 * (`GET /workbench/terrain`, one statement). The table view carries every
 * number the map does, and is what a browser without WebGL gets.
 *
 * 5.329.0 (design review 2026-10-02) — the section's finding is its sentence
 * and the block with the most untouched critical exposure; those are always
 * shown. The scene — 460 px and three lines of control instructions — opens
 * on demand ("Show the map", remembered per viewer), and three.js is fetched
 * only then: a reader who never opens it never downloads it.
 */
import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, Map as MapIcon, Minus, Plus, RefreshCw, RotateCcw, Table2 } from 'lucide-react';

import { getAddressTerrain, type AddressTerrainResponse, type TerrainBlock } from '../../services/api';
import PostureSection, { SectionCount } from '../posture/PostureSection';
import { Button } from '../ui/button';
import { InfoTip } from '../ui/info-tip';
import {
  TERRAIN_STAGES, UNTOUCHED_CRITICAL_HREF, layoutTerrain, summariseTerrain, terrainBlockHref,
  type TerrainStage,
} from '../../utils/addressTerrain';
import { readTerrainTokens, terrainPalette, type TerrainPalette } from '../../utils/terrainPalette';
import { cn } from '../../utils/cn';
import type { TerrainSceneHandle } from './TerrainScene';

const TerrainScene = lazy(() => import('./TerrainScene'));

type View = 'map' | 'table';

export const webglAvailable = (): boolean => {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
};

/** The live theme's terrain colours, for the legend and readout swatches. */
function useTerrainPalette(): TerrainPalette {
  const [palette, setPalette] = useState(() => terrainPalette(readTerrainTokens()));
  useEffect(() => {
    const obs = new MutationObserver(() => setPalette(terrainPalette(readTerrainTokens())));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class', 'data-theme'] });
    return () => obs.disconnect();
  }, []);
  return palette;
}

/** Load once the section is within a screen of the viewport. */
function useNearViewport<T extends Element>(): [React.RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return undefined;
    if (typeof IntersectionObserver === 'undefined') { setNear(true); return undefined; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setNear(true);
    }, { rootMargin: '600px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  return [ref, near];
}

/** Whether this viewer keeps the map open. A convenience, so every access is
 *  guarded: storage can be absent or refuse (private windows, blocked data). */
export const TERRAIN_OPEN_KEY = 'nm.operations.terrainOpen';
const readOpen = (): boolean => {
  try {
    return localStorage.getItem(TERRAIN_OPEN_KEY) === '1';
  } catch {
    return false;
  }
};
const writeOpen = (open: boolean): void => {
  try {
    if (open) localStorage.setItem(TERRAIN_OPEN_KEY, '1');
    else localStorage.removeItem(TERRAIN_OPEN_KEY);
  } catch {
    // The choice simply does not persist.
  }
};

const n = (v: number) => v.toLocaleString();
const plural = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

const Swatch: React.FC<{ colour: string; shape?: 'square' | 'diamond' }> = ({ colour, shape = 'square' }) => (
  <span
    aria-hidden
    className={cn('inline-block size-2.5 shrink-0', shape === 'diamond' ? 'rotate-45 rounded-[1px]' : 'rounded-[2px]')}
    style={{ backgroundColor: colour }}
  />
);

const AddressTerrainSection: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const navigate = useNavigate();
  const palette = useTerrainPalette();
  const [rootRef, near] = useNearViewport<HTMLElement>();
  const [data, setData] = useState<AddressTerrainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [canWebgl] = useState(webglAvailable);
  const [view, setView] = useState<View>(canWebgl ? 'map' : 'table');
  // Closed by default: the sentence and the hot block carry the finding.
  const [mapOpen, setMapOpen] = useState<boolean>(readOpen);
  const toggleMap = () => setMapOpen((was) => {
    writeOpen(!was);
    return !was;
  });
  const [hovered, setHovered] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const sceneRef = useRef<TerrainSceneHandle>(null);

  useEffect(() => {
    if (!near) return undefined;
    const controller = new AbortController();
    setLoading(true);
    getAddressTerrain(controller.signal)
      .then((r) => { setData(r); setUnavailable(false); })
      .catch((e) => { if (!controller.signal.aborted && e?.name !== 'CanceledError') setUnavailable(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [near, refreshKey, nonce]);

  const layout = useMemo(() => (data ? layoutTerrain(data.blocks) : null), [data]);
  const summary = useMemo(() => (data ? summariseTerrain(data.blocks) : null), [data]);
  useEffect(() => { setSelected(null); setHovered(null); }, [layout]);

  const blocks = useMemo(() => layout?.placed.map((p) => p.block) ?? [], [layout]);
  // The map's readout follows the pointer or the keyboard; with neither it
  // says how to read a block (the hot block is always shown above it).
  const focusIndex = hovered ?? selected;
  const focus = focusIndex != null && focusIndex >= 0 ? blocks[focusIndex] ?? null : null;

  const open = useCallback((i: number) => {
    const b = blocks[i];
    if (b) navigate(terrainBlockHref(b.cidr));
  }, [blocks, navigate]);

  const onKey = (ev: React.KeyboardEvent) => {
    if (!blocks.length) return;
    const cur = selected ?? -1;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') {
      ev.preventDefault();
      setSelected(Math.min(blocks.length - 1, cur + 1));
    } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      setSelected(Math.max(0, cur < 0 ? 0 : cur - 1));
    } else if (ev.key === 'Enter' && selected != null) {
      ev.preventDefault();
      open(selected);
    } else if (ev.key === 'Escape') {
      setSelected(null);
    }
  };

  const title = (
    <span className="flex items-center gap-xs">
      Where the team has been
      {data && <SectionCount>{plural(data.blocks.length, 'address block', 'address blocks')}</SectionCount>}
    </span>
  );
  const what = canWebgl ? 'map' : 'table';
  const toggle = data && data.blocks.length > 0 && (
    <div role="group" aria-label="View" className="flex flex-wrap items-center gap-xxs">
      <Button size="sm" variant={view === 'map' ? 'secondary' : 'ghost'} disabled={!canWebgl}
        onClick={() => setView('map')} aria-pressed={view === 'map'}
        title={canWebgl ? undefined : 'This browser cannot draw the map (no WebGL)'}>
        <MapIcon className="size-4" aria-hidden /> Map
      </Button>
      <Button size="sm" variant={view === 'table' ? 'secondary' : 'ghost'}
        onClick={() => setView('table')} aria-pressed={view === 'table'}>
        <Table2 className="size-4" aria-hidden /> Table
      </Button>
    </div>
  );

  return (
    <section ref={rootRef} className="min-w-0">
      <PostureSection
        title={title}
        description="How far the team has taken the hosts of each address block: tested, planned, someone has it, untouched."
      >
        {!data && (loading || !near) && !unavailable && (
          <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading the address blocks…
          </p>
        )}
        {unavailable && !data && (
          <p className="flex items-center gap-sm text-metadata text-muted-foreground">
            The address map could not be loaded.
            <Button size="sm" variant="outline" onClick={() => setNonce((v) => v + 1)}>
              <RefreshCw className="size-4" aria-hidden /> Retry
            </Button>
          </p>
        )}
        {data && data.blocks.length === 0 && (
          <p className="text-metadata text-muted-foreground">No hosts yet — the map fills in as scans are imported.</p>
        )}
        {data && summary && layout && data.blocks.length > 0 && (
          <div className="flex min-w-0 flex-col gap-sm">
            {/* Always shown: the sentence, and the block to go to first. */}
            <div className="grid min-w-0 gap-md lg:grid-cols-[minmax(0,1fr)_18rem]">
              <div className="flex min-w-0 flex-col items-start gap-sm">
                <TerrainLead summary={summary} />
                <Button
                  size="sm" variant="outline"
                  aria-expanded={mapOpen}
                  aria-controls="address-terrain-view"
                  onClick={toggleMap}
                >
                  {canWebgl ? <MapIcon className="size-4" aria-hidden /> : <Table2 className="size-4" aria-hidden />}
                  {mapOpen ? `Hide the ${what}` : `Show the ${what}`}
                </Button>
              </div>
              <BlockReadout block={summary.worst} palette={palette} label="Most untouched critical exposure" />
            </div>
            {mapOpen && (
            <div id="address-terrain-view" className="flex min-w-0 flex-col gap-sm">
            {toggle}
            {view === 'map' && canWebgl ? (
              <div className="grid min-w-0 gap-md lg:grid-cols-[minmax(0,1fr)_18rem]">
                <div
                  className="relative h-[460px] min-w-0 overflow-hidden rounded-md border border-border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  tabIndex={0}
                  role="application"
                  aria-roledescription="address map"
                  aria-label="Address map. Arrow keys step through the blocks in address order, Enter opens the selected block's hosts, Escape clears. The Table view lists the same numbers."
                  onKeyDown={onKey}
                >
                  <Suspense fallback={(
                    <p className="flex size-full items-center justify-center gap-xs text-metadata text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" aria-hidden /> Drawing the map…
                    </p>
                  )}>
                    <TerrainScene
                      ref={sceneRef}
                      layout={layout}
                      selected={selected}
                      onHover={setHovered}
                      onSelect={setSelected}
                      onOpen={open}
                      onUnavailable={() => setView('table')}
                    />
                  </Suspense>
                  <div className="absolute left-xs top-xs flex flex-col gap-xxs">
                    <Button size="icon" variant="outline" className="size-7 bg-background/80" aria-label="Zoom in"
                      onClick={() => sceneRef.current?.zoomBy(1.4)}><Plus className="size-4" aria-hidden /></Button>
                    <Button size="icon" variant="outline" className="size-7 bg-background/80" aria-label="Zoom out"
                      onClick={() => sceneRef.current?.zoomBy(1 / 1.4)}><Minus className="size-4" aria-hidden /></Button>
                    <Button size="icon" variant="outline" className="size-7 bg-background/80" aria-label="Reset the view"
                      onClick={() => sceneRef.current?.resetView()}><RotateCcw className="size-4" aria-hidden /></Button>
                  </div>
                  <p className="pointer-events-none absolute bottom-xs right-xs rounded bg-background/80 px-xs text-caption text-muted-foreground">
                    Drag to turn · right-drag to pan · Ctrl + scroll to zoom · double-click opens a block
                  </p>
                </div>
                {focus ? (
                  <BlockReadout block={focus} palette={palette}
                    label={hovered == null && selected != null ? 'Selected block' : 'Block'} />
                ) : (
                  <p className="min-w-0 border-l border-border pl-md text-caption text-muted-foreground">
                    Point at a block, or step through them with the arrow keys, to read its numbers here.
                  </p>
                )}
              </div>
            ) : (
              <TerrainTable blocks={data.blocks} palette={palette} />
            )}
            <Legend palette={palette} />
            </div>
            )}
            {(data.truncated || data.unplaced_hosts > 0) && (
              <p className="text-caption text-muted-foreground">
                {data.truncated && 'Only the largest blocks are counted here. '}
                {data.unplaced_hosts > 0 && `${plural(data.unplaced_hosts, 'host has', 'hosts have')} no IP address and ${data.unplaced_hosts === 1 ? 'is' : 'are'} not on the map.`}
              </p>
            )}
          </div>
        )}
      </PostureSection>
    </section>
  );
};

/** One sentence: the untouched critical exposure, and where most of it is. */
const TerrainLead: React.FC<{ summary: ReturnType<typeof summariseTerrain> }> = ({ summary }) => {
  const { hosts, byStage, criticalUntouched, blocksWithCriticalUntouched, worst } = summary;
  const reached = hosts - byStage.untouched;
  return (
    <p className="max-w-4xl text-body text-foreground">
      The team has reached <strong className="tabular-nums">{n(reached)}</strong> of {plural(hosts, 'host', 'hosts')}
      {' '}({n(byStage.tested)} tested).{' '}
      {criticalUntouched > 0 && worst ? (
        <>
          <Link to={UNTOUCHED_CRITICAL_HREF} className="font-semibold text-info hover:underline">
            {plural(criticalUntouched, 'untouched host carries', 'untouched hosts carry')} a critical scanner observation
          </Link>
          {' '}across {plural(blocksWithCriticalUntouched, 'block', 'blocks')} — the most in{' '}
          <Link to={terrainBlockHref(worst.cidr, 'critical_untouched')} className="font-mono text-info hover:underline">
            {worst.cidr}
          </Link>{' '}({n(worst.critical_untouched)}).
        </>
      ) : (
        <>No untouched host carries a critical scanner observation.</>
      )}
    </p>
  );
};

const STAGE_LINK_LABEL: Record<TerrainStage, string> = {
  tested: 'tested',
  planned: 'planned, not tested',
  worked: 'someone has it',
  untouched: 'untouched',
};

const BlockReadout: React.FC<{ block: TerrainBlock | null; palette: TerrainPalette; label: string }> = ({
  block, palette, label,
}) => {
  if (!block) return null;
  const colour: Record<TerrainStage, string> = palette;
  return (
    <aside aria-live="polite" aria-label={label} className="min-w-0 border-l border-border pl-md">
      <p className="text-caption text-muted-foreground">{label}</p>
      <h3 className="truncate font-mono text-subheading font-semibold text-foreground" title={block.cidr}>{block.cidr}</h3>
      <Link to={terrainBlockHref(block.cidr)} className="text-metadata text-info hover:underline">
        Open {plural(block.hosts, 'host', 'hosts')}
      </Link>
      {/* The tower, flat: the same bands, the same order (bottom → left). */}
      <div className="my-sm flex h-2.5 w-full gap-[2px] overflow-hidden rounded-[4px]" aria-hidden>
        {TERRAIN_STAGES.map(({ key }) => (block[key] > 0 ? (
          <span key={key} style={{ flexGrow: block[key], backgroundColor: colour[key] }} />
        ) : null))}
      </div>
      <ul className="flex flex-col gap-xxs text-metadata">
        {TERRAIN_STAGES.map(({ key }) => (
          <li key={key} className="flex items-center gap-xs">
            <Swatch colour={colour[key]} />
            {block[key] > 0 ? (
              <Link to={terrainBlockHref(block.cidr, key)} className="hover:text-info hover:underline">
                <span className="tabular-nums font-semibold">{n(block[key])}</span> {STAGE_LINK_LABEL[key]}
              </Link>
            ) : (
              <span className="text-muted-foreground"><span className="tabular-nums">0</span> {STAGE_LINK_LABEL[key]}</span>
            )}
          </li>
        ))}
        <li className="mt-xxs flex items-center gap-xs">
          <Swatch colour={palette.beacon} shape="diamond" />
          {block.critical_untouched > 0 ? (
            <Link to={terrainBlockHref(block.cidr, 'critical_untouched')} className="hover:text-info hover:underline">
              <span className="tabular-nums font-semibold">{n(block.critical_untouched)}</span> untouched with a critical observation
            </Link>
          ) : (
            <span className="text-muted-foreground">No untouched critical exposure</span>
          )}
        </li>
      </ul>
    </aside>
  );
};

const Legend: React.FC<{ palette: TerrainPalette }> = ({ palette }) => {
  const colour: Record<TerrainStage, string> = palette;
  return (
    <div className="flex flex-wrap items-center gap-x-md gap-y-xxs text-caption text-muted-foreground">
      {TERRAIN_STAGES.map(({ key, label, description }) => (
        <span key={key} className="flex items-center gap-xxs" title={description}>
          <Swatch colour={colour[key]} /> {label}
        </span>
      ))}
      <span className="flex items-center gap-xxs">
        <Swatch colour={palette.beacon} shape="diamond" /> Untouched critical exposure
      </span>
      <InfoTip text="Height is the block's host count. Bands from the ground up: tested (evidence of a test that ran is recorded), planned (a test is proposed or in progress, none has run), someone has it (reviewed, assigned, noted or in a finding), untouched. A diamond floats over a block whose untouched hosts carry a critical scanner observation — bigger for more of them. Each /16 is a district holding its blocks in address order; consecutive blocks stay neighbours. Exact addresses are in the readout and the Table view." />
    </div>
  );
};

const TABLE_PREVIEW = 25;

/** Every number the map carries, most untouched critical exposure first. */
const TerrainTable: React.FC<{ blocks: TerrainBlock[]; palette: TerrainPalette }> = ({ blocks, palette }) => {
  const [all, setAll] = useState(false);
  const rows = useMemo(() => [...blocks].sort((a, b) => (
    b.critical_untouched - a.critical_untouched || b.untouched - a.untouched || b.hosts - a.hosts
  )), [blocks]);
  const shown = all ? rows : rows.slice(0, TABLE_PREVIEW);
  const colour: Record<TerrainStage, string> = palette;
  const cell = (b: TerrainBlock, part: TerrainStage | 'critical_untouched') => {
    const v = b[part];
    return v > 0 ? (
      <Link to={terrainBlockHref(b.cidr, part)} className="tabular-nums hover:text-info hover:underline">{n(v)}</Link>
    ) : <span className="tabular-nums text-muted-foreground">0</span>;
  };
  return (
    <div className="min-w-0">
      <table className="w-full table-fixed text-metadata">
        <caption className="sr-only">Hosts by address block and how far the team has taken them</caption>
        <thead>
          <tr className="border-b border-border text-left text-caption text-muted-foreground">
            <th className="w-[26%] py-xxs font-medium">Block</th>
            <th className="py-xxs text-right font-medium">Hosts</th>
            {TERRAIN_STAGES.map(({ key, label }) => (
              <th key={key} className="py-xxs text-right font-medium">
                <span className="inline-flex items-center gap-xxs"><Swatch colour={colour[key]} />{label}</span>
              </th>
            ))}
            <th className="py-xxs text-right font-medium">
              <span className="inline-flex items-center gap-xxs"><Swatch colour={palette.beacon} shape="diamond" />Untouched critical</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((b) => (
            <tr key={b.cidr} className="border-b border-border/60">
              <td className="truncate py-xxs font-mono" title={b.cidr}>
                <Link to={terrainBlockHref(b.cidr)} className="hover:text-info hover:underline">{b.cidr}</Link>
              </td>
              <td className="py-xxs text-right">
                <Link to={terrainBlockHref(b.cidr)} className="tabular-nums hover:text-info hover:underline">{n(b.hosts)}</Link>
              </td>
              {TERRAIN_STAGES.map(({ key }) => <td key={key} className="py-xxs text-right">{cell(b, key)}</td>)}
              <td className="py-xxs text-right">{cell(b, 'critical_untouched')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > TABLE_PREVIEW && (
        <Button size="sm" variant="ghost" className="mt-xxs" onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all ${n(rows.length)} blocks`}
        </Button>
      )}
    </div>
  );
};

export default AddressTerrainSection;
