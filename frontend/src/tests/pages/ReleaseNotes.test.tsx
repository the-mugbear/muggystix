/** Release notes (5.420.0): one section per release, lines by kind, the running one marked. */
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReleaseNotes, { releaseAnchor } from '../../pages/ReleaseNotes';
import * as api from '../../services/api';

// The page reads the address's hash: the real router, not setupTests' fixed location.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));
vi.mock('../../services/api', () => ({ getReleaseNotes: vi.fn() }));
const mocked = vi.mocked(api);

const NOTES = {
  current: { backend: '2.525.0', frontend: '5.420.0' },
  kinds: ['new', 'changed', 'fixed', 'before_upgrading'] as const,
  releases: [
    { backend: '2.525.0', frontend: '5.420.0', date: '2026-10-23', entries: [
      { kind: 'new' as const, text: 'Release notes in Reference.' },
    ] },
    { backend: '2.524.0', frontend: '5.419.0', date: '2026-10-22', entries: [
      { kind: 'changed' as const, text: 'Promote reaches every host of the issue.' },
      { kind: 'fixed' as const, text: 'Enter opens the highlighted host.' },
      { kind: 'fixed' as const, text: 'The CVSS score is read-only under a vector.' },
      { kind: 'before_upgrading' as const, text: 'Back up first.' },
    ] },
  ],
};

beforeEach(() => {
  mocked.getReleaseNotes.mockReset();
});

const renderPage = (path = '/reference/release-notes') =>
  render(<MemoryRouter initialEntries={[path]}><ReleaseNotes /></MemoryRouter>);

describe('ReleaseNotes', () => {
  it('shows each release as a section, newest first, its lines under their kind', async () => {
    mocked.getReleaseNotes.mockResolvedValue({ ...NOTES, kinds: [...NOTES.kinds] });
    const { container } = renderPage();
    expect(await screen.findByText('Release notes in Reference.')).toBeInTheDocument();

    const older = container.querySelector(`#${CSS.escape(releaseAnchor('2.524.0', '5.419.0'))}`) as HTMLElement;
    const fixed = within(older).getByText('Fixed').closest('div') as HTMLElement;
    expect(within(fixed).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Enter opens the highlighted host.', 'The CVSS score is read-only under a vector.',
    ]);
    expect(within(older).getByText('Before upgrading')).toBeInTheDocument();
    expect(within(older).queryByText('Running here')).not.toBeInTheDocument();

    const newest = container.querySelector(`#${CSS.escape(releaseAnchor('2.525.0', '5.420.0'))}`) as HTMLElement;
    expect(within(newest).getByText('Running here')).toBeInTheDocument();
    expect(newest.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('says a refusal instead of drawing no releases', async () => {
    mocked.getReleaseNotes.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { detail: 'Release notes are for analysts and administrators.' } },
    });
    renderPage();
    expect(await screen.findByText(/analysts and administrators/)).toBeInTheDocument();
    expect(screen.queryByText('No release has notes yet.')).not.toBeInTheDocument();
  });

  it('opens on the release the SERVER runs when the address names none (5.420.1)', async () => {
    // The server runs the older pair: the frontend's own build is not asked.
    mocked.getReleaseNotes.mockResolvedValue({
      ...NOTES, kinds: [...NOTES.kinds], current: { backend: '2.524.0', frontend: '5.419.0' },
    });
    const scrolled: string[] = [];
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(function (this: HTMLElement) {
      scrolled.push(this.id);
    });
    renderPage();
    await screen.findByText('Release notes in Reference.');
    expect(scrolled).toEqual([releaseAnchor('2.524.0', '5.419.0')]);
    spy.mockRestore();
  });

  it('opens on the release the address names', async () => {
    mocked.getReleaseNotes.mockResolvedValue({ ...NOTES, kinds: [...NOTES.kinds] });
    const scrolled: string[] = [];
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(function (this: HTMLElement) {
      scrolled.push(this.id);
    });
    renderPage(`/reference/release-notes#${releaseAnchor('2.524.0', '5.419.0')}`);
    await screen.findByText('Release notes in Reference.');
    expect(scrolled).toEqual([releaseAnchor('2.524.0', '5.419.0')]);
    spy.mockRestore();
  });

  it('a hash that is not valid percent-encoding opens on the running release, not an error (review 2026-10-23)', async () => {
    mocked.getReleaseNotes.mockResolvedValue({ ...NOTES, kinds: [...NOTES.kinds] });
    const scrolled: string[] = [];
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(function (this: HTMLElement) {
      scrolled.push(this.id);
    });
    renderPage('/reference/release-notes#%zz');
    expect(await screen.findByText('Release notes in Reference.')).toBeInTheDocument();
    expect(scrolled).toEqual([releaseAnchor('2.525.0', '5.420.0')]);
    spy.mockRestore();
  });
});
