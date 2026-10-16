/**
 * Inventory → Scanner observations (5.404.0): everything the scanners reported,
 * one row per issue across the project's hosts, with an inspector for each —
 * what the scanner wrote, every host carrying it and what it printed there.
 *
 * It was the Findings page's second view ("waiting for a judgment") until
 * 5.404.0; users asked for every vulnerability and a host-inspector-like view,
 * and observations are what the scanners reported — inventory, beside Hosts —
 * not what the engagement concluded.  `/findings?view=observations` redirects
 * here, narrowed to what is not yet judged.
 */
import React from 'react';

import ScannerObservations from '../components/observations/ScannerObservations';
import { InfoTip } from '../components/ui/info-tip';
import { useProjectRole } from '../hooks/useProjectRole';

const ScannerObservationsPage: React.FC = () => {
  const { canWrite } = useProjectRole();
  return (
    <div className="p-md md:p-lg">
      <div className="mb-md">
        <h1 className="text-page-title font-semibold">Scanner observations</h1>
        <p className="flex flex-wrap items-center gap-xxs text-metadata text-muted-foreground">
          What the scanners reported, one row per issue across the project’s hosts — open one to see every host
          carrying it.
          <InfoTip
            label="Scanner observations and findings"
            text={
              <>
                A scanner observation is what a scanner reported, not yet a conclusion. Promote one to record it as a
                finding — on every host carrying it, or only on the hosts you verified — or mark it a false positive.
                “Judged” means a finding covers it on that host. The same issue reported by two scanners is one row.
              </>
            }
          />
        </p>
      </div>
      <ScannerObservations canManage={canWrite} />
    </div>
  );
};

export default ScannerObservationsPage;
