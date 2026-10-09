import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EvidenceRecord } from '../../services/api';

const api = vi.hoisted(() => ({ listEvidenceRecords: vi.fn(), getEvidenceRawOutput: vi.fn() }));
vi.mock('../../services/api', () => api);

import FindingEvidence from '../../components/FindingEvidence';

const record = (over: Partial<EvidenceRecord> = {}): EvidenceRecord => ({
  id: 90, host_test_id: 11, host_id: 5, host_ip: '10.0.0.5', finding_id: 37, finding_host_id: null,
  tool: 'curl', command: 'curl -sI https://10.0.0.5/', outcome: 'finding',
  summary: 'No X-Frame-Options header.', raw_output_preview: 'HTTP/2 200', raw_output_bytes: 10,
  raw_output_truncated_in_preview: false, observed_ip: '10.0.0.5', executed_at: '2026-09-30T11:00:00Z',
  agent_session_id: null, recorded_by: 'Alice Analyst', agent_model: null, agent_client: null,
  created_at: '2026-09-30T11:00:00Z',
  ...over,
});

const renderIt = () => render(<MemoryRouter><FindingEvidence findingId={37} /></MemoryRouter>);

beforeEach(() => vi.clearAllMocks());

describe('FindingEvidence', () => {
  it('shows the result a finding was created from and links back to its test', async () => {
    api.listEvidenceRecords.mockResolvedValue({ items: [record()], total: 1, has_more: false });
    renderIt();
    expect(await screen.findByText('No X-Frame-Options header.')).toBeInTheDocument();
    expect(api.listEvidenceRecords).toHaveBeenCalledWith(1, expect.objectContaining({ finding_id: 37 }), expect.any(AbortSignal));
    expect(screen.getByRole('link', { name: 'The test on 10.0.0.5' })).toHaveAttribute('href', '/hosts/5#host-test-11');
    // Already on the finding: no link to itself.
    expect(screen.queryByRole('link', { name: 'Finding #37' })).not.toBeInTheDocument();
  });

  it('renders nothing for a finding with no evidence, and says so when the read fails', async () => {
    api.listEvidenceRecords.mockResolvedValueOnce({ items: [], total: 0, has_more: false });
    const { container, unmount } = renderIt();
    await waitFor(() => expect(api.listEvidenceRecords).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    api.listEvidenceRecords.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'database is down' } } });
    renderIt();
    expect(await screen.findByRole('alert')).toHaveTextContent('database is down');
  });

  it('survives a record with no test, no address and a very long command', async () => {
    api.listEvidenceRecords.mockResolvedValue({
      items: [record({ host_test_id: null, host_ip: null, observed_ip: null, command: 'x'.repeat(400), recorded_by: null })],
      total: 1, has_more: false,
    });
    renderIt();
    expect(await screen.findByText('No X-Frame-Options header.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /The test on/ })).not.toBeInTheDocument();
  });
});
