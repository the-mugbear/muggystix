/**
 * `/posture`'s `focus`, written out as the server would send it.  The ranking
 * and its sentences are the server's (`app/services/posture_focus.py`, pinned
 * by `backend/tests/test_posture_focus.py`); a page test states the answer it
 * draws, so this builds rows with defaults and computes nothing.
 */
import type { ConcentrationRow, PostureFocus } from '../../services/api/posture';

export const focusRow = (row: Partial<ConcentrationRow> & Pick<ConcentrationRow, 'segment' | 'label'>): ConcentrationRow => ({
  affected: 0, assessed: 0, in_scope: 0, eligible: null, eligible_assessed: null,
  rate: null, coverage: null, unknown: null,
  rest_affected: 0, rest_assessed: 0, rest_rate: null, delta_points: null, share_of_affected: null,
  state: 'comparable', limited_because: '', sentence: `${row.label}: sentence`,
  ...row,
});

export const focusOf = (lead: string | null, families: Record<string, ConcentrationRow[]>): PostureFocus => ({
  lead_family: lead, limited_min_assessed: 10, limited_min_coverage: 0.6, families,
});
