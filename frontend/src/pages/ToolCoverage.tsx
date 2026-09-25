/**
 * What BlueStick reads (v5.296.0) — for every import format, what the tool
 * reports, how far BlueStick takes it, where an analyst sees it, and what it
 * drops.  The page to open when an upload seems to have carried something the
 * interface never showed.
 *
 * The data is audited against the parsers and served by
 * `GET /references/parser-coverage`; this page only lays it out.  Links land
 * on a tool with `?tool=<id or registry name>` (Tool reference, the host
 * inspector) or `?format=<file_type>` (Ingestion Results).
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Search } from 'lucide-react';

import {
  CoverageLevel,
  CoverageSignal,
  ParserCoverageResponse,
  getParserCoverage,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { cn } from '../utils/cn';
import {
  FilteredTool,
  countByLevel,
  filterCoverage,
  resolveFocus,
  toolAnchor,
} from '../utils/toolCoverage';
import { CardListSkeleton } from '../components/PageSkeleton';
import PostureLead from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge, BadgeProps } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

const LEVEL_BADGE: Record<CoverageLevel, BadgeProps['variant']> = {
  observation: 'success',
  field: 'info',
  text: 'secondary',
  stored: 'outline',
  discarded: 'warning',
};

const LEVEL_ORDER: CoverageLevel[] = ['observation', 'field', 'text', 'stored', 'discarded'];

/** The audit writes element and key names between backticks. */
const InlineCode: React.FC<{ text: string }> = ({ text }) => (
  <>
    {text.split('`').map((part, i) =>
      i % 2 === 1
        ? <code key={i} className="break-all rounded-sm bg-muted px-xxs font-mono text-caption text-foreground">{part}</code>
        : <React.Fragment key={i}>{part}</React.Fragment>,
    )}
  </>
);

const SignalRow: React.FC<{ signal: CoverageSignal; levelLabel: string }> = ({ signal, levelLabel }) => (
  <TableRow>
    <TableCell className="align-top">
      <p className="break-words font-medium text-foreground">{signal.what}</p>
      {signal.note && (
        <p className="mt-xxs break-words text-caption text-muted-foreground"><InlineCode text={signal.note} /></p>
      )}
    </TableCell>
    <TableCell className="align-top break-words text-caption text-muted-foreground">
      <InlineCode text={signal.input} />
    </TableCell>
    <TableCell className="align-top">
      <Badge variant={LEVEL_BADGE[signal.level]} className="whitespace-nowrap">{levelLabel}</Badge>
    </TableCell>
    <TableCell className="align-top">
      {signal.level === 'discarded' ? (
        <span className="text-caption text-muted-foreground">Not kept</span>
      ) : (
        <>
          <p className="break-words text-caption text-foreground">
            {signal.shown ?? (signal.level === 'stored' ? 'Not shown in the interface' : '—')}
          </p>
          {signal.stored_as.length > 0 && (
            <p className="mt-xxs line-clamp-2 break-all font-mono text-caption text-muted-foreground"
              title={signal.stored_as.join(', ')}>
              {signal.stored_as.join(', ')}
            </p>
          )}
        </>
      )}
    </TableCell>
  </TableRow>
);

