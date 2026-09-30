import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import PlanFromSelectionDialog, { AGENT_TASK_MAX_HOSTS } from '../../components/hosts/PlanFromSelectionDialog';
import { describeSelection } from '../../utils/planSelection';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const api = vi.hoisted(() => ({
  createPlanFromHosts: vi.fn(),
  getTestPlans: vi.fn(),
}));
vi.mock('../../services/api', () => api);

// The agent path opens the Start Agent Session dialog, which reads the
// operator's live sessions.
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: [], loading: false, failed: false, refresh: vi.fn() }),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const renderDialog = (over: Partial<React.ComponentProps<typeof PlanFromSelectionDialog>> = {}) =>
  render(
    <MemoryRouter>
      <PlanFromSelectionDialog
        open
        onOpenChange={() => {}}
        resolveIds={() => Promise.resolve([1, 2, 3])}
        selectionSummary="3 hosts checked on the Hosts page"
        sampleIps={['10.0.0.1', '10.0.0.2', '10.0.0.3']}
        {...over}
      />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.getTestPlans.mockResolvedValue([{ id: 7, title: 'Old draft', status: 'draft' }]);
  api.createPlanFromHosts.mockImplementation(async (body: { dry_run?: boolean }) =>
    body.dry_run
      ? { plan: null, created_plan: false, requested: 3, added: 3, already_in_plan: 0, not_in_project: 0, planned_elsewhere: 1, dry_run: true }
      : { plan: { id: 42, title: 'DMZ' }, created_plan: true, requested: 3, added: 3, already_in_plan: 0, not_in_project: 0, planned_elsewhere: 1, dry_run: false },
  );
});

describe('PlanFromSelectionDialog', () => {
  it('shows the fixed list and what will happen before anything is written', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText(/^3 hosts/)).toBeInTheDocument());
    expect(screen.getByText(/3 hosts checked on the Hosts page/)).toBeInTheDocument();
    expect(screen.getByText(/10\.0\.0\.1, 10\.0\.0\.2, 10\.0\.0\.3/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/entries will be added/)).toBeInTheDocument());
    expect(screen.getByText(/already carried by a running or completed plan/)).toBeInTheDocument();
    // The preview was a dry run, nothing else has been sent.
    expect(api.createPlanFromHosts).toHaveBeenCalledTimes(1);
    expect(api.createPlanFromHosts.mock.calls[0][0]).toMatchObject({ dry_run: true, host_ids: [1, 2, 3] });
  });

  it('needs a title and a rationale, then creates the draft and opens it', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText(/entries will be added/)).toBeInTheDocument());
    const create = screen.getByRole('button', { name: 'Create draft plan' });
    expect(create).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Plan title'), { target: { value: 'DMZ' } });
    fireEvent.change(screen.getByLabelText('Why these hosts'), { target: { value: 'admin ports' } });
    expect(create).toBeEnabled();
    fireEvent.click(create);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/test-plans/42'));
    const real = api.createPlanFromHosts.mock.calls.find(([b]) => !b.dry_run)?.[0];
    expect(real).toMatchObject({
      host_ids: [1, 2, 3],
      title: 'DMZ',
      rationale: 'admin ports',
      priority: 'medium',
      test_phase: 'enumeration',
      selection_summary: '3 hosts checked on the Hosts page',
    });
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('Created “DMZ” with 3 hosts'), expect.anything());
  });

  // 5.313.0 — no "Generate with AI" key: the operator's one agent session is
  // handed a task naming exactly these hosts.
  it('hands the same fixed list to your agent session as a task', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText(/entries will be added/)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/Have your agent draft it/));
    fireEvent.change(screen.getByLabelText('Why these hosts'), { target: { value: 'because' } });
    fireEvent.click(screen.getByRole('button', { name: /Hand to your agent/ }));

    expect(await screen.findByText('Start Agent Session')).toBeInTheDocument();
    expect(
      screen.getByText('Draft a test plan in BlueStick for these hosts only (host ids): 1, 2, 3. Why these hosts: because'),
    ).toBeInTheDocument();
    // Nothing was written and nothing navigated: the agent writes the plan.
    expect(api.createPlanFromHosts.mock.calls.filter(([b]) => !b.dry_run)).toHaveLength(0);
    expect(navigate).not.toHaveBeenCalled();
  });

  // 5.313.1 — the task names every id; 5,000 of them was a ~30 KB paste.
  it('does not hand a selection larger than the cap to the agent as a list of ids', async () => {
    const many = Array.from({ length: AGENT_TASK_MAX_HOSTS + 1 }, (_, i) => i + 1);
    renderDialog({ resolveIds: () => Promise.resolve(many) });
    await waitFor(() => expect(screen.getByText(/entries will be added/)).toBeInTheDocument());
    expect(screen.getByRole('radio', { name: /Have your agent draft it/ })).toBeDisabled();
    expect(screen.getByText(/takes at most 200 hosts/)).toBeInTheDocument();
  });
});

describe('describeSelection', () => {
  it('names a checked selection and a resolved all-matching query differently', () => {
    expect(describeSelection(3, false, {})).toBe('3 hosts checked on the Hosts page');
    expect(describeSelection(41, true, { subnet: '10.1.0.0/16', q: 'has:weak_tls', state: undefined, tags: [] })).toBe(
      'all 41 hosts matching subnet=10.1.0.0/16 q=has:weak_tls, resolved to a fixed list',
    );
    expect(describeSelection(9, true, {})).toBe('all 9 hosts in the project, resolved to a fixed list');
  });
});
