import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
vi.mock('../../services/api', () => ({ getHosts: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// The inspector is the side sheet's too; here it only needs to report dirt.
let reportDirty: ((dirty: boolean) => void) | undefined;
vi.mock('../../components/HostInspector', () => ({
  default: ({ onDirtyChange }: { onDirtyChange?: (d: boolean) => void }) => {
    reportDirty = onDirtyChange;
    return <div>inspector</div>;
  },
}));

import HostDetail from '../../pages/HostDetail';

const renderPage = (state?: Record<string, unknown>) =>
  render(
    <MemoryRouter initialEntries={[{ pathname: '/hosts/5', state }]}>
      <Routes><Route path="/hosts/:hostId" element={<HostDetail />} /></Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  reportDirty = undefined;
});

describe('HostDetail — unsaved-work guard', () => {
  it('leaves straight away when the inspector holds no draft', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Back to Hosts/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hosts'));
  });

  it('a host opened from Operations goes back to the work list, not to Hosts', async () => {
    renderPage({ fromOperations: true });
    fireEvent.click(screen.getByRole('button', { name: /Back to my work/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/operations'));
  });

  it('asks before Back discards a draft, and stays when the operator cancels', async () => {
    renderPage();
    expect(reportDirty).toBeDefined();
    reportDirty?.(true);
    fireEvent.click(screen.getByRole('button', { name: /Back to Hosts/ }));
    expect(await screen.findByText('Discard unsaved work?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
    await waitFor(() => expect(screen.queryByText('Discard unsaved work?')).not.toBeInTheDocument());
    expect(navigate).not.toHaveBeenCalled();
  });

  it('leaves once the operator confirms the discard', async () => {
    renderPage();
    reportDirty?.(true);
    fireEvent.click(screen.getByRole('button', { name: /Back to Hosts/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hosts'));
  });

  it('warns on a tab close or reload only while something is unsaved', () => {
    renderPage();
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    reportDirty?.(true);
    const dirty = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });
});

// v5.243.0 — "Back to my work" worked; Next did not follow the queue, because
// the page only knew how to walk a Hosts query by index.
describe('HostDetail — the Operations queue', () => {
  const queue = { fromOperations: true, hostIds: [9, 5, 12], queueLabel: 'Worth a look' };

  it('Prev / Next step through the section the host was opened from, carrying it along', async () => {
    renderPage(queue);
    expect(screen.getByText('2 of 3 in Worth a look')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hosts/12', { state: queue, replace: true }));
    fireEvent.click(screen.getByRole('button', { name: /Prev/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hosts/9', { state: queue, replace: true }));
    // No Hosts query is re-run for a fixed list.
    const api = await import('../../services/api');
    expect(api.getHosts).not.toHaveBeenCalled();
  });

  it('stops at the ends of the queue', () => {
    renderPage({ ...queue, hostIds: [5, 12] });
    expect(screen.getByRole('button', { name: /Prev/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Next/ })).toBeEnabled();
  });

  it('asks before Next discards a draft', async () => {
    renderPage(queue);
    reportDirty?.(true);
    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    expect(await screen.findByText('Discard unsaved work?')).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('offers Back only when there is no queue, or the host is not in it', () => {
    renderPage({ fromOperations: true });
    expect(screen.queryByRole('button', { name: /Next/ })).not.toBeInTheDocument();
  });

  it('offers Back only when the host is not part of the queue it arrived with', () => {
    renderPage({ ...queue, hostIds: [9, 12] });
    expect(screen.queryByRole('button', { name: /Next/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Back to my work/ })).toBeInTheDocument();
  });
});
