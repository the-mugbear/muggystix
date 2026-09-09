/**
 * UX review H2 — the heatmap's denominator is the site's in-scope inventory
 * (systemic_insight_service: "assessed": len(seg_hosts[k])), so the copy must
 * say "in scope", never "assessed".
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

import { ConditionSegmentHeatmap } from '../../pages/SecurityPosture';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { PostureResponse } from '../../services/api/posture';

const data = {
  heatmap: {
    segments: [{ key: 'site-a', label: 'Site A', assessed: 40 }],
    rows: [
      {
        family: 'weak_tls', family_label: 'Weak TLS', conditions: ['weak_tls'], affected_total: 6,
        cells: [{ segment: 'site-a', value: 0.15, numerator: 6, denominator: 40,
          drilldown_filter: { conditions: ['weak_tls'], site: 'Site A' } }],
      },
      {
        family: 'smb_signing', family_label: 'SMB signing off', conditions: ['smb_signing'], affected_total: 0,
        cells: [{ segment: 'site-a', value: 0, numerator: 0, denominator: 40, drilldown_filter: null }],
      },
    ],
  },
} as unknown as PostureResponse;

describe('SecurityPosture heatmap denominator copy', () => {
  it('labels the denominator as in-scope inventory, never "assessed"', () => {
    const { container } = render(
      <MemoryRouter><TooltipProvider><ConditionSegmentHeatmap data={data} /></TooltipProvider></MemoryRouter>,
    );
    expect(screen.getByText('40 in scope')).toBeInTheDocument();
    expect(screen.getByTitle('Weak TLS — 6 of 40 in-scope hosts affected')).toBeInTheDocument();
    expect(screen.getByText(/not the same as assessed clean/)).toBeInTheDocument();
    // No "n=… assessed" framing anywhere in the visible markup.
    expect(container.textContent).not.toMatch(/n=\d/);
    expect(container.textContent).not.toMatch(/\d+ assessed/);
  });
});
