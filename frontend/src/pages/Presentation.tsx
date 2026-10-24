/**
 * Presentation mode (5.424.0): a shared, driven view of an ISSUED report.
 *
 * Three kinds of tab:
 *   - **Display** (`/reports/:id/present/display`, full screen, no app chrome):
 *     what the audience sees — whoever shares their screen shares this tab.
 *     It always follows, through every handoff, and reports what it has
 *     finished rendering (E1);
 *   - **Controller** (`/reports/:id/present`): the driver's controls — the slide
 *     list, Previous / Next (`j` / `k`), the host filter, End — and, for
 *     everyone else, "Now driving: Ana" and Take control (E5);
 *   - **Browse** (`/reports/:id/present?browse=1`): its own position, in its own
 *     address, never sent.
 *
 * Control belongs to a TAB: each page load makes its id in memory (never in
 * session storage, which a duplicated or opened tab copies).  A reload is a new
 * tab, no longer the driver — the page says so and one click takes it back.
 * Nothing live is shown: every slide is the issued report's frozen content.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, ExternalLink, Loader2, MonitorUp } from 'lucide-react';

import {
  endPresentation, getLivePresentation, getPresentationSlides, reportPresentationLayout, startPresentation,
  takeControlOfPresentation,
  type HostFilter, type PresentationSlides, type PresentationState, type Slide,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';
import { useNow } from '../hooks/useNow';
import { usePresentationSync, type TabKind } from '../hooks/usePresentationSync';
import { pollEvery, queryErrorText, rememberFor, useLastSettled } from '../lib/query';
import { routeId } from '../utils/routeId';
import { formatApiError } from '../utils/apiErrors';
import type { Target } from '../utils/presentationSync';
import StageView from '../components/presentation/Stage';
import PersonAvatar from '../components/PersonAvatar';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';

/** No answer for this long: the tab says it is out of sync. */
export const STALE_AFTER_MS = 3000;

