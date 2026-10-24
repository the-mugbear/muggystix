/**
 * One slide on the fixed presentation stage (5.424.0).
 *
 * The stage is laid out at the server's logical size (`stage.width` ×
 * `stage.height`, 1600 × 900) and scaled to its frame as one unit, so a line
 * wraps at the same word on every screen.  What is on the slide is the
 * server's (`Slide.blocks`): this component only draws it, with the sizes the
 * server measured by (set as CSS variables, `presentationStage.css`).
 *
 * When the slide has finished rendering — fonts loaded, every image decoded —
 * `onRendered` is called with the `token` it was drawn for; a render that a
 * newer slide overtook never calls it.  If the body still overflows (the
 * server's estimate was wrong for this browser), the body is scaled down
 * uniformly until it fits — never clipped, never scrolled — a small "fitted"
 * mark is shown, and `onOverflow` reports it once.
 *
 * Since 5.425.0 the stage also draws the spotlight (a ring at stage
 * coordinates, so it lands on the same word on every screen) and, for the
 * driver, turns a click into stage coordinates (`onPoint`).  The previews of
 * the presenter view are this same component in a smaller frame.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/400-italic.css';
import '@fontsource/ibm-plex-sans/700.css';
import '@fontsource/ibm-plex-sans/700-italic.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/700.css';
import './presentationStage.css';

import { fetchPresentationImage, type Slide, type SlideBlock, type Stage as StageSizes } from '../../services/api';
import SafeMarkdown from '../SafeMarkdown';
import { useBlobUrl } from '../../lib/objectUrls';
import { rememberFor } from '../../lib/query';
import { SEVERITY_HSL } from '../../utils/severity';

const cssVars = (stage: StageSizes): React.CSSProperties => {
  const out: Record<string, string> = {};
  Object.entries(stage).forEach(([k, v]) => { out[`--ps-${k.replace(/_/g, '-')}`] = String(v); });
  return out as React.CSSProperties;
};

const sevColor = (s: string) => SEVERITY_HSL[s as keyof typeof SEVERITY_HSL] ?? 'hsl(var(--muted-foreground))';

const Block: React.FC<{ block: SlideBlock }> = ({ block }) => {
  switch (block.kind) {
    case 'p':
    case 'meta':
      return <div className="ps-block"><SafeMarkdown text={block.md} /></div>;
    case 'h':
      return <div className="ps-block ps-h"><SafeMarkdown text={block.md} /></div>;
    case 'title':
      return <div className="ps-title"><SafeMarkdown text={block.md} /></div>;
    case 'quote':
      return <div className="ps-block ps-quote"><SafeMarkdown text={block.md} /></div>;
    case 'li':
      return (
        <div className="ps-li" style={{ paddingLeft: `calc(var(--ps-list-indent) * ${block.level}px)` }}>
          <span className="ps-li-marker" aria-hidden>{block.cont ? '' : block.marker}</span>
          <div className="ps-block" style={{ margin: 0, minWidth: 0, flex: 1 }}><SafeMarkdown text={block.md} /></div>
        </div>
      );
    case 'rule':
      return <div className="ps-rule"><hr /></div>;
    case 'code':
      return (
        <>
          {block.cont_before && <div className="ps-continued">continued</div>}
          <pre className="ps-code">{block.lines.join('\n')}</pre>
        </>
      );
    case 'table':
      return (
        <>
          {block.columns && <div className="ps-continued">{block.columns}</div>}
          <table className="ps-table" style={{ width: `${block.widths.reduce((a, b) => a + b, 0)}px` }}>
            <colgroup>{block.widths.map((w, i) => <col key={i} style={{ width: `${w}px` }} />)}</colgroup>
            <thead>
              <tr>{block.header.map((h, i) => (
                <th key={i} style={{ textAlign: (block.align[i] as React.CSSProperties['textAlign']) ?? 'left' }}>
                  <SafeMarkdown text={h} />
                </th>
              ))}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>{row.map((c, i) => (
                  <td key={i} style={{ textAlign: (block.align[i] as React.CSSProperties['textAlign']) ?? 'left' }}>
                    <SafeMarkdown text={c} />
                  </td>
                ))}</tr>
              ))}
            </tbody>
          </table>
        </>
      );
    case 'counts':
      return (
        <div className="ps-counts" data-testid="ps-counts">
          {block.counts.map((c) => (
            <div key={c.severity} className="ps-count" style={{ color: sevColor(c.severity) }}>
              <span className="ps-count-n">{c.count.toLocaleString()}</span>
              <span className="ps-count-label">{c.label}</span>
            </div>
          ))}
        </div>
      );
    case 'facts':
      return (
        <div className="ps-facts">
          <span><span className="ps-fact-label">Severity</span>
            <span style={{ color: sevColor(block.severity), fontWeight: 700 }}>{block.severity_label}</span></span>
          {block.cvss_score != null && (
            <span><span className="ps-fact-label">CVSS</span>{block.cvss_score.toFixed(1)}</span>
          )}
          <span><span className="ps-fact-label">Affected endpoints</span>{block.affected_count.toLocaleString()}</span>
          {block.status_note && <span><span className="ps-fact-label">Status</span>{block.status_note}</span>}
        </div>
      );
    default:
      return null;
  }
};

const SlideImage: React.FC<{
  projectId: number; reportId: number; image: NonNullable<Slide['image']>; onReady: (ok: boolean) => void;
}> = ({ projectId, reportId, image, onReady }) => {
  const query = useQuery({
    queryKey: ['fetchPresentationImage', projectId, reportId, image.attachment_id],
    queryFn: ({ signal }) => fetchPresentationImage(projectId, reportId, image.attachment_id, signal),
    enabled: image.available,
    // An issued report's frozen copy never changes.
    ...rememberFor(Infinity),
  });
  const url = useBlobUrl(query.data);
  const readyRef = useRef(onReady);
  readyRef.current = onReady;
  useEffect(() => {
    if (!image.available || query.isError) readyRef.current(true);
  }, [image.available, query.isError]);
  return (
    <div className="ps-image" style={{ height: `${image.height}px` }}>
      {image.available && url ? (
        <img src={url} alt="" data-testid="ps-image"
          onLoad={(e) => {
            const img = e.currentTarget;
            void (img.decode ? img.decode().catch(() => undefined) : Promise.resolve()).then(() => readyRef.current(true));
          }}
          onError={() => readyRef.current(true)} />
      ) : image.available && !query.isError ? (
        <div className="ps-image-missing">Loading image…</div>
      ) : (
        <div className="ps-image-missing" data-testid="ps-image-missing">Image not available</div>
      )}
    </div>
  );
};

export interface StageProps {
  projectId: number;
  reportId: number;
  stage: StageSizes;
  slide: Slide | null;
  /** What this render is for (the presentation's revision on a Display). */
  token?: number;
  onRendered?: (token: number | undefined, fitted: boolean) => void;
  onOverflow?: (slideKey: string, overflowPx: number, font: string) => void;
  /** Shown instead of a slide (no presentation yet, loading…). */
  placeholder?: React.ReactNode;
  /** A ring at this point of the stage (stage coordinates) — the spotlight. */
  spotlight?: { x: number; y: number; seq: number } | null;
  /** A click on the slide, in stage coordinates (the driver's spotlight). */
  onPoint?: (x: number, y: number) => void;
}

