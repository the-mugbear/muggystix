/**
 * The Scans page's two views (5.380.0): what was imported, and how each
 * upload went.  They were two pages — "Scans" and "Ingestion Results" — over
 * the same uploads, each with its own totals; the second is now a view of the
 * first, as Scanner observations is a view of Findings.  The view is in the
 * address (`?view=results`), so a link can land on either.
 *
 * "Ingestion results" reads what needs an analyst (its routes do), so the tabs
 * are shown to analysts and above; anyone else has the one view and no tabs.
 */
import React from 'react';
import { useSearchParams } from 'react-router-dom';

import { useProjectRole } from '../../hooks/useProjectRole';
import { IMPORT_RESULTS_VIEW } from '../../utils/importResultsPath';
import { ViewTabs } from '../ui/view-tabs';

export type ScansView = 'scans' | 'results';

/** The view the address names — `results` only for someone who may read it. */
export function useScansView(): ScansView {
  const [params] = useSearchParams();
  const { canWrite } = useProjectRole();
  return canWrite && params.get('view') === IMPORT_RESULTS_VIEW ? 'results' : 'scans';
}

const VIEWS: readonly (readonly [ScansView, string])[] = [
  ['scans', 'Imported scans'],
  // The name the page had, so "Ingestion Results" in a document, a link's
  // words or an agent's answer still names something on screen.
  ['results', 'Ingestion results'],
];

const ScansViewTabs: React.FC = () => {
  const [, setParams] = useSearchParams();
  const { canWrite } = useProjectRole();
  const view = useScansView();
  if (!canWrite) return null;
  // Each view has its own filters, so a switch starts the other one clean.
  const setView = (next: ScansView) =>
    setParams(next === 'results' ? { view: IMPORT_RESULTS_VIEW } : {}, { replace: true });
  return <ViewTabs label="Scans view" value={view} options={VIEWS} onChange={setView} className="mb-md" />;
};

export default ScansViewTabs;
