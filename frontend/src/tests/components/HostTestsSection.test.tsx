/**
 * Tests on a host (5.320.0; one-line rows, the result side panel and the
 * weakness link since 5.322.0). A test is a row of its own with a revision;
 * what it produced is the evidence recorded against it.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EvidenceRecord, HostTest } from '../../services/api';

const api = vi.hoisted(() => ({
  listHostTests: vi.fn(),
  createHostTests: vi.fn(),
  updateHostTest: vi.fn(),
  recordHostTestResult: vi.fn(),
  createFindingFromEvidence: vi.fn(),
  listEvidenceRecords: vi.fn(),
  getEvidenceRawOutput: vi.fn(),
  listProposals: vi.fn(),
}));
vi.mock('../../services/api', () => api);

// The hand-off itself (copy when a session is live, else the start dialog)
// is pinned in useAgentTask.test.tsx; here only which task is handed over.
const agent = vi.hoisted(() => ({ allowed: true, give: vi.fn() }));
vi.mock('../../hooks/useAgentTask', () => ({
  useAgentTask: () => ({ allowed: agent.allowed, give: agent.give, dialog: null }),
}));

import { HostTestsSection } from '../../components/host-inspector/HostTestsSection';
import { openHostTest } from '../../components/host-inspector/hostTestsController';

const test = (over: Partial<HostTest> = {}): HostTest => ({
  id: 11, host_id: 5, host_ip: '10.0.0.5', tool: 'curl', description: 'Check response headers',
  command: 'curl -sI https://{ip}/', rationale: 'The portal is exposed.', expected_result: 'No X-Frame-Options',
  references: null, target_fqdn: null, priority: 'high', label: 'Web review', status: 'proposed',
  assigned_to_id: null, assigned_to: null, created_by: 'Alice Analyst', source: 'agent',
  agent_session_id: 72, agent_model: 'claude-opus-5-5', agent_client: 'claude-code',
  tester_summary: null, dismissed_reason: null, revision: 3, evidence_count: 0,
  issue_key: null, issue_title: null, last_outcome: null, unpromoted_findings: 0, finding_ids: [],
  created_at: '2026-09-30T10:00:00Z',
  ...over,
});

const evidence = (over: Partial<EvidenceRecord> = {}): EvidenceRecord => ({
  id: 90, host_test_id: 11, host_id: 5, host_ip: '10.0.0.5', finding_id: null, finding_host_id: null,
  tool: 'curl', command: 'curl -sI https://10.0.0.5/', outcome: 'finding',
  summary: 'No X-Frame-Options header.', raw_output_preview: 'HTTP/2 200', raw_output_bytes: 10,
  raw_output_truncated_in_preview: false, observed_ip: '10.0.0.5', executed_at: '2026-09-30T11:00:00Z',
  agent_session_id: 72, recorded_by: 'Alice Analyst', agent_model: null, agent_client: null,
  created_at: '2026-09-30T11:00:00Z',
  ...over,
});

/** A test whose result showed an issue nobody has made a finding of. */
const shown = (over: Partial<HostTest> = {}) =>
  test({ status: 'done', evidence_count: 1, last_outcome: 'finding', unpromoted_findings: 1, ...over });

const page = (items: HostTest[], total = items.length) => ({ items, total, has_more: total > items.length });

const renderSection = (props: Partial<React.ComponentProps<typeof HostTestsSection>> = {}) =>
  render(
    <MemoryRouter>
      <HostTestsSection hostId={5} canEdit userId={1} {...props} />
    </MemoryRouter>,
  );

