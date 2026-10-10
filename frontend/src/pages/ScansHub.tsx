/**
 * /scans — one page, two views (5.380.0): the imported scans, and the import
 * results (every upload and how it went — what was the "Ingestion Results"
 * page).  Each view is its own component with its own filters and reads; this
 * only chooses between them from the address, so the list that is not on
 * screen asks for nothing.
 */
import React from 'react';

import { useScansView } from '../components/scans/ScansViewTabs';
import IngestionResults from './IngestionResults';
import Scans from './Scans';

const ScansHub: React.FC = () => (useScansView() === 'results' ? <IngestionResults /> : <Scans />);

export default ScansHub;
