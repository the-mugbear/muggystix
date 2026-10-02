/**
 * Review 2026-10-01, branch findings on the finding page's endpoint table:
 *   S3 — what the bulk bar acts on is always among the rows the filter shows;
 *   S7 — a linked endpoint is scrolled to once;
 *   M3 — shift-click reads its anchor before it moves it;
 *   M4 / M5 — in-flight rows are locked, responses apply in the order sent,
 *             and a bulk request leaves a later selection alone.
 */
import React, { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const setFindingEndpointStatus = vi.fn();
const setFindingEndpointsStatus = vi.fn();
vi.mock('../../services/api', () => ({
  setFindingEndpointStatus: (...a: unknown[]) => setFindingEndpointStatus(...a),
  setFindingEndpointsStatus: (...a: unknown[]) => setFindingEndpointsStatus(...a),
}));
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
// A native <select> stands in for the Radix one: the table's logic is under
// test, not the widget.
vi.mock('../../components/ui/select', () => {
  type SelectProps = {
    value?: string; disabled?: boolean; onValueChange?: (v: string) => void; children?: React.ReactNode;
  };
  const Ctx = React.createContext<{ label?: string; id?: string }>({});
  const labelOf = (children: React.ReactNode): { label?: string; id?: string } => {
    let found: { label?: string; id?: string } = {};
    React.Children.forEach(children, (child) => {
      if (React.isValidElement(child) && (child.props as Record<string, unknown>)['aria-label']) {
        const props = child.props as Record<string, string>;
        found = { label: props['aria-label'], id: props.id };
      }
    });
    return found;
  };
  const options = (children: React.ReactNode): React.ReactNode[] => {
    const out: React.ReactNode[] = [];
    React.Children.forEach(children, (child) => {
      if (!React.isValidElement(child)) return;
      const props = child.props as { value?: string; children?: React.ReactNode };
      if (props.value !== undefined) out.push(<option key={props.value} value={props.value}>{props.children}</option>);
      else out.push(...options(props.children));
    });
    return out;
  };
  return {
    Select: ({ value, disabled, onValueChange, children }: SelectProps) => {
      const { label, id } = labelOf(children);
      return (
        <Ctx.Provider value={{ label, id }}>
          <select aria-label={label} id={id} value={value ?? ''} disabled={disabled}
            onChange={(e) => onValueChange?.(e.target.value)}>
            <option value="" />
            {options(children)}
          </select>
        </Ctx.Provider>
      );
    },
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    SelectItem: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  };
});

import FindingEndpoints from '../../components/findings/FindingEndpoints';
import type { Finding, FindingHostInfo, FindingHostStatus } from '../../services/api';

const scrollIntoView = vi.fn();
Element.prototype.scrollIntoView = scrollIntoView;

const endpoint = (id: number, host_status: FindingHostStatus = 'open'): FindingHostInfo => ({
  id, host_id: id, ip_address: `10.0.0.${id}`, hostname: null, host_status,
});
const finding = (hosts: FindingHostInfo[]): Finding => ({
  id: 7, title: 'F', hosts, host_count: hosts.length,
} as unknown as Finding);
const withState = (f: Finding, changes: Record<number, FindingHostStatus>): Finding => ({
  ...f, hosts: f.hosts.map((h) => (changes[h.id] ? { ...h, host_status: changes[h.id] } : h)),
});

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
const deferred = <T,>(): Deferred<T> => {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

const onChanged = vi.fn();
const onRemove = vi.fn();
let setFromOutside: (f: Finding) => void = () => undefined;

const Harness: React.FC<{ initial: Finding; focus?: number | null }> = ({ initial, focus = null }) => {
  const [current, setCurrent] = useState(initial);
  setFromOutside = setCurrent;
  return (
    <MemoryRouter>
      <FindingEndpoints
        finding={current} canManage focusEndpointId={focus} onRemove={onRemove}
        onChanged={(f) => { onChanged(f); setCurrent(f); }}
      />
    </MemoryRouter>
  );
};

const box = (id: number) => screen.getByLabelText(`Select 10.0.0.${id}`);
const stateOf = (id: number) => screen.getByLabelText(`State of 10.0.0.${id}`) as HTMLSelectElement;
const bar = () => screen.queryByRole('group', { name: 'Set the selected endpoints' });
const chip = (name: RegExp) =>
  within(screen.getByRole('group', { name: 'Endpoint state filter' })).getByRole('button', { name });

beforeEach(() => {
  vi.clearAllMocks();
});

// Browser pass 2026-10-01 — the server answered a change with the changed row
// LAST, and the table drew the response order: rows jumped under the reader.
describe('FindingEndpoints — the rows keep their order', () => {
  const rowOrder = () => [...document.querySelectorAll('[data-endpoint-row]')]
    .map((tr) => Number(tr.getAttribute('data-endpoint-row')));
  const named = (id: number, ip: string | null, over: Partial<FindingHostInfo> = {}): FindingHostInfo => ({
    id, host_id: id, ip_address: ip, hostname: null, host_status: 'open', ...over,
  });

  it('lists by address — numerically — then by name, whatever order the server sent', () => {
    render(<Harness initial={finding([
      named(1, '10.0.0.10'), named(2, '10.0.0.9'), named(3, null, { hostname: 'orphan' }),
      named(4, '10.0.0.9', { fqdn: 'b.example.com' }), named(5, '10.0.0.9', { fqdn: 'a.example.com' }),
      named(6, '2001:db8::1'), named(7, '9.0.0.200'),
    ])} />);
    expect(rowOrder()).toEqual([7, 2, 5, 4, 1, 6, 3]);
  });

  it('a single change and a bulk change leave every row where it was', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3), endpoint(4)]);
    render(<Harness initial={start} />);
    expect(rowOrder()).toEqual([1, 2, 3, 4]);

    // The server's answer: the changed row moved to the end.
    const movedToEnd = (f: Finding, ids: number[], state: FindingHostStatus): Finding => ({
      ...f,
      hosts: [
        ...f.hosts.filter((h) => !ids.includes(h.id)),
        ...f.hosts.filter((h) => ids.includes(h.id)).map((h) => ({ ...h, host_status: state })),
      ],
    });
    const afterOne = movedToEnd(start, [2], 'remediated');
    setFindingEndpointStatus.mockResolvedValue(afterOne);
    fireEvent.change(stateOf(2), { target: { value: 'remediated' } });
    await waitFor(() => expect(stateOf(2).value).toBe('remediated'));
    expect(rowOrder()).toEqual([1, 2, 3, 4]);

    setFindingEndpointsStatus.mockResolvedValue(movedToEnd(afterOne, [1, 3], 'retest'));
    fireEvent.click(box(1));
    fireEvent.click(box(3));
    fireEvent.change(screen.getByLabelText('Set the selected endpoints to'), { target: { value: 'retest' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set 2 endpoints' }));
    await waitFor(() => expect(stateOf(3).value).toBe('retest'));
    expect(rowOrder()).toEqual([1, 2, 3, 4]);
  });

  it('a ticked row says it is selected', () => {
    render(<Harness initial={finding([endpoint(1), endpoint(2)])} />);
    const row = (id: number) => document.querySelector(`[data-endpoint-row="${id}"]`)!;
    expect(row(1)).toHaveAttribute('aria-selected', 'false');
    fireEvent.click(box(1));
    expect(row(1)).toHaveAttribute('data-state', 'selected');
    expect(row(1)).toHaveAttribute('aria-selected', 'true');
    expect(row(2)).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByLabelText('Select every endpoint shown')).toHaveAttribute('aria-checked', 'mixed');
  });

  it('a shift-click range follows the order on screen', () => {
    render(<Harness initial={finding([endpoint(4), endpoint(2), endpoint(3), endpoint(1)])} />);
    fireEvent.click(box(1));
    fireEvent.click(box(3), { shiftKey: true });
    expect(bar()).toHaveTextContent('3 selected');
    expect(box(2)).toBeChecked();
    expect(box(4)).not.toBeChecked();
  });
});

