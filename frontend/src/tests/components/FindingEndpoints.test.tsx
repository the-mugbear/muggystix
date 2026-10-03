/**
 * Review 2026-10-01, branch findings on the finding page's endpoint table:
 *   S3 — what the bulk bar acts on is always among the rows the filter shows;
 *   S7 — a linked endpoint is scrolled to once;
 *   M3 — shift-click reads its anchor before it moves it;
 *   M4 / M5 — in-flight rows are locked, and a bulk request leaves a later
 *             selection alone.
 * Review 2026-10-02 H2 — changes go to the server one at a time and every
 * answer is applied ("responses apply in the order sent" dropped an answer
 * whose request was sent first and committed last).
 */
import React, { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const setFindingEndpointStatus = vi.fn();
const setFindingEndpointsStatus = vi.fn();
const rejectProposal = vi.fn();
vi.mock('../../services/api', () => ({
  setFindingEndpointStatus: (...a: unknown[]) => setFindingEndpointStatus(...a),
  setFindingEndpointsStatus: (...a: unknown[]) => setFindingEndpointsStatus(...a),
  acceptProposal: vi.fn(),
  rejectProposal: (...a: unknown[]) => rejectProposal(...a),
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
import type { Finding, FindingHostInfo, FindingHostStatus, Proposal } from '../../services/api';

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
  it('locks each changed row until its own request is back, and sends the changes one at a time', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    const first = deferred<Finding>();
    const second = deferred<Finding>();
    setFindingEndpointStatus.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    render(<Harness initial={start} />);

    fireEvent.change(stateOf(1), { target: { value: 'retest' } });
    fireEvent.change(stateOf(2), { target: { value: 'remediated' } });
    // Both rows are busy from the click — the queued one too.
    expect(stateOf(1)).toBeDisabled();
    expect(stateOf(2)).toBeDisabled();
    expect(box(1)).toBeDisabled();
    expect(screen.getByLabelText('Detach 10.0.0.1 from finding')).toBeDisabled();
    expect(stateOf(3)).not.toBeDisabled();
    // The second change waits for the first one's answer.
    await waitFor(() => expect(setFindingEndpointStatus).toHaveBeenCalledTimes(1));
    expect(setFindingEndpointStatus).toHaveBeenLastCalledWith(7, 1, 'retest');

    await act(async () => { first.resolve(withState(start, { 1: 'retest' })); });
    expect(stateOf(1).value).toBe('retest');
    expect(stateOf(1)).not.toBeDisabled();
    expect(stateOf(2)).toBeDisabled();
    expect(setFindingEndpointStatus).toHaveBeenCalledTimes(2);
    expect(setFindingEndpointStatus).toHaveBeenLastCalledWith(7, 2, 'remediated');

    await act(async () => { second.resolve(withState(start, { 1: 'retest', 2: 'remediated' })); });
    // Every answer is applied: none is "older news" when they come in turn.
    expect(onChanged).toHaveBeenCalledTimes(2);
    expect(stateOf(2).value).toBe('remediated');
    expect(stateOf(1).value).toBe('retest');
    expect(stateOf(2)).not.toBeDisabled();
  });

  // Review 2026-10-02 H2.  A stand-in for the server: each request changes
  // the stored finding WHEN IT IS COMMITTED and answers with the finding as
  // it then stands — which is why the order requests were sent in says
  // nothing about which answer is the newest.
  const server = (start: Finding) => {
    let stored = start;
    const waiting: Array<{ label: string; commit: () => void; refuse: (e: unknown) => void }> = [];
    const receive = (label: string, changes: () => Record<number, FindingHostStatus>) => {
      const d = deferred<Finding>();
      waiting.push({
        label,
        commit: () => { stored = withState(stored, changes()); d.resolve(stored); },
        refuse: d.reject,
      });
      return d.promise;
    };
    setFindingEndpointStatus.mockImplementation((_f: number, id: number, state: FindingHostStatus) =>
      receive(`one:${id}`, () => ({ [id]: state })));
    setFindingEndpointsStatus.mockImplementation(
      (_f: number, body: { finding_host_ids: number[]; host_status: FindingHostStatus }) =>
        receive(`bulk:${body.finding_host_ids.join(',')}`,
          () => Object.fromEntries(body.finding_host_ids.map((id) => [id, body.host_status]))));
    /** Settle the request the server received LAST among those waiting. */
    const settleNewest = async (how: 'commit' | 'refuse' = 'commit') => {
      await waitFor(() => expect(waiting.length).toBeGreaterThan(0));
      const request = waiting.pop()!;
      await act(async () => {
        if (how === 'commit') request.commit();
        else request.refuse(new Error('refused'));
      });
      return request.label;
    };
    return { settleNewest, waiting, stored: () => stored };
  };

  it('shows every saved state when the server commits the later change first', async () => {
    const start = finding([endpoint(1), endpoint(2)]);
    const api = server(start);
    render(<Harness initial={start} />);
    fireEvent.change(stateOf(1), { target: { value: 'retest' } });         // A
    fireEvent.change(stateOf(2), { target: { value: 'remediated' } });     // B
    // The server takes the newest request it holds first.  Sent together (the
    // old code), B commits and answers with endpoint 1 still open, then A
    // answers with both — and was dropped as "sent earlier", leaving the
    // saved retest shown as open.  Sent one at a time, there is no such race.
    await api.settleNewest();
    await api.settleNewest();
    expect(api.waiting).toHaveLength(0);
    expect(api.stored().hosts.map((h) => h.host_status)).toEqual(['retest', 'remediated']);
    expect(stateOf(1).value).toBe('retest');
    expect(stateOf(2).value).toBe('remediated');
    expect(stateOf(1)).not.toBeDisabled();
    expect(stateOf(2)).not.toBeDisabled();
  });

  it('a refused change in the middle neither blocks nor loses the ones behind it', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    const api = server(start);
    render(<Harness initial={start} />);
    fireEvent.change(stateOf(1), { target: { value: 'retest' } });
    fireEvent.change(stateOf(2), { target: { value: 'remediated' } });
    fireEvent.change(stateOf(3), { target: { value: 'false_positive' } });

    expect(await api.settleNewest()).toBe('one:1');
    expect(await api.settleNewest('refuse')).toBe('one:2');
    // The refused row is said, unlocked and unchanged; the third is still on its way.
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(stateOf(2).value).toBe('open');
    expect(stateOf(2)).not.toBeDisabled();
    expect(stateOf(3)).toBeDisabled();
    expect(await api.settleNewest()).toBe('one:3');

    expect(stateOf(1).value).toBe('retest');
    expect(stateOf(2).value).toBe('open');
    expect(stateOf(3).value).toBe('false_positive');
    expect(stateOf(3)).not.toBeDisabled();
    expect(onChanged).toHaveBeenCalledTimes(2);
    expect(setFindingEndpointStatus).toHaveBeenCalledTimes(3);
  });

  it('a bulk change waits its turn behind a single change still on its way', async () => {
    const start = finding([endpoint(1), endpoint(2), endpoint(3)]);
    const api = server(start);
    render(<Harness initial={start} />);
    fireEvent.click(box(2));
    fireEvent.click(box(3));
    fireEvent.change(stateOf(1), { target: { value: 'retest' } });
    fireEvent.change(screen.getByLabelText('Set the selected endpoints to'), { target: { value: 'remediated' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set 2 endpoints' }));
    // Every row is locked while the bulk change is queued or in flight.
    expect(stateOf(2)).toBeDisabled();

    expect(await api.settleNewest()).toBe('one:1');
    expect(await api.settleNewest()).toBe('bulk:2,3');
    await waitFor(() => expect(bar()).toBeNull());
    expect([1, 2, 3].map((id) => stateOf(id).value)).toEqual(['retest', 'remediated', 'remediated']);
    expect(stateOf(2)).not.toBeDisabled();
    expect(toast.success).toHaveBeenCalledWith('Set 2 endpoints to remediated here.');
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

// 5.334.0 — an agent's endpoint change is decided on the row it would change.
describe('FindingEndpoints — a proposed endpoint change sits on its row', () => {
  const pr = {
    id: 9, kind: 'endpoint_status', status: 'pending', source: 'agent', finding_id: 7, vulnerability_id: null,
    finding_host_id: 2, field: null, payload: { host_status: 'retest' }, current_value: null,
    target: { finding_title: 'F', observation_title: null, host_id: 2, host_ip: '10.0.0.2' },
    rationale: 'The patch shipped Friday.', evidence_ids: [], agent_session_id: 81, proposed_by: 'Ana',
    agent_model: 'model-a', agent_client: null, prompt_version: null, created_at: null, decided_by: null,
    decided_at: null, decision_note: null, result_finding_id: null, error: null,
  } as Proposal;

  it('names the proposed state and why, and rejects with a note from the row', async () => {
    const onProposalDecided = vi.fn();
    rejectProposal.mockResolvedValue({ ...pr, status: 'rejected' });
    render(
      <MemoryRouter>
        <FindingEndpoints finding={finding([endpoint(1), endpoint(2)])} canManage canDecide
          onChanged={vi.fn()} onRemove={vi.fn()} proposals={new Map([[2, [pr]]])} onProposalDecided={onProposalDecided} />
      </MemoryRouter>,
    );
    const row = document.querySelector('[data-endpoint-row="2"]') as HTMLElement;
    expect(within(row).getByText(/Proposed: Still present → Retest here/)).toBeInTheDocument();
    expect(within(row).getByText(/The patch shipped Friday/)).toBeInTheDocument();
    const other = document.querySelector('[data-endpoint-row="1"]') as HTMLElement;
    expect(within(other).queryByText(/Proposed:/)).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: /Reject…/ }));
    fireEvent.change(within(row).getByRole('textbox', { name: /Why reject it/ }), { target: { value: 'Not retested yet.' } });
    fireEvent.click(within(row).getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(rejectProposal).toHaveBeenCalledWith(9, 'Not retested yet.'));
    expect(onProposalDecided).toHaveBeenCalledWith(expect.objectContaining({ status: 'rejected' }));
  });
});