const StageView: React.FC<StageProps> = ({
  projectId, reportId, stage, slide, token, onRendered, onOverflow, placeholder, spotlight, onPoint,
}) => {
  const frame = useRef<HTMLDivElement | null>(null);
  const body = useRef<HTMLDivElement | null>(null);
  const inner = useRef<HTMLDivElement | null>(null);
  const [fitTo, setFitTo] = useState({ scale: 1, left: 0, top: 0 });
  const [fit, setFit] = useState<{ key: string; scale: number } | null>(null);
  // Readiness belongs to ONE render instance — a slide shown again (image →
  // text → the same image) mounts its image afresh and must decode it again
  // before the render is acknowledged (code review 2026-10-24).
  const instanceCounter = useRef(0);
  const [imageReady, setImageReady] = useState<number | null>(null);
  const width = stage.width ?? 1600;
  const height = stage.height ?? 900;
  const slideKey = slide?.key ?? '';

  useLayoutEffect(() => {
    const el = frame.current;
    if (!el) return undefined;
    const resize = () => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      // Scaled as one unit and centred: the frame's spare room is split on both sides.
      const s = Math.min(r.width / width, r.height / height);
      setFitTo({ scale: s, left: (r.width - width * s) / 2, top: (r.height - height * s) / 2 });
    };
    resize();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, [width, height]);

  const instance = useMemo(() => {
    instanceCounter.current += 1;
    return instanceCounter.current;
    // A new instance per slide shown (identity: the slide object).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slide]);
  const needsImage = Boolean(slide?.image);
  const imageDone = !needsImage || imageReady === instance;
  const renderedRef = useRef(onRendered);
  renderedRef.current = onRendered;
  const overflowRef = useRef(onOverflow);
  overflowRef.current = onOverflow;
  const reported = useRef<Set<string>>(new Set());

  // Once fonts are in and the image (if any) decoded: measure, fit if the
  // estimate was wrong here, and report the render — unless a newer slide
  // took its place meanwhile.
  useEffect(() => {
    if (!slide || !imageDone) return undefined;
    let current = true;
    const fonts = typeof document !== 'undefined' && document.fonts ? document.fonts.ready : Promise.resolve();
    void Promise.resolve(fonts).then(() => {
      if (!current) return;
      const b = body.current;
      const i = inner.current;
      let fitted = false;
      if (b && i && b.clientHeight > 0) {
        const over = Math.max(i.scrollHeight - b.clientHeight, i.scrollWidth - b.clientWidth, 0);
        if (over > 0.5) {
          fitted = true;
          const s = Math.min(b.clientHeight / i.scrollHeight, b.clientWidth / i.scrollWidth);
          setFit({ key: slide.key, scale: s });
          const font = getComputedStyle(i).fontFamily || 'unknown';
          if (!reported.current.has(`${slide.key}|${font}`)) {
            reported.current.add(`${slide.key}|${font}`);
            overflowRef.current?.(slide.key, Math.round(over), font);
          }
        }
      }
      renderedRef.current?.(token, fitted);
    });
    return () => { current = false; };
  }, [slide, imageDone, token, instance]);

  const fittedScale = fit && fit.key === slideKey ? fit.scale : 1;
  const style = useMemo(() => ({
    ...cssVars(stage), left: `${fitTo.left}px`, top: `${fitTo.top}px`, transform: `scale(${fitTo.scale})`,
  }), [stage, fitTo]);

  // The stage is scaled as one unit, so a point on screen is a point of the
  // stage through the same rectangle on every screen.
  const point = onPoint && slide ? (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const x = Math.round(((e.clientX - r.left) / r.width) * width);
    const y = Math.round(((e.clientY - r.top) / r.height) * height);
    onPoint(Math.min(Math.max(x, 0), width), Math.min(Math.max(y, 0), height));
  } : undefined;

  return (
    <div ref={frame} className="ps-frame" data-testid="ps-frame">
      <div className="ps-stage" style={style} data-testid="ps-stage" data-slide={slideKey}
        data-pointable={point ? 'true' : undefined} onClick={point}>
        {slide ? (
          <>
            {slide.header && <div className="ps-header">{slide.header}</div>}
            <div ref={body} className="ps-body" data-testid="ps-body">
              <div ref={inner} className="ps-body-inner" data-testid="ps-body-inner"
                style={fittedScale < 1 ? { transform: `scale(${fittedScale})` } : undefined}>
                {slide.image && (
                  <SlideImage key={`${slide.key}#${instance}`} projectId={projectId} reportId={reportId} image={slide.image}
                    onReady={() => setImageReady(instance)} />
                )}
                {slide.blocks.map((b, i) => <Block key={`${slide.key}-${i}`} block={b} />)}
              </div>
              {fittedScale < 1 && <span className="ps-fitted" data-testid="ps-fitted">fitted</span>}
            </div>
            <div className="ps-footer" data-testid="ps-footer">{slide.footer}</div>
            {spotlight && (
              <div key={spotlight.seq} className="ps-spotlight" data-testid="ps-spotlight" aria-hidden
                style={{ left: `${spotlight.x}px`, top: `${spotlight.y}px` }} />
            )}
          </>
        ) : (
          <div className="ps-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {placeholder}
          </div>
        )}
      </div>
    </div>
  );
};

export default StageView;