const ToolSection: React.FC<{
  entry: FilteredTool;
  levelLabels: Record<string, string>;
  filtered: boolean;
  focused: boolean;
}> = ({ entry, levelLabels, filtered, focused }) => {
  const { tool, signals, gaps } = entry;
  return (
    <div
      id={toolAnchor(tool.id)}
      // Clears the sticky top bar, so a linked tool lands with its heading visible.
      className={cn('scroll-mt-24 rounded-panel', focused && 'ring-2 ring-info/40 ring-offset-4 ring-offset-background')}
    >
      <PostureSection
        title={
          <>
            <span className="min-w-0 break-words">{tool.name}</span>
            <SectionCount>
              {filtered ? `${signals.length} of ${tool.signals.length}` : tool.signals.length}
            </SectionCount>
          </>
        }
        description={tool.accepted_input}
        actions={
          <div className="flex max-w-md flex-wrap justify-end gap-xxs">
            {tool.formats.map((f) => (
              <Badge key={f.file_type} variant="outline" className="max-w-[16rem] truncate" title={f.file_type}>
                {f.label}
              </Badge>
            ))}
          </div>
        }
      >
        {signals.length > 0 && (
          <div className="overflow-x-auto">
            <Table className="min-w-[880px]" style={{ tableLayout: 'fixed' }}>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[27%]">What the tool reports</TableHead>
                  <TableHead className="w-[25%]">In your file</TableHead>
                  <TableHead className="w-[12%]">Level</TableHead>
                  <TableHead className="w-[36%]">Where you see it</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {signals.map((s, i) => (
                  <SignalRow key={`${tool.id}-${i}-${s.what}`} signal={s} levelLabel={levelLabels[s.level] ?? s.level} />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {gaps.length > 0 && (
          <div className="mt-md">
            <h3 className="mb-xs text-metadata font-semibold text-foreground">Known gaps</h3>
            <ul className="space-y-xxs">
              {gaps.map((gap) => (
                <li key={gap} className="flex min-w-0 gap-xs text-metadata text-foreground">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
                  <span className="min-w-0 break-words"><InlineCode text={gap} /></span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!filtered && tool.unverified.length > 0 && (
          <p className="mt-sm break-words text-caption text-muted-foreground">
            <span className="font-medium">Not yet checked against real output of the tool:</span>{' '}
            {tool.unverified.map((u, i) => (
              <React.Fragment key={u}>{i > 0 && ' '}<InlineCode text={u} /></React.Fragment>
            ))}
          </p>
        )}
      </PostureSection>
    </div>
  );
};

const ToolCoverage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<ParserCoverageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = params.get('q') ?? '';
  const levelParam = params.get('level');
  const level: CoverageLevel | 'all' = LEVEL_ORDER.includes(levelParam as CoverageLevel)
    ? (levelParam as CoverageLevel)
    : 'all';

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  useEffect(() => {
    let cancelled = false;
    getParserCoverage()
      .then((r) => { if (!cancelled) setData(r); })
      .catch((err) => { if (!cancelled) setError(formatApiError(err, 'Could not load what BlueStick reads.')); });
    return () => { cancelled = true; };
  }, []);

  const tools = useMemo(() => data?.tools ?? [], [data]);
  const focus = useMemo(
    () => resolveFocus(tools, { tool: params.get('tool'), format: params.get('format') }),
    [tools, params],
  );
  const visible = useMemo(() => filterCoverage(tools, { query, level }), [tools, query, level]);
  const counts = useMemo(() => countByLevel(tools), [tools]);
  const levelLabels = useMemo(
    () => Object.fromEntries((data?.levels ?? []).map((l) => [l.id, l.label])),
    [data],
  );

  // Land on the linked tool once it is rendered.
  useEffect(() => {
    if (!focus) return;
    const el = document.getElementById(toolAnchor(focus.id));
    el?.scrollIntoView?.({ block: 'start' });
  }, [focus, visible.length]);

  if (error) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="p-md md:p-lg"><CardListSkeleton count={3} cardHeight={120} /></div>
    );
  }

  const formatCount = tools.reduce((n, t) => n + t.formats.length, 0);
  const observationTools = tools.filter((t) => t.signals.some((s) => s.level === 'observation'));
  const filtered = !!query.trim() || level !== 'all';

  return (
    <div className="space-y-lg p-md md:p-lg">
      <header>
        <h1 className="text-page-title">What BlueStick reads</h1>
        <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
          For every import format: what the tool reports, how far BlueStick takes it, and where you see it.
          Open it when an upload seems to have carried something the interface never showed.
        </p>
      </header>

      <PostureLead
        tone="info"
        restsOn="Audited against the parsers of this build. A tool's rows change in the same commit as its parser, and a fixed gap leaves the list."
      >
        {tools.length} tools, {formatCount} import formats. {observationTools.length} of them turn what they find
        into scanner observations ({observationTools.map((t) => t.name).join(', ')}); the rest keep it as fields or
        text, or do not keep it.
      </PostureLead>

      <div className="grid gap-y-md divide-border sm:grid-cols-3 lg:grid-cols-5 lg:divide-x" data-testid="coverage-measures">
        {data.levels.map((l) => (
          <PostureMeasure
            key={l.id}
            label={l.label}
            info={l.description}
            value={<span className="tabular-nums">{counts[l.id].toLocaleString()}</span>}
            to={`?level=${l.id}`}
            toLabel={`Show the ${counts[l.id]} rows at level ${l.label}`}
          >
            {tools.filter((t) => t.signals.some((s) => s.level === l.id)).length} tools
          </PostureMeasure>
        ))}
      </div>

      <PostureSection title="Levels" description="How far BlueStick takes a value, from furthest to not at all.">
        <dl className="grid gap-x-lg gap-y-sm md:grid-cols-2">
          {data.levels.map((l) => (
            <div key={l.id} className="flex min-w-0 items-start gap-sm">
              <dt className="w-40 shrink-0"><Badge variant={LEVEL_BADGE[l.id]}>{l.label}</Badge></dt>
              <dd className="min-w-0 break-words text-caption text-muted-foreground">{l.description}</dd>
            </div>
          ))}
        </dl>
      </PostureSection>

      <div className="flex flex-wrap items-end gap-md">
        <div className="relative min-w-60 flex-1 sm:max-w-md">
          <Search className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            value={query}
            onChange={(e) => setParam('q', e.target.value || null)}
            placeholder="Search: a tool, a key, “VULNERABLE”, “signing”…"
            className="pl-xl"
            aria-label="Search what BlueStick reads"
          />
        </div>
        <div role="group" aria-label="Filter by level" className="inline-flex flex-wrap rounded-control border border-border bg-card p-xxs">
          {(['all', ...LEVEL_ORDER] as const).map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={level === id}
              onClick={() => setParam('level', id === 'all' ? null : id)}
              className={cn(
                'rounded-control px-sm py-xxs text-metadata font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                level === id
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              {id === 'all' ? 'All' : levelLabels[id] ?? id}
            </button>
          ))}
        </div>
      </div>

      <nav aria-label="Tools" className="flex flex-wrap gap-x-md gap-y-xxs text-metadata">
        {visible.map(({ tool }) => (
          <a key={tool.id} href={`#${toolAnchor(tool.id)}`} className="text-info underline-offset-2 hover:underline">
            {tool.name}
          </a>
        ))}
      </nav>

      {visible.length === 0 ? (
        <div className="flex flex-wrap items-center gap-sm text-metadata text-muted-foreground">
          <span>Nothing matches{query.trim() ? ` “${query.trim()}”` : ''}{level !== 'all' ? ` at level ${levelLabels[level]}` : ''}.</span>
          <Button variant="outline" size="sm" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
            Clear filters
          </Button>
        </div>
      ) : (
        visible.map((entry) => (
          <ToolSection
            key={entry.tool.id}
            entry={entry}
            levelLabels={levelLabels}
            filtered={filtered}
            focused={focus?.id === entry.tool.id}
          />
        ))
      )}
    </div>
  );
};

export default ToolCoverage;
