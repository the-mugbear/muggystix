import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ProposeTestsDialog, { AGENT_TASK_MAX_HOSTS } from '../../components/hosts/ProposeTestsDialog';
import { describeSelection } from '../../utils/hostSelection';
import { agentInstruction } from '../../utils/agentRuns';

// The hand-off opens the Start Agent Session dialog, which reads the
// operator's live sessions.
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: [], loading: false, failed: false, refresh: vi.fn() }),
}));
vi.mock('../../services/api', () => ({ getMatchingHostIds: vi.fn() }));

const canStart = vi.hoisted(() => ({ value: true }));
vi.mock('../../hooks/useCanStartAgentSession', () => ({
  useCanStartAgentSession: () => canStart.value,
}));

// The real dialog mints a session; here it only has to show the task it was given.
vi.mock('../../components/StartAssistDialog', () => ({
  default: ({ instruction }: { instruction?: string }) => <div data-testid="agent-task">{instruction}</div>,
}));

import * as api from '../../services/api';

const getMatchingHostIds = api.getMatchingHostIds as unknown as ReturnType<typeof vi.fn>;

type DialogProps = React.ComponentProps<typeof ProposeTestsDialog>;
const dialog = (over: Partial<DialogProps> = {}) => (
  <MemoryRouter>
    <ProposeTestsDialog
      open
      onOpenChange={() => {}}
      selectedIds={[1, 2, 3]}
      allMatching={false}
      queryContext={{}}
      selectionSummary="3 hosts checked on the Hosts page"
      sampleIps={['10.0.0.1', '10.0.0.2', '10.0.0.3']}
      {...over}
    />
  </MemoryRouter>
);
const renderDialog = (over: Partial<DialogProps> = {}) => render(dialog(over));
/** "Every matching host", as the server resolves it. */
const allMatching = (over: Partial<DialogProps> = {}): Partial<DialogProps> => ({
  allMatching: true, queryContext: { q: 'port:22' }, sampleIps: [], ...over,
});
const matched = (ids: number[], over: Record<string, unknown> = {}) => ({
  ids, total: ids.length, capped: false, cap: 5000, ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  canStart.value = true;
});