const rowOf = (id: number) => screen.getByTestId(`host-test-${id}`);
const toggle = (id: number) => within(rowOf(id)).getAllByRole('button', { expanded: false })[0];
const open = async (id: number) => { await userEvent.click(toggle(id)); };
/** Open a row's "more actions" menu and pick an item (the menu is portalled). */
const pick = async (id: number, item: string) => {
  await userEvent.click(within(rowOf(id)).getByRole('button', { name: `More actions for test ${id}` }));
  await userEvent.click(await screen.findByRole('menuitem', { name: item }));
};
const menuItems = async (id: number) => {
  await userEvent.click(within(rowOf(id)).getByRole('button', { name: `More actions for test ${id}` }));
  const names = (await screen.findAllByRole('menuitem')).map((el) => el.textContent);
  await userEvent.keyboard('{Escape}');
  return names;
};
const tab = (name: RegExp) => screen.getByRole('tab', { name });
const panel = () => screen.getByRole('dialog');

beforeEach(() => {
  vi.clearAllMocks();
  agent.allowed = true;
  window.location.hash = '';
  api.listHostTests.mockResolvedValue(page([test()]));
  api.listEvidenceRecords.mockResolvedValue({ items: [], total: 0, has_more: false });
  api.listProposals.mockResolvedValue({ items: [], total: 0, has_more: false });
});

