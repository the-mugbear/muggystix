/**
 * The finding page's endpoint list as a bounded panel: pinned controls,
 * compact rows, removal in the row's menu, rows under their network (the
 * server's segment), the state bar, and a bounded number of mounted rows.
 */
import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  setFindingEndpointStatus: vi.fn(),
  setFindingEndpointsStatus: vi.fn(),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// A native <select> stands in for the Radix one (as in FindingEndpoints.test).
vi.mock('../../components/ui/select', () => {
  type SelectProps = { value?: string; disabled?: boolean; onValueChange?: (v: string) => void; children?: React.ReactNode };
  const labelOf = (children: React.ReactNode): string | undefined => {
    let found: string | undefined;
    React.Children.forEach(children, (child) => {
      if (React.isValidElement(child) && (child.props as Record<string, unknown>)['aria-label']) {
        found = (child.props as Record<string, string>)['aria-label'];
      }
    });
    return found;
  };
  return {
    Select: ({ value, disabled, onValueChange, children }: SelectProps) => (
      <select aria-label={labelOf(children)} value={value ?? ''} disabled={disabled}
        onChange={(e) => onValueChange?.(e.target.value)}>
        <option value="" />
        {['open', 'retest', 'remediated', 'false_positive'].map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: () => null,
    SelectItem: () => null,
  };
});

import FindingEndpoints from '../../components/findings/FindingEndpoints';
import type { Finding, FindingEndpointSegment, FindingHostInfo, FindingHostStatus } from '../../services/api';
import { ENDPOINT_MOUNT_MAX } from '../../utils/findingEndpoints';

const scrollIntoView = vi.fn();
Element.prototype.scrollIntoView = scrollIntoView;

const seg = (order: number, label: string, kind: FindingEndpointSegment['kind'] = 'site'): FindingEndpointSegment =>
  ({ key: kind === 'site' ? String(order + 1) : `${kind}:${order}`, label, kind, order });
const HQ = seg(0, 'HQ');
const LAB = seg(1, 'Lab');
const DMZ = seg(2, 'DMZ');

let nextId = 1;
const host = (
  ip: string, segment: FindingEndpointSegment | null, host_status: FindingHostStatus = 'open',
  over: Partial<FindingHostInfo> = {},
): FindingHostInfo => {
  const id = nextId++;
  return { id, host_id: id, ip_address: ip, hostname: null, host_status, segment, ...over };
};
const finding = (hosts: FindingHostInfo[]): Finding => {
  const counts: Record<string, number> = {};
  hosts.forEach((h) => { counts[h.host_status] = (counts[h.host_status] ?? 0) + 1; });
  return { id: 7, title: 'F', hosts, host_count: hosts.length, endpoint_status_counts: counts } as unknown as Finding;
};

const onRemove = vi.fn();
const Harness: React.FC<{ initial: Finding; focus?: number | null; canManage?: boolean }> = ({
  initial, focus = null, canManage = true,
}) => {
  const [current, setCurrent] = useState(initial);
  return (
    <MemoryRouter>
      <FindingEndpoints finding={current} canManage={canManage} focusEndpointId={focus} onRemove={onRemove} onChanged={setCurrent} />
    </MemoryRouter>
  );
};

const rows = () => [...document.querySelectorAll('[data-endpoint-row]')];
const rowIps = () => rows().map((tr) => tr.querySelector('a')?.textContent);
const groupRow = (label: string) => {
  const toggle = screen.getByRole('button', { name: label });
  return toggle.closest('tr') as HTMLElement;
};
const bulkBar = () => screen.queryByRole('group', { name: 'Set the selected endpoints' });
const chip = (name: RegExp) =>
  within(screen.getByRole('group', { name: 'Endpoint state filter' })).getByRole('button', { name });

/** Three networks, 6 endpoints: small enough that every network starts open. */
const small = () => finding([
  host('10.2.0.5', LAB), host('10.1.0.9', HQ, 'remediated'), host('10.1.0.10', HQ),
  host('10.1.0.2', HQ, 'retest'), host('10.2.0.1', LAB, 'remediated'), host('10.3.0.1', DMZ, 'false_positive'),
]);
/** Two networks, 60 endpoints: over the limit, so every network starts closed. */
const large = () => finding([
  ...Array.from({ length: 40 }, (_, i) => host(`10.1.0.${i + 1}`, HQ, i < 10 ? 'remediated' : 'open')),
  ...Array.from({ length: 20 }, (_, i) => host(`10.2.0.${i + 1}`, LAB)),
]);

beforeEach(() => {
  vi.clearAllMocks();
  nextId = 1;
});

describe('FindingEndpoints — a bounded panel with pinned controls', () => {
  it('scrolls the rows inside the panel, with the chips, filter and bulk bar above them and the count below', () => {
    render(<Harness initial={finding(Array.from({ length: 150 }, (_, i) => host(`10.0.${Math.floor(i / 200)}.${i + 1}`, HQ)))} />);
    const body = screen.getByTestId('endpoints-body');
    const toolbar = screen.getByTestId('endpoints-toolbar');
    expect(body.className).toMatch(/max-h-\[26rem\]/);
    expect(body.className).toMatch(/overflow-auto/);
    // The rows are in the scrolling body; the controls are not.
    expect(body.querySelectorAll('[data-endpoint-row]')).toHaveLength(100);
    expect(body.contains(toolbar)).toBe(false);
    expect(within(toolbar).getByRole('group', { name: 'Endpoint state filter' })).toBeInTheDocument();
    expect(within(toolbar).getByLabelText('Filter endpoints by address or name')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Select 10.0.0.1'));
    expect(toolbar.contains(bulkBar())).toBe(true);
    const cut = screen.getByTestId('endpoints-cut');
    expect(cut.textContent).toContain('Showing 100 of 150 endpoints.');
    expect(body.contains(cut)).toBe(false);
    expect(screen.getByTestId('endpoints-panel').contains(cut)).toBe(true);
    // The header stays while the rows scroll under it.
    expect(screen.getByRole('columnheader', { name: 'Address' }).className).toMatch(/sticky top-0/);
  });

  it('a small finding is the same panel with no footer and nothing to scroll to', () => {
    render(<Harness initial={finding([host('10.0.0.1', HQ), host('10.0.0.2', HQ), host('10.0.0.3', HQ)])} />);
    expect(rows()).toHaveLength(3);
    expect(screen.getByTestId('endpoints-body')).toBeInTheDocument();
    expect(screen.queryByTestId('endpoints-cut')).toBeNull();
    expect(screen.queryByRole('button', { name: /Open all networks/ })).toBeNull();
  });
});

describe('FindingEndpoints — compact rows', () => {
  it('a row is a tick, the address, the name and the state; removal is in its menu', async () => {
    const user = userEvent.setup();
    const f = finding([host('10.0.0.5', HQ, 'open', { hostname: 'web2', fqdn: 'portal.example.com', name_id: 4 })]);
    render(<Harness initial={f} />);
    const [row] = rows() as HTMLElement[];
    expect(within(row).getByLabelText('Select portal.example.com on 10.0.0.5')).toBeInTheDocument();
    const address = within(row).getByRole('link', { name: '10.0.0.5' });
    expect(address).toHaveAttribute('href', '/hosts/1');
    expect(address.className).toMatch(/font-mono/);
    expect(within(row).getByRole('link', { name: 'portal.example.com' })).toHaveAttribute('href', '/names?name_id=4');
    expect(within(row).getByText('web2')).toBeInTheDocument();
    expect(within(row).getByLabelText('State of portal.example.com on 10.0.0.5')).toHaveValue('open');
    // No destructive control sits on the row itself.
    expect(within(row).queryByRole('button', { name: /Remove|Detach/ })).toBeNull();

    await user.click(within(row).getByRole('button', { name: 'Actions for portal.example.com on 10.0.0.5' }));
    await user.click(await screen.findByRole('menuitem', { name: /Remove from finding/ }));
    await waitFor(() => expect(onRemove).toHaveBeenCalledWith(f.hosts[0]));
  });

  it('a 200-character name is cut with its full text on hover, and a missing one is a dash', () => {
    const long = `${'a'.repeat(190)}.internal`;
    render(<Harness initial={finding([host('2001:db8:85a3:8d3:1319:8a2e:370:7348', HQ, 'open', { hostname: long }), host('10.0.0.2', HQ)])} />);
    const name = screen.getByText(long);
    const cell = name.parentElement as HTMLElement;
    expect(cell.className).toMatch(/truncate/);
    expect(cell).toHaveAttribute('title', long);
    const address = screen.getByRole('link', { name: '2001:db8:85a3:8d3:1319:8a2e:370:7348' });
    expect(address.className).toMatch(/truncate/);
    expect(address).toHaveAttribute('title', '2001:db8:85a3:8d3:1319:8a2e:370:7348');
    // (IPv4 sorts before IPv6: the unnamed 10.0.0.2 is the first row.)
    expect(within(rows()[0] as HTMLElement).getByText('—')).toBeInTheDocument();
    // The table cannot be widened by its content.
    expect(screen.getByRole('table').className).toMatch(/table-fixed/);
  });
});

describe('FindingEndpoints — rows under their network', () => {
  it('lists each network in the server’s order with its count and states, rows by address inside', () => {
    render(<Harness initial={small()} />);
    const headers = [...document.querySelectorAll('[data-endpoint-group]')] as HTMLElement[];
    expect(headers.map((tr) => within(tr).getByRole('button', { expanded: true }).textContent)).toEqual(['HQ', 'Lab', 'DMZ']);
    expect(within(groupRow('HQ')).getByTestId('group-count')).toHaveTextContent('3 endpoints');
    expect(within(groupRow('HQ')).getByTestId('group-states'))
      .toHaveTextContent('Still present 1 · Retest 1 · Remediated 1');
    expect(within(groupRow('DMZ')).getByTestId('group-count')).toHaveTextContent('1 endpoint');
    expect(within(groupRow('DMZ')).getByTestId('group-states')).toHaveTextContent('False positive 1');
    // Network by network, numerically by address inside each.
    expect(rowIps()).toEqual(['10.1.0.2', '10.1.0.9', '10.1.0.10', '10.2.0.1', '10.2.0.5', '10.3.0.1']);
  });

  it('one network is a flat list: no header, no network controls', () => {
    render(<Harness initial={finding([host('10.1.0.2', HQ), host('10.1.0.1', HQ)])} />);
    expect(document.querySelector('[data-endpoint-group]')).toBeNull();
    expect(rowIps()).toEqual(['10.1.0.1', '10.1.0.2']);
    expect(screen.queryByRole('button', { name: /Open all networks/ })).toBeNull();
  });

  it('endpoints with no segment (an older response) are a flat list too', () => {
    render(<Harness initial={finding([host('10.1.0.2', null), host('10.9.0.1', null)])} />);
    expect(document.querySelector('[data-endpoint-group]')).toBeNull();
    expect(rows()).toHaveLength(2);
  });

  it('a filter hides a network with no match and counts the matches beside the total', () => {
    render(<Harness initial={small()} />);
    fireEvent.click(chip(/Remediated/));
    expect(screen.queryByRole('button', { name: 'DMZ' })).toBeNull();
    expect(within(groupRow('HQ')).getByTestId('group-count')).toHaveTextContent('1 of 3 endpoints');
    expect(within(groupRow('Lab')).getByTestId('group-count')).toHaveTextContent('1 of 2 endpoints');
    expect(rowIps()).toEqual(['10.1.0.9', '10.2.0.1']);
    fireEvent.change(screen.getByLabelText('Filter endpoints by address or name'), { target: { value: '10.2.' } });
    expect(screen.queryByRole('button', { name: 'HQ' })).toBeNull();
    expect(rowIps()).toEqual(['10.2.0.1']);
  });

  it('a network’s tick selects only its rows that match the current filter', () => {
    render(<Harness initial={small()} />);
    const tick = () => screen.getByLabelText('Select every endpoint in HQ');
    fireEvent.click(screen.getByLabelText('Select 10.1.0.9'));
    expect(tick()).toHaveAttribute('aria-checked', 'mixed');
    fireEvent.click(tick());   // a partial selection → the rest of the network
    expect(tick()).toHaveAttribute('aria-checked', 'true');
    expect(bulkBar()).toHaveTextContent('3 selected');
    expect(screen.getByLabelText('Select 10.2.0.1')).not.toBeChecked();
    fireEvent.click(tick());
    expect(bulkBar()).toBeNull();

    // Under a filter the tick is the network's MATCHING rows, not all of them.
    fireEvent.click(chip(/Still present/));
    fireEvent.click(tick());
    expect(bulkBar()).toHaveTextContent('1 selected');
    expect(screen.getByLabelText('Select 10.1.0.10')).toBeChecked();
  });

  it('a long list starts with every network closed and mounts no row until one is opened', () => {
    render(<Harness initial={large()} />);
    expect(rows()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'HQ' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: 'Lab' })).toHaveAttribute('aria-expanded', 'false');
    expect(within(groupRow('HQ')).getByTestId('group-states')).toHaveTextContent('Still present 30 · Remediated 10');
    expect(screen.getByTestId('endpoints-cut').textContent).toContain('Showing 0 of 60 endpoints. 60 in closed networks.');

    fireEvent.click(screen.getByRole('button', { name: 'Lab' }));
    expect(screen.getByRole('button', { name: 'Lab' })).toHaveAttribute('aria-expanded', 'true');
    expect(rows()).toHaveLength(20);
    expect(rowIps()[0]).toBe('10.2.0.1');
    fireEvent.click(screen.getByRole('button', { name: 'Lab' }));
    expect(rows()).toHaveLength(0);

    // A closed network can still be selected as a whole.
    fireEvent.click(screen.getByLabelText('Select every endpoint in HQ'));
    expect(bulkBar()).toHaveTextContent('40 selected');

    fireEvent.click(screen.getByRole('button', { name: 'Open all networks' }));
    expect(rows()).toHaveLength(60);
    fireEvent.click(screen.getByRole('button', { name: 'Close all' }));
    expect(rows()).toHaveLength(0);
  });

  it('a filter that leaves a short list opens the networks, and a network the reader closed stays closed', () => {
    render(<Harness initial={large()} />);
    fireEvent.click(chip(/Remediated/));          // 10 rows: short enough to open
    expect(rows()).toHaveLength(10);
    fireEvent.click(screen.getByRole('button', { name: 'HQ' }));
    expect(rows()).toHaveLength(0);
    fireEvent.click(chip(/Remediated/));          // filter off: 60 rows, closed again by size
    fireEvent.click(chip(/Remediated/));
    expect(screen.getByRole('button', { name: 'HQ' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('a long network label is cut in its header', () => {
    const label = `Building ${'north wing '.repeat(20)}`.trim();
    render(<Harness initial={finding([host('10.1.0.1', seg(0, label)), host('10.2.0.1', LAB)])} />);
    const text = screen.getByTitle(label);
    expect(text.className).toMatch(/truncate/);
  });
});

describe('FindingEndpoints — the state bar', () => {
  it('draws one part per state present, each a button that applies that state’s filter', () => {
    render(<Harness initial={small()} />);
    const bar = screen.getByRole('group', { name: 'Endpoint states' });
    expect(within(bar).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      '2 still present — filter', '1 retest — filter', '2 remediated — filter', '1 false positive — filter',
    ]);
    const remediated = within(bar).getByRole('button', { name: '2 remediated — filter' });
    expect(remediated).toHaveAttribute('title', '2 remediated — filter');
    expect(remediated.style.flexGrow).toBe('2');
    // Remediated and false positive differ by more than hue.
    expect(within(bar).getByRole('button', { name: '1 false positive — filter' }).className).toMatch(/repeating-linear-gradient/);
    expect(remediated.className).toMatch(/bg-success/);

    fireEvent.click(remediated);
    expect(chip(/Remediated/)).toHaveAttribute('aria-pressed', 'true');
    expect(remediated).toHaveAttribute('aria-pressed', 'true');
    expect(rowIps()).toEqual(['10.1.0.9', '10.2.0.1']);
    fireEvent.click(remediated);   // the chip's own toggle
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true');
    expect(rows()).toHaveLength(6);
  });

  it('sits beside the sentence, which stays the one explanation', () => {
    render(<Harness initial={small()} />);
    expect(screen.getByText(/The status above is the issue/)).toBeInTheDocument();
    expect(screen.getByText('still present on 2 of 6 · 2 remediated · 1 retest · 1 false positive there')).toBeInTheDocument();
    // No legend: the chips already name the states.
    expect(screen.getByRole('group', { name: 'Endpoint states' }).textContent).toBe('');
  });
});

describe('FindingEndpoints — a linked endpoint in a closed network', () => {
  it('opens its network, mounts its row and brings the panel into view once', () => {
    const f = large();
    const target = f.hosts.find((h) => h.ip_address === '10.2.0.15')!;
    render(<Harness initial={f} focus={target.id} />);
    expect(document.querySelector(`[data-endpoint-row="${target.id}"]`)).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Lab' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'HQ' })).toHaveAttribute('aria-expanded', 'false');
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    // The PANEL is what the page scrolls to; the row is scrolled inside it.
    expect(scrollIntoView.mock.instances[0]).toBe(document.getElementById('endpoints'));
    fireEvent.click(screen.getByRole('button', { name: 'HQ' }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });
});

describe('FindingEndpoints — 2,000 endpoints', () => {
  const flat = () => finding(Array.from({ length: 2000 }, (_, i) =>
    host(`10.${Math.floor(i / 60000)}.${Math.floor(i / 250)}.${(i % 250) + 1}`, HQ)));

  it('mounts 100 rows, more on request, and never more than the cap', () => {
    render(<Harness initial={flat()} />);
    expect(rows()).toHaveLength(100);
    expect(screen.getByTestId('endpoints-cut').textContent).toContain('Showing 100 of 2,000 endpoints.');
    // "Show all" is offered only when all of them fit under the cap.
    expect(screen.queryByRole('button', { name: /^Show all/ })).toBeNull();
    for (let i = 0; i < 6; i += 1) {
      fireEvent.click(screen.getByRole('button', { name: 'Show 100 more' }));
      expect(rows().length).toBeLessThanOrEqual(ENDPOINT_MOUNT_MAX);
    }
    // 700 asked for: the window moved on instead of growing past the cap.
    expect(rows()).toHaveLength(ENDPOINT_MOUNT_MAX);
    expect(screen.getByTestId('endpoints-cut').textContent).toContain('Showing 201–700 of 2,000 endpoints.');
    fireEvent.click(screen.getByRole('button', { name: 'Show 100 previous' }));
    expect(screen.getByTestId('endpoints-cut').textContent).toContain('Showing 101–600 of 2,000 endpoints.');
    expect(rows()).toHaveLength(ENDPOINT_MOUNT_MAX);
  }, 30000);   // seven renders of up to 500 rows in jsdom, beside the rest of the suite

  it('a linked endpoint far down the list is mounted without mounting the rest', () => {
    const f = flat();
    render(<Harness initial={f} focus={f.hosts[1800].id} />);
    expect(document.querySelector(`[data-endpoint-row="${f.hosts[1800].id}"]`)).not.toBeNull();
    expect(rows().length).toBeLessThanOrEqual(ENDPOINT_MOUNT_MAX);
  });

  it('across ten networks it mounts headers only', () => {
    const nets = Array.from({ length: 10 }, (_, n) => seg(n, `Site ${n + 1}`));
    render(<Harness initial={finding(Array.from({ length: 2000 }, (_, i) =>
      host(`10.${i % 10}.${Math.floor(i / 2500)}.${Math.floor(i / 10) % 250}`, nets[i % 10])))} />);
    expect(rows()).toHaveLength(0);
    expect(document.querySelectorAll('[data-endpoint-group]')).toHaveLength(10);
    fireEvent.click(screen.getByRole('button', { name: 'Open all networks' }));
    expect(rows()).toHaveLength(100);
  });
});

describe('FindingEndpoints — a reader', () => {
  it('reads every row, its network and its state, with no write control', () => {
    render(<Harness initial={small()} canManage={false} />);
    expect(rows()).toHaveLength(6);
    expect(screen.getAllByText('Remediated here')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'HQ' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: /Actions for/ })).toBeNull();
    // Reading controls stay: the filter, the chips, the bar, a network's toggle.
    fireEvent.click(screen.getByRole('button', { name: '2 remediated — filter' }));
    expect(rows()).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'HQ' }));
    expect(rows()).toHaveLength(1);
  });
});
