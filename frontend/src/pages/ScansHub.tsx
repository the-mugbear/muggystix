/**
 * /scans — ONE import history (5.418.0; owner decision 2026-10-22, G15).
 *
 * From 5.380.0 the page had two views: the imported scans, and Ingestion
 * results (`?view=results`, every upload and how it went).  Most rows of the
 * second were completed imports the first already listed, so the uploads that
 * were not imported became rows of the history and the view's filters became
 * its status filter.  An old address that names the view lands on the history
 * with the same filter, query kept (`utils/importResultsPath`) — for bookmarks
 * and old notification links, as `/ingestion-results` and `/parse-errors` do.
 */
import React from 'react';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';

import { importResultsPath } from '../utils/importResultsPath';
import Scans from './Scans';

const ScansHub: React.FC = () => {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  if (params.has('view')) return <Navigate to={`${importResultsPath(params)}${hash}`} replace />;
  return <Scans />;
};

export default ScansHub;
