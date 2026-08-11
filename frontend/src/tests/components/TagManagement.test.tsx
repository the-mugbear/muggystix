/**
 * Tag rename/delete — the gap this panel closes is that a typo'd tag was
 * permanent. These pin the parts an operator would be hurt by getting wrong.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TagManagement from '../../components/TagManagement';
import type { HostTagWithCount } from '../../services/api';

const listHostTags = vi.fn();
const updateHostTag = vi.fn();
const deleteHostTag = vi.fn();
vi.mock('../../services/api', () => ({
  listHostTags: (...a: unknown[]) => listHostTags(...a),
  updateHostTag: (...a: unknown[]) => updateHostTag(...a),
  deleteHostTag: (...a: unknown[]) => deleteHostTag(...a),
}));

const success = vi.fn();
const error = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success, error, info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Proj' } }),
}));

// Auto-confirm so the destructive path is exercised; the confirm copy itself
// is asserted separately below via the argument captured here.
const confirmSpy = vi.fn().mockResolvedValue(true);
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => [null, (...a: unknown[]) => confirmSpy(...a)],
}));

const tag = (over: Partial<HostTagWithCount> = {}): HostTagWithCount => ({
  id: 1, name: 'prod', color: null, host_count: 3, ...over,
} as HostTagWithCount);

beforeEach(() => {
  vi.clearAllMocks();
  confirmSpy.mockResolvedValue(true);
  listHostTags.mockResolvedValue([tag()]);
  updateHostTag.mockResolvedValue(tag({ name: 'production' }));
  deleteHostTag.mockResolvedValue(undefined);
});

describe('TagManagement', () => {
  it('lists tags with their host counts', async () => {
    render(<TagManagement />);
    expect(await screen.findByText('prod')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('renames a tag', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    const input = screen.getByLabelText(/rename tag prod/i);
    fireEvent.change(input, { target: { value: 'production' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() =>
      expect(updateHostTag).toHaveBeenCalledWith(1, { name: 'production' }),
    );
  });

  it('does not call the API when the name is unchanged', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(updateHostTag).not.toHaveBeenCalled());
  });

  it('surfaces a name collision instead of silently dropping the edit', async () => {
    // The backend answers 409 when another tag owns the name. Swallowing that
    // would leave the operator believing the rename worked.
    updateHostTag.mockRejectedValue(new Error('conflict'));
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    fireEvent.change(screen.getByLabelText(/rename tag prod/i), {
      target: { value: 'staging' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(error).toHaveBeenCalled());
  });

  it('warns how many hosts a delete will affect before doing it', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /delete prod/i }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    const body = String(confirmSpy.mock.calls[0][0].body);
    expect(body).toMatch(/3 hosts/);
    await waitFor(() => expect(deleteHostTag).toHaveBeenCalledWith(1));
  });

  it('does not delete when the operator cancels', async () => {
    confirmSpy.mockResolvedValue(false);
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /delete prod/i }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(deleteHostTag).not.toHaveBeenCalled();
  });

  it('a failed load reads as an error, not as "no tags"', async () => {
    listHostTags.mockRejectedValue(new Error('boom'));
    render(<TagManagement />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/No tags yet/i)).not.toBeInTheDocument();
  });
});
