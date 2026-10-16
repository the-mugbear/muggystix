/**
 * The Findings page says how many proposed findings wait for review (5.403.0):
 * a finding an agent proposed is on no Findings list until it is accepted.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getProposalSummary: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));

import ProposedFindingsLine from '../../components/findings/ProposedFindingsLine';

const summary = (n: number) => ({
  pending: n + 4, by_kind: n ? { finding_create: n, finding_text: 4 } : { finding_text: 4 },
  pending_mine: 0, by_kind_mine: {}, viewer_is_project_admin: false,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProposedFindingsLine', () => {
  it('counts the project’s pending proposed findings and opens exactly those', async () => {
    api.getProposalSummary.mockResolvedValue(summary(3));
    render(<MemoryRouter><ProposedFindingsLine /></MemoryRouter>);
    const link = await screen.findByRole('link', { name: '3 proposed findings are waiting for review' });
    expect(link).toHaveAttribute('href', '/proposals?kind=finding_create&scope=all');
    expect(api.getProposalSummary).toHaveBeenCalledWith(9, expect.any(AbortSignal));
  });

  it('says nothing when none is waiting', async () => {
    api.getProposalSummary.mockResolvedValue(summary(0));
    const { container } = render(<MemoryRouter><ProposedFindingsLine /></MemoryRouter>);
    await vi.waitFor(() => expect(api.getProposalSummary).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
