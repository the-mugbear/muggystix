/**
 * Tests for the v3 alpha.8.1 plan-vs-plan comparison page.
 *
 * Pins the diff contract for plan entries — a_only, b_only,
 * both_match, both_diff — and confirms the side-by-side cards
 * surface attribution.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({}),
  };
});

vi.mock('../../services/api', () => ({
  getTestPlan: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import PlanCompare from '../../pages/PlanCompare';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
          <Route path="/test-plans/compare" element={<PlanCompare />} />
        </Routes>
</MemoryRouter>,
  );
}

const baseEntry = (overrides: any = {}) => ({
  id: 1,
  host_id: 0,
  host_ip: null,
  host_hostname: null,
  priority: 'medium',
  test_phase: 'enumeration',
  proposed_tests: [],
  rationale: 'r',
  status: 'proposed',
  findings: null,
  results_data: null,
  notes: null,
  assigned_to_id: null,
  started_at: null,
  completed_at: null,
  created_at: '2026-05-15T10:00:00Z',
  updated_at: '2026-05-15T10:00:00Z',
  ...overrides,
});

const basePlan = (id: number, model: string, entries: any[]) => ({
  id,
  project_id: 1,
  version: 1,
  title: `Plan ${id}`,
  status: 'proposed',
  entry_count: entries.length,
  completion_pct: 0,
  generated_by_model: model,
  generated_by_tool: 'claude-code',
  created_at: '2026-05-15T10:00:00Z',
  updated_at: '2026-05-15T11:00:00Z',
  entries,
  new_hosts_since_creation: 0,
  filter_criteria: null,
  api_key: { has_key: false, is_active: false },
  execution_session_count: 0,
});

// Plan A: hosts 100 (high, 2 tests), 101 (medium, 1 test — A only)
// Plan B: hosts 100 (high, 3 tests — same priority, different test count
// → both_diff), 102 (low, 1 test — B only)
const planA = basePlan(11, 'claude-opus-4-7', [
  baseEntry({
    id: 1, host_id: 100, host_ip: '10.0.0.5',
    priority: 'high', proposed_tests: ['a', 'b'],
  }),
  baseEntry({
    id: 2, host_id: 101, host_ip: '10.0.0.6',
    priority: 'medium', proposed_tests: ['c'],
  }),
]);
const planB = basePlan(14, 'gpt-5-codex', [
  baseEntry({
    id: 10, host_id: 100, host_ip: '10.0.0.5',
    priority: 'high', proposed_tests: ['a', 'b', 'c'],
  }),
  baseEntry({
    id: 11, host_id: 102, host_ip: '10.0.0.7',
    priority: 'low', proposed_tests: ['z'],
  }),
]);

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  mockedApi.getTestPlan.mockImplementation(async (id: number) => {
    if (id === 11) return planA;
    if (id === 14) return planB;
    throw new Error(`unknown plan ${id}`);
  });
});

describe('PlanCompare page', () => {
  it('warns when ?a= or ?b= is missing', async () => {
    renderAt('/test-plans/compare?a=11');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/both/i);
  });

  it('warns when ?a= and ?b= point at the same plan', async () => {
    renderAt('/test-plans/compare?a=11&b=11');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/different/i);
  });

  it('renders both plan cards with attribution', async () => {
    renderAt('/test-plans/compare?a=11&b=14');
    await waitFor(() => {
      expect(screen.getByText(/Plan #11/)).toBeInTheDocument();
      expect(screen.getByText(/Plan #14/)).toBeInTheDocument();
    });
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5-codex/)).toBeInTheDocument();
  });

  it('classifies host entries into a_only / b_only / both_diff buckets', async () => {
    renderAt('/test-plans/compare?a=11&b=14');
    await waitFor(() => {
      expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
    });
    // 10.0.0.5: both, different proposed_tests counts → both_diff
    // 10.0.0.6: A only
    // 10.0.0.7: B only
    expect(screen.getByText('10.0.0.6')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.7')).toBeInTheDocument();
    // Bucket chips at the top.
    expect(screen.getByText(/1 A only/)).toBeInTheDocument();
    expect(screen.getByText(/1 B only/)).toBeInTheDocument();
    expect(screen.getByText(/1 differ/)).toBeInTheDocument();
    expect(screen.getByText(/0 identical/)).toBeInTheDocument();
  });
});
