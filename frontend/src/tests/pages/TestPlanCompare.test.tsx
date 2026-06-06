import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import TestPlanCompare from '../../pages/TestPlanCompare';

// Override the global setupTests.ts react-router-dom mock so useParams
// returns this page's expected ``planId`` shape (the global mock
// hardcodes ``{ id: '1' }`` which makes planId undefined here).
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ planId: '7' }),
  };
});

vi.mock('../../services/api', () => ({
  getAllEntryResults: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
          <Route path="/test-plans/:planId/compare" element={<TestPlanCompare />} />
        </Routes>
</MemoryRouter>,
  );
}

// Two sessions on plan #7 — same entry on host 10.0.0.5.  Session A
// (claude) reports a finding; session B (codex) does not.  This is
// the canonical comparison scenario.
const bundleA = {
  plan_id: 7,
  execution_session_id: 42,
  execution_session_status: 'completed',
  started_at: '2026-05-15T10:00:00Z',
  completed_at: '2026-05-15T11:00:00Z',
  started_by_username: 'alice',
  agent_name: "alice's-agent",
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  entries: [
    {
      entry_id: 101,
      host_id: 5,
      host_ip: '10.0.0.5',
      host_hostname: 'web01.internal',
      entry_status: 'completed',
      tests: [
        {
          id: 1,
          test_index: 0,
          status: 'executed',
          command_run: 'nmap -sV 10.0.0.5',
          raw_output: null,
          findings_summary: 'CVE found',
          severity: 'high',
          is_finding: true,
          executed_at: '2026-05-15T10:30:00Z',
          created_at: null,
        },
      ],
      sanity_checks: [
        {
          id: 11,
          method: 'banner_grab',
          target_ip: '10.0.0.5',
          port_checked: 80,
          expected_value: null,
          actual_value: null,
          source_ip: null,
          dns_result: null,
          passed: true,
          details: null,
          checked_at: '2026-05-15T10:10:00Z',
        },
      ],
    },
  ],
};

const bundleB = {
  plan_id: 7,
  execution_session_id: 47,
  execution_session_status: 'completed',
  started_at: '2026-05-15T14:00:00Z',
  completed_at: '2026-05-15T15:00:00Z',
  started_by_username: 'bob',
  agent_name: "bob's-agent",
  generated_by_model: 'gpt-5-codex',
  generated_by_tool: 'codex',
  prompt_version: '1.13.0',
  entries: [
    {
      entry_id: 101,
      host_id: 5,
      host_ip: '10.0.0.5',
      host_hostname: 'web01.internal',
      entry_status: 'completed',
      tests: [
        {
          id: 99,
          test_index: 0,
          status: 'executed',
          // Different command — codex translated the SAME intent differently.
          command_run: 'powershell -Command "Get-NetTCPConnection ..."',
          raw_output: null,
          findings_summary: null,
          severity: null,
          // ...and didn't report a finding.  This is the diff that
          // makes cross-execution comparison valuable.
          is_finding: false,
          executed_at: '2026-05-15T14:30:00Z',
          created_at: null,
        },
      ],
      sanity_checks: [
        {
          id: 22,
          method: 'reverse_dns',
          target_ip: '10.0.0.5',
          port_checked: null,
          expected_value: null,
          actual_value: 'web01.internal',
          source_ip: null,
          dns_result: 'web01.internal',
          passed: true,
          details: null,
          checked_at: '2026-05-15T14:10:00Z',
        },
      ],
    },
  ],
};

describe('TestPlanCompare', () => {
  beforeEach(() => {
    mockedApi.getAllEntryResults.mockReset();
  });

  it('warns when ?a / ?b are missing', () => {
    renderAt('/test-plans/7/compare');
    expect(screen.getByText(/Pass/)).toBeInTheDocument();
    expect(mockedApi.getAllEntryResults).not.toHaveBeenCalled();
  });

  it('fetches both sessions and renders side-by-side attribution', async () => {
    mockedApi.getAllEntryResults.mockImplementation(async (_planId, sessionId) =>
      sessionId === 42 ? bundleA : bundleB,
    );
    renderAt('/test-plans/7/compare?a=42&b=47');

    // Both session headers eventually render with model + user attribution.
    await screen.findByText('claude-opus-4-7');
    expect(screen.getByText('gpt-5-codex')).toBeInTheDocument();
    expect(screen.getByText(/started by alice/)).toBeInTheDocument();
    expect(screen.getByText(/started by bob/)).toBeInTheDocument();
  });

  it('flags a major diff when one session has a finding and the other doesn\'t', async () => {
    mockedApi.getAllEntryResults.mockImplementation(async (_planId, sessionId) =>
      sessionId === 42 ? bundleA : bundleB,
    );
    renderAt('/test-plans/7/compare?a=42&b=47');

    // Wait for the rows to render.
    await screen.findByText('10.0.0.5');
    // The diff row should be tagged "major diff" because one side
    // reported a finding and the other didn't.
    expect(screen.getByText('major diff')).toBeInTheDocument();
    // Verdict-summary chip at the top should also reflect it.
    expect(screen.getByText(/Major diff: 1/)).toBeInTheDocument();
  });

  it('passes both session IDs to the API on mount', async () => {
    mockedApi.getAllEntryResults.mockResolvedValue(bundleA);
    renderAt('/test-plans/7/compare?a=42&b=47');
    // v2.86.6 — the initial fetch now passes a paginated query
    // {entriesLimit: ENTRIES_PAGE_SIZE} as the third arg.  The previous
    // assertion was a 2-arg exact match; loosen to expect.objectContaining
    // so the test stays focused on "the two sessions get fetched" without
    // pinning the page size constant.
    await waitFor(() => {
      expect(mockedApi.getAllEntryResults).toHaveBeenCalledWith(
        7, 42, expect.objectContaining({ entriesLimit: expect.any(Number) }),
      );
      expect(mockedApi.getAllEntryResults).toHaveBeenCalledWith(
        7, 47, expect.objectContaining({ entriesLimit: expect.any(Number) }),
      );
    });
  });
});
