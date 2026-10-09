/**
 * A scan row's expanded detail (code review 2026-10-09): a failed read of the
 * argument analysis is said, with Retry — it showed "Loading…" for ever — and
 * opening the row again asks again, although the answer is otherwise
 * remembered for the visit.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { ScanCommandDetail } from '../../pages/Scans';
import type { Scan } from '../../services/api';

const { getScanCommandExplanation } = vi.hoisted(() => ({ getScanCommandExplanation: vi.fn() }));
// The Scans page imports much of the barrel; this component calls one function.
vi.mock('../../services/api', () => new Proxy({ getScanCommandExplanation } as Record<string, unknown>, {
  get: (target, name: string) => (name in target ? target[name] : name === 'then' ? undefined : vi.fn()),
  has: () => true,
}));

const scan = { id: 7, command_line: 'nmap -sV 10.0.0.0/24', tool_name: 'nmap' } as unknown as Scan;
const answer = {
  has_command: true,
  arguments: [{ arg: '-sV', description: 'Probe open ports for service and version', category: 'detection' }],
};

beforeEach(() => getScanCommandExplanation.mockReset());

describe('ScanCommandDetail', () => {
  it('says a failed read, with Retry, and recovers', async () => {
    getScanCommandExplanation.mockRejectedValueOnce(new Error('503')).mockResolvedValue(answer);
    render(<ScanCommandDetail scan={scan} />);
    expect(screen.getByText('Loading argument analysis…')).toBeInTheDocument();

    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t load the argument analysis.');
    expect(screen.queryByText('Loading argument analysis…')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/Arguments \(1\)/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(getScanCommandExplanation).toHaveBeenCalledTimes(2);
  });

  it('asks again when the row is opened again after a failure, and not after an answer', async () => {
    // One client for the whole test, as on the page: the read is remembered.
    const client = createQueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    getScanCommandExplanation.mockRejectedValueOnce(new Error('503')).mockResolvedValue(answer);

    const first = render(<ScanCommandDetail scan={scan} />, { wrapper });
    await screen.findByRole('alert');
    first.unmount();                                   // the row is closed

    const second = render(<ScanCommandDetail scan={scan} />, { wrapper });   // …and opened again
    expect(await screen.findByText(/Arguments \(1\)/)).toBeInTheDocument();
    expect(getScanCommandExplanation).toHaveBeenCalledTimes(2);
    second.unmount();

    render(<ScanCommandDetail scan={scan} />, { wrapper });
    expect(await screen.findByText(/Arguments \(1\)/)).toBeInTheDocument();
    await waitFor(() => expect(getScanCommandExplanation).toHaveBeenCalledTimes(2));
  });
});
