import React from 'react';
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

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/hosts/5']}>
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
