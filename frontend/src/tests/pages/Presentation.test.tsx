/**
 * Presentation mode's page (5.424.0): the Display reports what it has finished
 * rendering (E1) — only for the revision it is still showing; the Controller
 * drives with `j` / `k` and says who drives (E5: "You are driving", "Ana took
 * control"); a viewer is not offered the mode; the view reads nothing live —
 * only the presentation's own reads.
 */
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
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
  listPresentationAgendas: vi.fn(),
  spotlightPresentation: vi.fn(),
  createPresentationAgenda: vi.fn(),
  savePresentationAgenda: vi.fn(),
  deletePresentationAgenda: vi.fn(),
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
  started_by: person(1, 'Ben'), started_at: null, ended: null, watching: [], displays: [],
  agenda: null, spotlight: null, ...over,
});

const slides: PresentationSlides = {
  stage: { width: 1600, height: 900 },
  filter: null, sites: ['DMZ'], segments: [],
  slides: [
    { key: 'overview:1', kind: 'overview', header: '', footer: 'Report #3 · Overview', page: 1, pages: 1,
      blocks: [{ kind: 'title', md: 'Internal assessment' }], anchors: ['overview'] },
    { key: 'summary:1', kind: 'summary', header: 'Executive summary', footer: 'Report #3 · Executive summary', page: 1, pages: 1,
      blocks: [{ kind: 'p', md: 'We tested the network.' }], anchors: ['summary'] },
    { key: 'scope:1', kind: 'scope', header: 'Scope', footer: 'Report #3 · Scope', page: 1, pages: 1,
      blocks: [{ kind: 'p', md: 'Two networks.' }], anchors: ['scope'] },
  ],
};

const mainStage = () => within(screen.getByTestId('presentation-stage'));

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
  api.listPresentationAgendas.mockResolvedValue([]);
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
    expect(await mainStage().findByTestId('ps-footer')).toHaveTextContent('Report #3 · Overview');
    fireEvent.keyDown(window, { key: 'j' });
    await waitFor(() => expect(api.movePresentation).toHaveBeenCalledWith(
      1, 9, expect.objectContaining({ revision: 4, slide_key: 'summary:1', host_filter: null, agenda: null }), expect.any(Number),
    ));
    expect(await mainStage().findByText('We tested the network.')).toBeInTheDocument();
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
    expect(called.every((n) => [
      'getLivePresentation', 'getPresentationSlides', 'heartbeatPresentation', 'listPresentationAgendas',
    ].includes(n))).toBe(true);
  });
});

describe('presentation mode — stages B and C', () => {
  it('E2: the presenter view keeps Going to, Accepted, Next and each screen apart', async () => {
    let release: (v: PresentationState) => void = () => undefined;
    api.movePresentation.mockImplementation(() => new Promise((r) => { release = r; }));
    // Ana's Display lags: it acknowledged the overview at revision 3.
    const lagging = state({
      displays: [{ client: 1, person: person(2, 'Ana'), status: 'catching_up', fitted: false,
        rendered: { revision: 3, slide_key: 'overview:1', host_filter: null } }],
    });
    api.getLivePresentation.mockResolvedValue({ live: lagging });
    api.heartbeatPresentation.mockResolvedValue(lagging);
    renderAt('/reports/3/present');
    expect(await screen.findByTestId('presenter-going')).toHaveTextContent('Accepted: Overview');
    expect(await within(screen.getByTestId('preview-next')).findByText('We tested the network.')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'j' });                            // held: not yet accepted
    expect(await screen.findByTestId('presenter-going')).toHaveTextContent('Going to: Executive summary');
    expect(await within(screen.getByTestId('preview-going')).findByText('We tested the network.')).toBeInTheDocument();
    expect(within(screen.getByTestId('preview-accepted')).getByText('Internal assessment')).toBeInTheDocument();
    const screenPreview = screen.getByTestId('preview-display-1');
    expect(screenPreview).toHaveTextContent("On Ana's screen · catching up · Overview");
    expect(within(screenPreview).getByText('Internal assessment')).toBeInTheDocument();
    await act(async () => { release(state({ revision: 5, slide_key: 'summary:1' })); });
  });

  it('E6: the driver clicks the slide; the answer\'s spotlight rings it', async () => {
    api.spotlightPresentation.mockResolvedValue(state({ spotlight: { seq: 1, revision: 4, x: 800, y: 450, remaining_ms: 5000 } }));
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    await mainStage().findByText('Internal assessment');
    const stage = mainStage().getByTestId('ps-stage');
    stage.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 450, right: 800, bottom: 450, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(stage, { clientX: 400, clientY: 225 });
    await waitFor(() => expect(api.spotlightPresentation).toHaveBeenCalledWith(1, 9, { client_id: expect.any(String), revision: 4, x: 800, y: 450 }));
    expect(await mainStage().findByTestId('ps-spotlight')).toHaveStyle({ left: '800px', top: '450px' });
  });

  it('E6: a Display does not ring a spotlight for a revision it has not rendered', async () => {
    const ahead = state({ you_are_driving: false, spotlight: { seq: 2, revision: 5, x: 10, y: 10, remaining_ms: 5000 } });
    api.getLivePresentation.mockResolvedValue({ live: ahead });
    api.heartbeatPresentation.mockResolvedValue(ahead);
    renderAt('/reports/3/present/display');
    await screen.findByText('Internal assessment');
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.queryByTestId('ps-spotlight')).toBeNull();
  });

  it('E3: following an agenda, Next goes to the next item', async () => {
    const following = state({
      slide_key: 'overview:1',
      agenda: { id: 4, name: 'Walkthrough', version: 1, latest_version: 2, mode: 'agenda', item_index: 0,
        items: ['overview', 'scope'] },
    });
    api.getLivePresentation.mockResolvedValue({ live: following });
    api.heartbeatPresentation.mockResolvedValue(following);
    api.movePresentation.mockImplementation(async (_p: number, _id: number, body: { slide_key: string; agenda: { item_index: number } }) =>
      state({ ...following, revision: 5, slide_key: body.slide_key,
        agenda: { ...following.agenda!, item_index: body.agenda.item_index } }));
    renderAt('/reports/3/present');
    expect(await screen.findByTestId('agenda-newer')).toHaveTextContent('A newer version of this agenda exists (version 2).');
    fireEvent.keyDown(window, { key: 'j' });
    await waitFor(() => expect(api.movePresentation).toHaveBeenCalledWith(1, 9, expect.objectContaining({
      slide_key: 'scope:1', agenda: { agenda_id: 4, version: 1, mode: 'agenda', item_index: 1 },
    }), expect.any(Number)));
  });
});
