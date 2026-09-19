/**
 * Hand-off of a Hosts-page selection to the "Generate with AI" dialog on
 * /test-plans (v5.221.0; design review item 6).
 *
 * The selection is a FIXED id list resolved at the moment of the click —
 * not the query that produced it — so it is carried as data, not as URL
 * filter parameters (which would re-run the query later with a different
 * membership).  It rides in sessionStorage because a selection of several
 * thousand ids does not fit a URL; the dialog takes it once and clears it.
 */
export interface PlanSelection {
  host_ids: number[];
  /** Why these hosts, in the operator's words. */
  rationale: string;
  /** How the list was arrived at ("41 hosts matching subnet:10.1.0.0/16"). */
  summary: string;
  /** ISO timestamp the list was resolved. */
  taken_at: string;
}

const KEY = 'bluestick.plan_selection';

export const stashPlanSelection = (sel: PlanSelection): boolean => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(sel));
    return true;
  } catch {
    return false;
  }
};

/** Read and clear the pending selection, or null when none is waiting. */
export const takePlanSelection = (): PlanSelection | null => {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    const parsed = JSON.parse(raw) as Partial<PlanSelection>;
    if (!Array.isArray(parsed.host_ids) || parsed.host_ids.length === 0) return null;
    return {
      host_ids: parsed.host_ids.filter((n): n is number => Number.isInteger(n)),
      rationale: typeof parsed.rationale === 'string' ? parsed.rationale : '',
      summary: typeof parsed.summary === 'string' ? parsed.summary : '',
      taken_at: typeof parsed.taken_at === 'string' ? parsed.taken_at : new Date().toISOString(),
    };
  } catch {
    return null;
  }
};

/**
 * One line describing how a bulk-bar selection was made, for the plan's
 * provenance note.  Filter values come from the Hosts page query context.
 */
export const describeSelection = (
  count: number,
  allMatching: boolean,
  queryContext: Record<string, string | boolean | number | string[] | undefined>,
): string => {
  if (!allMatching) return `${count} host${count === 1 ? '' : 's'} checked on the Hosts page`;
  const filters = Object.entries(queryContext)
    .filter(([, v]) => v !== undefined && v !== '' && v !== false && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(',') : String(v)}`);
  const via = filters.length ? ` matching ${filters.join(' ')}` : ' in the project';
  return `all ${count} hosts${via}, resolved to a fixed list`;
};
