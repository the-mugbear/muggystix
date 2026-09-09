/** "Create briefing" on the posture overview downloads the executive systemic
 *  report from here — no detour through Hosts → Export. Estate-wide on the
 *  overview (it has no site selection); Segments offers the per-site variant. */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

const downloadMock = vi.fn().mockResolvedValue(undefined);
const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../services/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
  p: () => '/api/v1/projects/1',
  setCurrentProjectId: vi.fn(),
  getCurrentProjectId: () => 1,
}));
vi.mock('../../services/api', () => ({
  getPosture: vi.fn().mockRejectedValue(new Error('offline')),
}));
vi.mock('../../services/api/insights', () => ({
  downloadSystemicReport: (...a: unknown[]) => downloadMock(...a),
  familyCellHostsHref: () => '/hosts',
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P' } }),
}));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import SecurityPosture from '../../pages/SecurityPosture';
import { TooltipProvider } from '../../components/ui/tooltip';

describe('SecurityPosture — Create briefing', () => {
  it('downloads the estate-wide briefing from the header', async () => {
    render(<MemoryRouter><TooltipProvider><SecurityPosture /></TooltipProvider></MemoryRouter>);
    const btn = await screen.findByRole('button', { name: /Create briefing/ });
    await userEvent.click(btn);
    await waitFor(() => expect(downloadMock).toHaveBeenCalledTimes(1));
    expect(downloadMock).toHaveBeenCalledWith();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('toasts when the briefing cannot be generated', async () => {
    downloadMock.mockRejectedValueOnce(new Error('boom'));
    render(<MemoryRouter><TooltipProvider><SecurityPosture /></TooltipProvider></MemoryRouter>);
    await userEvent.click(await screen.findByRole('button', { name: /Create briefing/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  });
});
