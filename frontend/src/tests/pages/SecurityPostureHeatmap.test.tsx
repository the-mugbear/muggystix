/**
 * Condition heatmap — three cell states (backend 2.329.0): affected / assessed,
 * assessed-and-clean, and UNASSESSED (no evidence in the family's domain for
 * the site), which must never look like clean.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

// axios is auto-mocked globally (setupTests), so the shared client would be
// undefined at import time; stub it — this test renders one presentational
// component and never calls the API.
vi.mock('../../services/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
  p: () => '/api/v1/projects/1',
  setCurrentProjectId: vi.fn(),
  getCurrentProjectId: () => 1,
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P' } }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { ConditionSegmentHeatmap } from '../../pages/SecurityPosture';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { PostureResponse } from '../../services/api/posture';

const cell = (segment: string, affected: number, assessed: number, in_scope: number) => ({
  segment, affected, assessed, in_scope, unassessed: assessed === 0,
  value: assessed ? affected / assessed : 0, numerator: affected, denominator: assessed,
  drilldown_filter: { conditions: ['weak_tls'], site: segment === 'a' ? 'Site A' : 'Site B' },
});

const data = {
  heatmap: {
    segments: [
      { key: 'a', label: 'Site A', in_scope: 40, assessed: 40 },
      { key: 'b', label: 'Site B', in_scope: 25, assessed: 25 },
    ],
    rows: [
      {
        family: 'encryption_trust', family_label: 'Encryption & trust', conditions: ['weak_tls'],
        evidence_domain: 'web_tls', evidence_domain_label: 'Web / TLS', affected_total: 6,
        cells: [cell('a', 6, 12, 40), cell('b', 0, 0, 25)],
      },
      {
        family: 'lateral_movement', family_label: 'Lateral-movement controls', conditions: ['smb_signing'],
        evidence_domain: 'auth_smb_ad', evidence_domain_label: 'Authentication / SMB / AD', affected_total: 0,
        cells: [cell('a', 0, 18, 40), cell('b', 0, 0, 25)],
      },
    ],
  },
} as unknown as PostureResponse;

const renderHeatmap = () =>
  render(<MemoryRouter><TooltipProvider><ConditionSegmentHeatmap data={data} /></TooltipProvider></MemoryRouter>);

describe('SecurityPosture heatmap — assessed denominator', () => {
  it('shows affected / assessed with the site inventory as context', () => {
    renderHeatmap();
    expect(screen.getByText('40 in scope')).toBeInTheDocument();
    expect(screen.getByTitle('Encryption & trust — 6 of 12 assessed hosts affected (40 in scope)')).toBeInTheDocument();
    expect(screen.getByText('6/12')).toBeInTheDocument();
    // The row says which evidence domain assessed it.
    expect(screen.getByText('via Web / TLS')).toBeInTheDocument();
  });

  it('renders an unassessed cell distinctly from an assessed-and-clean one', () => {
    const { container } = renderHeatmap();
    // Site B has no Web / TLS evidence → unassessed, hatched, explicit title.
    const unassessed = screen.getByTitle(
      "Encryption & trust — not assessed: no Web / TLS evidence for this site's hosts (25 in scope)",
    );
    expect(unassessed).toHaveAttribute('data-state', 'unassessed');
    expect(unassessed.textContent).toBe('n/a');
    // Site A was checked for SMB signing (18 hosts) and nothing found → clean, not unassessed.
    const clean = screen.getByTitle('Lateral-movement controls — 0 of 18 assessed hosts affected (40 in scope)');
    expect(clean).toHaveAttribute('data-state', 'clean');
    expect(clean.textContent).toBe('0/18');
    expect(container.querySelectorAll('[data-state="unassessed"]').length).toBe(2);
    expect(screen.getByText(/nobody looked, which is not the same as clean/)).toBeInTheDocument();
  });
});
