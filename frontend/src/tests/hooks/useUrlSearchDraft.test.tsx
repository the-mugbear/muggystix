/**
 * useUrlSearchDraft — a search box whose committed value is the address's.
 *
 * The defect it replaces (code review 2026-10-09, finding 6): a page copied
 * `?search=` into state once and wrote the state back from an effect, so
 * going Back — the address changes, the state does not — put the newer text
 * back over the address the reader had returned to.
 *
 * Tested with the REAL router: setupTests replaces `useLocation`, and a
 * mocked navigation proves nothing about Back.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () =>
  vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';

import { useUrlSearchDraft } from '../../hooks/useUrlSearchDraft';

const Page: React.FC = () => {
  const search = useUrlSearchDraft('search');
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <input aria-label="Search" value={search.draft} onChange={(e) => search.setDraft(e.target.value)} />
      <p data-testid="asked">{search.value}</p>
      <p data-testid="address">{location.pathname + location.search}</p>
      <button type="button" onClick={() => navigate('/things?search=linked&page=4')}>a link</button>
      <button type="button" onClick={() => navigate(-1)}>back</button>
      <button type="button" onClick={() => search.commit()}>go</button>
    </div>
  );
};

const mount = (at: string) => render(<MemoryRouter initialEntries={[at]}><Page /></MemoryRouter>);
const box = () => screen.getByLabelText('Search') as HTMLInputElement;
const asked = () => screen.getByTestId('asked').textContent;
const address = () => screen.getByTestId('address').textContent;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('useUrlSearchDraft', () => {
  it('opens on what the address says', () => {
    mount('/things?search=tls&status=open');
    expect(box().value).toBe('tls');
    expect(asked()).toBe('tls');
  });

  it('commits what was typed once the typing stops — trimmed, page dropped, the other filters kept', async () => {
    mount('/things?status=open&page=3');
    type('  weak');
    await pass(200);
    type('  weak tls ');
    // Still typing: nothing is asked for yet.
    await pass(200);
    expect(asked()).toBe('');
    expect(address()).toBe('/things?status=open&page=3');

    await pass(150);
    expect(asked()).toBe('weak tls');
    expect(address()).toBe('/things?status=open&search=weak+tls');
    // The box keeps what the reader typed, spaces and all.
    expect(box().value).toBe('  weak tls ');
  });

  it('leaves an emptied search out of the address', async () => {
    mount('/things?search=tls');
    type('');
    await pass(300);
    expect(address()).toBe('/things');
    expect(asked()).toBe('');
  });

  it('follows the address when it changes from elsewhere: a link to the same page', async () => {
    mount('/things?search=tls');
    fireEvent.click(screen.getByRole('button', { name: 'a link' }));
    expect(box().value).toBe('linked');
    expect(asked()).toBe('linked');
    // …and does not write itself back over it, now or once a timer fires.
    await pass(1000);
    expect(address()).toBe('/things?search=linked&page=4');
  });

  it('follows Back: the box shows the search of the page the reader returned to, and leaves its address alone', async () => {
    mount('/things?search=first');
    fireEvent.click(screen.getByRole('button', { name: 'a link' }));
    expect(box().value).toBe('linked');
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(box().value).toBe('first');
    expect(asked()).toBe('first');
    await pass(1000);
    expect(address()).toBe('/things?search=first');
  });

  it('Back while something half-typed is pending drops the half-typed text, not the address', async () => {
    mount('/things?search=first');
    fireEvent.click(screen.getByRole('button', { name: 'a link' }));
    type('linked and mo');
    await pass(100);
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(box().value).toBe('first');
    await pass(1000);
    expect(address()).toBe('/things?search=first');
    expect(asked()).toBe('first');
  });

  it('commit() asks at once', async () => {
    mount('/things');
    type('now');
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(asked()).toBe('now');
    await pass(1000);
    expect(address()).toBe('/things?search=now');
  });

  it('typing commits with replace: Back leaves the page, it does not step through keystrokes', async () => {
    mount('/things?search=first');
    fireEvent.click(screen.getByRole('button', { name: 'a link' }));   // one history entry
    type('a');
    await pass(300);
    type('ab');
    await pass(300);
    expect(address()).toBe('/things?search=ab');
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(address()).toBe('/things?search=first');
  });
});
