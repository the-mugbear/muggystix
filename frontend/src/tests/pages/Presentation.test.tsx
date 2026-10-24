/**
 * Presentation mode's page (5.424.0): the Display reports what it has finished
 * rendering (E1) — only for the revision it is still showing; the Controller
 * drives with `j` / `k` and says who drives (E5: "You are driving", "Ana took
 * control"); a viewer is not offered the mode; the view reads nothing live —
 * only the presentation's own reads.
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { PresentationState, PresentationSlides } from '../../services/api';

const api = vi.hoisted(() => ({
  getLivePresentation: vi.fn(),
  startPresentation: vi.fn(),
  getPresentationSlides: vi.fn(),
  heartbeatPresentation: vi.fn(),
  movePresentation: vi.fn(),
  takeControlOfPresentation: vi.fn(),
  endPresentation: vi.fn(),
  reportPresentationLayout: vi.fn().mockResolvedValue({ recorded: true }),
  fetchPresentationImage: vi.fn(),
  fetchProfileImage: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const role = vi.hoisted(() => ({ canWrite: true, canExport: true, isProjectAdmin: false }));
vi.mock('../../hooks/useProjectRole', () => ({ useProjectRole: () => role }));
vi.mock('../../hooks/useProjectId', () => ({ useProjectId: () => 1 }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, username: 'ben', role: 'member' } }) }));

import Presentation from '../../pages/Presentation';

const person = (id: number, name: string) => ({ id, name, profile_image: null });

const state = (over: Partial<PresentationState> = {}): PresentationState => ({
  id: 9, report_id: 3, revision: 4, slide_key: 'overview:1', host_filter: null,
  driver: person(1, 'Ben'), you_are_driving: true, driving_elsewhere: false,
  started_by: person(1, 'Ben'), started_at: null, ended: null, watching: [], displays: [], ...over,
});

const slides: PresentationSlides = {
  stage: { width: 1600, height: 900 },
  filter: null, sites: ['DMZ'], segments: [],
  slides: [
    { key: 'overview:1', kind: 'overview', header: '', footer: 'Report #3 · Overview', page: 1, pages: 1,
      blocks: [{ kind: 'title', md: 'Internal assessment' }] },
    { key: 'summary:1', kind: 'summary', header: 'Executive summary', footer: 'Report #3 · Executive summary', page: 1, pages: 1,
      blocks: [{ kind: 'p', md: 'We tested the network.' }] },
  ],
};

const renderAt = (url: string) => render(
  <MemoryRouter initialEntries={[url]}>
    <Routes>
      <Route path="/reports/:reportId/present" element={<Presentation />} />
      <Route path="/reports/:reportId/present/display" element={<Presentation display />} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  role.canExport = true;
  api.getLivePresentation.mockResolvedValue({ live: state() });
  api.getPresentationSlides.mockResolvedValue(slides);
  api.heartbeatPresentation.mockResolvedValue(state());
  api.movePresentation.mockImplementation(async (_p: number, _id: number, body: { slide_key: string }) =>
    state({ revision: 5, slide_key: body.slide_key }));
});

describe('presentation mode', () => {
  it('a viewer is not offered the mode', () => {
    role.canExport = false;
    renderAt('/reports/3/present');
    expect(screen.getByText(/for auditors and above/)).toBeInTheDocument();
    expect(api.getLivePresentation).not.toHaveBeenCalled();
  });

  it('the Controller says who drives and drives with j', async () => {
    renderAt('/reports/3/present');
    expect(await screen.findByText('You are driving')).toBeInTheDocument();
    expect(await screen.findByTestId('ps-footer')).toHaveTextContent('Report #3 · Overview');
    fireEvent.keyDown(window, { key: 'j' });
    await waitFor(() => expect(api.movePresentation).toHaveBeenCalledWith(
      1, 9, expect.objectContaining({ revision: 4, slide_key: 'summary:1', host_filter: null }), expect.any(Number),
    ));
    expect(await screen.findByText('We tested the network.')).toBeInTheDocument();
  });

  it('the tab that lost control says who took it, and Take control is offered', async () => {
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    api.heartbeatPresentation.mockResolvedValue(state({ revision: 5, driver: person(2, 'Ana'), you_are_driving: false, driving_elsewhere: false }));
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)); });
    expect(await screen.findByTestId('lost-control')).toHaveTextContent('Ana took control.');
    expect(screen.getByTestId('take-control')).toBeInTheDocument();
  });

  it('a reloaded driver is told it drives elsewhere', async () => {
    api.getLivePresentation.mockResolvedValue({ live: state({ you_are_driving: false, driving_elsewhere: true }) });
    api.heartbeatPresentation.mockResolvedValue(state({ you_are_driving: false, driving_elsewhere: true }));
    renderAt('/reports/3/present');
    expect(await screen.findByTestId('driving-elsewhere')).toBeInTheDocument();
  });

  it('the Display reports the revision and position it finished rendering', async () => {
    api.getLivePresentation.mockResolvedValue({ live: state({ you_are_driving: false }) });
    api.heartbeatPresentation.mockResolvedValue(state({ you_are_driving: false }));
    renderAt('/reports/3/present/display');
    expect(await screen.findByTestId('presentation-display')).toBeInTheDocument();
    await waitFor(() => {
      const withAck = api.heartbeatPresentation.mock.calls.find(([, , body]) => body.rendered);
      expect(withAck?.[2]).toEqual(expect.objectContaining({
        kind: 'display',
        rendered: { revision: 4, slide_key: 'overview:1', host_filter: null, fitted: false },
      }));
    });
  });

  it('the view reads nothing but the presentation', async () => {
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    const called = Object.entries(api).filter(([, fn]) => fn.mock.calls.length > 0).map(([name]) => name);
    expect(called.every((n) => ['getLivePresentation', 'getPresentationSlides', 'heartbeatPresentation'].includes(n))).toBe(true);
  });
});
