/**
 * The presenter view on the Controller (5.425.0, plan E2): small copies of the
 * stage — the same component in a smaller frame, so each is the slide's real
 * layout — kept visibly apart:
 *
 *   - **Going to** — the driver's optimistic target, not yet accepted;
 *   - **Accepted** — the presentation's position;
 *   - **Next** — where Next goes from what this tab shows (its images are
 *     fetched now, so they are in when the slide comes);
 *   - **On <person>'s screen** — one per Display, drawn from what that Display
 *     ACKNOWLEDGED rendering (E1), not from the position: a lagging Display
 *     shows the slide it really shows.  The first three get a preview; more
 *     are listed by name.
 */
import React from 'react';
import { useQueries } from '@tanstack/react-query';

import type { HostFilter, PresentationDisplay, PresentationState, Slide, Stage as StageSizes } from '../../services/api';
import type { Target } from '../../utils/presentationSync';
import { samePosition, positionOf } from '../../utils/presentationSync';
import StageView from './Stage';
import { slideLabel, slidesQuery } from './slidesQuery';

/** At most this many Displays get a preview of their own. */
export const DISPLAY_PREVIEWS = 3;

const DISPLAY_WORDS = { synchronized: 'synchronized', catching_up: 'catching up', disconnected: 'disconnected' } as const;

const filterKey = (f: HostFilter | null) => (f ? `${f.kind}:${f.value}` : '');

interface Props {
  projectId: number;
  reportId: number;
  stage: StageSizes;
  state: PresentationState;
  /** The list for what this tab shows (its filter). */
  slides: Slide[];
  /** The list for the accepted position's filter (it differs while a filter change is pending). */
  acceptedSlides: Slide[] | undefined;
  /** The driver's target not yet accepted, if any. */
  pending: Target | null;
  next: Target | null;
}

const Preview: React.FC<{
  projectId: number; reportId: number; stage: StageSizes; label: string; caption: string;
  slide: Slide | null; tone?: 'pending' | 'default'; testId: string;
}> = ({ projectId, reportId, stage, label, caption, slide, tone = 'default', testId }) => (
  <figure className="min-w-0" data-testid={testId}>
    <figcaption className={`truncate text-caption ${tone === 'pending' ? 'text-warning' : 'text-muted-foreground'}`}>
      <span className="font-semibold">{label}</span>{caption ? ` · ${caption}` : ''}
    </figcaption>
    <div className="mt-xxs aspect-video w-full overflow-hidden rounded border border-border">
      <StageView projectId={projectId} reportId={reportId} stage={stage} slide={slide}
        placeholder={<span className="text-caption text-muted-foreground">—</span>} />
    </div>
  </figure>
);

const PresenterView: React.FC<Props> = ({
  projectId, reportId, stage, state, slides, acceptedSlides, pending, next,
}) => {
  const displays = state.displays;
  const previewed = displays.slice(0, DISPLAY_PREVIEWS);
  // Each Display's rendered position is read from the list for ITS filter.
  const filters = Array.from(new Map(previewed
    .filter((d) => d.rendered)
    .map((d) => [filterKey(d.rendered!.host_filter), d.rendered!.host_filter] as const)).values());
  const lists = useQueries({ queries: filters.map((f) => slidesQuery(projectId, reportId, f)) });
  const listFor = (f: HostFilter | null) => lists[filters.findIndex((x) => filterKey(x) === filterKey(f))]?.data?.slides;

  const accepted = positionOf(state);
  const acceptedSlide = acceptedSlides?.find((s) => s.key === accepted.slide_key) ?? null;
  const going = pending && !samePosition(pending, accepted) ? pending : null;
  const goingSlide = going ? slides.find((s) => s.key === going.slide_key) ?? null : null;
  const nextSlide = next ? slides.find((s) => s.key === next.slide_key) ?? null : null;

  const onScreen = (d: PresentationDisplay) => {
    const list = d.rendered ? listFor(d.rendered.host_filter) : undefined;
    return d.rendered ? list?.find((s) => s.key === d.rendered!.slide_key) ?? null : null;
  };
  const screenName = (d: PresentationDisplay) => (d.person ? `On ${d.person.name}'s screen` : 'On a display');

  return (
    <section aria-label="Presenter view" className="space-y-xs" data-testid="presenter-view">
      <p className="text-metadata" data-testid="presenter-going">
        {going ? (
          <span className="text-warning">Going to: <span className="font-semibold">{slideLabel(goingSlide, going.slide_key)}</span></span>
        ) : (
          <span className="text-muted-foreground">Accepted: <span className="font-semibold text-foreground">{slideLabel(acceptedSlide, accepted.slide_key)}</span></span>
        )}
      </p>
      <div className="grid grid-cols-2 gap-sm xl:grid-cols-3">
        {going && (
          <Preview projectId={projectId} reportId={reportId} stage={stage} label="Going to" tone="pending"
            caption={slideLabel(goingSlide, going.slide_key)} slide={goingSlide} testId="preview-going" />
        )}
        <Preview projectId={projectId} reportId={reportId} stage={stage} label="Accepted"
          caption={slideLabel(acceptedSlide, accepted.slide_key)} slide={acceptedSlide} testId="preview-accepted" />
        <Preview projectId={projectId} reportId={reportId} stage={stage} label="Next"
          caption={next ? slideLabel(nextSlide, next.slide_key) : 'the end'} slide={nextSlide} testId="preview-next" />
        {previewed.map((d) => {
          const slide = onScreen(d);
          return (
            <Preview key={d.client} projectId={projectId} reportId={reportId} stage={stage}
              label={screenName(d)} testId={`preview-display-${d.client}`}
              caption={`${DISPLAY_WORDS[d.status]}${d.rendered ? ` · ${slideLabel(slide, d.rendered.slide_key)}` : ' · nothing shown yet'}`}
              slide={slide} />
          );
        })}
      </div>
      {displays.length > DISPLAY_PREVIEWS && (
        <ul className="text-caption text-muted-foreground" data-testid="more-displays">
          {displays.slice(DISPLAY_PREVIEWS).map((d) => (
            <li key={d.client}>{screenName(d)}: {DISPLAY_WORDS[d.status]}{d.rendered ? ` · ${d.rendered.slide_key}` : ''}</li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default PresenterView;