describe('HostTestsSection — the list', () => {
  it('reads the host\'s tests once and shows each as one row with its command to copy', async () => {
    renderSection();
    await screen.findByText('Check response headers');
    expect(api.listHostTests).toHaveBeenCalledTimes(1);
    expect(api.listHostTests).toHaveBeenCalledWith({ host_id: 5, limit: 200 });
    // The command is resolved against this host and is on the closed row.
    expect(within(rowOf(11)).getByText('curl -sI https://10.0.0.5/')).toBeInTheDocument();
    expect(within(rowOf(11)).getByRole('button', { name: 'Copy command' })).toBeInTheDocument();
    expect(within(rowOf(11)).getByTestId('host-test-state-11')).toHaveTextContent('not run');
    // Nothing else until it is opened.
    expect(screen.queryByText('The portal is exposed.')).not.toBeInTheDocument();
    expect(screen.queryByText(/Proposed by/)).not.toBeInTheDocument();
    expect(api.listEvidenceRecords).not.toHaveBeenCalled();
  });

  it('opens to why, what is expected and who proposed it', async () => {
    api.listHostTests.mockResolvedValue(page([test({
      rationale: '🤖 **Agent-generated** (claude)\n\nThe portal sets **no** frame header.',
      references: ['https://owasp.org/x', 'javascript:alert(1)'],
    })]));
    renderSection();
    await screen.findByText('Check response headers');
    await open(11);
    expect(screen.getByText(/The portal sets/)).toBeInTheDocument();
    // Markdown, and the agent's mark is not repeated in front of the reason.
    expect(screen.getByText('no').tagName).toBe('STRONG');
    expect(screen.queryByText(/Agent-generated/)).not.toBeInTheDocument();
    expect(screen.getByText(/No X-Frame-Options/)).toBeInTheDocument();
    expect(screen.getByText(/Proposed by claude-code · claude-opus-5-5 for Alice Analyst/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'owasp.org' })).toHaveAttribute('href', 'https://owasp.org/x');
    expect(screen.queryByText(/javascript:/)).not.toBeInTheDocument();
    expect(screen.getByText(/No result recorded yet/)).toBeInTheDocument();
  });

  it('counts every status and switches between them without asking the server again', async () => {
    api.listHostTests.mockResolvedValue(page([
      test({ id: 1 }), test({ id: 2, status: 'in_progress' }),
      test({ id: 3, status: 'done', description: 'Finished one' }),
      test({ id: 4, status: 'dismissed', description: 'Dropped one', dismissed_reason: 'Out of scope' }),
    ]));
    renderSection();
    await screen.findByTestId('host-test-1');
    expect(tab(/To do/)).toHaveTextContent('To do 2');
    expect(tab(/Done/)).toHaveTextContent('Done 1');
    expect(tab(/Dismissed/)).toHaveTextContent('Dismissed 1');
    expect(tab(/All/)).toHaveTextContent('All 4');
    expect(screen.queryByText('Finished one')).not.toBeInTheDocument();

    await userEvent.click(tab(/Done/));
    expect(screen.getByText('Finished one')).toBeInTheDocument();
    expect(screen.queryByTestId('host-test-1')).not.toBeInTheDocument();
    await userEvent.click(tab(/Dismissed/));
    expect(screen.getByText('Dropped one')).toBeInTheDocument();
    expect(api.listHostTests).toHaveBeenCalledTimes(1);
  });

  it('says so when there is nothing under the chosen status', async () => {
    api.listHostTests.mockResolvedValue(page([]));
    renderSection();
    expect(await screen.findByText('No tests to do on this host.')).toBeInTheDocument();
    await userEvent.click(tab(/All/));
    expect(screen.getByText('No tests on this host yet.')).toBeInTheDocument();
  });

  it('reports a failed load and retries', async () => {
    api.listHostTests.mockRejectedValueOnce(new Error('boom'));
    renderSection();
    expect(await screen.findByRole('alert')).toHaveTextContent(/boom|could not be loaded/i);
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Check response headers')).toBeInTheDocument();
  });

  it('says when the host has more tests than it reads', async () => {
    api.listHostTests.mockResolvedValue(page([test()], 450));
    renderSection();
    expect(await screen.findByText(/Showing the newest 200 of 450 tests/)).toBeInTheDocument();
  });

  it('opens a test named in the URL whatever its status', async () => {
    window.location.hash = '#host-test-3';
    api.listHostTests.mockResolvedValue(page([test({ id: 3, status: 'done', description: 'Finished one' })]));
    renderSection();
    await screen.findByText('Finished one');
    expect(tab(/All/)).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('The portal is exposed.')).toBeInTheDocument();
  });

  it('opens a test asked for from its weakness, whatever the filter shows', async () => {
    api.listHostTests.mockResolvedValue(page([test({ id: 3, status: 'done', description: 'Finished one' })]));
    renderSection();
    await screen.findByText('No tests to do on this host.');
    act(() => openHostTest(3));
    expect(await screen.findByText('Finished one')).toBeInTheDocument();
    expect(screen.getByText('The portal is exposed.')).toBeInTheDocument();
  });

  it('renders worst-case and missing values without breaking', async () => {
    api.listHostTests.mockResolvedValue(page([test({
      tool: null, command: null, expected_result: null, rationale: '', label: null, created_by: null,
      agent_client: null, agent_model: null, description: 'x'.repeat(400),
      last_outcome: undefined, unpromoted_findings: undefined, finding_ids: undefined,
    })]));
    renderSection();
    await screen.findByText('x'.repeat(400));
    expect(within(rowOf(11)).queryByRole('button', { name: 'Copy command' })).not.toBeInTheDocument();
    await open(11);
    expect(screen.getByText(/Proposed by an agent for unknown/)).toBeInTheDocument();
  });

  it('a viewer reads the tests and gets no controls', async () => {
    agent.allowed = false;
    renderSection({ canEdit: false });
    await screen.findByText('Check response headers');
    expect(screen.queryByRole('button', { name: 'Record result' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /More actions/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ask agent/ })).not.toBeInTheDocument();
  });
});

