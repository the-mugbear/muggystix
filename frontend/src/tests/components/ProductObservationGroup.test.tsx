import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

import ProductObservationGroup from '../../components/host-inspector/ProductObservationGroup';
import { groupByProduct, groupVulnerabilities } from '../../utils/vulnGrouping';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { HostVulnerability } from '../../services/api/hosts';

let nextId = 1;
const tomcat = (title: string, over: Partial<HostVulnerability> = {}): HostVulnerability =>
  ({
    id: nextId++, plugin_id: String(nextId), title, severity: 'critical',
    source: 'nessus', cvss_score: null, cvss_vector: null, cve_id: null,
    scan_id: 1, port_id: null, port_number: 8080, protocol: 'tcp',
    service_name: null, exploitable: null, finding_id: null,
    first_seen: '2026-08-01T00:00:00Z', last_seen: '2026-08-01T00:00:00Z',
    solution: null, cpe: 'a:apache:tomcat', installed_version: '9.0.13', ...over,
  }) as HostVulnerability;

describe('ProductObservationGroup', () => {
  it('is one line naming the product until opened, then every issue with its actions', () => {
    const [item] = groupByProduct(groupVulnerabilities([
      tomcat('Apache Tomcat 9.0.13 < 9.0.120 multiple vulnerabilities', { fixed_version: '9.0.120', exploitable: true }),
      tomcat('Apache Tomcat 9.0.0.M1 < 9.0.99', { fixed_version: '9.0.99' }),
    ]));
    if (item.kind !== 'product') throw new Error('expected a product group');
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ProductObservationGroup
            product={item.product}
            hostId={1}
            severityBadgeVariant={() => 'destructive'}
            expandedVulnIds={new Set()}
            onToggleDescription={vi.fn()}
            promotedVulns={{}}
            vulnActionId={null}
            onTriage={vi.fn()}
            onQueryHosts={vi.fn()}
            onQueryExploitPort={vi.fn()}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const header = screen.getByRole('button', { name: /Apache Tomcat 9\.0\.13: 2 issues/ });
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText(/fixed in 9\.0\.120/)).toBeInTheDocument();
    expect(screen.getByText('1 Exploit')).toBeInTheDocument();
    expect(screen.queryByText(/< 9\.0\.99/)).not.toBeInTheDocument();

    fireEvent.click(header);
    expect(screen.getByText('Apache Tomcat 9.0.0.M1 < 9.0.99')).toBeInTheDocument();
    expect(screen.getByText(/Apache Tomcat 9\.0\.120 or later satisfies all of them/)).toBeInTheDocument();
  });
});
