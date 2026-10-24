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

import Presentation, { keepCurrentInView, STALE_AFTER_MS } from '../../pages/Presentation';

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

describe('presentation mode — the review of 2026-10-24', () => {
  it('a Display left open through End follows the NEXT Start of the report', async () => {
    const shown = state({ revision: 6, you_are_driving: false });
    const ended = state({ revision: 7, you_are_driving: false, ended: { at: 'now', reason: 'ended', by: person(1, 'Ben') } });
    const next = state({ id: 10, revision: 1, slide_key: 'summary:1', you_are_driving: false });
    let endNow = false;
    api.getLivePresentation
      .mockResolvedValueOnce({ live: shown })
      .mockResolvedValueOnce({ live: null })                       // re-read once it ended
      .mockResolvedValue({ live: next });                          // someone started again
    // The server's rule: a Display cannot have rendered a revision the presentation has not reached.
    api.heartbeatPresentation.mockImplementation(async (_p: number, id: number, body: { rendered?: { revision: number } | null }) => {
      const p = id === 9 ? (endNow ? ended : shown) : next;
      if (body.rendered && body.rendered.revision > p.revision) {
        throw Object.assign(new Error('422'), { isAxiosError: true, response: { status: 422, data: { detail: 'A Display cannot have rendered that revision.' } } });
      }
      return p;
    });
    renderAt('/reports/3/present/display');
    // It rendered the first presentation's revision 6 — the ack the next one must never receive.
    await waitFor(() => expect(api.heartbeatPresentation).toHaveBeenCalledWith(
      1, 9, expect.objectContaining({ rendered: expect.objectContaining({ revision: 6 }) }), expect.anything()));
    endNow = true;
    expect(await screen.findByText(/The presentation ended/, {}, { timeout: 3000 })).toBeInTheDocument();
    // Not a dead end: the report's live presentation is read again, and the new one followed.
    await waitFor(() => expect(api.heartbeatPresentation).toHaveBeenCalledWith(1, 10, expect.anything(), expect.anything()),
      { timeout: 5000 });
    expect(await screen.findByText('We tested the network.')).toBeInTheDocument();
    expect(api.heartbeatPresentation.mock.calls.filter(([, id, body]) => id === 10 && body.rendered?.revision === 6)).toEqual([]);
    // …and the ended one is no longer polled.
    const before = api.heartbeatPresentation.mock.calls.filter(([, id]) => id === 9).length;
    await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
    expect(api.heartbeatPresentation.mock.calls.filter(([, id]) => id === 9).length).toBe(before);
  }, 10_000);

  it('an ended presentation, no longer polled, is not "reconnecting"', async () => {
    api.getLivePresentation
      .mockResolvedValueOnce({ live: state({ you_are_driving: false }) })
      .mockResolvedValue({ live: null });
    api.heartbeatPresentation.mockResolvedValue(
      state({ revision: 5, you_are_driving: false, ended: { at: 'now', reason: 'ended', by: person(1, 'Ben') } }));
    renderAt('/reports/3/present/display');
    expect(await screen.findByText(/The presentation ended/)).toBeInTheDocument();
    await act(async () => { await new Promise((r) => setTimeout(r, STALE_AFTER_MS + 1200)); });
    expect(screen.getByText(/The presentation ended/)).toBeInTheDocument();
    expect(screen.queryByText(/Reconnecting/)).not.toBeInTheDocument();
  }, 10_000);

  it('opened with ?start=1 while someone presents: joins, and does not start a new one when theirs ends', async () => {
    const theirs = state({ you_are_driving: false, driver: person(2, 'Ana'), started_by: person(2, 'Ana') });
    let ended = false;
    api.getLivePresentation.mockImplementation(async () => ({ live: ended ? null : theirs }));
    api.heartbeatPresentation.mockImplementation(async () => (ended
      ? { ...theirs, revision: 5, ended: { at: 'now', reason: 'ended', by: person(2, 'Ana') } } : theirs));
    renderAt('/reports/3/present?start=1');
    expect(await screen.findByText('Ana')).toBeInTheDocument();
    ended = true;
    expect(await screen.findByTestId('start-presentation', {}, { timeout: 3000 })).toBeInTheDocument();
    await act(async () => { await new Promise((r) => setTimeout(r, 2500)); });   // a live re-read or two
    expect(api.startPresentation).not.toHaveBeenCalled();
  }, 10_000);

  it('a failed read of the live presentation is not "nobody is presenting"', async () => {
    api.getLivePresentation.mockRejectedValue(Object.assign(new Error('502'), {
      isAxiosError: true, response: { status: 502, data: { detail: 'Bad gateway' } } }));
    const { unmount } = renderAt('/reports/3/present');
    expect(await screen.findByTestId('live-read-failed')).toHaveTextContent(/Trying again/);
    expect(screen.queryByText(/Nobody is presenting/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('start-presentation')).not.toBeInTheDocument();
    unmount();
    renderAt('/reports/3/present/display');
    expect(await screen.findByText('Reconnecting…')).toBeInTheDocument();
  });

  it('a Browse tab is not "reconnecting" between its polls', async () => {
    renderAt('/reports/3/present?browse=1');
    await screen.findByText('Browsing the presentation');
    await waitFor(() => expect(api.heartbeatPresentation).toHaveBeenCalled());
    await act(async () => { await new Promise((r) => setTimeout(r, STALE_AFTER_MS + 1200)); });
    expect(api.heartbeatPresentation).toHaveBeenCalledTimes(1);               // still before its next poll
    expect(screen.queryByText(/Reconnecting/)).not.toBeInTheDocument();
  }, 10_000);

  it('only the latest filter choice lands, and a failed one says so with Retry', async () => {
    let releaseFirst: (v: PresentationSlides) => void = () => undefined;
    api.getPresentationSlides.mockImplementation((_p: number, _r: number, f: { value: string } | null) => {
      if (f?.value === 'slow') return new Promise<PresentationSlides>((r) => { releaseFirst = r; });
      if (f?.value === 'broken') {
        return Promise.reject(Object.assign(new Error('500'), {
          isAxiosError: true, response: { status: 500, data: { detail: 'Server error' } } }));
      }
      return Promise.resolve({ ...slides, filter: f });
    });
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    await mainStage().findByText('Internal assessment');
    const box = screen.getByLabelText('Endpoints matching');
    fireEvent.change(box, { target: { value: 'slow' } });
    fireEvent.submit(box.closest('form')!);
    fireEvent.change(box, { target: { value: 'fast' } });
    fireEvent.submit(box.closest('form')!);
    await waitFor(() => expect(api.movePresentation).toHaveBeenCalledWith(
      1, 9, expect.objectContaining({ host_filter: { kind: 'text', value: 'fast' } }), expect.any(Number)));
    await act(async () => { releaseFirst({ ...slides, filter: { kind: 'text', value: 'slow' } }); });
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(api.movePresentation.mock.calls.filter(([, , body]) => body.host_filter?.value === 'slow')).toEqual([]);

    const again = screen.getByLabelText('Endpoints matching');            // re-seeded with the filter now shown
    fireEvent.change(again, { target: { value: 'broken' } });
    fireEvent.submit(again.closest('form')!);
    expect(await screen.findByTestId('filter-failed')).toHaveTextContent(/Retry/);
  });

  it("a failed read of the report's agendas is not \"no agendas\"", async () => {
    api.listPresentationAgendas.mockRejectedValue(Object.assign(new Error('500'), {
      isAxiosError: true, response: { status: 500, data: { detail: 'Server error' } } }));
    renderAt('/reports/3/present');
    expect(await screen.findByTestId('agendas-failed')).toHaveTextContent(/Retry/);
  });

  it("another driver's text filter re-seeds the filter box", async () => {
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    await mainStage().findByText('Internal assessment');
    expect(screen.getByLabelText('Endpoints matching')).toHaveValue('');
    api.heartbeatPresentation.mockResolvedValue(state({ revision: 6, host_filter: { kind: 'text', value: '10.0.0.' } }));
    await waitFor(() => expect(screen.getByLabelText('Endpoints matching')).toHaveValue('10.0.0.'), { timeout: 3000 });
  });

  it('a presenter remote steps with Page Down and the arrows', async () => {
    renderAt('/reports/3/present');
    await screen.findByText('You are driving');
    await mainStage().findByText('Internal assessment');
    fireEvent.keyDown(window, { key: 'PageDown' });
    await waitFor(() => expect(api.movePresentation).toHaveBeenCalledWith(
      1, 9, expect.objectContaining({ slide_key: 'summary:1' }), expect.any(Number)));
  });

  it('the slide list keeps the current slide in view, scrolling only itself', () => {
    const list = document.createElement('nav');
    const row = document.createElement('button');
    row.setAttribute('aria-current', 'true');
    list.appendChild(row);
    Object.defineProperty(list, 'clientHeight', { value: 100 });
    Object.defineProperty(row, 'offsetTop', { value: 450, configurable: true });
    Object.defineProperty(row, 'offsetHeight', { value: 20 });
    list.scrollTop = 0;
    keepCurrentInView(list);
    expect(list.scrollTop).toBe(370);                              // its bottom at the list's bottom
    Object.defineProperty(row, 'offsetTop', { value: 40, configurable: true });
    keepCurrentInView(list);
    expect(list.scrollTop).toBe(40);                               // above: its top at the list's top
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
