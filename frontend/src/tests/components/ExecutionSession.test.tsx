/**
 * Tests for the three v3 alpha.4 session-detail primitives:
 *   - ExecutionSessionHeader
 *   - ExecutionSessionPicker
 *   - ExecutionCompareLinks
 *
 * Each component is pulled out of TestPlanDetail; these tests pin
 * their public contract so the alpha.7 /executions/:id page (which
 * will re-assemble them) gets a stable shape to compose against.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { ExecutionSessionHeader } from '../../components/execution/ExecutionSessionHeader';
import { ExecutionSessionPicker } from '../../components/execution/ExecutionSessionPicker';
import { ExecutionCompareLinks } from '../../components/execution/ExecutionCompareLinks';
import { ExecutionSessionSummary } from '../../services/api/test-plans';

// React-router navigate spy.  Override the global setupTests mock so
// ExecutionCompareLinks's useNavigate is the spy we assert on.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({ id: '1' }),
  };
});


function wrap(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      {node}
</MemoryRouter>,
  );
}

// Canonical session shape used across the tests below — claude on
// Kali, paused mid-run.  Subtests override specific fields.
const session: ExecutionSessionSummary = {
  id: 42,
  status: 'paused',
  mode: 'guided',
  started_at: '2026-05-15T10:00:00Z',
  completed_at: null,
  started_by_username: 'alice',
  agent_name: "alice's-agent",
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  environment_os_family: 'linux',
  environment_shell: 'bash',
};

// ---------------------------------------------------------------------------
// ExecutionSessionHeader
// ---------------------------------------------------------------------------

describe('ExecutionSessionHeader', () => {
  it('renders session metadata, attribution, and env probe', () => {
    wrap(<ExecutionSessionHeader session={session} totalSessionCount={1} />);
    expect(screen.getByText('Execution session')).toBeInTheDocument();
    expect(screen.getByText('paused')).toBeInTheDocument();
    expect(screen.getByText('guided')).toBeInTheDocument();
    // Attribution line names the model.
    expect(screen.getByText('claude-opus-4-7')).toBeInTheDocument();
    // Env-probe line names the host family.
    expect(screen.getByText('linux')).toBeInTheDocument();
    // No "N runs" chip when totalSessionCount is 1.
    expect(screen.queryByText(/1 runs/)).not.toBeInTheDocument();
  });

  it('surfaces multi-run chip and uses plural title when count > 1', () => {
    wrap(<ExecutionSessionHeader session={session} totalSessionCount={4} />);
    expect(screen.getByText('Execution sessions')).toBeInTheDocument();
    expect(screen.getByText('4 runs')).toBeInTheDocument();
  });

  it('renders the actions slot when provided', () => {
    wrap(
      <ExecutionSessionHeader
        session={session}
        actions={<button>open-report</button>}
      />,
    );
    expect(screen.getByText('open-report')).toBeInTheDocument();
  });

  it('omits attribution and env-probe lines when null', () => {
    const bare: ExecutionSessionSummary = {
      ...session,
      generated_by_model: null,
      generated_by_tool: null,
      prompt_version: null,
      environment_os_family: null,
      environment_shell: null,
    };
    wrap(<ExecutionSessionHeader session={bare} />);
    expect(screen.queryByText(/Executed by/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Operator host/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ExecutionSessionPicker
// ---------------------------------------------------------------------------

describe('ExecutionSessionPicker', () => {
  const sessionA: ExecutionSessionSummary = { ...session, id: 42 };
  const sessionB: ExecutionSessionSummary = {
    ...session,
    id: 43,
    generated_by_model: 'gpt-5-codex',
    generated_by_tool: 'codex',
    started_by_username: 'bob',
  };

  it('renders nothing when only one session is available', () => {
    const { container } = wrap(
      <ExecutionSessionPicker
        sessions={[sessionA]}
        selectedId={42}
        onSelect={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders one chip per session and marks the selected one filled', () => {
    wrap(
      <ExecutionSessionPicker
        sessions={[sessionA, sessionB]}
        selectedId={42}
        onSelect={() => {}}
      />,
    );
    // Both session IDs appear inside chips.
    expect(screen.getByText(/#42/)).toBeInTheDocument();
    expect(screen.getByText(/#43/)).toBeInTheDocument();
    // The model name + user surface in the chip label.
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5-codex/)).toBeInTheDocument();
  });

  it('calls onSelect with the session id when a chip is clicked', () => {
    const onSelect = vi.fn();
    wrap(
      <ExecutionSessionPicker
        sessions={[sessionA, sessionB]}
        selectedId={42}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText(/#43/));
    expect(onSelect).toHaveBeenCalledWith(43);
  });

  it('shows the loading hint in the label when loading', () => {
    wrap(
      <ExecutionSessionPicker
        sessions={[sessionA, sessionB]}
        selectedId={42}
        onSelect={() => {}}
        loading
      />,
    );
    expect(screen.getByText(/loading…/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ExecutionCompareLinks
// ---------------------------------------------------------------------------

describe('ExecutionCompareLinks', () => {
  const sessionA: ExecutionSessionSummary = { ...session, id: 42 };
  const sessionB: ExecutionSessionSummary = { ...session, id: 43 };
  const sessionC: ExecutionSessionSummary = { ...session, id: 44 };

  beforeEach(() => {
    navigateSpy.mockReset();
  });

  it('renders nothing when there are no other sessions to compare', () => {
    const { container } = wrap(
      <ExecutionCompareLinks
        activeId={42}
        sessions={[sessionA]}
        planId={7}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the primary "Compare with #N" button targeting the first non-active session', () => {
    // v3 alpha.13: the chip-per-sibling layout was slimmed to a single
    // primary button + a "Pick from all N" secondary link.  Tests pin
    // that shape.
    wrap(
      <ExecutionCompareLinks
        activeId={42}
        sessions={[sessionA, sessionB, sessionC]}
        planId={7}
      />,
    );
    expect(screen.getByText(/Compare this run with another/)).toBeInTheDocument();
    // Primary button surfaces the first non-active sibling.
    expect(screen.getByRole('button', { name: /Compare with #43/ })).toBeInTheDocument();
    // Pick-from-all link appears when there's more than one other session.
    expect(screen.getByRole('button', { name: /Pick from all 3/ })).toBeInTheDocument();
  });

  it('primary button navigates to /test-plans/{planId}/compare?a=...&b=...', () => {
    wrap(
      <ExecutionCompareLinks
        activeId={42}
        sessions={[sessionA, sessionB]}
        planId={7}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Compare with #43/ }));
    expect(navigateSpy).toHaveBeenCalledWith(
      '/test-plans/7/compare?a=42&b=43',
    );
  });
});
