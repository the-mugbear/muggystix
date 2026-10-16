/**
 * Scanner observations moved from the Findings page's second view to
 * Inventory (5.404.0).  An old link — a bookmark, a shared address — lands on
 * the new page with what that view showed: its filters and page, narrowed to
 * what is not yet judged unless it had asked for the judged too.
 */
import { describe, expect, it } from 'vitest';

import { OBSERVATIONS_TO_JUDGE_PATH, observationsPathFromFindingsView } from '../../utils/observationsPath';

const from = (search: string) => observationsPathFromFindingsView(new URLSearchParams(search));

describe('observationsPathFromFindingsView', () => {
  it('the bare view is the list of what still waits', () => {
    expect(from('view=observations')).toBe('/observations?obs_pending=1');
    expect(OBSERVATIONS_TO_JUDGE_PATH).toBe('/observations?obs_pending=1');
  });

  it('keeps the filters and the page', () => {
    expect(from('view=observations&obs_severity=critical&obs_search=ssh&page=3'))
      .toBe('/observations?obs_severity=critical&obs_search=ssh&page=3&obs_pending=1');
  });

  it('the old "include covered" switch is the new default: no narrowing', () => {
    expect(from('view=observations&obs_judged=1&obs_min=5')).toBe('/observations?obs_min=5');
  });
});