describe('HostTestsSection — recording a result', () => {
  it('opens the side panel with the command and what counts, and needs an outcome and a summary', async () => {
    const onResultRecorded = vi.fn();
    api.recordHostTestResult.mockResolvedValue({
      test: test({ status: 'done', revision: 4, evidence_count: 1, last_outcome: 'no_finding' }),
    });
    renderSection({ onResultRecorded });
    await screen.findByText('Check response headers');
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));

    const dialog = panel();
    expect(within(dialog).getByText('curl -sI https://10.0.0.5/')).toBeInTheDocument();
    expect(within(dialog).getByText(/What counts as a finding/)).toBeInTheDocument();
    const save = within(dialog).getByRole('button', { name: /Save result/ });
    expect(save).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('radio', { name: 'No finding' }));
    expect(save).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText('Summary'), 'Header is set');
    fireEvent.change(within(dialog).getByLabelText(/Output/), { target: { value: 'HTTP/2 200\nx-frame-options: DENY' } });
    await userEvent.click(save);

    await waitFor(() => expect(api.recordHostTestResult).toHaveBeenCalledTimes(1));
    const [id, body] = api.recordHostTestResult.mock.calls[0];
    expect(id).toBe(11);
    expect(body).toMatchObject({
      expected_revision: 3, outcome: 'no_finding', summary: 'Header is set',
      raw_output: 'HTTP/2 200\nx-frame-options: DENY',
    });
    expect(body.request_key).toEqual(expect.any(String));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(onResultRecorded).toHaveBeenCalledTimes(1);
    // The test left the to-do list; the list was not read again.
    expect(await screen.findByText('No tests to do on this host.')).toBeInTheDocument();
    expect(tab(/Done/)).toHaveTextContent('Done 1');
    expect(api.listHostTests).toHaveBeenCalledTimes(1);
  });

  it('a result that settles nothing leaves the test in the list, saying so', async () => {
    api.recordHostTestResult.mockResolvedValue({
      test: test({ status: 'in_progress', revision: 4, evidence_count: 1, last_outcome: 'inconclusive' }),
    });
    renderSection();
    await screen.findByText('Check response headers');
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'Inconclusive' }));
    await userEvent.type(within(panel()).getByLabelText('Summary'), 'Timed out');
    await userEvent.click(within(panel()).getByRole('button', { name: /Save result/ }));
    await waitFor(() => expect(screen.getByTestId('host-test-state-11')).toHaveTextContent('inconclusive'));
    expect(within(rowOf(11)).getByRole('button', { name: 'Record result' })).toBeInTheDocument();
  });

  it('a result that showed an issue says what it needs and offers the next step', async () => {
    api.recordHostTestResult.mockResolvedValue({ test: shown({ revision: 4 }) });
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    renderSection();
    await screen.findByText('Check response headers');
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'Finding' }));
    await userEvent.type(within(panel()).getByLabelText('Summary'), 'No header');
    await userEvent.click(within(panel()).getByRole('button', { name: /Save result/ }));
    // It must not vanish with the to-do filter: it still needs a decision.
    await waitFor(() => expect(screen.getByTestId('host-test-state-11')).toHaveTextContent('issue shown · no finding yet'));
    expect(await screen.findByRole('button', { name: 'Create finding' })).toBeInTheDocument();
  });

  it('keeps what was typed when someone else changed the test, and saves on the fresh revision', async () => {
    api.recordHostTestResult
      .mockRejectedValueOnce({ response: { status: 409 } })
      .mockResolvedValueOnce({ test: test({ status: 'done', revision: 9, evidence_count: 1, last_outcome: 'no_finding' }) });
    renderSection();
    await screen.findByText('Check response headers');
    api.listHostTests.mockResolvedValue(page([test({ revision: 8, assigned_to: 'Bob' })]));
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'No finding' }));
    await userEvent.type(within(panel()).getByLabelText('Summary'), 'Header is set');
    await userEvent.click(within(panel()).getByRole('button', { name: /Save result/ }));
    expect(await within(panel()).findByRole('alert')).toHaveTextContent(/Someone changed this test/);
    expect(within(panel()).getByLabelText('Summary')).toHaveValue('Header is set');
    await userEvent.click(within(panel()).getByRole('button', { name: /Save result/ }));
    await waitFor(() => expect(api.recordHostTestResult).toHaveBeenCalledTimes(2));
    expect(api.recordHostTestResult.mock.calls[1][1].expected_revision).toBe(8);
    // A refusal stored nothing under the first key, or stored a result the
    // analyst has since changed; either way the next save is its own result
    // (the server refuses changed content under a used key).
    expect(api.recordHostTestResult.mock.calls[1][1].request_key)
      .not.toBe(api.recordHostTestResult.mock.calls[0][1].request_key);
  });

  it('shows the server\'s reason when a result cannot be saved', async () => {
    api.recordHostTestResult.mockRejectedValue({ response: { status: 422, data: { detail: 'summary too long' } } });
    renderSection();
    await screen.findByText('Check response headers');
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'Could not run' }));
    await userEvent.type(within(panel()).getByLabelText('Summary'), 'x');
    await userEvent.click(within(panel()).getByRole('button', { name: /Save result/ }));
    expect(await within(panel()).findByRole('alert')).toHaveTextContent('summary too long');
  });

  it('reports typed-but-unsaved text to the inspector\'s leave guard', async () => {
    const onDirtyChange = vi.fn();
    renderSection({ onDirtyChange });
    await screen.findByText('Check response headers');
    await userEvent.click(within(rowOf(11)).getByRole('button', { name: 'Record result' }));
    await userEvent.type(within(panel()).getByLabelText('Summary'), 'half a thought');
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    await userEvent.click(within(panel()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });
});

