/**
 * Tests for the v3 alpha.7 ExecutionDetail page.
 *
 * Pins the standalone-permalink contract: bundle loads from session
 * id alone, header surfaces attribution, entry table summarises tests
 * and sanity checks, sort order puts findings-bearing entries first.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({ sessionId: '42' }),
  };
});

vi.mock('../../services/api', () => ({
  getExecutionSessionById: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import ExecutionDetail from '../../pages/ExecutionDetail';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/executions/42']}>
      <Routes>
          <Route path="/executions/:sessionId" element={<ExecutionDetail />} />
        </Routes>
</MemoryRouter>,
  );
}

// Two entries.  Entry #101 has a critical finding (should sort
// first); entry #100 has no findings (sorts second).
const bundleFixture = {
  plan_id: 7,
  execution_session_id: 42,
  execution_session_status: 'completed',
  started_at: '2026-05-15T10:00:00Z',
  completed_at: '2026-05-15T12:00:00Z',
  started_by_username: 'alice',
  agent_name: "alice's-agent",
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  entries: [
    {
      entry_id: 100,
      host_id: 500,
      host_ip: '10.0.0.5',
      host_hostname: 'web01',
      entry_status: 'completed',
      tests: [
        {
          id: 1, entry_id: 100, test_index: 0,
          status: 'executed', is_finding: false, severity: null,
          command_run: 'nmap -sV 10.0.0.5',
          raw_output: '', findings_summary: '',
          executed_at: '2026-05-15T11:00:00Z',
          created_at: '2026-05-15T10:30:00Z',
          updated_at: '2026-05-15T11:00:00Z',
        },
      ],
      sanity_checks: [
        {
          id: 1, entry_id: 100, method: 'reverse_dns', target_ip: '10.0.0.5',
          passed: true, checked_at: '2026-05-15T10:30:00Z',
        },
      ],
    },
    {
      entry_id: 101,
      host_id: 501,
      host_ip: '10.0.0.6',
      host_hostname: 'db01',
      entry_status: 'completed',
      tests: [
        {
          id: 2, entry_id: 101, test_index: 0,
          status: 'executed', is_finding: true, severity: 'critical',
          command_run: 'sqlmap -u http://10.0.0.6/login',
          raw_output: 'vulnerable', findings_summary: 'SQL injection',
          executed_at: '2026-05-15T11:30:00Z',
          created_at: '2026-05-15T11:00:00Z',
          updated_at: '2026-05-15T11:30:00Z',
        },
      ],
      sanity_checks: [],
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  mockedApi.getExecutionSessionById.mockResolvedValue(bundleFixture);
});

describe('ExecutionDetail page', () => {
  it('renders the session header from the bundle attribution', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Execution session #42/)).toBeInTheDocument();
    });
    // "completed" appears in the header chip and on each entry's
    // status chip — assert presence rather than uniqueness.
    expect(screen.getAllByText('completed').length).toBeGreaterThan(0);
    // Attribution line surfaces the model.
    expect(screen.getByText('claude-opus-4-7')).toBeInTheDocument();
  });

  it('renders one row per entry in the results table', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Entry results/)).toBeInTheDocument();
    });
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.6')).toBeInTheDocument();
    expect(screen.getByText('web01')).toBeInTheDocument();
    expect(screen.getByText('db01')).toBeInTheDocument();
  });

  it('sorts the critical-finding entry above the clean one', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('10.0.0.6')).toBeInTheDocument();
    });
    // Read the rendered IPs in DOM order; #101 (critical) must
    // appear before #100 (clean) per the rollup sort.
    const rows = screen.getAllByText(/^10\.0\.0\.\d/);
    expect(rows[0].textContent).toContain('10.0.0.6');
    expect(rows[1].textContent).toContain('10.0.0.5');
  });

  it('surfaces a critical finding chip on the entry with a finding', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('10.0.0.6')).toBeInTheDocument();
    });
    expect(screen.getAllByText(/crit/i).length).toBeGreaterThan(0);
  });

  it('Plan-link button navigates to /test-plans/{plan_id}', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Plan #7/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Plan #7/i }));
    expect(navigateSpy).toHaveBeenCalledWith('/test-plans/7');
  });

  it('renders an error alert when the API rejects', async () => {
    mockedApi.getExecutionSessionById.mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
