/**
 * R10 — a host answering on every port: the Services table mounts its rows a
 * step at a time (10,000 rows took 4.4 s to mount; 65,535 ran out of memory).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getHostWebInterfaces: vi.fn(() => Promise.resolve([])),
  getHostNetexecResults: vi.fn(() => Promise.resolve([])),
  getHostWebPaths: vi.fn(() => Promise.resolve([])),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import PortDetailsCard, { PORT_ROWS_STEP } from '../../components/host-inspector/PortDetailsCard';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { Port } from '../../services/api';

const ports = (count: number, state: string, from = 1): Port[] =>
  Array.from({ length: count }, (_, i) => ({
    id: from + i, port_number: from + i, protocol: 'tcp', state, service_name: 'http',
  }) as Port);

const renderCard = (open: Port[], closed: Port[] = []) =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <PortDetailsCard hostId={1} hostIp="10.0.0.5" openPorts={open} closedPorts={closed} filteredPorts={[]}
          connectionHelpersByPort={new Map()} />
      </TooltipProvider>
    </MemoryRouter>,
  );

const bodyRows = (table: HTMLElement) => table.querySelectorAll('tbody tr').length;

describe('PortDetailsCard — rows mounted a step at a time', () => {
  it('mounts the first step of open ports, in port order, and adds a step per "Show more"', () => {
    const total = PORT_ROWS_STEP * 2 + 50;
    renderCard(ports(total, 'open').reverse());
    const [table] = screen.getAllByRole('table');
    expect(bodyRows(table)).toBe(PORT_ROWS_STEP);
    expect(screen.getByTestId('port-rows-more')).toHaveTextContent(`Showing ${PORT_ROWS_STEP} of ${total} open ports.`);
    // The lowest ports first: the step is of the sorted list, not the order received.
    expect(table.querySelector('tbody tr')).toHaveTextContent('1/tcp');

    fireEvent.click(screen.getByRole('button', { name: `Show ${PORT_ROWS_STEP} more` }));
    expect(bodyRows(table)).toBe(PORT_ROWS_STEP * 2);
    fireEvent.click(screen.getByRole('button', { name: 'Show 50 more' }));
    expect(bodyRows(table)).toBe(total);
    expect(screen.queryByTestId('port-rows-more')).not.toBeInTheDocument();
  });

  it('a host with few ports shows them all and no line', () => {
    renderCard(ports(3, 'open'));
    expect(bodyRows(screen.getAllByRole('table')[0])).toBe(3);
    expect(screen.queryByTestId('port-rows-more')).not.toBeInTheDocument();
  });

  it('the closed and filtered table is stepped too', () => {
    renderCard(ports(1, 'open'), ports(PORT_ROWS_STEP + 1, 'closed', 100));
    fireEvent.click(screen.getByRole('button', { name: /closed · show/ }));
    const tables = screen.getAllByRole('table');
    expect(bodyRows(tables[tables.length - 1])).toBe(PORT_ROWS_STEP);
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 more' }));
    expect(bodyRows(tables[tables.length - 1])).toBe(PORT_ROWS_STEP + 1);
  });
});
