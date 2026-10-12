/**
 * What a finding still needs before the report goes out, as the pages say it.
 *
 * The server decides which findings need what and writes each row's sentence
 * (`services/finding_needs.py`); this module holds only what a page adds: the
 * filter's labels and the section of the finding page where each need is met.
 */
import type { FindingNeedItem, FindingNeedKind, FindingNeedsFilter } from '../services/api';

/** The section of the finding page (`SectionJumpBar` ids in FindingDetail)
 *  where the need is met. */
export const NEED_SECTION: Record<FindingNeedKind, string> = {
  missing_text: 'section-report-text',
  uncaptioned_images: 'section-comments',
  proposals: 'section-proposals',
};

/** The Needs filter's options, in the order shown. */
export const NEED_FILTER_OPTIONS: ReadonlyArray<readonly [FindingNeedsFilter, string]> = [
  ['any', 'Needs anything'],
  ['missing_text', 'Needs report text'],
  ['uncaptioned_images', 'Needs an image caption'],
  ['proposals', 'Has proposals to decide'],
];

const NEED_FILTERS = new Set<string>(NEED_FILTER_OPTIONS.map(([value]) => value));

/** `?needs=` as the address holds it: a word the list does not know is no
 *  filter (the server would refuse it; a hand-edited address shows the list). */
export const needsFilterOf = (raw: string | null): FindingNeedsFilter | 'all' =>
  raw !== null && NEED_FILTERS.has(raw) ? (raw as FindingNeedsFilter) : 'all';

/** The needs a Findings row says — every kind that has a section to open.
 *  ("Under investigation" is the row's Status.) */
export const rowNeeds = (
  needs: ReadonlyArray<FindingNeedItem> | null | undefined,
): Array<FindingNeedItem & { kind: FindingNeedKind }> =>
  (needs ?? []).filter(
    (n): n is FindingNeedItem & { kind: FindingNeedKind } => n.kind in NEED_SECTION,
  );