describe('ProposeTestsDialog', () => {
  it('shows the fixed list it resolved, and says tests land on the host page', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText(/^3 hosts/)).toBeInTheDocument());
    expect(screen.getByText(/3 hosts checked on the Hosts page/)).toBeInTheDocument();
    expect(screen.getByText(/10\.0\.0\.1, 10\.0\.0\.2, 10\.0\.0\.3/)).toBeInTheDocument();
    expect(screen.getByText(/they appear on the host's page/i)).toBeInTheDocument();
    // Nothing about plans, drafts or approval is left.
    expect(screen.queryByText(/draft/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/approv/i)).not.toBeInTheDocument();
  });

  it('hands exactly the resolved ids, and what to test, to the agent', async () => {
    getMatchingHostIds.mockResolvedValue(matched([11, 12, 13]));
    renderDialog(allMatching());
    await waitFor(() => expect(screen.getByText(/^3 hosts/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/What to test/), { target: { value: 'SMB signing' } });
    fireEvent.click(screen.getByRole('button', { name: /Hand to your agent/ }));
    const task = (await screen.findByTestId('agent-task')).textContent ?? '';
    expect(task).toContain('host ids): 11, 12, 13.');
    expect(task).toContain('What to test: SMB signing');
    expect(task).toContain('host_tests_propose');
    expect(task).toContain('Do not run anything yet');
  });

  it('does not hand over a selection larger than the cap, and says what to do instead', async () => {
    const many = Array.from({ length: AGENT_TASK_MAX_HOSTS + 1 }, (_, i) => i + 1);
    getMatchingHostIds.mockResolvedValue(matched(many));
    renderDialog(allMatching());
    await waitFor(() => expect(screen.getByText(/^201 hosts/)).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/at most 200 hosts/);
    expect(screen.getByRole('alert')).toHaveTextContent(/Narrow it on the Hosts page/);
    expect(screen.getByRole('button', { name: /Hand to your agent/ })).toBeDisabled();
  });

  it('reports a selection that could not be resolved instead of offering an empty hand-off', async () => {
    getMatchingHostIds.mockRejectedValue(new Error('boom'));
    renderDialog(allMatching());
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not resolve the selection|boom/);
    expect(screen.getByRole('button', { name: /Hand to your agent/ })).toBeDisabled();
  });

  it('checked rows are the list as given: nothing is asked of the server', async () => {
    renderDialog({ selectedIds: [4, 5] , selectionSummary: '2 hosts checked on the Hosts page' });
    expect(screen.getByText(/^2 hosts/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Hand to your agent/ }));
    expect((await screen.findByTestId('agent-task')).textContent).toContain('host ids): 4, 5.');
    expect(getMatchingHostIds).not.toHaveBeenCalled();
  });

  // The read is the query's own: it carries the query's signal, so closing
  // the dialog cancels a read still in flight (it was a callback the dialog
  // could only wait for), and each opening asks again.
  it('"all matching" is read under the filters with a signal that closing aborts, and again at the next opening', async () => {
    let seen: AbortSignal | undefined;
    getMatchingHostIds.mockImplementation((_projectId: number, _query: unknown, signal?: AbortSignal) => {
      seen = signal;
      return new Promise(() => {});
    });
    const { rerender } = renderDialog(allMatching());
    expect(await screen.findByText(/Resolving selection/)).toBeInTheDocument();
    expect(getMatchingHostIds).toHaveBeenCalledTimes(1);
    expect(getMatchingHostIds.mock.calls[0].slice(0, 2)).toEqual([1, { q: 'port:22' }]);
    expect(seen).toBeInstanceOf(AbortSignal);
    expect(seen?.aborted).toBe(false);
    expect(screen.getByRole('button', { name: /Hand to your agent/ })).toBeDisabled();

    rerender(dialog(allMatching({ open: false })));
    await waitFor(() => expect(seen?.aborted).toBe(true));

    getMatchingHostIds.mockResolvedValue(matched([7, 8]));
    rerender(dialog(allMatching()));
    expect(await screen.findByText(/^2 hosts/)).toBeInTheDocument();
    expect(getMatchingHostIds).toHaveBeenCalledTimes(2);
  });

  it('tells a viewer why there is no hand-off', async () => {
    canStart.value = false;
    renderDialog();
    await waitFor(() => expect(screen.getByText(/^3 hosts/)).toBeInTheDocument());
    expect(screen.getByText(/needs the auditor role/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Hand to your agent/ })).not.toBeInTheDocument();
  });
});

describe('agentInstruction.proposeTests', () => {
  it('names the hosts and omits the focus when none was given', () => {
    const task = agentInstruction.proposeTests([4, 5]);
    expect(task).toContain('(host ids): 4, 5.');
    expect(task).not.toContain('What to test:');
  });

  it('ends the operator\'s words as a sentence so the next one does not run on', () => {
    expect(agentInstruction.proposeTests([4], 'Confirm the TLS weaknesses'))
      .toContain('What to test: Confirm the TLS weaknesses. Read what each host exposes');
    expect(agentInstruction.proposeTests([4], 'Is SMB signing required?'))
      .toContain('What to test: Is SMB signing required? Read what');
  });
});

describe('describeSelection', () => {
  it('names a checked selection and a resolved all-matching query differently', () => {
    expect(describeSelection(3, false, {})).toBe('3 hosts checked on the Hosts page');
    expect(describeSelection(41, true, { subnet: '10.1.0.0/16', q: 'has:weak_tls', state: undefined }))
      .toBe('all 41 hosts matching subnet=10.1.0.0/16 q=has:weak_tls, resolved to a fixed list');
    expect(describeSelection(9, true, {})).toBe('all 9 hosts in the project, resolved to a fixed list');
  });
});
