import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import HostConflictsPanel, { keptSide } from '../../components/host-inspector/HostConflictsPanel';
import type { ConflictHistoryEntry, HostConflict } from '../../services/api';

const entry = (over: Partial<ConflictHistoryEntry> = {}): ConflictHistoryEntry => ({
  id: 1,
  object_type: 'host',
  object_id: 7,
  field_name: 'os_name',
  previous_value: 'Microsoft Windows Server 2022',
  previous_confidence: null,
  previous_scan_id: 139,
  previous_method: null,
  new_value: 'Windows 11',
  new_confidence: null,
  new_scan_id: 143,
  new_method: null,
  resolved_at: '2026-09-10T02:38:13Z',
  previous_scan_filename: 'internal-sweep.xml',
  new_scan_filename: 'netexec-smb.txt',
  current_value: 'Microsoft Windows Server 2022',
  ...over,
});

const renderPanel = (history: ConflictHistoryEntry[], confidence: HostConflict[] = [], count = history.length) =>
  render(
    <MemoryRouter>
      <HostConflictsPanel
        id="host-detail-conflicts"
        conflictCount={count}
        history={history}
        confidence={confidence}
        ports={[{ id: 55, port_number: 443, protocol: 'tcp' }]}
      />
    </MemoryRouter>,
  );

describe('HostConflictsPanel', () => {
  // The defect: every conflict the dedup service writes has NO confidence
  // record, and the old panel only rendered history under a confidence
  // heading — so "1 conflict" opened a panel that named no conflict.
  it('states a disagreement that has no confidence record', () => {
    renderPanel([entry()], []);
    const list = screen.getByRole('list', { name: 'Host conflicts' });
    expect(within(list).getByText('Operating system')).toBeInTheDocument();
    expect(within(list).getByText('Microsoft Windows Server 2022')).toBeInTheDocument();
    expect(within(list).getByText('Windows 11')).toBeInTheDocument();
  });

  it('links each side to its scan by filename', () => {
    renderPanel([entry()]);
    expect(screen.getByRole('link', { name: 'internal-sweep.xml' })).toHaveAttribute('href', '/scans/139');
    expect(screen.getByRole('link', { name: 'netexec-smb.txt' })).toHaveAttribute('href', '/scans/143');
  });

  it('marks the value the host shows today — a recorded conflict is not always adopted', () => {
    renderPanel([entry()]);
    const held = screen.getByText('Microsoft Windows Server 2022').parentElement as HTMLElement;
    expect(within(held).getByText('shown')).toBeInTheDocument();
    const reported = screen.getByText('Windows 11').parentElement as HTMLElement;
    expect(within(reported).queryByText('shown')).toBeNull();
  });

  it('says so when a later scan changed the value again', () => {
    renderPanel([entry({ current_value: 'Windows Server 2025' })]);
    expect(screen.getByText('Windows Server 2025')).toBeInTheDocument();
    expect(screen.queryByText('shown')).toBeNull();
  });

  it('names the port for a port-level disagreement and keeps it out of the counted list', () => {
    renderPanel(
      [entry(), entry({ id: 2, object_type: 'port', object_id: 55, field_name: 'service_name', current_value: null })],
      [],
      1,
    );
    expect(within(screen.getByRole('list', { name: 'Host conflicts' })).getAllByRole('listitem')).toHaveLength(1);
    expect(within(screen.getByRole('list', { name: 'Port conflicts' })).getByText('443/tcp · service name')).toBeInTheDocument();
  });

  it('admits when the count exceeds what could be listed', () => {
    renderPanel([], [], 3);
    expect(screen.getByText(/3 recorded, but the detail could not be listed/)).toBeInTheDocument();
  });

  it('keeps the source ranking behind a disclosure', () => {
    const conf: HostConflict = {
      id: 9, field_name: 'os_name', confidence_score: 95, scan_type: 'nmap',
      data_source: 'nmap', method: 'os_detection', scan_id: 139, updated_at: '2026-09-10T02:38:13Z',
    };
    renderPanel([entry()], [conf]);
    expect(screen.queryByText('weight 95')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /How sources are ranked/ }));
    expect(screen.getByText('weight 95')).toBeInTheDocument();
  });

  it('keptSide is null when the current value is unknown', () => {
    expect(keptSide(entry({ current_value: null }))).toBeNull();
    expect(keptSide(entry({ current_value: 'Windows 11' }))).toBe('new');
  });
});
