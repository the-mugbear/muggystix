/**
 * Presentation agendas (5.425.0, plan E3): where Next / Previous go, what a
 * pick or a filter change does to the agenda state, and how items are named.
 *
 * An agenda item is an ANCHOR (`overview`, `summary`, `scope`, `finding:F-07`,
 * `section:F-07:recommendation`), and which slides it covers is the SERVER's
 * rule: each slide lists its `anchors`.  Nothing here decides membership; it
 * only matches those strings, so pagination and the host filter can never make
 * the browser and the server disagree about an item's pages.
 *
 * The agenda state is part of the position (`Target.agenda`): following the
 * agenda (`agenda`, the active item moves with every step) or exploring the
 * full report (`explore`, the item index FROZEN — it is the item "Back to the
 * agenda" returns to).  Every tab reads Next from the shared state, so a new
 * driver's Next is the old driver's.
 */
import type { AgendaPosition, Slide } from '../services/api';
import type { Target } from './presentationSync';

export interface PinnedAgenda { id: number; version: number; items: string[] }

export const SECTION_LABEL: Record<string, string> = {
  description: 'Description',
  impact: 'Impact',
  recommendation: 'Recommendation',
  steps_to_reproduce: 'Steps to reproduce',
  hosts: 'Affected endpoints',
};

/** The pages of an agenda item in a slide list, in report order. */
export const itemPages = (slides: Slide[], anchor: string): string[] =>
  slides.filter((s) => s.anchors.includes(anchor)).map((s) => s.key);

const firstPage = (slides: Slide[], anchor: string): string | null => itemPages(slides, anchor)[0] ?? null;

const withAgenda = (agenda: PinnedAgenda, mode: AgendaPosition['mode'], index: number): AgendaPosition =>
  ({ agenda_id: agenda.id, version: agenda.version, mode, item_index: index });

/** Where Next (+1) / Previous (−1) go.  In `agenda` mode: the next page of
 *  the active item, then the first page of the next item (Previous: the last
 *  page of the item before); null past either end.  Exploring, or with no
 *  agenda: the full list, the agenda state unchanged. */
export const stepTarget = (
  slides: Slide[], position: Target, agenda: PinnedAgenda | null, delta: 1 | -1,
): Target | null => {
  const a = position.agenda;
  if (!a || a.mode === 'explore' || !agenda) {
    const at = slides.findIndex((s) => s.key === position.slide_key);
    const next = at < 0 ? slides[0] : slides[at + delta];
    return next ? { ...position, slide_key: next.key } : null;
  }
  const pages = itemPages(slides, agenda.items[a.item_index] ?? '');
  const at = pages.indexOf(position.slide_key);
  if (at >= 0 && pages[at + delta]) return { ...position, slide_key: pages[at + delta] };
  for (let i = a.item_index + delta; i >= 0 && i < agenda.items.length; i += delta) {
    const its = itemPages(slides, agenda.items[i]);
    if (its.length) {
      return { ...position, slide_key: delta === 1 ? its[0] : its[its.length - 1], agenda: withAgenda(agenda, 'agenda', i) };
    }
  }
  return null;
};

/** Follow `agenda` from item `index` (its first page). */
export const pickItem = (slides: Slide[], position: Target, agenda: PinnedAgenda, index: number): Target | null => {
  const key = firstPage(slides, agenda.items[index] ?? '');
  return key ? { ...position, slide_key: key, agenda: withAgenda(agenda, 'agenda', index) } : null;
};

/** A slide picked from the full list: still the active item's page → keep
 *  following; else → explore, the item index frozen. */
export const pickSlide = (slides: Slide[], position: Target, agenda: PinnedAgenda | null, key: string): Target => {
  const a = position.agenda;
  if (!a || !agenda) return { ...position, slide_key: key };
  const inItem = itemPages(slides, agenda.items[a.item_index] ?? '').includes(key);
  return { ...position, slide_key: key, agenda: { ...a, mode: a.mode === 'agenda' && inItem ? 'agenda' : 'explore' } };
};

/** Back from exploring: the frozen item's first page. */
export const backToAgenda = (slides: Slide[], position: Target, agenda: PinnedAgenda): Target | null =>
  position.agenda ? pickItem(slides, position, agenda, position.agenda.item_index) : null;

/** A host filter change lands on `landing` (the server's rule, `filterLanding`)
 *  in the NEW filter's list; if that leaves the active item's pages, the
 *  presentation goes exploring rather than leave the agenda inconsistent. */
export const withFilterLanding = (newSlides: Slide[], position: Target, agenda: PinnedAgenda | null,
  landing: string, filter: Target['host_filter']): Target => {
  const a = position.agenda;
  const next: Target = { ...position, slide_key: landing, host_filter: filter };
  if (!a || a.mode === 'explore' || !agenda) return next;
  const inItem = itemPages(newSlides, agenda.items[a.item_index] ?? '').includes(landing);
  return inItem ? next : { ...next, agenda: { ...a, mode: 'explore' } };
};

/** Switch to another agenda or version.  Following: the item holding the
 *  current slide (the active index first), keeping the slide; else the item
 *  at the same place (or the last), from its first page.  Exploring: keep
 *  exploring, the index kept where it can be. */
export const switchAgenda = (slides: Slide[], position: Target, agenda: PinnedAgenda): Target | null => {
  const was = position.agenda;
  const last = agenda.items.length - 1;
  const index = Math.min(was?.item_index ?? 0, last);
  if (was?.mode === 'explore') return { ...position, agenda: withAgenda(agenda, 'explore', index) };
  const holds = (i: number) => itemPages(slides, agenda.items[i]).includes(position.slide_key);
  const found = holds(index) ? index : agenda.items.findIndex((_, i) => holds(i));
  if (found >= 0) return { ...position, agenda: withAgenda(agenda, 'agenda', found) };
  return pickItem(slides, position, agenda, index);
};

/** Leave the agenda for the full report: the agenda state goes. */
export const leaveAgenda = (position: Target): Target => ({ ...position, agenda: null });

const plainText = (md: string) => md.replace(/[*_`]+/g, '').trim();

/** What an item is called, from the slides it names. */
export const itemLabel = (slides: Slide[], anchor: string): string => {
  if (anchor === 'overview') return 'Overview';
  if (anchor === 'summary') return 'Executive summary';
  if (anchor === 'scope') return 'Scope';
  const [kind, ref, section] = anchor.split(':');
  if (kind === 'finding') {
    const slide = slides.find((s) => s.ref === ref && s.kind === 'finding');
    const title = slide?.blocks.find((b) => b.kind === 'title');
    return title && 'md' in title ? `${ref} · ${plainText(title.md)}` : ref;
  }
  if (kind === 'section') return `${ref} · ${SECTION_LABEL[section] ?? section}`;
  return anchor;
};

export interface AnchorOption { anchor: string; label: string; nested: boolean }

/** Every anchor of a slide list, in report order — what an agenda can name. */
export const anchorOptions = (slides: Slide[]): AnchorOption[] => {
  const seen = new Set<string>();
  const out: AnchorOption[] = [];
  slides.forEach((s) => s.anchors.forEach((anchor) => {
    if (seen.has(anchor)) return;
    seen.add(anchor);
    out.push({ anchor, label: itemLabel(slides, anchor), nested: anchor.startsWith('section:') });
  }));
  return out;
};