describe('HostTestsSection — the row\'s menu', () => {
  it('one primary action per row; the rest is in its menu', async () => {
    api.listHostTests.mockResolvedValue(page([test(), test({ id: 12, status: 'done' })]));
    renderSection();
    await screen.findByTestId('host-test-11');
    expect(await menuItems(11)).toEqual(['Claim', 'Dismiss…']);
    await userEvent.click(tab(/Done/));
    expect(within(rowOf(12)).queryByRole('button', { name: 'Record result' })).not.toBeInTheDocument();
    expect(await menuItems(12)).toEqual(['Reopen', 'Record another result', 'Dismiss…']);
  });

  it('Claim assigns the test to the caller', async () => {
    api.updateHostTest.mockResolvedValue(test({ assigned_to_id: 1, assigned_to: 'Me', revision: 4 }));
    renderSection();
    await screen.findByText('Check response headers');
    await pick(11, 'Claim');
    await waitFor(() => expect(api.updateHostTest).toHaveBeenCalledWith(11, { expected_revision: 3, assigned_to_id: 1 }));
    expect(await menuItems(11)).toEqual(['Dismiss…']);
  });

  it('Dismiss needs a reason and sends it', async () => {
    const onDirtyChange = vi.fn();
    api.updateHostTest.mockResolvedValue(test({ status: 'dismissed', dismissed_reason: 'Out of scope', revision: 4 }));
    renderSection({ onDirtyChange });
    await screen.findByText('Check response headers');
    await pick(11, 'Dismiss…');
    const dismiss = screen.getByRole('button', { name: 'Dismiss test' });
    expect(dismiss).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Why this test should not be run'), 'Out of scope');
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    await userEvent.click(dismiss);
    await waitFor(() => expect(api.updateHostTest).toHaveBeenCalledWith(11, {
      expected_revision: 3, status: 'dismissed', dismissed_reason: 'Out of scope',
    }));
    expect(await screen.findByText('No tests to do on this host.')).toBeInTheDocument();
    expect(tab(/Dismissed/)).toHaveTextContent('Dismissed 1');
  });

  it('a stale write says so and reads the list again', async () => {
    api.updateHostTest.mockRejectedValue({ response: { status: 409 } });
    renderSection();
    await screen.findByText('Check response headers');
    await pick(11, 'Claim');
    expect(await screen.findByRole('alert')).toHaveTextContent(/Someone changed that test/);
    await waitFor(() => expect(api.listHostTests).toHaveBeenCalledTimes(2));
  });
});

