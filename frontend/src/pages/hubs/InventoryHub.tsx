import React from 'react';
import { Network } from 'lucide-react';
import HubLanding from './HubLanding';
import { ScanLinesIcon, ScopeIcon, ServerStackIcon } from '../../components/AppIcons';

const InventoryHub: React.FC = () => (
  <HubLanding
    title="Inventory"
    subtitle="Everything you've discovered about the target environment — scans you've uploaded, hosts the scanners surfaced, and the scope boundaries that organize them."
    links={[
      {
        label: 'Scans',
        path: '/scans',
        description: 'Upload scan files, track ingestion jobs, and inspect raw scanner output.',
        Icon: ScanLinesIcon,
      },
      {
        label: 'Hosts',
        path: '/hosts',
        description: 'Deduplicated inventory of every host the scanners observed — with ports, vulnerabilities, notes, and follow status.',
        Icon: ServerStackIcon,
      },
      {
        label: 'Scopes',
        path: '/scopes',
        description: 'Subnet boundaries that map raw hosts to engagement scopes.  Manage coverage and out-of-scope flags.',
        Icon: ScopeIcon,
      },
      {
        label: 'Topology',
        path: '/network-topology',
        description: 'Visual map of scopes and subnets, sized by host count and flagged for critical findings.  Click through to filtered hosts.',
        Icon: Network,
      },
    ]}
  />
);

export default InventoryHub;