describe('FindingEndpoints — the selection is of rows the filter shows (S3)', () => {
  it('a row given its own state leaves the selection with the filter', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    render(<Harness initial={start} />);
    fireEvent.click(chip(/Still present/));
    fireEvent.click(box(1));
    fireEvent.click(box(2));
    expect(bar()).toHaveTextContent('2 selected');

    setFindingEndpointStatus.mockResolvedValue(withState(start, { 1: 'remediated' }));
    fireEvent.change(stateOf(1), { target: { value: 'remediated' } });
    await waitFor(() => expect(screen.queryByLabelText('Select 10.0.0.1')).toBeNull());
    expect(bar()).toHaveTextContent('1 selected');

    setFindingEndpointsStatus.mockResolvedValue(withState(start, { 1: 'remediated', 2: 'retest' }));
    fireEvent.change(screen.getByLabelText('Set the selected endpoints to'), { target: { value: 'retest' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set 1 endpoint' }));
    await waitFor(() => expect(setFindingEndpointsStatus).toHaveBeenCalledTimes(1));
    expect(setFindingEndpointsStatus.mock.calls[0][1].finding_host_ids).toEqual([2]);
    // …and the row is not ticked when the filter is taken off again.
    await waitFor(() => expect(bar()).toBeNull());
    fireEvent.click(chip(/All/));
    expect(box(1)).not.toBeChecked();
  });

  it('a ticked row someone else moved out of the filter is not acted on', () => {
    const start = finding([endpoint(1), endpoint(2)]);
    render(<Harness initial={start} />);
    fireEvent.click(chip(/Still present/));
    fireEvent.click(box(1));
    fireEvent.click(box(2));
    act(() => setFromOutside(withState(start, { 2: 'false_positive' })));
    expect(bar()).toHaveTextContent('1 selected');
    expect(screen.getByRole('button', { name: 'Set 1 endpoint' })).toBeInTheDocument();
  });
});

describe('FindingEndpoints — a linked endpoint (S7)', () => {
  it('is scrolled to once, not again on every filter keystroke or "Show more"', () => {
    const hosts = Array.from({ length: 150 }, (_, i) => endpoint(i + 1));
    render(<Harness initial={finding(hosts)} focus={3} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    const filter = screen.getByLabelText('Filter endpoints by address or name');
    fireEvent.change(filter, { target: { value: '10.0.0' } });
    fireEvent.change(filter, { target: { value: '10.0.0.' } });
    fireEvent.click(screen.getByRole('button', { name: /^Show 50 more$/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it('still brings a linked row past the cap into the table', () => {
    const hosts = Array.from({ length: 150 }, (_, i) => endpoint(i + 1));
    render(<Harness initial={finding(hosts)} focus={140} />);
    expect(document.querySelector('[data-endpoint-row="140"]')).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });
});

describe('FindingEndpoints — shift-click (M3)', () => {
  it('selects from the last row ticked to this one', () => {
    render(
      <React.StrictMode><Harness initial={finding([1, 2, 3, 4, 5].map((i) => endpoint(i)))} /></React.StrictMode>,
    );
    fireEvent.click(box(2));
    fireEvent.click(box(4), { shiftKey: true });
    expect(bar()).toHaveTextContent('3 selected');
    expect([1, 2, 3, 4, 5].filter((i) => (box(i) as HTMLInputElement).getAttribute('aria-checked') === 'true'))
      .toEqual([2, 3, 4]);
  });
});

describe('FindingEndpoints — requests in flight (M4 / M5)', () => {
  it('locks each changed row until its own request is back, and drops a response older than the last applied', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    const first = deferred<Finding>();
    const second = deferred<Finding>();
    setFindingEndpointStatus.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    render(<Harness initial={start} />);

    fireEvent.change(stateOf(1), { target: { value: 'retest' } });
    fireEvent.change(stateOf(2), { target: { value: 'remediated' } });
    expect(stateOf(1)).toBeDisabled();
    expect(stateOf(2)).toBeDisabled();
    expect(box(1)).toBeDisabled();
    expect(screen.getByLabelText('Detach 10.0.0.1 from finding')).toBeDisabled();
    expect(stateOf(3)).not.toBeDisabled();

    // The later request answers first, with both changes.
    await act(async () => { second.resolve(withState(start, { 1: 'retest', 2: 'remediated' })); });
    expect(stateOf(2)).not.toBeDisabled();
    expect(stateOf(1)).toBeDisabled();
    // The earlier one answers last, from before the second change.
    await act(async () => { first.resolve(withState(start, { 1: 'retest' })); });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(stateOf(2).value).toBe('remediated');
    expect(stateOf(1).value).toBe('retest');
    expect(stateOf(1)).not.toBeDisabled();
  });

  it('locks the rows during a bulk request and unticks only what it changed', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    const bulk = deferred<Finding>();
    setFindingEndpointsStatus.mockReturnValue(bulk.promise);
    render(<Harness initial={start} />);
    fireEvent.click(box(1));
    fireEvent.click(box(2));
    fireEvent.change(screen.getByLabelText('Set the selected endpoints to'), { target: { value: 'retest' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set 2 endpoints' }));
    await waitFor(() => expect(setFindingEndpointsStatus).toHaveBeenCalled());

    expect(box(3)).toBeDisabled();
    expect(screen.getByLabelText('Select every endpoint shown')).toBeDisabled();
    expect(screen.getByLabelText('Detach 10.0.0.3 from finding')).toBeDisabled();
    expect(stateOf(3)).toBeDisabled();

    await act(async () => { bulk.resolve(withState(start, { 1: 'retest', 2: 'retest' })); });
    expect(bar()).toBeNull();
    expect(box(3)).not.toBeDisabled();
  });
});