describe('HostTestsSection — from a result to a finding', () => {
  it('shows a test whose result needs a decision under To do, open, with its evidence asked for by the test\'s id', async () => {
    api.listHostTests.mockResolvedValue(page([shown()]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    renderSection();
    expect(await screen.findByText('No X-Frame-Options header.')).toBeInTheDocument();
    expect(tab(/To do/)).toHaveTextContent('To do 1');
    expect(api.listEvidenceRecords).toHaveBeenCalledWith({ host_test_id: 11, limit: 10, offset: 0 });
  });

  it('a test about no scanned weakness makes a new finding, prefilled, and links it once made', async () => {
    const onFindingCreated = vi.fn();
    api.listHostTests.mockResolvedValue(page([shown()]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    api.createFindingFromEvidence.mockResolvedValue({ finding_id: 77, joined_issue: false });
    renderSection({ onFindingCreated });
    // The form is there at once: one button, one click.
    expect(await screen.findByLabelText('Finding title')).toHaveValue('No X-Frame-Options header.');
    expect(screen.getAllByRole('button', { name: 'Create finding' })).toHaveLength(1);
    api.listHostTests.mockResolvedValue(page([shown({ unpromoted_findings: 0, finding_ids: [77] })]));
    await userEvent.click(screen.getByRole('button', { name: 'Create finding' }));
    await waitFor(() => expect(api.createFindingFromEvidence).toHaveBeenCalledWith(90, {
      title: 'No X-Frame-Options header.', severity: 'high',
    }));
    // The page is told which finding, so it can offer the write-up.
    expect(onFindingCreated).toHaveBeenCalledWith(77);
    // Settled: it leaves To do, and under Done it names its finding.
    await waitFor(() => expect(tab(/To do/)).toHaveTextContent('To do 0'));
    await userEvent.click(tab(/Done/));
    expect(screen.getByTestId('host-test-state-11')).toHaveTextContent('finding #77');
  });

  it('a test that confirms a weakness promotes that weakness in one click, with no title form', async () => {
    api.listHostTests.mockResolvedValue(page([shown({ issue_key: 'cve:CVE-2024-1', issue_title: 'Clickjacking' })]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    api.createFindingFromEvidence.mockResolvedValue({ finding_id: 40, joined_issue: true });
    renderSection();
    expect(await screen.findByText(/Joins the issue.s finding if it has one/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clickjacking' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create finding' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Promote to finding/ }));
    await waitFor(() => expect(api.createFindingFromEvidence).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText('Finding title')).not.toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Finding #40' })).toBeInTheDocument();
  });

  it('says why when the finding cannot be created', async () => {
    api.listHostTests.mockResolvedValue(page([shown()]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    api.createFindingFromEvidence.mockRejectedValue({ response: { status: 409, data: { detail: 'already on a finding' } } });
    renderSection();
    await userEvent.click(await screen.findByRole('button', { name: 'Create finding' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already on a finding');
  });

  it('points at the agent\'s pending proposal for the same result', async () => {
    api.listHostTests.mockResolvedValue(page([shown()]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    api.listProposals.mockResolvedValue({ items: [{ id: 31, evidence_ids: [90] }], total: 1, has_more: false });
    renderSection();
    expect(await screen.findByRole('link', { name: /Review proposal #31/ })).toHaveAttribute('href', '/proposals');
    expect(api.listProposals).toHaveBeenCalledWith({ host_id: 5, status: 'pending', kind: 'finding_create', limit: 100 });
  });

  it('a reader is shown the result and offered no promotion', async () => {
    api.listHostTests.mockResolvedValue(page([shown()]));
    api.listEvidenceRecords.mockResolvedValue({ items: [evidence()], total: 1, has_more: false });
    renderSection({ canEdit: false });
    await screen.findByText('No X-Frame-Options header.');
    expect(screen.queryByRole('button', { name: 'Create finding' })).not.toBeInTheDocument();
  });
});

describe('HostTestsSection — asking the agent', () => {
  it('one control hands the agent this host only: to propose, and to run what is to do', async () => {
    renderSection();
    await screen.findByText('Check response headers');
    await userEvent.click(screen.getByRole('button', { name: /Ask agent/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Propose tests for this host' }));
    expect(agent.give.mock.calls[0][0]).toContain('(host ids): 5.');
    await userEvent.click(screen.getByRole('button', { name: /Ask agent/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Run the test to do' }));
    expect(agent.give.mock.calls[1][0]).toContain('Run the proposed tests on host 5');
  });

  it('does not offer to run tests when nothing is to do', async () => {
    api.listHostTests.mockResolvedValue(page([test({ status: 'done' })]));
    renderSection();
    await screen.findByText('No tests to do on this host.');
    await userEvent.click(screen.getByRole('button', { name: /Ask agent/ }));
    expect((await screen.findAllByRole('menuitem')).map((el) => el.textContent)).toEqual(['Propose tests for this host']);
  });
});

describe('HostTestsSection — a person adds a test', () => {
  const fill = async () => {
    await userEvent.click(screen.getByRole('button', { name: 'Add test' }));
    const sheet = panel();
    await userEvent.type(within(sheet).getByLabelText('What to check'), 'Anonymous FTP login');
    await userEvent.type(within(sheet).getByLabelText('Tool'), 'ftp');
    return sheet;
  };

  it('needs what, with what and why, then stores one test on this host and shows it open', async () => {
    api.listHostTests.mockResolvedValue(page([]));
    api.createHostTests.mockResolvedValue({
      items: [test({ id: 31, tool: 'ftp', description: 'Anonymous FTP login', source: 'person', command: null })],
    });
    renderSection();
    await screen.findByText('No tests to do on this host.');
    const sheet = await fill();
    const save = within(sheet).getByRole('button', { name: 'Add test' });
    // No weakness named, so the reason is the analyst's to give.
    expect(save).toBeDisabled();
    await userEvent.type(within(sheet).getByLabelText('Why'), 'Port 21 is open.');
    await userEvent.click(save);

    await waitFor(() => expect(api.createHostTests).toHaveBeenCalledTimes(1));
    const [body] = api.createHostTests.mock.calls[0][0];
    expect(body).toMatchObject({
      host_id: 5, tool: 'ftp', description: 'Anonymous FTP login', rationale: 'Port 21 is open.',
      priority: 'medium', assigned_to_id: 1,
    });
    expect(body.request_key).toBeTruthy();
    expect(body).not.toHaveProperty('vulnerability_id');
    expect(body).not.toHaveProperty('command');
    // Shown without reading the list again.
    await waitFor(() => expect(rowOf(31)).toBeInTheDocument());
    expect(api.listHostTests).toHaveBeenCalledTimes(1);
  });

  it('keeps what was typed and says why when the server refuses', async () => {
    api.createHostTests.mockRejectedValue({ response: { status: 422, data: { detail: 'No such host.' } } });
    renderSection();
    await screen.findByText('Check response headers');
    const sheet = await fill();
    await userEvent.type(within(sheet).getByLabelText('Why'), 'Port 21 is open.');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add test' }));
    expect(await within(sheet).findByRole('alert')).toBeInTheDocument();
    expect(within(sheet).getByLabelText('What to check')).toHaveValue('Anonymous FTP login');
  });

  it('is not offered to a reader', async () => {
    renderSection({ canEdit: false });
    await screen.findByText('Check response headers');
    expect(screen.queryByRole('button', { name: 'Add test' })).not.toBeInTheDocument();
  });
});

describe('HostTestsSection — stepping to another host', () => {
  it('an answer for the host that was left never replaces this host\'s tests', async () => {
    const answers: Record<number, (value: unknown) => void> = {};
    api.listHostTests.mockImplementation(({ host_id }: { host_id: number }) =>
      new Promise((resolve) => { answers[host_id] = resolve; }));
    const view = renderSection({ hostId: 5 });
    view.rerender(
      <MemoryRouter>
        <HostTestsSection hostId={6} canEdit userId={1} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(answers[6]).toBeDefined());
    await act(async () => { answers[6](page([test({ id: 61, host_id: 6, description: 'On host six' })])); });
    await act(async () => { answers[5](page([test({ id: 51, host_id: 5, description: 'On host five' })])); });
    expect(await screen.findByText('On host six')).toBeInTheDocument();
    expect(screen.queryByText('On host five')).not.toBeInTheDocument();
  });
});