const newTabId = (): string =>
  (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
    ? crypto.randomUUID()
    : `tab-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;

const slidesKey = (projectId: number, reportId: number, filter: HostFilter | null) =>
  ['getPresentationSlides', projectId, reportId, filter?.kind ?? null, filter?.value ?? null] as const;

const slidesQuery = (projectId: number, reportId: number, filter: HostFilter | null) => ({
  queryKey: slidesKey(projectId, reportId, filter),
  queryFn: ({ signal }: { signal: AbortSignal }) => getPresentationSlides(projectId, reportId, filter, signal),
  // An issued report never changes: its slide list for a filter is the same on every read.
  ...rememberFor(Infinity),
});

const filterLabel = (f: HostFilter | null) =>
  !f ? 'All endpoints' : f.kind === 'text' ? `Matching “${f.value}”` : `${f.kind === 'site' ? 'Site' : 'Segment'} ${f.value}`;

/** Where Next / Previous go on a slide list. */
export const stepKey = (slides: Slide[], key: string, delta: 1 | -1): string | null => {
  const at = slides.findIndex((s) => s.key === key);
  if (at < 0) return slides[0]?.key ?? null;
  const next = slides[at + delta];
  return next ? next.key : null;
};

/** The slide a host filter change lands on: the current finding's first
 *  endpoint slide, or — off a finding — the same slide. */
export const filterLanding = (slides: Slide[], currentKey: string): string => {
  const ref = currentKey.startsWith('f:') ? currentKey.split(':')[1] : null;
  if (!ref) return currentKey;
  return slides.find((s) => s.ref === ref && s.kind === 'hosts')?.key ?? currentKey;
};

const Presentation: React.FC<{ display?: boolean }> = ({ display = false }) => {
  const { reportId: raw } = useParams<{ reportId: string }>();
  const reportId = routeId(raw) ?? 0;
  const projectId = useProjectId();
  const { canExport } = useProjectRole();
  const [searchParams] = useSearchParams();
  const browse = !display && searchParams.get('browse') === '1';
  const kind: TabKind = display ? 'display' : browse ? 'browse' : 'controller';
  const [clientId] = useState(newTabId);

  if (!canExport) {
    return <p className="p-lg text-muted-foreground">Presentation mode is for auditors and above.</p>;
  }
  return (
    <PresentationBody projectId={projectId} reportId={reportId} clientId={clientId} kind={kind}
      startOnOpen={!display && !browse && searchParams.get('start') === '1'} />
  );
};

interface BodyProps {
  projectId: number;
  reportId: number;
  clientId: string;
  kind: TabKind;
  startOnOpen: boolean;
}

const PresentationBody: React.FC<BodyProps> = ({ projectId, reportId, clientId, kind, startOnOpen }) => {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { isProjectAdmin } = useProjectRole();
  const [searchParams, setSearchParams] = useSearchParams();
  const now = useNow(1000);

  const live = useQuery({
    queryKey: ['getLivePresentation', projectId, reportId, clientId],
    queryFn: ({ signal }) => getLivePresentation(projectId, reportId, clientId, signal),
    // Only while there is no presentation yet: a Display waits for one to start.
    ...pollEvery((q) => ((q.state.data as { live: PresentationState | null } | undefined)?.live ? null : 2000)),
  });
  const start = useMutation({
    mutationFn: () => startPresentation(projectId, reportId, clientId),
    onSuccess: (state) => {
      queryClient.setQueryData(['getLivePresentation', projectId, reportId, clientId], { live: state });
    },
  });
  const presentationId = live.data?.live?.id ?? null;

  // "Present" on the report page opens this tab with `?start=1`: start (or
  // join) once, from THIS tab, so it is the driving one.
  const started = useRef(false);
  useEffect(() => {
    if (!startOnOpen || started.current || !live.isSuccess || live.data.live) return;
    started.current = true;
    start.mutate();
    setSearchParams((prev) => { const next = new URLSearchParams(prev); next.delete('start'); return next; }, { replace: true });
  }, [startOnOpen, live.isSuccess, live.data, start, setSearchParams]);

  const sync = usePresentationSync(projectId, presentationId, clientId, kind);
  const state = sync.state;

  // What this tab shows.
  const urlSlide = searchParams.get('slide');
  const urlKind = searchParams.get('fk');
  const urlValue = searchParams.get('fv');
  const position: Target | null = useMemo(() => {
    if (kind === 'browse') {
      return {
        slide_key: urlSlide ?? state?.slide_key ?? 'overview:1',
        host_filter: urlKind && urlValue
          ? { kind: urlKind as HostFilter['kind'], value: urlValue }
          : (urlSlide ? null : state?.host_filter ?? null),
      };
    }
    if (kind === 'display') return state ? { slide_key: state.slide_key, host_filter: state.host_filter } : null;
    return sync.position;
  }, [kind, urlSlide, urlKind, urlValue, state, sync.position]);

  const filter = position?.host_filter ?? null;
  const slidesRead = useQuery({ ...slidesQuery(projectId, reportId, filter), enabled: presentationId != null });
  const slides: PresentationSlides | undefined = useLastSettled(slidesRead.data, { resetKey: reportId });
  // The slide for THIS position, from the list for THIS position's filter —
  // never a same-named slide of the list before (a filter change is loading).
  const exactSlide = slidesRead.data?.slides.find((s) => s.key === position?.slide_key) ?? null;
  // While it loads, the slide shown before stays (and is not acknowledged again).
  const slide = useLastSettled(exactSlide, { resetKey: reportId }) ?? null;

  const layoutReport = useMutation({
    mutationFn: (body: { slide_key: string; overflow_px: number; font: string }) =>
      reportPresentationLayout(projectId, reportId, body),
  });
  const onOverflow = useCallback((slideKey: string, overflowPx: number, font: string) => {
    layoutReport.mutate({ slide_key: slideKey, overflow_px: overflowPx, font });
  }, [layoutReport]);

  // E1: a Display reports a render only while it is still the one shown.
  const shownRevision = kind === 'display' && exactSlide ? state?.revision : undefined;
  const shownRef = useRef<{ revision?: number; position: Target | null }>({ revision: undefined, position: null });
  shownRef.current = { revision: shownRevision, position };
  const onRendered = useCallback((token: number | undefined, fitted: boolean) => {
    const shown = shownRef.current;
    if (kind !== 'display' || token == null || token !== shown.revision || !shown.position) return;
    sync.setRendered({ revision: token, slide_key: shown.position.slide_key, host_filter: shown.position.host_filter, fitted });
  }, [kind, sync]);

  // Navigation.
  const go = useCallback((target: Target) => {
    if (kind === 'browse') {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set('slide', target.slide_key);
        if (target.host_filter) { next.set('fk', target.host_filter.kind); next.set('fv', target.host_filter.value); } else { next.delete('fk'); next.delete('fv'); }
        return next;
      }, { replace: true });
      return;
    }
    sync.go(target);
  }, [kind, setSearchParams, sync]);
  const canNavigate = kind === 'browse' || (kind === 'controller' && Boolean(state?.you_are_driving) && !state?.ended);
  const step = useCallback((delta: 1 | -1) => {
    if (!canNavigate || !slides || !position) return;
    const key = stepKey(slides.slides, position.slide_key, delta);
    if (key) go({ slide_key: key, host_filter: position.host_filter });
  }, [canNavigate, slides, position, go]);
  useKeyboardShortcuts({ j: () => step(1), k: () => step(-1) }, kind !== 'display');

  const changeFilter = useCallback(async (next: HostFilter | null) => {
    if (!canNavigate || !position) return;
    const list = await queryClient.fetchQuery(slidesQuery(projectId, reportId, next));
    go({ slide_key: filterLanding(list.slides, position.slide_key), host_filter: next });
  }, [canNavigate, position, queryClient, projectId, reportId, go]);

  const take = useMutation({
    mutationFn: () => takeControlOfPresentation(projectId, presentationId ?? 0, clientId),
    onSuccess: (answer) => sync.read(answer),
  });
  const end = useMutation({
    mutationFn: () => endPresentation(projectId, presentationId ?? 0, clientId),
    onSuccess: (answer) => sync.read(answer),
  });

  const stale = Boolean(state) && sync.lastAnswerAt > 0 && now - sync.lastAnswerAt > STALE_AFTER_MS;
  const inSync = stale ? `Reconnecting — in sync ${Math.round((now - sync.lastAnswerAt) / 1000)} s ago` : null;

  // --- Display ---------------------------------------------------------------
  if (kind === 'display') {
    return (
      <div className="fixed inset-0 bg-black" data-testid="presentation-display">
        <StageView projectId={projectId} reportId={reportId} stage={slides?.stage ?? { width: 1600, height: 900 }}
          slide={state?.ended ? null : slide} token={shownRevision} onRendered={onRendered} onOverflow={onOverflow}
          placeholder={
            <span className="text-2xl text-muted-foreground">
              {state?.ended ? `The presentation ended${state.ended.by ? ` — ${state.ended.by.name}` : ''}.`
                : presentationId == null ? 'Waiting for the presentation to start…' : 'Loading…'}
            </span>
          } />
        {(inSync || sync.readFailed) && (
          <div className="absolute bottom-2 left-2 rounded bg-background/80 px-2 py-1 text-caption text-warning" role="status">
            {inSync ?? 'Reconnecting…'}
          </div>
        )}
      </div>
    );
  }

  // --- Controller and Browse --------------------------------------------------
  const isStarter = state?.started_by?.id === user?.id;
  const loading = live.isPending || (presentationId != null && !state);
  return (
    <div className="flex min-h-0 flex-col gap-sm p-md md:p-lg" data-testid={`presentation-${kind}`}>
      <div className="flex flex-wrap items-center gap-x-md gap-y-xs">
        <Link to={`/reports/${reportId}`} className="text-info hover:underline">← The report</Link>
        <h1 className="text-page-title font-semibold">{kind === 'browse' ? 'Browsing the presentation' : 'Presentation'}</h1>
        {state && !state.ended && (
          <span className="inline-flex items-center gap-xs text-metadata" data-testid="presentation-driver">
            {state.you_are_driving ? (
              <><PersonAvatar person={state.driver} /> <span className="font-semibold">You are driving</span></>
            ) : (
              <>Now driving: <PersonAvatar person={state.driver} /> <span className="font-medium">{state.driver?.name ?? 'nobody'}</span></>
            )}
          </span>
        )}
        {inSync && <span className="text-caption text-warning" role="status">{inSync}</span>}
      </div>

      {sync.lostControlTo && kind === 'controller' && (
        <div role="status" className="flex items-center gap-sm border-l-4 border-warning bg-warning/10 px-sm py-xs" data-testid="lost-control">
          <PersonAvatar person={sync.lostControlTo} />
          <span><span className="font-semibold">{sync.lostControlTo.name}</span> took control.</span>
          <Button variant="ghost" size="sm" onClick={() => sync.dismissLostControl()}>Dismiss</Button>
        </div>
      )}
      {state?.driving_elsewhere && !state.you_are_driving && kind === 'controller' && (
        <p className="text-metadata text-muted-foreground" data-testid="driving-elsewhere">
          You are driving from another tab (or were, before this page reloaded).
        </p>
      )}
      {state?.ended && (
        <p className="text-metadata" role="status">
          The presentation ended{state.ended.reason === 'idle' ? ' after 30 minutes without activity' : state.ended.by ? ` — ${state.ended.by.name} ended it` : ''}.
        </p>
      )}

      {loading ? (
        <p className="flex items-center gap-xs text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden /> Loading…</p>
      ) : presentationId == null ? (
        <div className="flex flex-col items-start gap-sm">
          <p className="text-muted-foreground">Nobody is presenting this report.</p>
          <Button onClick={() => start.mutate()} disabled={start.isPending} data-testid="start-presentation">
            <MonitorUp className="size-4" aria-hidden /> Start presenting
          </Button>
          {start.isError && <p className="text-destructive">{formatApiError(start.error, 'Could not start the presentation.')}</p>}
        </div>
      ) : (
        <div className="flex min-h-0 flex-wrap items-start gap-md">
          <div className="min-w-0 flex-[3_1_640px]">
            <div className="aspect-video w-full overflow-hidden rounded-panel border border-border">
              <StageView projectId={projectId} reportId={reportId} stage={slides?.stage ?? { width: 1600, height: 900 }}
                slide={slide} onOverflow={onOverflow}
                placeholder={<span className="text-muted-foreground">{slidesRead.isError ? queryErrorText(slidesRead.error, 'Could not read the slides.') : 'Loading…'}</span>} />
            </div>
            <div className="mt-sm flex flex-wrap items-center gap-sm">
              {canNavigate && (
                <>
                  <Button variant="outline" size="sm" onClick={() => step(-1)} aria-label="Previous slide">
                    <ChevronLeft className="size-4" aria-hidden /> Previous
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => step(1)} aria-label="Next slide">
                    Next <ChevronRight className="size-4" aria-hidden />
                  </Button>
                  {slides && (
                    <HostFilterControl slides={slides} value={filter} onChange={(f) => { void changeFilter(f); }} />
                  )}
                </>
              )}
              {kind === 'controller' && state && !state.ended && !state.you_are_driving && (
                <Button size="sm" onClick={() => take.mutate()} disabled={take.isPending} data-testid="take-control">
                  Take control
                </Button>
              )}
              {kind === 'controller' && state && !state.ended && (
                <a href={`/reports/${reportId}/present/display`} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-xxs text-metadata text-info hover:underline">
                  Open the display <ExternalLink className="size-3.5" aria-hidden />
                </a>
              )}
              {kind === 'controller' && state && !state.ended && (
                <a href={`/reports/${reportId}/present?browse=1`} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-xxs text-metadata text-info hover:underline">
                  Browse on my own <ExternalLink className="size-3.5" aria-hidden />
                </a>
              )}
              {kind === 'browse' && (
                <Link to={`/reports/${reportId}/present`} className="text-metadata text-info hover:underline">Back to the presentation</Link>
              )}
              {kind === 'controller' && state && !state.ended && (isStarter || isProjectAdmin) && (
                <Button variant="ghost" size="sm" onClick={() => end.mutate()} disabled={end.isPending} data-testid="end-presentation">
                  End the presentation
                </Button>
              )}
            </div>
            {(take.isError || end.isError) && (
              <p className="mt-xs text-destructive">{formatApiError(take.error ?? end.error, 'That did not go through.')}</p>
            )}
          </div>
          <aside className="min-w-0 flex-[1_1_260px] space-y-sm">
            {state && <DisplayStatus state={state} />}
            {state && <Watching state={state} />}
            {slides && position && (
              <SlideList slides={slides.slides} current={position.slide_key}
                onPick={canNavigate ? (key) => go({ slide_key: key, host_filter: position.host_filter }) : undefined} />
            )}
          </aside>
        </div>
      )}
    </div>
  );
};

const HostFilterControl: React.FC<{
  slides: PresentationSlides; value: HostFilter | null; onChange: (f: HostFilter | null) => void;
}> = ({ slides, value, onChange }) => {
  const [text, setText] = useState(value?.kind === 'text' ? value.value : '');
  const selectValue = value ? `${value.kind}:${value.value}` : 'all';
  const options = useMemo(() => [
    ...slides.sites.map((s) => ({ value: `site:${s}`, label: `Site ${s}` })),
    ...slides.segments.map((s) => ({ value: `segment:${s}`, label: `Segment ${s}` })),
  ], [slides.sites, slides.segments]);
  return (
    <span className="inline-flex flex-wrap items-center gap-xs">
      <Select value={value?.kind === 'text' ? 'text' : selectValue} onValueChange={(v) => {
        if (v === 'all') onChange(null);
        else if (v === 'text') onChange(text.trim() ? { kind: 'text', value: text.trim() } : null);
        else { const at = v.indexOf(':'); onChange({ kind: v.slice(0, at) as HostFilter['kind'], value: v.slice(at + 1) }); }
      }}>
        <SelectTrigger className="h-8 w-56" aria-label="Endpoints shown"><SelectValue placeholder={filterLabel(value)} /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All endpoints</SelectItem>
          {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          {value?.kind === 'text' && <SelectItem value="text">{filterLabel(value)}</SelectItem>}
        </SelectContent>
      </Select>
      <form className="inline-flex items-center gap-xs" onSubmit={(e) => {
        e.preventDefault();
        onChange(text.trim() ? { kind: 'text', value: text.trim() } : null);
      }}>
        <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={200}
          placeholder="Endpoints matching…" className="h-8 w-48" aria-label="Endpoints matching" />
        <Button type="submit" variant="outline" size="sm">Filter</Button>
      </form>
    </span>
  );
};

const DISPLAY_WORDS = { synchronized: 'Display synchronized', catching_up: 'Display catching up', disconnected: 'Display disconnected' } as const;

const DisplayStatus: React.FC<{ state: PresentationState }> = ({ state }) => (
  <section aria-label="Displays" data-testid="display-status">
    {state.displays.length === 0 ? (
      <p className="text-metadata text-muted-foreground">No display open.</p>
    ) : state.displays.map((d) => (
      <p key={d.client} className="text-metadata">
        <span className={d.status === 'synchronized' ? 'text-success' : d.status === 'catching_up' ? 'text-warning' : 'text-destructive'}>
          {DISPLAY_WORDS[d.status]}
        </span>
        {d.person && <span className="text-muted-foreground"> · {d.person.name}&apos;s screen</span>}
        {d.fitted && <span className="text-muted-foreground"> · slide fitted</span>}
      </p>
    ))}
  </section>
);

const Watching: React.FC<{ state: PresentationState }> = ({ state }) => (
  <section aria-label="Watching" className="flex flex-wrap items-center gap-xs text-metadata text-muted-foreground">
    <span>Watching:</span>
    {state.watching.length === 0 ? <span>nobody</span> : state.watching.map((w, i) => (
      <span key={i} className="inline-flex items-center gap-xxs" title={w.kind === 'display' ? `Display (${w.person?.name ?? ''}'s screen)` : w.person?.name ?? ''}>
        <PersonAvatar person={w.person} size={20} />
        {w.kind === 'display' && <span className="text-caption">display</span>}
      </span>
    ))}
  </section>
);

const SlideList: React.FC<{ slides: Slide[]; current: string; onPick?: (key: string) => void }> = ({ slides, current, onPick }) => (
  <nav aria-label="Slides" className="max-h-[60vh] overflow-y-auto border-t border-border pt-xs">
    <ol className="space-y-px text-metadata">
      {slides.map((s, i) => {
        // Numbered: two slides can share a header (a finding's evidence images).
        const label = `${i + 1}. ${s.header || 'Overview'}${s.pages > 1 ? ` — ${s.page} of ${s.pages}` : ''}`;
        const here = s.key === current;
        return (
          <li key={s.key}>
            {onPick ? (
              <button type="button" onClick={() => onPick(s.key)} aria-current={here ? 'true' : undefined} data-slide-key={s.key}
                className={`w-full truncate rounded px-xs py-px text-left ${here ? 'bg-accent font-semibold' : 'hover:bg-accent/50'}`}>
                {label}
              </button>
            ) : (
              <span className={`block truncate px-xs py-px ${here ? 'font-semibold' : ''}`} aria-current={here ? 'true' : undefined}>{label}</span>
            )}
          </li>
        );
      })}
    </ol>
  </nav>
);

export default Presentation;
