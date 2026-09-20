import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import HostFilterPopover from '../../components/hosts/HostFilterPopover';
import type { HostFilterOptions } from '../../components/HostFilters';
import type { HostFilterData } from '../../services/api';

// The api barrel is only imported for a type here, but HostFilters pulls it in.
vi.mock('../../services/api', () => ({}));

const DATA: HostFilterData = {
  common_ports: [
    { port: 22, service: 'ssh', state: 'open', count: 9 },
    { port: 443, service: 'https', state: 'open', count: 5 },
  ],
  services: [{ name: 'ssh', count: 9 }, { name: 'https', count: 5 }],
  operating_systems: [{ name: 'Linux', count: 7 }],
  subnets: [{ cidr: '10.0.0.0/24', host_count: 12 }, { cidr: '10.0.1.0/24', host_count: 3 }],
  scans: [{ id: 9, filename: 'nightly.xml', tool_name: 'nmap', created_at: '2026-09-01T00:00:00Z' }],
};

function Harness({
  initial = {}, fieldId = null, data = DATA, onApply = vi.fn(), error = false,
}: {
  initial?: HostFilterOptions; fieldId?: string | null; data?: HostFilterData | null;
  onApply?: (next: HostFilterOptions) => void; error?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [field, setField] = useState<string | null>(fieldId);
  return (
    <HostFilterPopover
      open={open}
      onOpenChange={setOpen}
      fieldId={field}
      onFieldChange={setField}
      filters={initial}
      onApply={onApply}
      data={data}
      optionsLoading={false}
      optionsError={error}
    />
  );
}

describe('HostFilterPopover', () => {
  it('shows the catalog: a search box, the common fields, and categories that open to field NAMES', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByLabelText('Find a filter')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Port \/ service/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Registered country/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Discovery & attribution/ }));
    expect(screen.getByRole('button', { name: /Registered country/ })).toBeInTheDocument();
  });

  it('finds a field by an analyst\'s word and explains it', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByLabelText('Find a filter'), 'unreviewed');
    const results = screen.getByRole('list', { name: 'Matching filters' });
    expect(within(results).getByText('Team review')).toBeInTheDocument();
    expect(within(results).getByText(/shared, not yours alone/)).toBeInTheDocument();
  });

  it('applies a positive-only toggle straight from the catalog, and removes it the same way — never "No"', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const { unmount } = render(<Harness onApply={onApply} />);
    await user.click(screen.getByRole('button', { name: /Assigned to me/ }));
    expect(onApply).toHaveBeenLastCalledWith({ assignedToMe: true });
    unmount();

    render(<Harness initial={{ assignedToMe: true, sites: ['East'] }} onApply={onApply} />);
    await user.click(screen.getByRole('button', { name: /Assigned to me/ }));
    expect(onApply).toHaveBeenLastCalledWith({ sites: ['East'] });
  });

  it('edits a multi-value field as a draft: nothing is applied until Apply, then once', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="subnets" initial={{ sites: ['East'] }} onApply={onApply} />);
    await user.click(screen.getByRole('checkbox', { name: /10\.0\.0\.0\/24/ }));
    await user.click(screen.getByRole('checkbox', { name: /10\.0\.1\.0\/24/ }));
    expect(onApply).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith({ sites: ['East'], subnets: ['10.0.0.0/24', '10.0.1.0/24'] });
  });

  it('Cancel discards the draft', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="subnets" onApply={onApply} />);
    await user.click(screen.getByRole('checkbox', { name: /10\.0\.0\.0\/24/ }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('keeps a selected value visible and removable when it is not among the loaded options', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="subnets" initial={{ subnets: ['172.16.0.0/16'] }} onApply={onApply} />);
    const orphan = screen.getByRole('checkbox', { name: /172\.16\.0\.0\/16/ });
    expect(orphan).toBeChecked();
    await user.click(orphan);
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledWith({});
  });

  it('tells "no data collected" from "options unavailable"', () => {
    const { unmount } = render(<Harness fieldId="tech" />);
    expect(screen.getByText(/No technologies yet/)).toBeInTheDocument();
    unmount();
    render(<Harness fieldId="tech" data={null} error />);
    expect(screen.getByText(/Options unavailable/)).toBeInTheDocument();
  });

  it('says so when the value list hit the server cap — the search cannot promise the rest', () => {
    const many: HostFilterData = {
      ...DATA,
      operating_systems: Array.from({ length: 100 }, (_, i) => ({ name: `OS ${i}`, count: 1 })),
    };
    render(<Harness fieldId="osFilter" data={many} />);
    expect(screen.getByText(/Only the 100 most common values are loaded/)).toBeInTheDocument();
    expect(screen.getByText('os:"Windows Server 2019"')).toBeInTheDocument();
  });

  it('builds ONE endpoint condition — port, service and open on the same recorded port', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="endpoint" initial={{ sites: ['East'] }} onApply={onApply} />);
    expect(screen.getByText(/same recorded port/)).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: /443 \(https\)/ }));
    await user.click(within(screen.getByRole('list', { name: 'Services' })).getByRole('checkbox', { name: /https/ }));
    await user.click(screen.getByRole('checkbox', { name: 'The port is open' }));
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledWith({
      sites: ['East'], ports: ['443'], services: ['https'], portStates: ['open'],
    });
  });

  it('a port group fills the endpoint draft, and a restored non-open state is never silently dropped', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="endpoint" initial={{ portStates: ['filtered'] }} onApply={onApply} />);
    expect(screen.getByText(/Also matching port state: filtered/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'SSH Servers' }));
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledWith({ ports: ['22'], portStates: ['filtered', 'open'] });
  });

  it('warns that a port condition replaces "no recorded open ports" instead of being silently ignored', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="endpoint" initial={{ hasOpenPorts: false }} onApply={onApply} />);
    await user.click(screen.getByRole('checkbox', { name: /22 \(ssh\)/ }));
    expect(screen.getByText(/cannot hold together with a port condition/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledWith({ ports: ['22'] });
  });

  it('"first discovered in" needs a scan, and leaves with the scans', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<Harness fieldId="scans" onApply={onApply} />);
    await user.click(screen.getByRole('radio', { name: 'First discovered in' }));
    expect(screen.getByRole('button', { name: 'Apply condition' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /nightly\.xml/ }));
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
    expect(onApply).toHaveBeenCalledWith({ scanIds: ['9'], firstSeenInSelectedScans: true });
  });

  it('a choice applies as it is picked, and "Any" removes the condition', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const { unmount } = render(<Harness fieldId="hasWebInterface" onApply={onApply} />);
    await user.click(screen.getByRole('radio', { name: /Not recorded/ }));
    expect(onApply).toHaveBeenLastCalledWith({ hasWebInterface: false });
    unmount();
    render(<Harness fieldId="hasWebInterface" initial={{ hasWebInterface: false }} onApply={onApply} />);
    await user.click(screen.getByRole('radio', { name: 'Any' }));
    expect(onApply).toHaveBeenLastCalledWith({});
  });
});
